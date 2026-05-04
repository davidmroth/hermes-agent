# Custom Integrations

This file records the custom integration work that has landed in this branch, plus the pieces that were explored but intentionally not shipped. Keep it updated whenever private diff slices are merged so the next integration task starts from facts instead of archaeology.

## Current Branch Snapshot

| Area | Status | Notes |
| --- | --- | --- |
| WebChat gateway platform | Landed | Reverse-polling adapter, env wiring, trusted auth path, default toolset |
| Renderer-backed briefings | Landed | `create_briefing`, config wiring, ACP/toolset registration, prompt/skill support |
| Briefing HTML fallback skill | Partial | Skill metadata landed, but it depends on `send_html_to_webchat`, which is still missing |
| NeuTTS Air provider | Landed | Runtime provider support plus setup, status, and schema surfaces |
| Gateway media and diagnostics groundwork | Landed | Broader local-file extraction and structured exception logging |
| Docker sidecar override | Not landed | Sidecar source trees were absent in this branch when the integration was done |

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
3. If the check passes, the adapter starts a poll loop.
4. The poll loop calls `GET /api/internal/hermes/inbox/next`.
5. If the WebUI returns an event, Hermes converts it into a `MessageEvent`.
6. If the event includes attachments, Hermes downloads them and stores them in the local image, audio, or document cache.
7. Hermes runs the normal gateway message pipeline and agent flow.
8. Hermes posts the assistant reply to `POST /api/internal/hermes/conversations/{conversationId}/assistant`.
9. Hermes only acks the event after successful processing.
10. Failed events are intentionally left unacked so the WebUI can retry them.

### Important implementation details

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

#### The primary skill is real; the fallback skill is only partially usable today

`skills/research/rendered-briefing/SKILL.md` is the primary path when `create_briefing` is in the active tool list.

`skills/research/briefing-html-fallback/SKILL.md` also landed, but it requires both `write_file` and `send_html_to_webchat`.

`send_html_to_webchat` is not implemented in this branch yet. That means the fallback skill metadata is preserved for future work, but the actual HTML-delivery path is still blocked until that helper tool exists.

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

#### The provider is integrated, but the sidecar is not shipped here

The Hermes-side provider wiring landed. The sidecar service definition did not.

There is no checked-in Docker override in this branch that starts a NeuTTS Air container. If you want to use this provider, you still need to run the service yourself or add the sidecar sources and compose wiring separately.

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

## 5. Focused Validation Commands

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

## 6. Deployment Notes

After runtime code changes land, the running Docker gateway still needs to be rebuilt or restarted.

If WebChat code exists in the repo but the live container is still acting like it only has `api_server`, check that the container was actually rebuilt from the updated source.

The same rule applies to briefing and TTS integrations: config and code can be correct in git while the running container is still on an older image.

## 7. Still Missing or Intentionally Deferred

These items are still not landed in this branch and should not be assumed to exist:

- `send_file_to_webchat`
- `send_html_to_webchat`
- A checked-in Docker sidecar override for NeuTTS Air and the briefing renderer

The Docker override was intentionally skipped because the upstream compose fragment referenced sidecar source trees that were not present here at integration time:

- `.services/NeuTTSTTS`
- `.services/briefing-service`

If those source trees are added later, create the override against the current branch's compose layout instead of replaying the old diff blindly.

## 8. Next-Round Checkpoints

If this custom work is extended again, start here:

1. Confirm the WebUI route contract still matches `gateway/platforms/webchat.py`.
2. Confirm `Platform.WEBCHAT` still resolves as connected only when both URL and token exist.
3. Confirm `hermes-webchat` still exists and is included by `hermes-gateway`.
4. Confirm the renderer token and base URL contract still matches `tools/briefing_tool.py`.
5. Remember that the briefing fallback skill is blocked until `send_html_to_webchat` exists.
6. Remember that NeuTTS Air support in Hermes does not imply the sidecar is provisioned.
7. Rebuild the live Docker gateway before debugging runtime symptoms.