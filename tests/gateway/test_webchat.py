import asyncio
from unittest.mock import AsyncMock, Mock

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome
from gateway.platforms.webchat import WebChatAdapter
from gateway.run import GatewayRunner


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


def test_fetch_event_does_not_ack_before_processing():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.get = AsyncMock(
        return_value=_Response(
            payload={
                "eventId": "evt-123",
                "conversationId": "conv-1",
                "contextUrl": "/api/internal/hermes/conversations/conv-1/context",
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