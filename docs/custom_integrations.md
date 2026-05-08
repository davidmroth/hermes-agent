# Custom Integrations

This file records the custom integration work that has landed in this branch, plus the pieces that were explored but intentionally not shipped. Keep it updated whenever private diff slices are merged so the next integration task starts from facts instead of archaeology.

## Recently Completed Follow-Up Slices

The selected follow-up set from `v2026.4.23.diff` is now landed in this branch:

- WebChat helper tools for downloadable files and previewable HTML
- WebChat delivery plumbing outside the base adapter
- WebChat runner context reconciliation and status-buffering behavior
- llama.cpp timings capture and propagation
- Dashboard/plugin follow-up work from the diff

These were selected together because they complete the WebChat delivery path, unblock the HTML fallback briefing skill, surface timings in the browser UI, and finish the remaining dashboard-facing diff slice that was left behind after the first pass.

One follow-up detail matters for future merges: the first timing landing was not the whole fix. We initially landed llama.cpp timing extraction and preview reconciliation, but ordinary non-previewed WebChat replies still dropped timings until a later send-order fix in `gateway/platforms/base.py`. That post-landing correction is part of the current branch state and is documented below so the next integration does not stop at the same false milestone.

## Current Branch Snapshot

| Area | Status | Notes |
| --- | --- | --- |
| WebChat gateway platform | Landed | Reverse-polling adapter, env wiring, trusted auth path, default toolset |
| Renderer-backed briefings | Landed | `create_briefing`, config wiring, ACP/toolset registration, prompt/skill support |
| Briefing HTML fallback skill | Landed | `send_html_to_webchat` now exists, so the fallback skill is actionable instead of placeholder-only |
| NeuTTS Air provider | Landed | Runtime provider support plus setup, status, and schema surfaces |
| Gateway media and diagnostics groundwork | Landed | Broader local-file extraction and structured exception logging |
| WebChat helper tools and delivery surfaces | Landed | File/HTML helpers, cron `webui` alias, platform detection, and dashboard cron wiring |
| WebChat runner context and timings | Landed | Context reconciliation, WebChat system-status metadata, preview timing reconciliation, llama.cpp timing propagation, and the post-landing non-previewed send-order fix |
| Dashboard plugin follow-up | Landed | Theme-gated Strike Freedom slots plus the bundled Task Management dashboard plugin/theme |
| Docker sidecar override | Landed | `docker-compose.override.yml` and the `.services/NeuTTSTTS` and `.services/briefing-service` trees now exist in this branch |

## 1. WebChat Platform

### What landed

We integrated Hermes with the sibling browser WebUI through a reverse-polling adapter called `webchat`.

Before this work, the runtime could have `WEBCHAT_*` environment variables set and `hermes status` would still say Webchat was configured, but the gateway had no real `WEBCHAT` platform, no adapter, and nothing polling the WebUI inbox.

The landed WebChat slice added:

- A real gateway platform: `Platform.WEBCHAT`
- Environment and config wiring for `WEBCHAT_*`
- A reverse-polling adapter in `gateway/platforms/webchat.py`
- Gateway runner support so the adapter is actually created and trusted
- A dedicated default toolset: `hermes-webchat`
- Focused tests for config, adapter behavior, and platform toolset fallback

### Files added or changed

#### Hermes runtime

- `gateway/config.py`
  - Added `Platform.WEBCHAT`
  - Added `WEBCHAT_*` environment parsing
  - Added connection gating so WebChat only counts as connected when both the service token and base URL exist

- `gateway/platforms/webchat.py`
  - New adapter implementation
  - Performs health check against the WebUI
  - Polls the WebUI inbox for queued user messages
  - Downloads inbound attachments to the Hermes cache
  - Sends assistant messages back to the WebUI
  - Sends typing and stop-typing signals
  - Acks processed events only after successful handling

- `gateway/run.py`
  - Registers `Platform.WEBCHAT` in `_create_adapter()`
  - Treats WebChat events as already authenticated at the service-token boundary, similar to webhook and Home Assistant trust rules

- `toolsets.py`
  - Added `hermes-webchat`
  - Added `hermes-webchat` to `hermes-gateway`

- `hermes_cli/status.py`
  - Added WebChat to the status/config surface

#### Tests

- `tests/gateway/test_webchat.py`
- `tests/gateway/test_config.py`
- `tests/gateway/test_platform_connected_checkers.py`
- `tests/hermes_cli/test_tools_config.py`

### Required Hermes-side environment variables

Important variables:

- `WEBCHAT_ENABLED=true`
- `WEBCHAT_URL=http://<webui-host>:<port>`
- `WEBCHAT_SERVICE_TOKEN=<shared-service-token>`

Optional variables:

- `WEBCHAT_POLL_INTERVAL`
  - Seconds between empty-poll retries

- `WEBCHAT_TIMEOUT_SECONDS`
  - HTTP timeout for WebUI health, inbox, download, typing, ack, and assistant-post requests

- `WEBCHAT_PUBLIC_BASE_URL`
  - Public-facing base URL used in outbound message payloads when it differs from the internal URL

- `WEBCHAT_HOME_CHANNEL`
- `WEBCHAT_HOME_CHANNEL_NAME`

### Required WebUI contract

This Hermes integration assumes the sibling WebUI exposes the following authenticated endpoints:

- `GET /api/internal/hermes/health`
  - Used during adapter connect and liveness checks

- `GET /api/internal/hermes/inbox/next`
  - Reverse-polling entrypoint
  - Returns `204` when there is no queued event
  - Returns a JSON event payload when work is waiting

- `POST /api/internal/hermes/commands`
  - Stores the current Hermes gateway-visible slash command catalog for browser autocomplete

- `POST /api/internal/hermes/events/{eventId}/ack`
  - Marks an inbox event as processed

- `POST /api/internal/hermes/conversations/{conversationId}/assistant`
  - Stores assistant replies and uploaded attachments

- `POST /api/internal/hermes/conversations/{conversationId}/typing`
- `POST /api/internal/hermes/conversations/{conversationId}/typing/stop`

- `GET /api/internal/hermes/attachments/{attachmentId}/download`
  - Used when inbound browser messages contain uploaded attachments

All of these are authenticated with:

- `Authorization: Bearer <WEBCHAT_SERVICE_TOKEN>`

### Runtime message flow

1. Hermes starts the gateway and creates `WebChatAdapter`.
2. The adapter checks `GET /api/internal/hermes/health`.
3. Hermes posts the current gateway slash command catalog to `POST /api/internal/hermes/commands`.
4. If the check passes, the adapter starts a poll loop.
5. The poll loop calls `GET /api/internal/hermes/inbox/next`.
6. If the WebUI returns an event, Hermes converts it into a `MessageEvent`.
7. If the event includes attachments, Hermes downloads them and stores them in the local image, audio, or document cache.
8. Hermes runs the normal gateway message pipeline and agent flow.
9. Hermes posts the assistant reply to `POST /api/internal/hermes/conversations/{conversationId}/assistant`.
10. Hermes only acks the event after successful processing.
11. Failed events are intentionally left unacked so the WebUI can retry them.

### Important implementation details

#### Slash command sync is push-only

The WebUI does not poll Hermes for slash commands.

Hermes pushes the current gateway-visible command catalog to `POST /api/internal/hermes/commands` during adapter connect, and the WebUI serves that last stored catalog from its own durable cache.

If slash commands come back empty in the browser, debug the push path or the receiver-side cache persistence. Do not add a WebUI polling loop as a fallback.

#### `conversationId` is the outbound routing key

Use `conversationId` when posting replies back to the WebUI.

`sessionChatId` is useful for Hermes-side session labeling and diagnostics, but the WebUI assistant endpoint is conversation-scoped.

#### Attachments are normalized into Hermes caches

Inbound WebUI attachments are downloaded and converted into local cached files using the existing cache helpers:

- `cache_image_from_bytes(...)`
- `cache_audio_from_bytes(...)`
- `cache_document_from_bytes(...)`

That lets the rest of the agent stack treat browser uploads like any other inbound local media artifact.

#### Local files and remote images are handled differently

For outbound responses:

- Local files are uploaded back to the WebUI as JSON attachments with base64 payloads.
- Remote image URLs are left as markdown image content so the browser UI can render them naturally.

That split keeps the browser UX clean without forcing every remote image through a file-upload path.

#### Ack policy matters

WebChat does not ack an event when processing fails.

That behavior is deliberate. It preserves retry semantics in the WebUI queue.

There is one exception: if a message was cancelled because a newer event superseded it for the same session, Hermes will ack the cancelled event to avoid pointless re-delivery of stale work.

#### WebChat is trusted at the service-token boundary

In `gateway/run.py`, `_is_user_authorized()` treats `Platform.WEBCHAT` like an already-authenticated source.

This is correct only because the WebUI internal Hermes endpoints enforce the shared bearer token before the event ever reaches Hermes.

Do not add separate end-user allowlist gating for WebChat unless the trust model changes.

### Critical gotchas

#### A new platform needs both runtime wiring and a toolset

Adding a new gateway platform is not enough. You also need a matching `hermes-<platform>` toolset in `toolsets.py`.

Without that, `hermes_cli.tools_config._get_platform_tools(..., platform)` falls back to a default toolset name that does not exist, and the platform comes up with the wrong effective tool selection.

For WebChat, that meant adding:

- `hermes-webchat`
- `hermes-webchat` inside `hermes-gateway` includes

#### `hermes status` is not the source of truth for adapter connectivity

`hermes status` reports whether WebChat looks configured from env, not whether the adapter is actually running and polling.

The real sources of truth are:

- gateway logs
- runtime gateway state
- actual WebUI inbox and health traffic

#### The WebUI contract is custom and tightly coupled

This is not a generic OpenAI-compatible integration.

The adapter depends on WebUI-specific internal routes for:

- health
- inbox dequeue
- ack
- assistant message persistence
- typing indicators
- attachment download

If those routes change, Hermes stops working until this adapter is updated.

## 2. Renderer-Backed Briefings

### What landed

We added a renderer-backed `create_briefing` tool that submits a structured briefing payload to a separate rendering service, optionally waits for completion, and returns renderer URLs plus a WebUI preview path.

The landed briefing slice added:

- `tools/briefing_tool.py` with the `create_briefing` tool and request validation
- `briefing` config in `hermes_cli/config.py`
- Toolset registration in `toolsets.py` and `hermes_cli/tools_config.py`
- ACP title and tool-kind mapping in `acp_adapter/tools.py`
- A primary rendered-briefing skill and prompt-selection coverage

### Files added or changed

- `tools/briefing_tool.py`
- `hermes_cli/config.py`
- `toolsets.py`
- `hermes_cli/tools_config.py`
- `acp_adapter/tools.py`
- `skills/research/rendered-briefing/SKILL.md`
- `skills/research/briefing-html-fallback/SKILL.md`
- `agent/prompt_builder.py`
- `tests/tools/test_briefing_tool.py`
- `tests/agent/test_prompt_builder.py`

### Config and runtime contract

Config lives under `briefing` in `config.yaml`:

- `renderer_base_url`
- `request_timeout_seconds`
- `poll_interval_seconds`
- `max_wait_seconds`

Auth uses:

- `BRIEFING_RENDERER_SERVICE_TOKEN`

Optional env override:

- `BRIEFING_RENDERER_BASE_URL`

Default renderer base URL behavior:

- In containers: `http://briefing:8080`
- On host: `http://127.0.0.1:9910`

### Important implementation details

#### The primary skill is real; the fallback skill is now usable end to end

`skills/research/rendered-briefing/SKILL.md` is the primary path when `create_briefing` is in the active tool list.

`skills/research/briefing-html-fallback/SKILL.md` also landed, and it now has the delivery helper it depended on.

The fallback path depends on both `write_file` and `send_html_to_webchat`, and both are now available in this branch.

#### The tool is meant to be called in the same turn as the research synthesis

The prompt and skill flow assume the model researches, structures, and renders in one pass instead of deferring rendering to a later turn.

## 3. NeuTTS Air Provider

### What landed

We added `neutts-air` as a first-class TTS provider in `tools/tts_tool.py` and updated the surrounding CLI, status, setup, and schema surfaces so the provider is selectable end to end.

### Files added or changed

- `tools/tts_tool.py`
- `hermes_cli/config.py`
- `hermes_cli/setup.py`
- `hermes_cli/nous_subscription.py`
- `hermes_cli/web_server.py`
- `hermes_cli/tools_config.py`
- `tests/tools/test_tts_neutts_air.py`
- `tests/tools/test_tts_max_text_length.py`
- `tests/hermes_cli/test_tts_surfaces.py`
- `tests/hermes_cli/test_setup.py`
- `tests/hermes_cli/test_setup_model_provider.py`

### Config and runtime contract

Set:

- `tts.provider: neutts-air`

Provider config lives under `tts.neutts-air`:

- `base_url`
- `ref_audio`
- `ref_text`
- `timeout_seconds`

Default base URL behavior:

- In containers: `http://neutts-air:8000`
- On host: `http://127.0.0.1:9099`

### Important implementation details

#### Voice cloning requires both reference inputs

If you want cloned voice output, both `tts.neutts-air.ref_audio` and `tts.neutts-air.ref_text` must be set. Supplying only one is treated as invalid configuration.

#### The provider wiring and sidecar override now ship together here

The Hermes-side provider wiring landed first, and this branch now also carries the checked-in sidecar override and supporting service trees.

If NeuTTS Air is not available at runtime, debug the live Docker deployment first before assuming the provider wiring regressed.

## 4. Shared Gateway Groundwork

### What landed

We also landed the smaller shared pieces that made the larger integrations usable and debuggable:

- Broader local-file extraction in `gateway/platforms/base.py`
  - Detects more document, archive, config, and source-file paths in model output
- Structured exception diagnostics in `gateway/error_debug.py`
  - Used by `gateway/platforms/base.py` and `gateway/run.py`
- Stronger platform guidance in `agent/prompt_builder.py`
  - Pushes Telegram, Signal, and WebChat flows toward explicit file sending when appropriate

### Files added or changed

- `gateway/error_debug.py`
- `gateway/platforms/base.py`
- `gateway/run.py`
- `agent/prompt_builder.py`
- `tests/gateway/test_error_debug.py`
- `tests/gateway/test_extract_local_files.py`
- `tests/gateway/test_platform_base.py`
- `tests/agent/test_prompt_builder.py`

### Why this matters

These changes are easy to overlook because they are not new top-level features, but they are part of the custom integration contract now:

- WebChat and other platforms rely on the broader file extraction behavior for attachment delivery
- Gateway failures are much easier to debug because the shared exception path now emits structured diagnostics instead of low-context log noise

## 5. WebChat Follow-Through and Dashboard Plugins

### What landed

The second integration pass finished the remaining WebChat, timing, and dashboard slices that were still open after the first landing:

- `send_file_to_webchat` and `send_html_to_webchat` now exist as first-class helper tools
- outbound WebChat delivery accepts uploaded files and previewable HTML through the shared send-message path
- cron delivery now recognizes the `webui` alias and `WEBCHAT_HOME_CHANNEL`
- CLI/platform detection and dashboard cron UI now expose WebChat as a delivery target
- the WebChat adapter can reconcile browser conversation state from `contextUrl` and `contextVersion`
- WebChat system notifications now carry explicit system-role metadata for status, approval, and deferred background-review messages
- failed WebChat runs preserve buffered retry details in the final visible error text
- previewed WebChat replies can be reconciled back onto the existing browser message using the preview `message_id`
- `run_agent.py` now extracts llama.cpp timing payloads and returns them with the final agent result
- the Strike Freedom cockpit slots only render when the matching theme is active
- the missing Task Management dashboard plugin and paired theme are now bundled in-tree

### Files added or changed

- `tools/send_message_tool.py`
- `tools/webchat_file_tool.py`
- `tools/webchat_html_tool.py`
- `toolsets.py`
- `tools/delegate_tool.py`
- `cron/scheduler.py`
- `hermes_cli/platforms.py`
- `hermes_cli/tools_config.py`
- `web/src/pages/CronPage.tsx`
- `web/src/i18n/en.ts`
- `web/src/i18n/zh.ts`
- `web/src/i18n/types.ts`
- `gateway/platforms/webchat.py`
- `gateway/run.py`
- `run_agent.py`
- `plugins/strike-freedom-cockpit/dashboard/dist/index.js`
- `plugins/task-management-dashboard/README.md`
- `plugins/task-management-dashboard/dashboard/dist/index.js`
- `plugins/task-management-dashboard/dashboard/manifest.json`
- `plugins/task-management-dashboard/theme/task-management.yaml`
- `tests/tools/test_send_message_tool.py`
- `tests/tools/test_webchat_file_tool.py`
- `tests/tools/test_webchat_html_tool.py`
- `tests/cron/test_scheduler.py`
- `tests/hermes_cli/test_tools_config.py`
- `tests/gateway/test_webchat.py`
- `tests/gateway/test_run_progress_topics.py`
- `tests/run_agent/test_run_agent.py`

### Important implementation details

#### WebChat context is now browser-authoritative when the page provides a fresh context snapshot

When an inbound WebChat event includes a `contextUrl`, Hermes fetches the browser-side conversation graph and rewrites the local transcript only when the fetched marker is fresh enough to match the event's `contextVersion`.

That keeps Hermes from replaying stale local history after the user has edited or branched the conversation in the Web UI.

#### Timings are extracted once per turn and attached to the final visible WebChat reply

`run_agent.py` now captures llama.cpp-style timings payloads from either the top-level response or usage metadata and returns them in the turn result.

`gateway/run.py` then uses those timings to reconcile a previewed WebChat assistant message back onto the original browser message record instead of sending a duplicate final answer.

#### The normal non-previewed WebChat reply path needed a second fix after the first timing landing

The first timing pass looked complete because two things were already true:

- `run_agent.py` extracted llama.cpp-style timings from top-level response fields and native usage fields
- previewed WebChat replies could reconcile timings back onto the preview message

But ordinary non-previewed WebChat replies were still wrong.

The root cause was ordering inside `gateway/platforms/base.py::_process_message_background()`:

- the adapter built `_thread_metadata` before awaiting `self._message_handler(event)`
- `gateway/run.py::_handle_message_with_agent()` only sets `event._hermes_timings` after the agent finishes
- result: the normal final send path snapshotted metadata too early, so `gateway/platforms/webchat.py` never received top-level `timings` on ordinary replies even though extraction already worked

This was easy to miss because a test that pre-populates `event._hermes_timings` before `_process_message_background()` runs will pass even though the real runtime ordering is still broken.

The actual fix was:

- keep typing metadata thread-only
- rebuild delivery metadata after the handler returns
- use that rebuilt metadata for the text send and all media sends so WebChat sees `metadata["timings"]` only after the handler has attached `event._hermes_timings`

The focused Hermes-side regression for this is:

- `tests/gateway/test_webchat.py::test_process_message_background_propagates_event_timings_to_webchat_send`

That regression now mutates `event._hermes_timings` inside the mocked handler so it matches production ordering instead of the easier but misleading pre-seeded event setup.

There is also a companion receiver-side regression in the sibling WebUI repo:

- `service/frontend/src/lib/server/maintenance.test.js`

That test protects the maintenance page model so stored assistant timings remain visible in the `Recent assistant timings` section.

The live smoke test is the WebUI maintenance page:

- `Recent Hermes delivery traces` should show new timing-bearing sends as `webchat_adapter+timings`
- `Recent assistant timings` should advance with a fresh reply row that has timings

If new traces still show `webchat_adapter` without the `+timings` suffix, the sender is still not posting a top-level `timings` payload, regardless of whether extraction tests pass.

#### Dashboard slot plugins must gate themselves on the active theme

The slot system is global. Without theme gating, a slot-only plugin can bleed its sidebar or footer chrome into unrelated themes.

The Strike Freedom cockpit plugin now checks the active theme name and cockpit layout variant before rendering any slot content. The Task Management plugin follows the same pattern.

## 6. Focused Validation Commands

Use the repo wrapper, not raw `pytest`:

```bash
./scripts/run_tests.sh ...
```

The focused validation commands used for the landed slices were:

```bash
./scripts/run_tests.sh tests/gateway/test_webchat.py tests/gateway/test_config.py tests/gateway/test_platform_connected_checkers.py tests/hermes_cli/test_tools_config.py -q
```

```bash
./scripts/run_tests.sh tests/tools/test_briefing_tool.py tests/hermes_cli/test_tools_config.py -q
```

```bash
./scripts/run_tests.sh tests/tools/test_tts_max_text_length.py tests/tools/test_tts_neutts_air.py tests/hermes_cli/test_tts_surfaces.py tests/hermes_cli/test_setup.py tests/hermes_cli/test_setup_model_provider.py -q
```

```bash
./scripts/run_tests.sh tests/agent/test_prompt_builder.py tests/gateway/test_platform_base.py tests/gateway/test_extract_local_files.py tests/gateway/test_error_debug.py -q
```

```bash
./scripts/run_tests.sh tests/tools/test_send_message_tool.py tests/tools/test_webchat_file_tool.py tests/tools/test_webchat_html_tool.py -q
```

```bash
./scripts/run_tests.sh tests/cron/test_scheduler.py tests/hermes_cli/test_tools_config.py -q
```

```bash
./scripts/run_tests.sh tests/gateway/test_webchat.py tests/gateway/test_run_progress_topics.py::test_run_agent_previewed_webchat_response_reconciles_timings tests/gateway/test_run_progress_topics.py::test_webchat_background_review_notification_is_system_metadata tests/run_agent/test_run_agent.py::TestLlamaCppTimingsExtraction tests/run_agent/test_run_agent.py::TestRunConversation::test_stop_finish_reason_surfaces_llamacpp_timings -q
```

```bash
./scripts/run_tests.sh tests/gateway/test_webchat.py::test_process_message_background_propagates_event_timings_to_webchat_send -q
```

Companion receiver-side regression in the sibling WebUI repo:

```bash
docker compose run --rm webui node --import tsx --test src/lib/server/maintenance.test.js
```

## 7. Deployment Notes

After runtime code changes land, the running Docker gateway still needs to be rebuilt or restarted.

If WebChat code exists in the repo but the live container is still acting like it only has `api_server`, check that the container was actually rebuilt from the updated source.

The same rule applies to briefing and TTS integrations: config and code can be correct in git while the running container is still on an older image.

## 8. Still Missing or Intentionally Deferred

No selected WebChat, briefing, timing, or dashboard items from this integration round remain pending in this branch.

The only safe assumption for future merge work is that this ledger, not the original diff, is the current source of truth.

If a later upstream diff touches the same surfaces again, re-check the current branch before replaying anything into:

- `gateway/platforms/webchat.py`
- `gateway/run.py`
- `run_agent.py`
- `toolsets.py`
- `cron/scheduler.py`
- `plugins/strike-freedom-cockpit/`
- `plugins/task-management-dashboard/`

## 9. Next-Round Checkpoints

If this custom work is extended again, start here:

1. Confirm the WebUI route contract still matches `gateway/platforms/webchat.py`.
2. Confirm `Platform.WEBCHAT` still resolves as connected only when both URL and token exist.
3. Confirm `hermes-webchat` still exists and is included by `hermes-gateway`.
4. Confirm the renderer token and base URL contract still matches `tools/briefing_tool.py`.
5. Confirm `send_file_to_webchat` and `send_html_to_webchat` are still registered in `toolsets.py` and still blocked from delegate-tool child runs.
6. Confirm WebChat preview reconciliation still attaches timings onto the previewed browser message instead of sending a duplicate final answer.
7. Confirm the slot-only dashboard plugins still gate themselves on the active theme and cockpit layout.
8. Rebuild the live Docker gateway before debugging runtime symptoms.
9. Confirm the normal non-previewed WebChat send path still rebuilds delivery metadata after `self._message_handler(event)` returns. If metadata is constructed before the handler runs, timings will disappear again even though extraction and preview reconciliation still pass.
10. Use the WebUI `/maintenance` page as the live contract check. Fresh timing-bearing replies should create `webchat_adapter+timings` traces and advance the `Recent assistant timings` section. If not, keep debugging the sender path instead of the maintenance page.