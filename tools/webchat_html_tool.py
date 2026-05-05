"""Dedicated tool for sending previewable HTML files to the current webchat conversation."""

import json
import logging
import mimetypes
from pathlib import Path

from tools.registry import registry
from tools.send_message_tool import (
    _check_send_message,
    _sanitize_error_text,
    _send_webchat,
)
from tools.webchat_file_tool import _debug_payload, _resolve_webchat_target

logger = logging.getLogger(__name__)

_HTML_EXTENSIONS = {".html", ".htm"}
_HTML_MIME_TYPES = {"text/html"}


SEND_HTML_TO_WEBCHAT_SCHEMA = {
    "name": "send_html_to_webchat",
    "description": (
        "Send a local HTML file to the current webchat conversation as a previewable attachment. "
        "Use this when the user is chatting in the web UI and wants an HTML artifact rendered in the chat modal with a download option. "
        "The file_path must be an absolute path to an existing .html or .htm file. "
        "This tool resolves the current webchat conversation automatically and returns debug details about the exact target URL used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the HTML file to send to webchat, for example '/tmp/report.html'."
            },
            "caption": {
                "type": "string",
                "description": "Optional short message to include alongside the HTML file."
            },
            "conversation_id": {
                "type": "string",
                "description": "Optional explicit webchat conversation id. Usually omit this and let the tool use the current chat session automatically."
            }
        },
        "required": ["file_path"]
    }
}


def _is_html_file(path: Path) -> bool:
    if path.suffix.lower() in _HTML_EXTENSIONS:
        return True

    guessed_type, _ = mimetypes.guess_type(path.name)
    return guessed_type in _HTML_MIME_TYPES


def send_html_to_webchat_tool(args, **_kw):
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

    if not _is_html_file(path):
        return json.dumps({
            "error": "file_path must point to an HTML file (.html or .htm).",
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
        logger.exception("send_html_to_webchat failed")
        return json.dumps({
            "error": _sanitize_error_text(f"send_html_to_webchat failed: {exc}"),
            "debug": debug,
        })


registry.register(
    name="send_html_to_webchat",
    toolset="messaging",
    schema=SEND_HTML_TO_WEBCHAT_SCHEMA,
    handler=send_html_to_webchat_tool,
    check_fn=_check_send_message,
    emoji="🧾",
)