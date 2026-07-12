"""Register CloakBrowser overrides for Hermes browser_* tools."""

from __future__ import annotations

import logging
from typing import Any

from . import backend

logger = logging.getLogger(__name__)

_TOOLSET = "browser"
_EMOJI = {
    "browser_navigate": "🌐",
    "browser_snapshot": "📸",
    "browser_click": "👆",
    "browser_type": "⌨️",
    "browser_scroll": "📜",
    "browser_back": "◀️",
    "browser_press": "⌨️",
    "browser_get_images": "🖼️",
    "browser_vision": "👁️",
    "browser_console": "🖥️",
}


def _schema_map() -> dict[str, dict]:
    from tools.browser_tool import BROWSER_TOOL_SCHEMAS

    return {schema["name"]: schema for schema in BROWSER_TOOL_SCHEMAS}


def navigate(url: str, task_id: str | None = None) -> str:
    return backend.run_op("navigate", url=url, task_id=task_id)


def snapshot(full: bool = False, task_id: str | None = None, user_task: str | None = None) -> str:
    return backend.run_op("snapshot", full=full, task_id=task_id, user_task=user_task)


def click(ref: str, task_id: str | None = None) -> str:
    return backend.run_op("click", ref=ref, task_id=task_id)


def type_text(ref: str, text: str, task_id: str | None = None) -> str:
    return backend.run_op("type", ref=ref, text=text, task_id=task_id)


def scroll(direction: str, task_id: str | None = None) -> str:
    return backend.run_op("scroll", direction=direction, task_id=task_id)


def back(task_id: str | None = None) -> str:
    return backend.run_op("back", task_id=task_id)


def press(key: str, task_id: str | None = None) -> str:
    return backend.run_op("press", key=key, task_id=task_id)


def get_images(task_id: str | None = None) -> str:
    return backend.run_op("get_images", task_id=task_id)


def vision(question: str, annotate: bool = False, task_id: str | None = None) -> str:
    return backend.run_op("vision", question=question, annotate=annotate, task_id=task_id)


def console(
    clear: bool = False,
    expression: str | None = None,
    task_id: str | None = None,
) -> str:
    return backend.run_op(
        "console",
        clear=clear,
        expression=expression,
        task_id=task_id,
    )


def register_tools(ctx) -> None:
    if not backend.is_plugin_enabled():
        logger.info(
            "CloakBrowser plugin loaded but inactive "
            "(set CLOAKBROWSER_CDP_URL to enable, e.g. http://cloakbrowser:9222)"
        )
        return

    if not backend.check_available():
        logger.warning(
            "CloakBrowser plugin enabled but CDP endpoint %s is unreachable",
            backend.get_cdp_url(),
        )

    schemas = _schema_map()
    registrations: list[tuple[str, Any, dict[str, Any]]] = [
        (
            "browser_navigate",
            navigate,
            {"url": "url"},
        ),
        (
            "browser_snapshot",
            snapshot,
            {"full": "full", "user_task": "user_task"},
        ),
        (
            "browser_click",
            click,
            {"ref": "ref"},
        ),
        (
            "browser_type",
            type_text,
            {"ref": "ref", "text": "text"},
        ),
        (
            "browser_scroll",
            scroll,
            {"direction": "direction"},
        ),
        (
            "browser_back",
            back,
            {},
        ),
        (
            "browser_press",
            press,
            {"key": "key"},
        ),
        (
            "browser_get_images",
            get_images,
            {},
        ),
        (
            "browser_vision",
            vision,
            {"question": "question", "annotate": "annotate"},
        ),
        (
            "browser_console",
            console,
            {"clear": "clear", "expression": "expression"},
        ),
    ]

    for name, handler, arg_map in registrations:
        schema = schemas[name]

        def make_handler(fn, mapping):
            def _handle(args, **kw):
                kwargs = {param: args.get(arg, "") for param, arg in mapping.items()}
                if "full" in mapping:
                    kwargs["full"] = bool(args.get("full", False))
                if "clear" in mapping:
                    kwargs["clear"] = bool(args.get("clear", False))
                if "annotate" in mapping:
                    kwargs["annotate"] = bool(args.get("annotate", False))
                if "expression" in mapping:
                    expr = args.get("expression")
                    kwargs["expression"] = expr if isinstance(expr, str) and expr else None
                kwargs["task_id"] = kw.get("task_id")
                return fn(**kwargs)

            return _handle

        ctx.register_tool(
            name=name,
            toolset=_TOOLSET,
            schema=schema,
            handler=make_handler(handler, arg_map),
            check_fn=backend.check_available,
            emoji=_EMOJI.get(name, ""),
            override=True,
        )

    logger.info(
        "CloakBrowser plugin overriding browser_* tools via %s",
        backend.get_cdp_url(),
    )
