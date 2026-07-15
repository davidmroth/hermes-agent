"""Tests for BasePlatformAdapter.create_title_callback wiring."""

from unittest.mock import MagicMock

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter
from gateway.platforms.telegram import TelegramAdapter
from gateway.session import SessionSource


def _make_source(platform: Platform = Platform.TELEGRAM) -> SessionSource:
    return SessionSource(
        platform=platform,
        user_id="user-1",
        chat_id="123",
        user_name="tester",
        chat_type="dm",
        thread_id="42",
    )


def test_base_adapter_default_title_callback_is_none():
    # Default implementation does not use instance state.
    assert BasePlatformAdapter.create_title_callback(object(), {"source": object()}) is None


def test_telegram_title_callback_schedules_topic_rename_for_topic_lane():
    adapter = object.__new__(TelegramAdapter)
    runner = MagicMock()
    runner._is_telegram_topic_lane.return_value = True
    source = _make_source()

    callback = adapter.create_title_callback(
        {
            "runner": runner,
            "source": source,
            "session_id": "sess-1",
        }
    )
    assert callback is not None
    callback("Daily Digest")
    runner._schedule_telegram_topic_title_rename.assert_called_once_with(
        source,
        "sess-1",
        "Daily Digest",
    )


def test_telegram_title_callback_returns_none_outside_topic_lane():
    adapter = object.__new__(TelegramAdapter)
    runner = MagicMock()
    runner._is_telegram_topic_lane.return_value = False

    callback = adapter.create_title_callback(
        {
            "runner": runner,
            "source": _make_source(),
            "session_id": "sess-1",
        }
    )
    assert callback is None
