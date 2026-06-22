"""Dispatch helpers for platform-plugin gateway hooks."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from gateway.config import Platform
from gateway.platform_registry import GatewayPlatformHooks, platform_registry

logger = logging.getLogger(__name__)


def get_platform_hooks(platform: Platform) -> Optional[GatewayPlatformHooks]:
    try:
        entry = platform_registry.get(platform.value)
        if entry and entry.gateway_hooks is not None:
            return entry.gateway_hooks
    except Exception as exc:
        logger.debug("platform hook lookup failed for %s: %s", platform, exc)
    return None


def platform_trusted_auth(platform: Platform) -> bool:
    hooks = get_platform_hooks(platform)
    return bool(hooks and hooks.trusted_auth)


async def reconcile_session_history(
    *,
    runner: Any,
    session_entry: Any,
    event: Any,
    source: Any,
    history: list,
    adapter: Any,
) -> list:
    hooks = get_platform_hooks(source.platform)
    if hooks is None:
        return history
    try:
        return await hooks.reconcile_session_history(
            runner=runner,
            session_entry=session_entry,
            event=event,
            source=source,
            history=history,
            adapter=adapter,
        )
    except Exception as exc:
        logger.debug(
            "reconcile_session_history hook failed for %s: %s",
            source.platform,
            exc,
            exc_info=True,
        )
        return history


def enrich_progress_metadata(platform: Platform, metadata: Optional[dict]) -> Optional[dict]:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return metadata
    try:
        return hooks.enrich_progress_metadata(metadata)
    except Exception as exc:
        logger.debug("enrich_progress_metadata hook failed for %s: %s", platform, exc)
        return metadata


def system_message_metadata(
    platform: Platform,
    base_metadata: Optional[dict],
) -> Optional[dict]:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return base_metadata
    try:
        return hooks.system_message_metadata(base_metadata)
    except Exception as exc:
        logger.debug("system_message_metadata hook failed for %s: %s", platform, exc)
        return base_metadata


def should_buffer_lifecycle_status(platform: Platform, message: str) -> bool:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return False
    try:
        return hooks.should_buffer_lifecycle_status(message)
    except Exception as exc:
        logger.debug("should_buffer_lifecycle_status hook failed for %s: %s", platform, exc)
        return False


def create_transcript_callback(platform: Platform, ctx: dict) -> Optional[Callable[..., Any]]:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return None
    try:
        return hooks.create_transcript_callback(ctx)
    except Exception as exc:
        logger.debug("create_transcript_callback hook failed for %s: %s", platform, exc)
        return None


def merge_error_buffer(
    platform: Platform,
    buffer: list[str],
    text: str,
    *,
    failed: bool,
) -> str:
    hooks = get_platform_hooks(platform)
    if hooks is None or not buffer:
        return text
    try:
        return hooks.merge_error_buffer(buffer, text, failed=failed)
    except Exception as exc:
        logger.debug("merge_error_buffer hook failed for %s: %s", platform, exc)
        return text


async def reconcile_preview_timings(platform: Platform, ctx: dict) -> None:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return
    try:
        await hooks.reconcile_preview_timings(ctx)
    except Exception as exc:
        logger.debug("reconcile_preview_timings hook failed for %s: %s", platform, exc)


def enrich_busy_message_metadata(
    platform: Platform,
    thread_meta: Optional[dict],
) -> Optional[dict]:
    hooks = get_platform_hooks(platform)
    if hooks is None:
        return thread_meta
    try:
        return hooks.enrich_busy_message_metadata(thread_meta)
    except Exception as exc:
        logger.debug("enrich_busy_message_metadata hook failed for %s: %s", platform, exc)
        return thread_meta
