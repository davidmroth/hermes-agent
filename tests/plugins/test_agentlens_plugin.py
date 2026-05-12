from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / ".services" / "agentlens" / "plugin" / "__init__.py"


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location("agentlens_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _DummyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def tool_call_start(self, **kwargs):
        self.calls.append(("tool_call_start", kwargs))

    def tool_call_end(self, **kwargs):
        self.calls.append(("tool_call_end", kwargs))

    def session_start(self, **kwargs):
        self.calls.append(("session_start", kwargs))

    def session_end(self, **kwargs):
        self.calls.append(("session_end", kwargs))


def test_tool_events_use_hermes_session_id_and_tool_call_id():
    mod = _load_plugin_module()
    client = _DummyClient()

    mod._client = client
    mod._tool_timers.clear()
    mod._session_models.clear()
    mod._session_platforms.clear()

    mod._on_pre_tool_call(
        tool_name="terminal",
        args={"command": "pwd"},
        task_id="task-123",
        session_id="session-abc",
        tool_call_id="call-xyz",
    )
    mod._on_post_tool_call(
        tool_name="terminal",
        args={"command": "pwd"},
        result='{"ok": true}',
        task_id="task-123",
        session_id="session-abc",
        tool_call_id="call-xyz",
        duration_ms=12,
    )

    assert client.calls == [
        (
            "tool_call_start",
            {
                "session_id": "session-abc",
                "tool_name": "terminal",
                "args": {"command": "pwd"},
                "call_id": "call-xyz",
                "args_size_chars": 18,
                "session_scope": "session-abc",
            },
        ),
        (
            "tool_call_end",
            {
                "session_id": "session-abc",
                "tool_name": "terminal",
                "call_id": "call-xyz",
                "result": '{"ok": true}',
                "duration_ms": 12.0,
                "success": True,
                "error": "",
                "result_size_chars": 12,
                "result_json": {"ok": True},
            },
        ),
    ]


def test_session_end_backfills_missing_session_start_for_continued_session():
    mod = _load_plugin_module()
    client = _DummyClient()

    mod._client = client
    mod._tool_timers.clear()
    mod._session_models.clear()
    mod._session_platforms.clear()
    mod._session_platforms["session-continued"] = "webchat"

    mod._on_session_end(
        session_id="session-continued",
        completed=True,
        interrupted=False,
        model="",
        platform="",
        total_tokens=42,
    )

    assert client.calls == [
        (
            "session_start",
            {
                "session_id": "session-continued",
                "model": "",
                "platform": "webchat",
            },
        ),
        (
            "session_end",
            {
                "session_id": "session-continued",
                "completed": True,
                "interrupted": False,
                "total_tokens": 42,
                "total_cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "last_prompt_tokens": 0,
                "timings": {},
            },
        ),
    ]