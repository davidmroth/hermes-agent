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