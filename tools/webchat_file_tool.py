"""Dedicated tool for sending downloadable files to the current webchat conversation."""

import json
import logging
from pathlib import Path

from gateway.session_context import get_session_env
from tools.registry import registry
from tools.send_message_tool import (
    _check_send_message,
    _parse_target_ref,
    _resolve_recent_session_target,
    _send_webchat,
    _sanitize_error_text,
)

logger = logging.getLogger(__name__)


SEND_FILE_TO_WEBCHAT_SCHEMA = {
    "name": "send_file_to_webchat",
    "description": (
        "Send a local file to the current webchat conversation as a downloadable attachment. "
        "Use this when the user is chatting in the web UI and asks for a file they can download. "
        "The file_path must be an absolute path to a file that already exists on disk. "
        "This tool resolves the current webchat conversation automatically and returns debug details about the exact target URL used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to send to webchat, for example '/tmp/report.pdf'."
            },
            "caption": {
                "type": "string",
                "description": "Optional short message to include alongside the file."
            },
            "conversation_id": {
                "type": "string",
                "description": "Optional explicit webchat conversation id. Usually omit this and let the tool use the current chat session automatically."
            }
        },
        "required": ["file_path"]
    }
}


def _debug_payload(file_path: str) -> dict:
    path = Path(file_path)
    exists = path.exists()
    return {
        "filePath": file_path,
        "fileName": path.name,
        "isAbsolute": path.is_absolute(),
        "fileExists": exists,
        "fileSizeBytes": path.stat().st_size if exists and path.is_file() else None,
        "currentSessionPlatform": get_session_env("HERMES_SESSION_PLATFORM", ""),
        "currentSessionChatId": get_session_env("HERMES_SESSION_CHAT_ID", ""),
        "currentSessionThreadId": get_session_env("HERMES_SESSION_THREAD_ID", ""),
        "recentWebchatTarget": None,
        "targetSource": None,
        "resolvedChatId": None,
        "resolvedThreadId": None,
    }


def _resolve_webchat_target(config, explicit_conversation_id: str, debug: dict) -> tuple[str, str | None]:
    if explicit_conversation_id:
        chat_id, thread_id, _ = _parse_target_ref("webchat", explicit_conversation_id)
        if not chat_id and ":" in explicit_conversation_id:
            chat_id, thread_id = explicit_conversation_id.split(":", 1)
        debug["targetSource"] = "explicit"
        debug["resolvedChatId"] = chat_id or explicit_conversation_id
        debug["resolvedThreadId"] = thread_id
        return chat_id or explicit_conversation_id, thread_id

    current_platform = debug["currentSessionPlatform"].strip().lower()
    current_chat_id = debug["currentSessionChatId"].strip()
    current_thread_id = debug["currentSessionThreadId"].strip() or None
    if current_platform == "webchat" and current_chat_id:
        debug["targetSource"] = "current-session"
        debug["resolvedChatId"] = current_chat_id
        debug["resolvedThreadId"] = current_thread_id
        return current_chat_id, current_thread_id

    recent_target = _resolve_recent_session_target("webchat")
    debug["recentWebchatTarget"] = recent_target
    if recent_target:
        chat_id, thread_id, _ = _parse_target_ref("webchat", recent_target)
        if not chat_id and ":" in recent_target:
            chat_id, thread_id = recent_target.split(":", 1)
        debug["targetSource"] = "recent-session"
        debug["resolvedChatId"] = chat_id or recent_target
        debug["resolvedThreadId"] = thread_id
        return chat_id or recent_target, thread_id

    from gateway.config import Platform

    home = config.get_home_channel(Platform.WEBCHAT)
    if home and home.chat_id:
        debug["targetSource"] = "home-channel"
        debug["resolvedChatId"] = str(home.chat_id)
        debug["resolvedThreadId"] = None
        return str(home.chat_id), None

    raise ValueError(
        "No active webchat conversation was found. Start from a webchat session, or pass conversation_id explicitly."
    )


def send_file_to_webchat_tool(args, **_kw):
    file_path = str(args.get("file_path", "")).strip()
    caption = str(args.get("caption", ""))
    conversation_id = str(args.get("conversation_id", "")).strip()

    if not file_path:
        return json.dumps({"error": "'file_path' is required.", "debug": {"filePath": ""}})

    debug = _debug_payload(file_path)
    path = Path(file_path)
    if not path.is_absolute():
        return json.dumps({
            "error": "file_path must be an absolute path.",
            "debug": debug,
        })

    if not path.exists() or not path.is_file():
        return json.dumps({
            "error": f"File not found: {file_path}",
            "debug": debug,
        })

    try:
        from gateway.config import Platform, load_gateway_config

        config = load_gateway_config()
        pconfig = config.platforms.get(Platform.WEBCHAT)
        if not pconfig or not pconfig.enabled:
            return json.dumps({
                "error": "Webchat platform is not configured.",
                "debug": debug,
            })

        chat_id, thread_id = _resolve_webchat_target(config, conversation_id, debug)

        from model_tools import _run_async

        result = _run_async(
            _send_webchat(
                pconfig.token,
                pconfig.extra,
                chat_id,
                caption,
                thread_id=thread_id,
                media_files=[(file_path, False)],
            )
        )

        if isinstance(result, dict):
            result.setdefault("platform", "webchat")
            result.setdefault("chat_id", chat_id)
            result["debug"] = {
                **debug,
                "resolvedChatId": chat_id,
                "resolvedThreadId": thread_id,
                "senderTraceId": result.get("sender_trace_id"),
                "senderTargetUrl": result.get("sender_target_url"),
            }
            if result.get("error"):
                result["error"] = _sanitize_error_text(result["error"])
            return json.dumps(result)

        return json.dumps({
            "success": True,
            "platform": "webchat",
            "chat_id": chat_id,
            "debug": debug,
        })
    except Exception as exc:
        logger.exception("send_file_to_webchat failed")
        return json.dumps({
            "error": _sanitize_error_text(f"send_file_to_webchat failed: {exc}"),
            "debug": debug,
        })


registry.register(
    name="send_file_to_webchat",
    toolset="messaging",
    schema=SEND_FILE_TO_WEBCHAT_SCHEMA,
    handler=send_file_to_webchat_tool,
    check_fn=_check_send_message,
    emoji="📎",
)