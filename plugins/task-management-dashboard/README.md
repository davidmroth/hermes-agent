# Task Management Dashboard

A self-contained Hermes dashboard reskin inspired by clean task-management
interfaces: light canvas, readable text, soft cards, project progress, status
chips, and a productivity sidebar.

The package has two independent pieces:

- `theme/task-management.yaml` paints the dashboard palette, typography,
  rounded card chrome, light header/sidebar surfaces, and the `cockpit` layout
  variant.
- `dashboard/` is a hidden slot-only plugin that fills the cockpit sidebar,
  header-left, header-banner, and footer-right slots with task-dashboard chrome.

No dashboard source files need to be patched. The plugin bundle is a plain IIFE
and uses `window.__HERMES_PLUGIN_SDK__`, so it does not need React or a bundler.

## Install

1. Copy the theme YAML into your Hermes home:

   ```bash
   mkdir -p ~/.hermes/dashboard-themes
   cp theme/task-management.yaml ~/.hermes/dashboard-themes/
   ```

2. Copy the plugin into a dashboard plugin discovery directory. From this
   directory on a user install:

   ```bash
   mkdir -p ~/.hermes/plugins
   cp -r . ~/.hermes/plugins/task-management-dashboard
   ```

   When developing from the Hermes repo, the bundled `plugins/` directory is
   already discovered by the dashboard server.

3. Restart `hermes dashboard`, or force a plugin rescan:

   ```bash
   curl http://127.0.0.1:9119/api/dashboard/plugins/rescan
   ```

4. Open the dashboard and pick **Task Management** from the theme switcher.

## Customising

Edit `theme/task-management.yaml` to adjust the core visual system:

- `palette` controls the light canvas, primary text, vignette, and noise.
- `colorOverrides` pins readable shadcn-compatible tokens for cards, borders,
  muted text, primary/accent, and status colors.
- `componentStyles` controls global card, header, sidebar, tab, footer, and
  backdrop chrome.
- `customCSS` contains small theme-scoped refinements for cockpit layout.

The plugin intentionally reads dashboard CSS variables such as `--color-primary`
and `--theme-font-sans`, so theme edits flow through the sidebar and header
without changing JavaScript.

## Validation

From the repo root, a quick syntax check catches malformed plugin JavaScript:

```bash
node --check plugins/task-management-dashboard/dashboard/dist/index.js
```

To verify discovery while the dashboard is running:

```bash
curl http://127.0.0.1:9119/api/dashboard/themes
curl http://127.0.0.1:9119/api/dashboard/plugins
```

The cockpit sidebar is rendered by the dashboard shell only when the active
theme has `layoutVariant: cockpit`, so the plugin can stay installed while other
themes are active.