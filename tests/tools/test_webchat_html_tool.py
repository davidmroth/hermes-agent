"""Tests for tools/webchat_html_tool.py."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gateway.config import Platform
from tools.webchat_html_tool import send_html_to_webchat_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _webchat_config(home_channel=None):
    webchat_cfg = SimpleNamespace(enabled=True, token="svc-token", extra={"url": "http://webui:3000"})
    return SimpleNamespace(
        platforms={Platform.WEBCHAT: webchat_cfg},
        get_home_channel=lambda _platform: home_channel,
    ), webchat_cfg


class TestSendHtmlToWebchatTool:
    def test_uses_current_webchat_session_target(self, tmp_path):
        file_path = tmp_path / "artifact.html"
        file_path.write_text("<html><body>hello</body></html>", encoding="utf-8")
        config, webchat_cfg = _webchat_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.webchat_file_tool.get_session_env", side_effect=lambda name, default="": {
                 "HERMES_SESSION_PLATFORM": "webchat",
                 "HERMES_SESSION_CHAT_ID": "conv-1",
                 "HERMES_SESSION_THREAD_ID": "",
             }.get(name, default)), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.webchat_html_tool._send_webchat", new=AsyncMock(return_value={
                 "success": True,
                 "platform": "webchat",
                 "chat_id": "conv-1",
                 "message_id": "msg-1",
                 "sender_trace_id": "trace-1",
                 "sender_target_url": "http://webui:3000/api/internal/hermes/conversations/conv-1/assistant",
             })) as send_mock:
            result = json.loads(
                send_html_to_webchat_tool({
                    "file_path": str(file_path),
                    "caption": "Attached HTML",
                })
            )

        send_mock.assert_awaited_once_with(
            webchat_cfg.token,
            webchat_cfg.extra,
            "conv-1",
            "Attached HTML",
            thread_id=None,
            media_files=[(str(file_path), False)],
        )
        assert result["success"] is True
        assert result["debug"]["targetSource"] == "current-session"
        assert result["debug"]["senderTraceId"] == "trace-1"

    def test_falls_back_to_recent_webchat_session(self, tmp_path):
        file_path = tmp_path / "artifact.htm"
        file_path.write_text("<html><body>hello</body></html>", encoding="utf-8")
        config, webchat_cfg = _webchat_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.webchat_file_tool.get_session_env", return_value=""), \
             patch("tools.webchat_file_tool._resolve_recent_session_target", return_value="conv-recent:thread-9"), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.webchat_html_tool._send_webchat", new=AsyncMock(return_value={"success": True, "message_id": "msg-2"})) as send_mock:
            result = json.loads(
                send_html_to_webchat_tool({
                    "file_path": str(file_path),
                })
            )

        send_mock.assert_awaited_once_with(
            webchat_cfg.token,
            webchat_cfg.extra,
            "conv-recent",
            "",
            thread_id="thread-9",
            media_files=[(str(file_path), False)],
        )
        assert result["success"] is True
        assert result["debug"]["targetSource"] == "recent-session"

    def test_rejects_non_html_files(self, tmp_path):
        file_path = tmp_path / "artifact.txt"
        file_path.write_text("hello", encoding="utf-8")

        result = json.loads(send_html_to_webchat_tool({"file_path": str(file_path)}))

        assert result["error"] == "file_path must point to an HTML file (.html or .htm)."
        assert result["debug"]["fileExists"] is True
        assert result["debug"]["filePath"] == str(file_path)