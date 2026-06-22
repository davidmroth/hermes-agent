# Upgrade Playbook — Custom Integrations on Upstream Hermes

This branch layers **WebChat**, **CloakBrowser (docker)**, and **briefing-service** on top of upstream Hermes (`v2026.5.16` baseline). The goal is to keep custom work in **drop-in surfaces** so future upstream releases merge cleanly.

## Layer model

| Layer | Location | Survives upstream merge? |
| --- | --- | --- |
| **Upstream core** | Repo root (match tag) | Replaced each upgrade |
| **WebUI platform plugin** | `../webui/plugin/` (mounted to `~/.hermes/plugins/webchat-platform`) | Single repo for UI + adapter contract |
| **User plugins** | `~/.hermes/plugins/` or `.services/*/plugin` | Outside core tree |
| **Docker sidecars** | `docker-compose.override.yml` | Local only — never commit conflicts with upstream compose |
| **Core fork touchpoints** | `gateway/run.py`, `run_agent.py`, `toolsets.py`, `web/` | Minimize; migrate to hooks over time |

## What upstream already ships

- **Camofox** — `tools/browser_camofox.py`, `BROWSER_BACKEND=camofox`, `@askjo/camofox-browser` in `package.json`
- **Plugin platforms** — `plugins/platforms/*` + `gateway/platform_registry.py`
- **General plugins** — tools, hooks, CLI commands via `hermes_cli/plugins.py`

This branch **restores Camofox** for upstream parity. Docker dev uses **CloakBrowser** via `docker-compose.override.yml` (`BROWSER_BACKEND=cloakbrowser`) — no Camofox container required locally.

## WebChat as a platform plugin (WebUI repo)

All WebUI integration ships from **`webui/plugin/`** and mounts into Hermes:

```
webui/plugin/
├── adapter.py         # reverse-polling gateway adapter
├── gateway_hooks.py   # reconciliation, status buffer, transcript, preview timings
├── tools.py           # send_file_to_webchat, send_html_to_webchat
├── plugin.yaml
└── README.md
```

Hermes core adds only **generic** `GatewayPlatformHooks` (`gateway/platform_registry.py` + `gateway/platform_hook_dispatch.py`). `gateway/run.py` dispatches to hooks — no WebUI-specific logic left in core.

`gateway/platforms/webchat.py` is a thin re-export shim for tests/legacy imports.

**Hindsight** stays in `docker-compose.override.yml` as-is (required sidecar).

### Still in core (next migration)

These WebChat-specific paths remain in core and are the main merge friction:

| File | What |
| --- | --- |
| `gateway/run.py` | Session context reconciliation, system-status buffering, transcript streaming, preview timing reconciliation |
| `run_agent.py` | `webchat_transcript_callback` |
| `toolsets.py` | `hermes-webchat` toolset |
| `tools/webchat_*_tool.py` | File/HTML helpers |
| `gateway/config.py` | `Platform.WEBCHAT` enum member (optional once all checks use `Platform("webchat")`) |
| `web/` | Dashboard chat UI |

**Target:** add generic gateway extension hooks (session reconcile, trusted-auth set, progress metadata, transcript stream) so `run.py` stops branching on `Platform.WEBCHAT`. Until then, keep WebChat core diffs small and documented.

## Briefing service

Ship as a **user plugin** mounted in docker:

```yaml
# docker-compose.override.yml
volumes:
  - ./.services/briefing-service/plugin:/opt/data/plugins/briefing:ro
```

Tests: `tests/plugins/test_briefing_service_plugin.py`, `tests/tools/test_briefing_tool.py`

## CloakBrowser

- Code: `tools/browser_cloakbrowser.py` + routing in `tools/browser_tool.py` (fork commit `2a64539e0`)
- Runtime: `docker-compose.override.yml` only — not required in upstream

Upstream Camofox and fork CloakBrowser coexist; `BROWSER_BACKEND` selects the active backend.

## Recommended upgrade workflow

1. **Tag baseline** — e.g. `git merge v2026.5.17` (or reset + cherry-pick)
2. **Resolve core first** — `gateway/run.py`, `run_agent.py` only where WebChat hooks exist
3. **Re-apply plugin dirs** — `plugins/platforms/webchat/`, briefing plugin mount
4. **Re-apply docker override** — `docker-compose.override.yml` (local file)
5. **Run focused tests in docker:**
   ```bash
   docker compose exec gateway scripts/run_tests.sh tests/gateway/test_webchat.py tests/tools/test_browser_hybrid_routing.py -q
   ```
6. **Drop fork-only cruft** — task-management dashboard, AgentLens, etc. unless still needed

## Slim branch target

Ideal commit stack on top of upstream tag:

1. `feat(webchat): WebUI plugin mount + gateway hooks` (adapter in `webui/plugin`; minimal run.py)
2. `feat(cloakbrowser): optional browser backend`
3. `feat(briefing): plugin + docker sidecar docs`
4. `chore(docker): compose override for local stack`

Everything else should live in `~/.hermes/plugins/` or `.services/`.

## Installing WebChat on a stock upstream checkout

Copy `webui/plugin/` from the WebUI repo (or mount it in docker), set env vars, enable in gateway config:

```bash
WEBCHAT_ENABLED=true
WEBCHAT_URL=http://127.0.0.1:3000
WEBCHAT_SERVICE_TOKEN=<shared-secret>
```

You still need the **core hook slices** in `gateway/run.py` / `run_agent.py` until generic platform hooks land upstream or in a small companion patch set.
