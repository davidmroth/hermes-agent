/**
 * Task Management Dashboard - slot-only Hermes dashboard plugin.
 *
 * The paired task-management theme supplies the readable light palette and
 * cockpit layout. This plugin fills the shell slots with real dashboard status
 * chrome only: a sidebar, compact header summary, and footer status line.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const PLUGINS = window.__HERMES_PLUGINS__;
  if (!SDK || !PLUGINS || !PLUGINS.registerSlot) return;

  const { React } = SDK;
  const { useEffect, useState } = SDK.hooks;
  const { api } = SDK;

  const NAME = "task-management-dashboard";
  const THEME_NAME = "task-management";
  const THEME_STORAGE_KEY = "hermes-dashboard-theme";
  const COLORS = {
    ink: "#172033",
    muted: "#667085",
    line: "rgba(121, 136, 164, 0.22)",
    blue: "#3e69ff",
    green: "#12b886",
    amber: "#f59f00",
    surface: "rgba(255, 255, 255, 0.84)",
  };

  function el(type, props) {
    const children = Array.prototype.slice.call(arguments, 2);
    return React.createElement(type, props || null, ...children);
  }

  function isTaskThemeActive() {
    if (typeof document === "undefined" || typeof window === "undefined") return false;
    let activeTheme = "";
    try {
      activeTheme = window.localStorage.getItem(THEME_STORAGE_KEY) || "";
    } catch {
      activeTheme = "";
    }
    return activeTheme === THEME_NAME && document.documentElement.dataset.layoutVariant === "cockpit";
  }

  function useTaskThemeActive() {
    const [active, setActive] = useState(isTaskThemeActive());
    useEffect(function () {
      if (typeof document === "undefined") return undefined;
      const root = document.documentElement;
      function update() {
        setActive(isTaskThemeActive());
      }
      update();
      const observer = typeof MutationObserver !== "undefined"
        ? new MutationObserver(update)
        : null;
      if (observer) {
        observer.observe(root, { attributes: true, attributeFilter: ["data-layout-variant", "style"] });
      }
      if (typeof window !== "undefined") {
        window.addEventListener("storage", update);
      }
      return function () {
        if (observer) observer.disconnect();
        if (typeof window !== "undefined") window.removeEventListener("storage", update);
      };
    }, []);
    return active;
  }

  function useDashboardStatus() {
    const [status, setStatus] = useState(null);
    useEffect(function () {
      let cancelled = false;
      api.getStatus()
        .then(function (nextStatus) {
          if (!cancelled) setStatus(nextStatus);
        })
        .catch(function () {});
      return function () {
        cancelled = true;
      };
    }, []);
    return status;
  }

  function counts(status) {
    const platformMap = status && status.gateway_platforms && typeof status.gateway_platforms === "object"
      ? status.gateway_platforms
      : {};
    const gatewayOnline = Boolean(status && (status.gateway_running || status.gateway_state === "running"));
    return {
      activeSessions: typeof (status && status.active_sessions) === "number" ? status.active_sessions : 0,
      platformCount: Object.keys(platformMap).length,
      platforms: Object.entries(platformMap).slice(0, 5),
      gatewayOnline,
      gatewayLabel: status ? (gatewayOnline ? "Online" : "Offline") : "Loading",
      version: status && status.version ? "v" + status.version : "unknown",
      updatedAt: status && status.gateway_updated_at ? status.gateway_updated_at : "No recent gateway update",
    };
  }

  function Pill(props) {
    return el(
      "span",
      {
        style: {
          alignItems: "center",
          background: props.background || "rgba(255, 255, 255, 0.72)",
          border: "1px solid " + (props.border || COLORS.line),
          borderRadius: 999,
          color: props.color || COLORS.ink,
          display: "inline-flex",
          fontSize: 13,
          fontWeight: 700,
          gap: 7,
          minHeight: 30,
          padding: "5px 11px",
          whiteSpace: "nowrap",
        },
      },
      props.dot
        ? el("span", {
            style: {
              background: props.dot,
              borderRadius: 999,
              boxShadow: "0 0 0 3px " + props.dot + "22",
              height: 8,
              width: 8,
            },
          })
        : null,
      props.children,
    );
  }

  function Panel(props) {
    return el(
      "section",
      {
        style: {
          background: props.background || COLORS.surface,
          border: "1px solid " + COLORS.line,
          borderRadius: 22,
          boxShadow: props.shadow || "0 16px 36px -34px rgba(23, 32, 51, 0.52)",
          padding: props.padding || 16,
        },
      },
      props.children,
    );
  }

  function Metric(props) {
    return el(
      Panel,
      { padding: "14px 16px", shadow: "none" },
      el("div", { style: { color: COLORS.muted, fontSize: 13, fontWeight: 700 } }, props.label),
      el("div", { style: { color: props.color || COLORS.ink, fontSize: 28, fontWeight: 900, lineHeight: 1.05, marginTop: 5 } }, props.value),
    );
  }

  function PlatformList(props) {
    const state = counts(props.status);
    if (!state.platforms.length) {
      return el("p", { style: { color: COLORS.muted, fontSize: 14, lineHeight: 1.45, margin: 0 } }, "No gateway platforms are reporting status.");
    }
    return el(
      "div",
      { style: { display: "flex", flexDirection: "column", gap: 10 } },
      ...state.platforms.map(function (entry) {
        const name = entry[0];
        const platform = entry[1] || {};
        const online = platform.state === "connected" || platform.state === "running" || platform.state === "ok";
        return el(
          "div",
          {
            key: name,
            style: {
              alignItems: "center",
              borderTop: "1px solid rgba(121, 136, 164, 0.14)",
              display: "flex",
              gap: 10,
              justifyContent: "space-between",
              paddingTop: 10,
            },
          },
          el("span", { style: { color: COLORS.ink, fontSize: 14, fontWeight: 700 } }, name),
          el(Pill, { dot: online ? COLORS.green : COLORS.amber }, platform.state || "unknown"),
        );
      }),
    );
  }

  function SidebarSlot() {
    const active = useTaskThemeActive();
    const status = useDashboardStatus();
    if (!active) return null;

    const state = counts(status);
    return el(
      "div",
      {
        style: {
          boxSizing: "border-box",
          color: COLORS.ink,
          display: "flex",
          flexDirection: "column",
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
          gap: 16,
          minHeight: "100%",
          padding: 0,
        },
      },
      el(
        Panel,
        { background: "linear-gradient(135deg, rgba(62,105,255,0.08), rgba(18,184,134,0.08)), rgba(255,255,255,0.9)" },
        el(
          "div",
          { style: { alignItems: "flex-start", display: "flex", gap: 12, justifyContent: "space-between" } },
          el(
            "div",
            null,
            el("div", { style: { color: COLORS.muted, fontSize: 13, fontWeight: 700 } }, "Gateway"),
            el("div", { style: { color: COLORS.ink, fontSize: 30, fontWeight: 900, lineHeight: 1.05, marginTop: 4 } }, state.gatewayLabel),
          ),
          el(Pill, { dot: state.gatewayOnline ? COLORS.green : COLORS.amber }, state.gatewayLabel),
        ),
        el("p", { style: { color: COLORS.muted, fontSize: 14, lineHeight: 1.5, margin: "14px 0 0" } }, state.gatewayOnline ? "Live operational status from the Hermes dashboard API." : "Gateway is not currently reporting as running."),
        el("p", { style: { color: COLORS.muted, fontSize: 12, lineHeight: 1.4, margin: "6px 0 0" } }, state.updatedAt),
      ),
      el(Metric, { label: "Active sessions", value: String(state.activeSessions), color: COLORS.blue }),
      el(Metric, { label: "Platforms", value: String(state.platformCount), color: COLORS.green }),
      el(Metric, { label: "Version", value: state.version, color: COLORS.ink }),
      el(
        Panel,
        null,
        el("div", { style: { color: COLORS.ink, fontSize: 16, fontWeight: 900, marginBottom: 12 } }, "Platform status"),
        el(PlatformList, { status: status }),
      ),
    );
  }

  function HeaderBannerSlot() {
    const active = useTaskThemeActive();
    const status = useDashboardStatus();
    if (!active) return null;

    const state = counts(status);
    return el(
      "div",
      {
        style: {
          display: "flex",
          justifyContent: "center",
          padding: "0 1.5rem",
          pointerEvents: "none",
          position: "relative",
          zIndex: 3,
        },
      },
      el(
        "div",
        {
          style: {
            alignItems: "center",
            background: "rgba(255, 255, 255, 0.82)",
            border: "1px solid rgba(121, 136, 164, 0.2)",
            borderRadius: 18,
            boxShadow: "0 18px 48px -42px rgba(23, 32, 51, 0.55)",
            color: COLORS.ink,
            display: "flex",
            flexWrap: "wrap",
            fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
            gap: 8,
            justifyContent: "space-between",
            marginTop: 58,
            maxWidth: 1180,
            padding: "10px 12px",
            pointerEvents: "auto",
            width: "100%",
          },
        },
        el("span", { style: { color: COLORS.muted, fontSize: 13, fontWeight: 700, padding: "0 8px" } }, "Dashboard status"),
        el(
          "div",
          { style: { display: "flex", flexWrap: "wrap", gap: 8 } },
          el(Pill, { dot: state.gatewayOnline ? COLORS.green : COLORS.amber }, "Gateway " + state.gatewayLabel.toLowerCase()),
          el(Pill, { dot: COLORS.blue }, state.activeSessions + " active sessions"),
          el(Pill, { dot: COLORS.green }, state.platformCount + " platforms"),
        ),
      ),
    );
  }

  function FooterStatusSlot() {
    const active = useTaskThemeActive();
    const status = useDashboardStatus();
    if (!active) return null;

    return el(
      "span",
      {
        style: {
          color: COLORS.muted,
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
          fontSize: 13,
          fontWeight: 700,
        },
      },
      counts(status).gatewayOnline ? "Workspace ready" : "Workspace standby",
    );
  }

  function HiddenPage() {
    return el(
      "div",
      {
        style: {
          color: COLORS.muted,
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
          padding: "2rem",
        },
      },
      "Task Management Dashboard is a slot-only plugin. Select the Task Management theme to see the sidebar and header chrome.",
    );
  }

  PLUGINS.register(NAME, HiddenPage);
  PLUGINS.registerSlot(NAME, "sidebar", SidebarSlot);
  PLUGINS.registerSlot(NAME, "header-banner", HeaderBannerSlot);
  PLUGINS.registerSlot(NAME, "footer-right", FooterStatusSlot);
})();