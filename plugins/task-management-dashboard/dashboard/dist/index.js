/**
 * Task Management Dashboard - slot-only Hermes dashboard plugin.
 *
 * The paired task-management theme supplies the readable light palette and
 * cockpit layout. This plugin fills the shell slots with productivity chrome:
 * a project sidebar, a compact header summary, a small brand mark, and a calm
 * footer status line. It uses only the dashboard SDK, so it can be dropped into
 * ~/.hermes/plugins without a build step.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const PLUGINS = window.__HERMES_PLUGINS__;
  if (!SDK || !PLUGINS || !PLUGINS.registerSlot) return;

  const { React } = SDK;
  const { useEffect, useMemo, useState } = SDK.hooks;
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
    red: "#ef4444",
    surface: "rgba(255, 255, 255, 0.82)",
  };

  function el(type, props) {
    const children = Array.prototype.slice.call(arguments, 2);
    return React.createElement(type, props || null, ...children);
  }

  function cssVar(name, fallback) {
    if (typeof document === "undefined") return fallback || "";
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback || "";
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
        if (typeof window !== "undefined") {
          window.removeEventListener("storage", update);
        }
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

  function statCounts(status) {
    const activeSessions = Array.isArray(status && status.active_sessions)
      ? status.active_sessions.length
      : 0;
    const connectedPlatforms = Array.isArray(status && status.connected_platforms)
      ? status.connected_platforms.length
      : 0;
    const gatewayOnline = Boolean(status && status.gateway_online);
    return {
      activeSessions,
      connectedPlatforms,
      gatewayOnline,
      todayFocus: Math.min(100, 64 + activeSessions * 8 + (gatewayOnline ? 10 : 0)),
      queueHealth: Math.min(100, 74 + connectedPlatforms * 6),
    };
  }

  function ProgressLine(props) {
    const color = props.color || COLORS.blue;
    return el(
      "div",
      { style: { display: "flex", flexDirection: "column", gap: 7 } },
      el(
        "div",
        { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 } },
        el("span", { style: { color: COLORS.ink, fontWeight: 700, fontSize: 12 } }, props.label),
        el("span", { style: { color: COLORS.muted, fontSize: 11, fontWeight: 600 } }, props.value + "%"),
      ),
      el(
        "div",
        {
          style: {
            height: 8,
            overflow: "hidden",
            borderRadius: 999,
            background: "rgba(121, 136, 164, 0.16)",
          },
        },
        el("div", {
          style: {
            width: props.value + "%",
            height: "100%",
            borderRadius: 999,
            background: color,
            boxShadow: "0 6px 14px -9px " + color,
          },
        }),
      ),
    );
  }

  function Pill(props) {
    return el(
      "span",
      {
        style: {
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          minHeight: 28,
          padding: "5px 10px",
          borderRadius: 999,
          border: "1px solid " + (props.border || COLORS.line),
          background: props.background || "rgba(255, 255, 255, 0.7)",
          color: props.color || COLORS.ink,
          fontSize: 12,
          fontWeight: 700,
          whiteSpace: "nowrap",
        },
      },
      props.dot
        ? el("span", {
            style: {
              width: 7,
              height: 7,
              borderRadius: 999,
              background: props.dot,
              boxShadow: "0 0 0 3px " + props.dot + "22",
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
          border: "1px solid " + COLORS.line,
          borderRadius: 22,
          background: props.background || COLORS.surface,
          boxShadow: props.shadow || "0 16px 36px -34px rgba(23, 32, 51, 0.52)",
          padding: props.padding || 16,
        },
      },
      props.children,
    );
  }

  function SidebarSlot() {
    const active = useTaskThemeActive();
    const status = useDashboardStatus();
    const counts = useMemo(function () {
      return statCounts(status);
    }, [status]);

    if (!active) return null;

    const projects = [
      { name: "Launch board", color: COLORS.blue, done: 76 },
      { name: "Agent follow-ups", color: COLORS.green, done: counts.queueHealth },
      { name: "Review queue", color: COLORS.amber, done: 48 },
    ];
    const tasks = [
      { title: "Review active sessions", state: counts.activeSessions ? "Live" : "Ready", color: COLORS.green },
      { title: "Check gateway signals", state: counts.gatewayOnline ? "Online" : "Quiet", color: counts.gatewayOnline ? COLORS.blue : COLORS.amber },
      { title: "Prepare next sprint notes", state: "Draft", color: COLORS.amber },
    ];

    return el(
      "div",
      {
        style: {
          boxSizing: "border-box",
          color: COLORS.ink,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          minHeight: "100%",
          padding: "14px 10px 18px 0",
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
        },
      },
      el(
        Panel,
        { background: "linear-gradient(135deg, rgba(62,105,255,0.1), rgba(18,184,134,0.11)), rgba(255,255,255,0.86)" },
        el(
          "div",
          { style: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 } },
          el(
            "div",
            null,
            el("div", { style: { color: COLORS.muted, fontSize: 12, fontWeight: 700 } }, "Today"),
            el("div", { style: { marginTop: 3, fontSize: 25, lineHeight: 1.05, fontWeight: 800 } }, "Focus Plan"),
          ),
          el(Pill, { dot: counts.gatewayOnline ? COLORS.green : COLORS.amber, color: COLORS.ink }, counts.gatewayOnline ? "Online" : "Standby"),
        ),
        el("p", { style: { margin: "12px 0 16px", color: COLORS.muted, fontSize: 13, lineHeight: 1.45 } }, "3 priorities planned - next review at 4 PM"),
        el(ProgressLine, { label: "Daily focus", value: counts.todayFocus, color: COLORS.blue }),
      ),
      el(
        Panel,
        null,
        el("div", { style: { marginBottom: 14, fontSize: 14, fontWeight: 800 } }, "Projects"),
        el(
          "div",
          { style: { display: "flex", flexDirection: "column", gap: 13 } },
          ...projects.map(function (project) {
            return el(ProgressLine, {
              key: project.name,
              label: project.name,
              value: project.done,
              color: project.color,
            });
          }),
        ),
      ),
      el(
        Panel,
        null,
        el("div", { style: { marginBottom: 12, fontSize: 14, fontWeight: 800 } }, "Next Tasks"),
        el(
          "div",
          { style: { display: "flex", flexDirection: "column", gap: 10 } },
          ...tasks.map(function (task) {
            return el(
              "div",
              {
                key: task.title,
                style: {
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                  padding: "10px 0",
                  borderTop: "1px solid rgba(121, 136, 164, 0.14)",
                },
              },
              el(
                "div",
                { style: { display: "flex", alignItems: "center", gap: 9, minWidth: 0 } },
                el("span", { style: { width: 9, height: 9, borderRadius: 999, background: task.color, flex: "0 0 auto" } }),
                el("span", { style: { fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, task.title),
              ),
              el("span", { style: { color: COLORS.muted, fontSize: 11, fontWeight: 700 } }, task.state),
            );
          }),
        ),
      ),
      el(
        "div",
        { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 } },
        el(
          Panel,
          { padding: 13, shadow: "none" },
          el("div", { style: { color: COLORS.muted, fontSize: 11, fontWeight: 700 } }, "Sessions"),
          el("div", { style: { marginTop: 4, fontSize: 24, fontWeight: 800 } }, String(counts.activeSessions)),
        ),
        el(
          Panel,
          { padding: 13, shadow: "none" },
          el("div", { style: { color: COLORS.muted, fontSize: 11, fontWeight: 700 } }, "Channels"),
          el("div", { style: { marginTop: 4, fontSize: 24, fontWeight: 800 } }, String(counts.connectedPlatforms)),
        ),
      ),
    );
  }

  function HeaderMarkSlot() {
    const active = useTaskThemeActive();
    if (!active) return null;
    const accent = cssVar("--color-primary", COLORS.blue);
    return el(
      "div",
      {
        style: {
          alignItems: "center",
          color: COLORS.ink,
          display: "flex",
          gap: 9,
          paddingLeft: 14,
          paddingRight: 4,
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
        },
      },
      el(
        "span",
        {
          style: {
            alignItems: "center",
            background: "linear-gradient(135deg, " + accent + ", " + COLORS.green + ")",
            borderRadius: 12,
            boxShadow: "0 12px 24px -18px " + accent,
            color: "#fff",
            display: "inline-flex",
            fontSize: 13,
            fontWeight: 900,
            height: 32,
            justifyContent: "center",
            width: 32,
          },
        },
        "H",
      ),
    );
  }

  function HeaderBannerSlot() {
    const active = useTaskThemeActive();
    const status = useDashboardStatus();
    const counts = statCounts(status);
    if (!active) return null;
    return el(
      "div",
      {
        style: {
          display: "flex",
          justifyContent: "center",
          padding: "0 1rem",
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
            background: "rgba(255, 255, 255, 0.8)",
            border: "1px solid rgba(121, 136, 164, 0.2)",
            borderRadius: 18,
            boxShadow: "0 18px 48px -42px rgba(23, 32, 51, 0.55)",
            color: COLORS.ink,
            display: "flex",
            flexWrap: "wrap",
            gap: 8,
            justifyContent: "space-between",
            marginTop: 56,
            maxWidth: 1180,
            padding: "8px 10px",
            width: "100%",
            fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
            pointerEvents: "auto",
          },
        },
        el("span", { style: { color: COLORS.muted, fontSize: 12, fontWeight: 700, padding: "0 8px" } }, "Workspace pulse"),
        el(
          "div",
          { style: { display: "flex", flexWrap: "wrap", gap: 8 } },
          el(Pill, { dot: COLORS.green }, counts.todayFocus + "% focus"),
          el(Pill, { dot: COLORS.blue }, counts.activeSessions + " active sessions"),
          el(Pill, { dot: counts.gatewayOnline ? COLORS.green : COLORS.amber }, counts.gatewayOnline ? "gateway online" : "gateway idle"),
        ),
      ),
    );
  }

  function FooterStatusSlot() {
    const active = useTaskThemeActive();
    if (!active) return null;
    return el(
      "span",
      {
        style: {
          color: COLORS.muted,
          fontFamily: "var(--theme-font-sans, ui-sans-serif, system-ui, sans-serif)",
          fontSize: 12,
          fontWeight: 700,
        },
      },
      "Workspace ready",
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
  PLUGINS.registerSlot(NAME, "header-left", HeaderMarkSlot);
  PLUGINS.registerSlot(NAME, "header-banner", HeaderBannerSlot);
  PLUGINS.registerSlot(NAME, "footer-right", FooterStatusSlot);
})();