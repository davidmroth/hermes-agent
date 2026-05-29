import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome
from gateway.platforms.webchat import (
    WebChatAdapter,
    build_webchat_context_marker,
    build_webchat_context_transcript,
    export_lacks_tool_round_trip,
    transcript_has_tool_round_trip,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _Response:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

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


def test_fetch_event_does_not_ack_before_processing():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.get = AsyncMock(
        return_value=_Response(
            payload={
                "eventId": "evt-123",
                "conversationId": "conv-1",
                "contextUrl": "/api/internal/hermes/conversations/conv-1/context",
                "publicBaseUrl": "https://briefings.example.com",
                "chatType": "dm",
                "userId": "user-1",
                "text": "hello",
                "attachments": [],
            }
        )
    )
    adapter._ack_event = AsyncMock()

    event = asyncio.run(adapter._fetch_event())

    assert event is not None
    assert event.text == "hello"
    assert event.message_type is MessageType.TEXT
    assert event.source.chat_id == "conv-1"
    assert event.raw_message["contextUrl"] == "http://webui:3000/api/internal/hermes/conversations/conv-1/context"
    assert event.raw_message["publicBaseUrl"] == "https://briefings.example.com"
    adapter._ack_event.assert_not_called()


def test_on_processing_complete_acks_only_success():
    adapter = _build_adapter()
    adapter._ack_event = AsyncMock()
    source = adapter.build_source(chat_id="conv-1", user_id="user-1")
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"eventId": "evt-123"},
    )

    asyncio.run(adapter.on_processing_complete(event, ProcessingOutcome.FAILURE))
    adapter._ack_event.assert_not_called()

    asyncio.run(adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS))
    adapter._ack_event.assert_awaited_once_with("evt-123")


def test_send_document_posts_json_attachment(tmp_path):
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

    result = asyncio.run(
        adapter.send_document(
            chat_id="conv-1",
            file_path=str(file_path),
            caption="Attached report",
            file_name="final-report.md",
        )
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


def test_runner_creates_webchat_adapter():
    runner = object.__new__(GatewayRunner)
    runner.config = Mock()
    runner.config.group_sessions_per_user = True
    runner.config.thread_sessions_per_user = False

    adapter = GatewayRunner._create_adapter(
        runner,
        Platform.WEBCHAT,
        PlatformConfig(enabled=True, token="svc-token", extra={"url": "http://webui:3000"}),
    )

    assert isinstance(adapter, WebChatAdapter)


def test_runner_authorizes_webchat_events_via_service_token_boundary():
    runner = object.__new__(GatewayRunner)
    source = Mock(platform=Platform.WEBCHAT, user_id=None)

    assert GatewayRunner._is_user_authorized(runner, source) is True


def test_build_webchat_context_transcript_uses_visible_branch_and_excludes_current_message():
    payload = {
        "schemaVersion": 1,
        "exportedAt": "2026-04-25T12:00:00.000Z",
        "publicBaseUrl": "https://briefings.example.com",
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
    assert transcript[0]["webchat_context"]["publicBaseUrl"] == "https://briefings.example.com"
    assert [message["role"] for message in transcript[1:]] == ["user", "assistant", "assistant"]
    assert [message["content"] for message in transcript[1:]] == [
        "Earlier question",
        "Earlier answer\n\n[Attachments: report.pdf (application/pdf, 1234 bytes)]",
        "[System status] Hermes worker appears stalled.",
    ]


def test_build_webchat_context_transcript_excludes_tool_progress_breadcrumbs():
    """Cosmetic tool_progress system messages must NOT be replayed to the model.

    Replaying them (mapped to the assistant role) is what degrades long webchat
    sessions: the model ends up looking at an assistant monologue where tool use
    appears as prose, and stops emitting real tool calls. They should be dropped
    while real user/assistant turns and genuine system status survive.
    """
    payload = {
        "schemaVersion": 1,
        "exportedAt": "2026-04-25T12:00:00.000Z",
        "conversation": {"id": "conv-1", "currNode": "assistant-2", "lastModified": 7},
        "visibleMessageIds": [
            "user-1",
            "tool-1",
            "assistant-1",
            "tool-2",
            "assistant-2",
            "status-1",
        ],
        "messages": [
            {"id": "user-1", "role": "user", "content": "Build the system", "attachments": []},
            {
                "id": "tool-1",
                "role": "system",
                "content": '🐍 execute_code: "import subprocess..."',
                "extra": {"displayType": "tool_progress"},
                "attachments": [],
            },
            {"id": "assistant-1", "role": "assistant", "content": "Good, now let me install.", "attachments": []},
            {
                "id": "tool-2",
                "role": "system",
                "content": '💻 terminal: "cd /opt/hermes..."',
                "extra": {"displayType": "tool_progress"},
                "attachments": [],
            },
            {"id": "assistant-2", "role": "assistant", "content": "Done.", "attachments": []},
            {"id": "status-1", "role": "system", "content": "Hermes worker appears stalled.", "attachments": []},
        ],
    }

    transcript = build_webchat_context_transcript(payload)

    assert transcript[0]["role"] == "session_meta"
    # tool_progress breadcrumbs and interim assistant narration before them are dropped.
    assert [m["role"] for m in transcript[1:]] == ["user", "assistant", "assistant"]
    assert [m["content"] for m in transcript[1:]] == [
        "Build the system",
        "Done.",
        "[System status] Hermes worker appears stalled.",
    ]
    # No tool-input preview text leaks into the model-facing transcript.
    assert not any("execute_code" in m["content"] for m in transcript[1:])
    assert not any("terminal:" in m["content"] for m in transcript[1:])


def test_tool_round_trip_presence_helpers():
    assert export_lacks_tool_round_trip({"messages": [{"role": "user", "content": "hi"}]})
    assert not export_lacks_tool_round_trip(
        {"messages": [{"role": "assistant", "toolCalls": [{"id": "call-1"}]}]}
    )
    assert transcript_has_tool_round_trip(
        [{"role": "assistant", "tool_calls": [{"id": "call-1", "type": "function"}]}]
    )
    assert not transcript_has_tool_round_trip([{"role": "user", "content": "hi"}])


def test_load_history_for_event_keeps_tool_structured_session_transcript():
    stored_history = [
        {"role": "session_meta", "webchat_context": {"conversationId": "conv-1"}},
        {"role": "user", "content": "Earlier question"},
        {
            "role": "assistant",
            "content": "navigating",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "browser_navigate", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "page html"},
    ]
    fetched_payload = {
        "schemaVersion": 1,
        "conversation": {"id": "conv-1", "currNode": "assistant-2", "lastModified": 99},
        "visibleMessageIds": ["user-1", "assistant-1", "tool-1", "assistant-2"],
        "messages": [
            {"id": "user-1", "role": "user", "content": "Earlier question", "attachments": []},
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "Let me open the page.",
                "attachments": [],
            },
            {
                "id": "tool-1",
                "role": "system",
                "content": '🌐 browser_navigate: "https://example.com"',
                "extra": {"displayType": "tool_progress"},
                "attachments": [],
            },
            {"id": "assistant-2", "role": "assistant", "content": "Done.", "attachments": []},
        ],
    }
    runner = _build_runner_with_history(stored_history, fetched_payload)
    source = SessionSource(platform=Platform.WEBCHAT, chat_id="conv-1")
    event = MessageEvent(
        text="Follow-up",
        message_type=MessageType.TEXT,
        message_id="user-2",
        source=source,
        raw_message={
            "conversationId": "conv-1",
            "contextUrl": "http://webui:3000/api/internal/hermes/conversations/conv-1/context",
            "contextVersion": {"currNode": "assistant-2", "lastModified": 99},
        },
    )
    session_entry = SimpleNamespace(session_id="sess-1")

    history = asyncio.run(runner._load_history_for_event(session_entry, event, source))

    assert history == stored_history
    runner.session_store.rewrite_transcript.assert_not_called()


def test_fetch_conversation_context_resolves_relative_url():
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

    payload = asyncio.run(
        adapter.fetch_conversation_context("/api/internal/hermes/conversations/conv-1/context")
    )

    assert payload is not None
    adapter._client.get.assert_awaited_once_with(
        "http://webui:3000/api/internal/hermes/conversations/conv-1/context",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer svc-token",
        },
    )


def test_sync_slash_commands_posts_gateway_catalog():
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        from gateway.platforms.webchat import _command_catalog_hash

        posted["url"] = url
        posted["json"] = json
        posted["headers"] = headers
        return _Response(
            payload={
                "ok": True,
                "acceptedCount": len(json["commands"]),
                "catalogHash": _command_catalog_hash(json["commands"]),
            }
        )

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    asyncio.run(adapter._sync_slash_commands())

    assert posted["url"] == "http://webui:3000/api/internal/hermes/commands"
    assert posted["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer svc-token",
    }
    new_entry = next(entry for entry in posted["json"]["commands"] if entry["command"] == "/new")
    assert new_entry["requiresConfirmation"] is True
    assert new_entry["aliases"] == ["/reset"]
    assert "/clear" not in {entry["command"] for entry in posted["json"]["commands"]}


def test_sync_slash_commands_rejects_mismatched_acknowledgement():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.post = AsyncMock(
        return_value=_Response(payload={"ok": True, "acceptedCount": 1, "catalogHash": "wrong"})
    )

    with pytest.raises(RuntimeError, match="acknowledgement mismatch"):
        asyncio.run(adapter._sync_slash_commands())


def test_connect_syncs_slash_commands_after_health_check():
    adapter = _build_adapter()
    fake_client = Mock()
    fake_client.get = AsyncMock(return_value=_Response(payload={"ok": True}))
    fake_client.aclose = AsyncMock()
    fake_task = Mock()
    fake_task.add_done_callback = Mock()

    def _create_task(coro):
        coro.close()
        return fake_task

    with (
        patch("gateway.platforms.webchat.httpx.AsyncClient", return_value=fake_client),
        patch.object(adapter, "_sync_slash_commands", AsyncMock()) as mock_sync,
        patch("gateway.platforms.webchat.asyncio.create_task", side_effect=_create_task),
    ):
        connected = asyncio.run(adapter.connect())

    assert connected is True
    mock_sync.assert_awaited_once()
    fake_client.get.assert_awaited_once_with(
        "http://webui:3000/api/internal/hermes/health",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer svc-token",
        },
    )


def test_connect_returns_false_when_slash_command_sync_cannot_be_verified():
    adapter = _build_adapter()
    fake_client = Mock()
    fake_client.get = AsyncMock(return_value=_Response(payload={"ok": True}))
    fake_client.aclose = AsyncMock()

    with (
        patch("gateway.platforms.webchat.httpx.AsyncClient", return_value=fake_client),
        patch.object(
            adapter,
            "_sync_slash_commands_with_retry",
            AsyncMock(side_effect=RuntimeError("ack failed")),
        ),
    ):
        connected = asyncio.run(adapter.connect())

    assert connected is False
    fake_client.aclose.assert_awaited_once()


def test_reconnect_poll_client_retries_with_backoff_until_success():
    adapter = _build_adapter()
    adapter._mark_connected()
    old_client = Mock()
    old_client.aclose = AsyncMock()
    adapter._client = old_client
    adapter._reconnect_backoff_seconds = 2.0
    adapter._reconnect_max_backoff_seconds = 5.0

    sleep_calls = []

    async def _sleep(delay):
        sleep_calls.append(delay)

    establish = AsyncMock(side_effect=[RuntimeError("offline"), RuntimeError("still offline"), None])

    with (
        patch.object(adapter, "_establish_client", establish),
        patch("gateway.platforms.webchat.asyncio.sleep", side_effect=_sleep),
    ):
        recovered = asyncio.run(adapter._reconnect_poll_client(RuntimeError("poll failed")))

    assert recovered is True
    assert sleep_calls == [2.0, 4.0]
    old_client.aclose.assert_awaited_once()


def test_reconnect_poll_client_retries_after_auth_failure():
    adapter = _build_adapter()
    adapter._mark_connected()
    old_client = Mock()
    old_client.aclose = AsyncMock()
    adapter._client = old_client

    request = httpx.Request("GET", "http://webui:3000/api/internal/hermes/health")
    response = httpx.Response(401, request=request)
    auth_error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

    sleep_calls = []

    async def _sleep(delay):
        sleep_calls.append(delay)

    establish = AsyncMock(side_effect=[auth_error, None])

    with (
        patch.object(adapter, "_establish_client", establish),
        patch("gateway.platforms.webchat.asyncio.sleep", side_effect=_sleep),
    ):
        recovered = asyncio.run(adapter._reconnect_poll_client(auth_error))

    assert recovered is True
    assert sleep_calls == [1.0]
    old_client.aclose.assert_awaited_once()


def test_poll_loop_reconnects_after_fetch_error_then_resumes_handling():
    adapter = _build_adapter()
    adapter._mark_connected()
    adapter.handle_message = AsyncMock(side_effect=lambda event: adapter._mark_disconnected())
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=adapter.build_source(chat_id="conv-1", user_id="user-1"),
        raw_message={"eventId": "evt-123"},
    )

    fetch_event = AsyncMock(side_effect=[RuntimeError("socket closed"), event])

    with (
        patch.object(adapter, "_fetch_event", fetch_event),
        patch.object(adapter, "_reconnect_poll_client", AsyncMock(return_value=True)) as reconnect,
        patch("gateway.platforms.webchat.asyncio.sleep", AsyncMock()),
    ):
        asyncio.run(adapter._poll_loop())

    reconnect.assert_awaited_once()
    adapter.handle_message.assert_awaited_once_with(event)


def test_load_history_for_event_ignores_stale_context_version():
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

    history = asyncio.run(runner._load_history_for_event(session_entry, event, source))

    assert history == stored_history
    runner.session_store.rewrite_transcript.assert_not_called()


def test_load_history_for_event_rewrites_when_context_version_matches():
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

    history = asyncio.run(runner._load_history_for_event(session_entry, event, source))

    assert history == expected_history
    runner.session_store.rewrite_transcript.assert_called_once_with("sess-1", expected_history)


def test_send_lifts_timings_metadata_to_top_level_payload():
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
    result = asyncio.run(
        adapter.send(
            chat_id="conv-1",
            content="Hi there",
            metadata={"thread_id": "t-1", "timings": timings},
        )
    )

    assert result.success is True
    assert posted["json"]["content"] == "Hi there"
    assert posted["json"]["timings"] == timings
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}


def test_send_uses_message_id_to_update_existing_assistant_message():
    adapter = _build_adapter()
    posted = {}

    async def _post(url, json, headers):
        posted["json"] = json
        return _Response(payload={"messageId": "msg-existing"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)

    result = asyncio.run(
        adapter.send(
            chat_id="conv-1",
            content="Updated answer",
            metadata={
                "message_id": "msg-existing",
                "thread_id": "t-1",
                "timings": {"prompt_n": 2, "prompt_ms": 3.5},
            },
        )
    )

    assert result.success is True
    assert posted["json"]["messageId"] == "msg-existing"
    assert posted["json"]["timings"] == {"prompt_n": 2, "prompt_ms": 3.5}
    assert posted["json"]["metadata"] == {"thread_id": "t-1"}


def test_process_message_background_propagates_event_timings_to_webchat_send():
    adapter = _build_adapter()
    posted = {}
    expected_timings = {
        "prompt_eval_count": 48,
        "prompt_eval_duration": 125_000_000,
        "eval_count": 96,
        "eval_duration": 640_000_000,
        "predicted_per_second": 150.0,
    }

    async def _post(url, json, headers):
        if url.endswith("/typing") or url.endswith("/typing/stop"):
            return _Response(payload={"ok": True})
        posted["json"] = json
        return _Response(payload={"messageId": "msg-final"})

    adapter._client = Mock()
    adapter._client.post = AsyncMock(side_effect=_post)
    adapter._run_processing_hook = AsyncMock()

    async def _handler(active_event):
        active_event._hermes_timings = expected_timings
        return "Final answer"

    adapter._message_handler = AsyncMock(side_effect=_handler)

    source = adapter.build_source(chat_id="conv-1", user_id="user-1")
    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"eventId": "evt-123"},
        message_id="msg-user",
    )
    asyncio.run(adapter._process_message_background(event, "agent:main:webchat:dm:conv-1"))

    assert posted["json"]["content"] == "Final answer"
    assert posted["json"]["replyToMessageId"] == "msg-user"
    assert posted["json"]["timings"] == expected_timings