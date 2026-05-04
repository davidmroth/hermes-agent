"""Webchat platform adapter.

This adapter integrates Hermes with the sibling browser web UI service.
It polls the web UI for queued inbound browser messages and posts assistant
messages back to the web UI over authenticated HTTP.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)
from gateway.session import build_session_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_TIMEOUT_SECONDS = 30.0


def check_webchat_requirements() -> bool:
    """Return True when the webchat adapter dependencies are available."""
    return HTTPX_AVAILABLE


def _normalize_webchat_context_url(base_url: str, context_url: Any) -> Optional[str]:
    raw = str(context_url or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.startswith("/"):
        return f"{base_url}{raw}"
    return f"{base_url}/{raw}"


class WebChatAdapter(BasePlatformAdapter):
    """Browser-chat adapter backed by the web UI service."""

    SUPPORTS_MESSAGE_EDITING = True
    MAX_MESSAGE_LENGTH = 65536

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.WEBCHAT)
        extra = config.extra or {}
        self._base_url = (
            extra.get("url") or os.getenv("WEBCHAT_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._public_base_url = (
            extra.get("public_base_url")
            or os.getenv("WEBCHAT_PUBLIC_BASE_URL", self._base_url)
        ).rstrip("/")
        self._service_token = config.token or os.getenv("WEBCHAT_SERVICE_TOKEN", "")
        self._poll_interval = float(
            extra.get("poll_interval")
            or os.getenv("WEBCHAT_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL))
        )
        self._timeout_seconds = float(
            extra.get("timeout_seconds")
            or os.getenv("WEBCHAT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        self._poll_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        return headers

    def _assistant_url(self, conversation_id: str) -> str:
        return f"{self._base_url}/api/internal/hermes/conversations/{conversation_id}/assistant"

    def _typing_url(self, conversation_id: str) -> str:
        return f"{self._base_url}/api/internal/hermes/conversations/{conversation_id}/typing"

    def _stop_typing_url(self, conversation_id: str) -> str:
        return f"{self._base_url}/api/internal/hermes/conversations/{conversation_id}/typing/stop"

    def _build_sender_trace(
        self,
        conversation_id: str,
        content: str,
        attachments: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        session_platform = os.getenv("HERMES_SESSION_PLATFORM", "")
        session_chat_id = os.getenv("HERMES_SESSION_CHAT_ID", "")
        attachment_list = attachments or []
        return {
            "traceId": str(uuid4()),
            "route": "webchat_adapter",
            "senderBaseUrl": self._base_url,
            "senderTargetUrl": self._assistant_url(conversation_id),
            "senderHostname": os.uname().nodename,
            "sessionPlatform": session_platform or None,
            "sessionChatId": session_chat_id or None,
            "attachmentCount": len(attachment_list),
            "attachmentNames": [
                str(attachment.get("fileName") or "attachment")
                for attachment in attachment_list
            ],
            "contentLength": len(content or ""),
            "startedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    async def connect(self) -> bool:
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed", self.name)
            return False
        if not self._service_token:
            logger.warning("[%s] WEBCHAT_SERVICE_TOKEN is not configured", self.name)
            return False

        self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            response = await self._client.get(
                f"{self._base_url}/api/internal/hermes/health",
                headers=self._headers(),
            )
            response.raise_for_status()
        except Exception as exc:
            logger.error(
                "[%s] Failed health check against %s: %s",
                self.name,
                self._base_url,
                exc,
            )
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            return False

        self._mark_connected()
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._background_tasks.add(self._poll_task)
        self._poll_task.add_done_callback(self._background_tasks.discard)
        logger.info("[%s] Connected to %s", self.name, self._base_url)
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()
        if self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("[%s] Disconnected", self.name)

    async def _poll_loop(self) -> None:
        while self.is_connected:
            try:
                event = await self._fetch_event()
                if event is None:
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self.handle_message(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[%s] Poll error: %s", self.name, exc)
                await asyncio.sleep(self._poll_interval)

    async def _fetch_event(self) -> Optional[MessageEvent]:
        if self._client is None:
            return None

        response = await self._client.get(
            f"{self._base_url}/api/internal/hermes/inbox/next",
            headers=self._headers(),
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None

        event_id = str(payload.get("eventId") or "").strip()
        if not event_id:
            return None

        conversation_id = str(
            payload.get("conversationId")
            or payload.get("chatId")
            or payload.get("sessionChatId")
            or event_id
        ).strip()
        if not conversation_id:
            conversation_id = event_id

        context_url = _normalize_webchat_context_url(
            self._base_url,
            payload.get("contextUrl"),
        )
        if context_url:
            payload["contextUrl"] = context_url

        media_urls, media_types = await self._materialize_attachments(
            payload.get("attachments") or []
        )

        created_at = str(payload.get("createdAt") or "").strip()
        timestamp = datetime.now()
        if created_at:
            try:
                timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                pass

        source = self.build_source(
            chat_id=conversation_id,
            chat_name=payload.get("conversationName"),
            chat_type=payload.get("chatType", "dm"),
            user_id=str(payload.get("userId") or payload.get("senderId") or "web-user"),
            user_name=payload.get("userName"),
            thread_id=payload.get("threadId"),
            message_id=str(payload.get("messageId") or event_id),
        )

        return MessageEvent(
            text=str(payload.get("text") or ""),
            message_type=self._derive_message_type(
                str(payload.get("text") or ""),
                media_types,
            ),
            source=source,
            raw_message=payload,
            message_id=str(payload.get("messageId") or event_id),
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=str(payload.get("replyToMessageId") or "") or None,
            timestamp=timestamp,
        )

    async def _materialize_attachments(
        self,
        attachments: list[dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        media_urls: list[str] = []
        media_types: list[str] = []
        if self._client is None:
            return media_urls, media_types

        for attachment in attachments:
            attachment_id = attachment.get("attachmentId")
            if not attachment_id:
                continue

            content_type = str(attachment.get("contentType") or "application/octet-stream")
            file_name = str(attachment.get("fileName") or attachment_id)
            download_url = attachment.get("internalDownloadUrl") or (
                f"/api/internal/hermes/attachments/{attachment_id}/download"
            )
            if str(download_url).startswith("/"):
                download_url = f"{self._base_url}{download_url}"

            try:
                response = await self._client.get(str(download_url), headers=self._headers())
                response.raise_for_status()
                data = response.content
                ext = Path(file_name).suffix or mimetypes.guess_extension(content_type) or ""

                if content_type.startswith("image/"):
                    cached = cache_image_from_bytes(data, ext or ".png")
                elif content_type.startswith("audio/"):
                    cached = cache_audio_from_bytes(data, ext or ".ogg")
                else:
                    cached = cache_document_from_bytes(data, file_name)

                media_urls.append(cached)
                media_types.append(content_type)
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to download attachment %s: %s",
                    self.name,
                    attachment_id,
                    exc,
                )

        return media_urls, media_types

    @staticmethod
    def _derive_message_type(text: str, media_types: list[str]) -> MessageType:
        if any(media_type.startswith("image/") for media_type in media_types):
            return MessageType.TEXT if text else MessageType.PHOTO
        if any(media_type.startswith("audio/") for media_type in media_types):
            return MessageType.TEXT if text else MessageType.AUDIO
        if media_types:
            return MessageType.DOCUMENT
        return MessageType.TEXT

    async def _ack_event(self, event_id: str) -> None:
        if self._client is None:
            return
        response = await self._client.post(
            f"{self._base_url}/api/internal/hermes/events/{event_id}/ack",
            headers=self._headers(),
        )
        response.raise_for_status()

    def _cancelled_event_was_superseded(self, event: MessageEvent) -> bool:
        current_task = asyncio.current_task()
        if current_task is None or event.source is None:
            return False

        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        if session_key not in self._active_sessions:
            return False

        owner_task = self._session_tasks.get(session_key)
        return owner_task is not current_task

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        payload = event.raw_message if isinstance(event.raw_message, dict) else {}
        event_id = payload.get("eventId")
        if not event_id:
            return

        if outcome is ProcessingOutcome.CANCELLED and self._cancelled_event_was_superseded(event):
            logger.info(
                "[%s] Acknowledging superseded cancelled event %s",
                self.name,
                event_id,
            )
            await self._ack_event(str(event_id))
            return

        if outcome is not ProcessingOutcome.SUCCESS:
            logger.warning(
                "[%s] Leaving event %s unacked after %s so it can be retried",
                self.name,
                event_id,
                outcome.value,
            )
            return

        await self._ack_event(str(event_id))

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if self._client is None:
            return
        try:
            await self._client.post(self._typing_url(chat_id), headers=self._headers())
        except Exception as exc:
            logger.debug("[%s] Failed to send typing indicator: %s", self.name, exc)

    async def stop_typing(self, chat_id: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.post(self._stop_typing_url(chat_id), headers=self._headers())
        except Exception as exc:
            logger.debug("[%s] Failed to stop typing indicator: %s", self.name, exc)

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        try:
            return await self._post_assistant_message(
                chat_id=chat_id,
                content=content,
                message_id=message_id,
            )
        except Exception as exc:
            logger.warning(
                "[%s] Failed to edit webchat message %s: %s",
                self.name,
                message_id,
                exc,
            )
            return SendResult(success=False, error=f"Webchat edit failed: {exc}", retryable=True)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        try:
            update_message_id: Optional[str] = None
            transport_metadata = metadata
            if isinstance(metadata, dict):
                raw_message_id = metadata.get("message_id")
                if isinstance(raw_message_id, str) and raw_message_id.strip():
                    update_message_id = raw_message_id.strip()
                    transport_metadata = {
                        key: value
                        for key, value in metadata.items()
                        if key != "message_id"
                    }
                    if not transport_metadata:
                        transport_metadata = None
            return await self._post_assistant_message(
                chat_id=chat_id,
                content=content,
                reply_to=reply_to,
                metadata=transport_metadata,
                message_id=update_message_id,
            )
        except Exception as exc:
            return SendResult(success=False, error=f"Webchat send failed: {exc}", retryable=True)

    async def _post_assistant_message(
        self,
        chat_id: str,
        content: str = "",
        attachments: Optional[list[Dict[str, Any]]] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> SendResult:
        if self._client is None:
            return SendResult(success=False, error="Webchat adapter is not connected")

        payload: Dict[str, Any] = {
            "conversationId": chat_id,
            "content": content,
            "publicBaseUrl": self._public_base_url,
        }
        if message_id:
            payload["messageId"] = message_id
        if attachments:
            payload["attachments"] = attachments
        if reply_to:
            payload["replyToMessageId"] = reply_to
        if isinstance(metadata, dict) and metadata:
            timings = metadata.get("timings")
            message_role = metadata.get("message_role")
            display_type = metadata.get("display_type")
            if timings:
                payload["timings"] = timings
            if message_role in {"assistant", "system"}:
                payload["role"] = message_role
            if display_type == "tool_progress":
                payload["displayType"] = display_type
            filtered_metadata = {
                key: value
                for key, value in metadata.items()
                if key not in {"timings", "message_role", "display_type"}
            }
            if filtered_metadata:
                payload["metadata"] = filtered_metadata
        elif metadata:
            payload["metadata"] = metadata

        payload["senderTrace"] = self._build_sender_trace(chat_id, content, attachments)

        response = await self._client.post(
            self._assistant_url(chat_id),
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        data = response.json()
        return SendResult(
            success=True,
            message_id=str(data.get("messageId") or data.get("id") or ""),
            raw_response=data,
        )

    @staticmethod
    def _build_json_attachment(file_path: str, file_name: Optional[str] = None) -> Dict[str, Any]:
        resolved_path = Path(file_path)
        attachment_name = file_name or resolved_path.name
        content_type = mimetypes.guess_type(attachment_name)[0] or "application/octet-stream"
        encoded = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
        return {
            "fileName": attachment_name,
            "contentType": content_type,
            "base64Data": encoded,
        }

    async def _send_file(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> SendResult:
        try:
            attachment = self._build_json_attachment(file_path, file_name=file_name)
            return await self._post_assistant_message(
                chat_id=chat_id,
                content=caption or "",
                attachments=[attachment],
            )
        except Exception as exc:
            return SendResult(success=False, error=f"Webchat file send failed: {exc}", retryable=True)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_file(chat_id, file_path, caption, file_name=file_name)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_file(chat_id, image_path, caption)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_file(chat_id, video_path, caption)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        return await self._send_file(chat_id, audio_path, caption)

    async def send_animation(
        self,
        chat_id: str,
        animation_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if animation_url.startswith(("http://", "https://")):
            content = f"![{caption or ''}]({animation_url})" if caption else f"![]({animation_url})"
            return await self.send(chat_id=chat_id, content=content, reply_to=reply_to, metadata=metadata)
        return await self._send_file(chat_id, animation_url, caption)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if image_url.startswith(("http://", "https://")):
            parts = []
            if caption:
                parts.append(caption)
            parts.append(f"![]({image_url})")
            return await self.send(
                chat_id=chat_id,
                content="\n\n".join(parts),
                reply_to=reply_to,
                metadata=metadata,
            )
        return await self._send_file(chat_id, image_url, caption)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "name": f"Webchat conversation {chat_id}",
            "type": "dm",
            "chat_id": chat_id,
            "public_base_url": self._public_base_url,
        }