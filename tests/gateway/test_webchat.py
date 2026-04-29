import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome
from gateway.platforms.webchat import (
    WebChatAdapter,
    build_webchat_context_marker,
    build_webchat_context_transcript,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _build_adapter() -> WebChatAdapter:
    config = PlatformConfig(enabled=True, token="svc-token", extra={"url": "http://webui:3000"})
    return WebChatAdapter(config)


def _build_runner_with_history(stored_history, fetched_payload):
    runner = object.__new__(GatewayRunner)
    runner.session_store = Mock()
    runner.session_store.load_transcript.return_value = stored_history
    runner.session_store.rewrite_transcript = Mock()
    runner.adapters = {
        Platform.WEBCHAT: SimpleNamespace(
            fetch_conversation_context=AsyncMock(return_value=fetched_payload)
        )
    }
    return runner


def test_truncate_message_uses_base_chunking_without_placeholder_counts():
    content = "Intro\n\n" + "hello world " * 30 + "\n\n```python\nprint('hello')\n```"

    chunks = WebChatAdapter.truncate_message(content, max_length=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert not any("/0)" in chunk for chunk in chunks)
    assert chunks[-1].endswith(f"/{len(chunks)})")


@pytest.mark.asyncio
async def test_attempt_reconnect_reuses_existing_poll_loop(monkeypatch):
    adapter = _build_adapter()
    old_client = Mock()
    old_client.aclose = AsyncMock()
    adapter._client = old_client
    adapter._poll_task = None

    new_client = Mock()
    new_client.get = AsyncMock(return_value=_Response())
    new_client.post = AsyncMock(return_value=_Response())
    new_client.aclose = AsyncMock()

    monkeypatch.setattr(
        "gateway.platforms.webchat.httpx.AsyncClient",
        lambda timeout: new_client,
    )

    await adapter._attempt_reconnect()

    assert adapter.is_connected is True
    assert adapter._client is new_client
    assert adapter._poll_task is None
    assert not adapter._background_tasks
    old_client.aclose.assert_awaited_once()
    new_client.get.assert_awaited_once_with(
        "http://webui:3000/api/internal/hermes/health",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer svc-token",
        },
    )


@pytest.mark.asyncio
async def test_fetch_event_does_not_ack_before_processing():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.get = AsyncMock(
        return_value=_Response(
            payload={
                "eventId": "evt-123",
                "conversationId": "conv-1",
                "sessionChatId": "session-conv-1",
                "contextUrl": "/api/internal/hermes/conversations/conv-1/context",
                "chatType": "dm",
                "userId": "user-1",
                "text": "hello",
                "attachments": [],
            }
        )
    )
    adapter._ack_event = AsyncMock()

    event = await adapter._fetch_event()

    assert event is not None
    assert event.text == "hello"
    assert event.message_type is MessageType.TEXT
    assert event.source.chat_id == "session-conv-1"
    assert event.raw_message["contextUrl"] == "http://webui:3000/api/internal/hermes/conversations/conv-1/context"
    adapter._ack_event.assert_not_called()


def test_build_webchat_context_transcript_uses_visible_branch_and_excludes_current_message():
    payload = {
        "schemaVersion": 1,
        "exportedAt": "2026-04-25T12:00:00.000Z",
        "conversation": {
            "id": "conv-1",
            "currNode": "assistant-2",
            "lastModified": 42,
        },
        "visibleMessageIds": ["user-1", "assistant-1", "user-2", "assistant-2"],
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "Earlier question",
                "createdAt": "2026-04-25T11:50:00.000Z",
                "attachments": [],
            },
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "Earlier answer",
                "createdAt": "2026-04-25T11:51:00.000Z",
                "attachments": [
                    {
                        "fileName": "report.pdf",
                        "contentType": "application/pdf",
                        "sizeBytes": 1234,
                    }
                ],
            },
            {
                "id": "user-2",
                "role": "user",
                "content": "Newest inbound prompt",
                "createdAt": "2026-04-25T11:59:00.000Z",
                "attachments": [],
            },
            {
                "id": "assistant-2",
                "role": "system",
                "content": "Hermes worker appears stalled.",
                "createdAt": "2026-04-25T11:59:30.000Z",
                "attachments": [],
            },
        ],
    }

    transcript = build_webchat_context_transcript(payload, exclude_message_id="user-2")

    assert transcript[0]["role"] == "session_meta"
    assert transcript[0]["webchat_context"] == build_webchat_context_marker(payload)
    assert [message["role"] for message in transcript[1:]] == ["user", "assistant", "assistant"]
    assert [message["content"] for message in transcript[1:]] == [
        "Earlier question",
        "Earlier answer\n\n[Attachments: report.pdf (application/pdf, 1234 bytes)]",
        "[System status] Hermes worker appears stalled.",
    ]


@pytest.mark.asyncio
async def test_fetch_conversation_context_resolves_relative_url():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.get = AsyncMock(
        return_value=_Response(
            payload={
                "schemaVersion": 1,
                "conversation": {"id": "conv-1", "currNode": "msg-2", "lastModified": 42},
                "visibleMessageIds": [],
                "messages": [],
            }
        )
    )

    payload = await adapter.fetch_conversation_context("/api/internal/hermes/conversations/conv-1/context")

    assert payload is not None
    adapter._client.get.assert_awaited_once_with(
        "http://webui:3000/api/internal/hermes/conversations/conv-1/context",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer svc-token",
        },
    )


def test_build_gateway_command_payload_includes_aliases_and_dynamic_commands(monkeypatch):
    import agent.skill_commands as skill_commands_module
    import agent.skill_utils as skill_utils_module
    import gateway.config as gateway_config_module
    import hermes_cli.commands as commands_module

    monkeypatch.setattr(commands_module, "_resolve_config_gates", lambda: {})
    monkeypatch.setattr(
        commands_module,
        "_iter_plugin_command_entries",
        lambda: [("plugin-cmd", "Plugin command", "[arg]")],
    )
    monkeypatch.setattr(
        gateway_config_module,
        "load_gateway_config",
        lambda: SimpleNamespace(
            reset_triggers=["/new", "/reset"],
            quick_commands={
                "build": {"type": "exec", "command": "npm run build"},
                "notes": {
                    "type": "exec",
                    "command": "cat NOTES.md",
                    "description": "Open design notes",
                },
            }
        ),
    )
    monkeypatch.setattr(
        skill_utils_module,
        "get_disabled_skill_names",
        lambda platform=None: {"Disabled Skill"} if platform == "webchat" else set(),
    )
    monkeypatch.setattr(
        skill_commands_module,
        "scan_skill_commands",
        lambda: {
            "/ship": {"name": "Ship Skill", "description": "Ship it"},
            "/skip": {"name": "Disabled Skill", "description": "Skip me"},
        },
    )

    payload = WebChatAdapter._build_gateway_command_payload()
    by_command = {entry["command"]: entry for entry in payload}

    assert "/new" in by_command
    assert "/reset" in by_command["/new"]["aliases"]
    assert by_command["/new"]["requiresConfirmation"] is True
    assert by_command["/plugin-cmd"]["description"] == "Plugin command"
    assert by_command["/plugin-cmd"]["argsHint"] == "[arg]"
    assert by_command["/build"]["description"] == "exec: npm run build"
    assert by_command["/notes"]["description"] == "Open design notes"
    assert by_command["/ship"]["description"] == "Ship it"
    assert "/skip" not in by_command


@pytest.mark.asyncio
async def test_on_processing_complete_acks_only_success():
    adapter = _build_adapter()
    adapter._ack_event = AsyncMock()
    source = adapter.build_source(chat_id="conv-1", user_id="user-1")
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"eventId": "evt-123"},
    )

    await adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
    adapter._ack_event.assert_not_called()

    await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
    adapter._ack_event.assert_awaited_once_with("evt-123")


@pytest.mark.asyncio
async def test_on_processing_complete_acks_superseded_cancelled_event():
    adapter = _build_adapter()
    adapter._ack_event = AsyncMock()
    source = adapter.build_source(chat_id="conv-1", user_id="user-1")
    session_key = build_session_key(
        source,
        group_sessions_per_user=adapter.config.extra.get("group_sessions_per_user", True),
        thread_sessions_per_user=adapter.config.extra.get("thread_sessions_per_user", False),
    )
    adapter._active_sessions[session_key] = asyncio.Event()
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"eventId": "evt-456"},
    )

    await adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)

    adapter._ack_event.assert_awaited_once_with("evt-456")


@pytest.mark.asyncio
async def test_on_processing_complete_leaves_shutdown_cancelled_event_unacked():
    adapter = _build_adapter()
    adapter._ack_event = AsyncMock()
    source = adapter.build_source(chat_id="conv-1", user_id="user-1")
    session_key = build_session_key(
        source,
        group_sessions_per_user=adapter.config.extra.get("group_sessions_per_user", True),
        thread_sessions_per_user=adapter.config.extra.get("thread_sessions_per_user", False),
    )
    adapter._active_sessions[session_key] = asyncio.Event()
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"eventId": "evt-789"},
    )
    adapter._session_tasks[session_key] = asyncio.current_task()

    await adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)

    adapter._ack_event.assert_not_called()


@pytest.mark.asyncio
async def test_load_history_for_event_ignores_stale_context_version():
    stored_history = [{"role": "user", "content": "Stored transcript"}]
    fetched_payload = {
        "schemaVersion": 1,
        "conversation": {
            "id": "conv-1",
            "currNode": "assistant-1",
            "lastModified": 41,
        },
        "visibleMessageIds": ["user-1", "assistant-1"],
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "Earlier question",
                "createdAt": "2026-04-25T11:50:00.000Z",
                "attachments": [],
            },
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "Earlier answer",
                "createdAt": "2026-04-25T11:51:00.000Z",
                "attachments": [],
            },
        ],
    }
    runner = _build_runner_with_history(stored_history, fetched_payload)
    source = SessionSource(platform=Platform.WEBCHAT, chat_id="conv-1")
    event = MessageEvent(
        text="Newest inbound prompt",
        message_type=MessageType.TEXT,
        message_id="user-2",
        source=source,
        raw_message={
            "conversationId": "conv-1",
            "contextUrl": "http://webui:3000/api/internal/hermes/conversations/conv-1/context",
            "contextVersion": {"currNode": "assistant-2", "lastModified": 42},
        },
    )
    session_entry = SimpleNamespace(session_id="sess-1")

    history = await runner._load_history_for_event(session_entry, event, source)

    assert history == stored_history
    runner.session_store.rewrite_transcript.assert_not_called()


@pytest.mark.asyncio
async def test_load_history_for_event_rewrites_when_context_version_matches():
    stored_history = [{"role": "user", "content": "Stored transcript"}]
    fetched_payload = {
        "schemaVersion": 1,
        "exportedAt": "2026-04-25T12:00:00.000Z",
        "conversation": {
            "id": "conv-1",
            "currNode": "assistant-2",
            "lastModified": 42,
        },
        "visibleMessageIds": ["user-1", "assistant-1", "user-2", "assistant-2"],
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "Earlier question",
                "createdAt": "2026-04-25T11:50:00.000Z",
                "attachments": [],
            },
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "Earlier answer",
                "createdAt": "2026-04-25T11:51:00.000Z",
                "attachments": [],
            },
            {
                "id": "user-2",
                "role": "user",
                "content": "Newest inbound prompt",
                "createdAt": "2026-04-25T11:59:00.000Z",
                "attachments": [],
            },
            {
                "id": "assistant-2",
                "role": "assistant",
                "content": "Latest answer",
                "createdAt": "2026-04-25T12:00:00.000Z",
                "attachments": [],
            },
        ],
    }
    expected_history = build_webchat_context_transcript(fetched_payload, exclude_message_id="user-2")
    runner = _build_runner_with_history(stored_history, fetched_payload)
    source = SessionSource(platform=Platform.WEBCHAT, chat_id="conv-1")
    event = MessageEvent(
        text="Newest inbound prompt",
        message_type=MessageType.TEXT,
        message_id="user-2",
        source=source,
        raw_message={
            "conversationId": "conv-1",
            "contextUrl": "http://webui:3000/api/internal/hermes/conversations/conv-1/context",
            "contextVersion": {"currNode": "assistant-2", "lastModified": 42},
        },
    )
    session_entry = SimpleNamespace(session_id="sess-1")

    history = await runner._load_history_for_event(session_entry, event, source)

    assert history == expected_history
    runner.session_store.rewrite_transcript.assert_called_once_with("sess-1", expected_history)


@pytest.mark.asyncio
async def test_send_document_posts_json_attachment(tmp_path):
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        return _Response(payload={"messageId": "msg-123"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    file_path = tmp_path / "report.md"
    file_path.write_text("# Report\n", encoding="utf-8")

    result = await adapter.send_document(
        chat_id="conv-1",
        file_path=str(file_path),
        caption="Attached report",
        file_name="final-report.md",
    )

    assert result.success is True
    assert result.message_id == "msg-123"
    assert posted["url"] == "http://webui:3000/api/internal/hermes/conversations/conv-1/assistant"
    assert posted["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer svc-token",
    }
    assert posted["json"]["content"] == "Attached report"
    assert posted["json"]["attachments"][0]["fileName"] == "final-report.md"
    assert posted["json"]["attachments"][0]["contentType"] == "text/markdown"
    assert posted["json"]["attachments"][0]["base64Data"]
    assert posted["json"]["senderTrace"]["route"] == "webchat_adapter"
    assert posted["json"]["senderTrace"]["senderTargetUrl"] == posted["url"]
    assert posted["json"]["senderTrace"]["attachmentCount"] == 1
    assert posted["json"]["senderTrace"]["attachmentNames"] == ["final-report.md"]


@pytest.mark.asyncio
async def test_send_document_returns_retryable_error_when_post_fails(tmp_path):
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=RuntimeError("boom"))

    file_path = tmp_path / "artifact.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = await adapter.send_document(chat_id="conv-1", file_path=str(file_path))

    assert result.success is False
    assert result.retryable is True
    assert "Webchat file send failed" in (result.error or "")


@pytest.mark.asyncio
async def test_send_lifts_timings_metadata_to_top_level_payload():
    """Webchat adapter should hoist llama.cpp timings out of metadata."""
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-42"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    timings = {
        "prompt_n": 12,
        "prompt_ms": 34.5,
        "predicted_n": 7,
        "predicted_ms": 89.0,
        "cache_n": 0,
    }
    result = await adapter.send(
        chat_id="conv-1",
        content="Hi there",
        metadata={"thread_id": "t-1", "timings": timings},
    )

    assert result.success is True
    assert posted["json"]["content"] == "Hi there"
    # timings hoisted to top-level
    assert posted["json"]["timings"] == timings
    # metadata retained but timings stripped
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}


@pytest.mark.asyncio
async def test_send_omits_timings_when_metadata_only_has_timings():
    """When metadata contained only timings, no metadata key is posted."""
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-43"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    await adapter.send(
        chat_id="conv-1",
        content="Hello",
        metadata={"timings": {"prompt_n": 1, "prompt_ms": 2.0,
                              "predicted_n": 3, "predicted_ms": 4.0}},
    )

    assert posted["json"]["timings"]["prompt_n"] == 1
    assert "metadata" not in posted["json"]


@pytest.mark.asyncio
async def test_send_lifts_system_role_to_top_level_payload():
    """Webchat adapter should hoist system-role transport metadata."""
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-44"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    result = await adapter.send(
        chat_id="conv-1",
        content="⚡ Interrupting current task.",
        metadata={"thread_id": "t-1", "message_role": "system"},
    )

    assert result.success is True
    assert posted["json"]["role"] == "system"
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}


@pytest.mark.asyncio
async def test_send_lifts_tool_progress_display_type_to_top_level_payload():
    """Webchat adapter should hoist tool-progress display metadata."""
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-45"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    result = await adapter.send(
        chat_id="conv-1",
        content="browser_navigate...",
        metadata={"thread_id": "t-1", "display_type": "tool_progress"},
    )

    assert result.success is True
    assert posted["json"]["displayType"] == "tool_progress"
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}


@pytest.mark.asyncio
async def test_send_uses_message_id_to_update_existing_assistant_message():
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-existing"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    result = await adapter.send(
        chat_id="conv-1",
        content="Updated answer",
        metadata={
            "message_id": "msg-existing",
            "thread_id": "t-1",
            "timings": {"prompt_n": 2, "prompt_ms": 3.5},
        },
    )

    assert result.success is True
    assert posted["json"]["messageId"] == "msg-existing"
    assert posted["json"]["timings"] == {"prompt_n": 2, "prompt_ms": 3.5}
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}