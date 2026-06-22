"""Tests for WebUI plugin send_file_to_webchat tool."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform
from tests.gateway._webchat_plugin import import_plugin_module


@pytest.fixture
def webchat_tools():
    return import_plugin_module("tools")


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _webchat_config(home_channel=None):
    webchat_cfg = SimpleNamespace(enabled=True, token="svc-token", extra={"url": "http://webui:3000"})
    return SimpleNamespace(
        platforms={Platform("webchat"): webchat_cfg},
        get_home_channel=lambda _platform: home_channel,
    ), webchat_cfg


class TestSendFileToWebchatTool:
    def test_uses_current_webchat_session_target(self, tmp_path, webchat_tools):
        file_path = tmp_path / "artifact.txt"
        file_path.write_text("hello", encoding="utf-8")
        config, webchat_cfg = _webchat_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch.object(webchat_tools, "get_session_env", side_effect=lambda name, default="": {
                 "HERMES_SESSION_PLATFORM": "webchat",
                 "HERMES_SESSION_CHAT_ID": "conv-1",
                 "HERMES_SESSION_THREAD_ID": "",
             }.get(name, default)), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch.object(webchat_tools, "_send_webchat", new=AsyncMock(return_value={
                 "success": True,
                 "platform": "webchat",
                 "chat_id": "conv-1",
             })) as send_mock:
            payload = json.loads(webchat_tools.send_file_to_webchat_tool({
                "file_path": str(file_path),
                "caption": "Here you go",
            }))

        assert payload["success"] is True
        assert payload["chat_id"] == "conv-1"
        assert payload["debug"]["targetSource"] == "current-session"
        send_mock.assert_awaited_once()
        assert send_mock.await_args.args[2] == "conv-1"

    def test_requires_absolute_path(self, webchat_tools):
        payload = json.loads(webchat_tools.send_file_to_webchat_tool({
            "file_path": "relative.txt",
        }))
        assert "absolute path" in payload["error"]

    def test_uses_home_channel_when_no_session(self, tmp_path, webchat_tools):
        file_path = tmp_path / "artifact.txt"
        file_path.write_text("hello", encoding="utf-8")
        home = SimpleNamespace(chat_id="home-conv")
        config, _ = _webchat_config(home_channel=home)

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch.object(webchat_tools, "get_session_env", return_value=""), \
             patch("tools.send_message_tool._resolve_recent_session_target", return_value=None), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch.object(webchat_tools, "_send_webchat", new=AsyncMock(return_value={
                 "success": True,
             })) as send_mock:
            payload = json.loads(webchat_tools.send_file_to_webchat_tool({
                "file_path": str(file_path),
            }))

        assert payload["success"] is True
        assert payload["debug"]["targetSource"] == "home-channel"
        send_mock.assert_awaited_once()
        assert send_mock.await_args.args[2] == "home-conv"
