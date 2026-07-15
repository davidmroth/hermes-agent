"""Tests for WebChat adapter create_title_callback (sibling webui plugin)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _webui_adapter_candidates() -> list[Path]:
    paths: list[Path] = []
    env_path = Path(__import__("os").environ.get("WEBUI_PLUGIN_PATH", "")).expanduser()
    if str(env_path).strip():
        paths.append(env_path / "adapter.py")
    paths.append(Path("/opt/data/plugins/webchat-platform/adapter.py"))
    repo_root = Path(__file__).resolve().parents[2]
    paths.append(repo_root.parent / "webui" / "plugin" / "adapter.py")
    return paths


def _load_webchat_adapter_class():
    adapter_path = next((p for p in _webui_adapter_candidates() if p.is_file()), None)
    if adapter_path is None:
        searched = ", ".join(str(p) for p in _webui_adapter_candidates())
        pytest.skip(f"WebUI plugin not found. Searched: {searched}")
    module_name = "webui_plugin_adapter_title_test"
    spec = importlib.util.spec_from_file_location(module_name, adapter_path)
    if spec is None or spec.loader is None:
        pytest.skip(f"Cannot load WebUI adapter from {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.WebChatAdapter


@pytest.mark.asyncio
async def test_webchat_title_callback_schedules_adapter_apply():
    WebChatAdapter = _load_webchat_adapter_class()
    adapter = object.__new__(WebChatAdapter)
    adapter.apply_session_title = AsyncMock(return_value=True)
    source = SimpleNamespace(chat_id="conv-1")
    loop = asyncio.get_running_loop()
    scheduled = []

    def safe_schedule(coro, _loop, **_kwargs):
        fut = asyncio.ensure_future(coro, loop=_loop)
        scheduled.append(fut)
        return fut

    callback = adapter.create_title_callback(
        {
            "source": source,
            "session_id": "sess-1",
            "loop": loop,
            "safe_schedule": safe_schedule,
            "logger": MagicMock(),
        }
    )
    assert callback is not None
    callback("Daily Digest")
    assert scheduled
    await scheduled[0]
    adapter.apply_session_title.assert_awaited_once()
    args = adapter.apply_session_title.await_args.args
    assert args[1] == "sess-1"
    assert args[2] == "Daily Digest"


def test_webchat_title_callback_returns_none_without_loop():
    WebChatAdapter = _load_webchat_adapter_class()
    adapter = object.__new__(WebChatAdapter)
    callback = adapter.create_title_callback(
        {
            "source": SimpleNamespace(chat_id="conv-1"),
            "session_id": "sess-1",
            "loop": None,
            "safe_schedule": MagicMock(),
        }
    )
    assert callback is None
