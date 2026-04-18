from unittest.mock import AsyncMock, Mock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome
from gateway.platforms.webchat import WebChatAdapter


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


@pytest.mark.asyncio
async def test_fetch_event_does_not_ack_before_processing():
    adapter = _build_adapter()
    adapter._client = Mock()
    adapter._client.get = AsyncMock(
        return_value=_Response(
            payload={
                "eventId": "evt-123",
                "conversationId": "conv-1",
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
    adapter._ack_event.assert_not_called()


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