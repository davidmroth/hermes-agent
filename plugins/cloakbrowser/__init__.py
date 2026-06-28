"""CloakBrowser plugin — thread-safe browser tool overrides."""

from __future__ import annotations

import logging

from . import backend
from .tools import register_tools

logger = logging.getLogger(__name__)


def _on_session_end(task_id: str = "", session_id: str = "", **_: object) -> None:
    if not backend.is_plugin_enabled():
        return
    if task_id:
        backend.cleanup_task(task_id)
    elif session_id:
        backend.cleanup_task(session_id)


def register(ctx) -> None:
    register_tools(ctx)
    if backend.is_plugin_enabled():
        ctx.register_hook("on_session_end", _on_session_end)
