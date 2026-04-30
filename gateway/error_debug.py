"""Gateway exception diagnostics for user-visible turn failures."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import platform
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home


def _safe_package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except Exception:
        return None


def _safe_find_spec(module_name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception as exc:
        return f"find_spec failed: {type(exc).__name__}: {exc}"
    if spec is None:
        return None
    return str(spec.origin or "<namespace package>")


def build_exception_diagnostics(
    exc: BaseException,
    *,
    context: str,
    fields: Mapping[str, Any] | None = None,
) -> str:
    """Build a compact but actionable diagnostic block for gateway logs."""
    exc_type = type(exc).__name__
    lines = [
        f"Gateway exception diagnostics ({context})",
        f"exception={exc_type}: {exc}",
        f"python={sys.version.split()[0]} executable={sys.executable}",
        f"platform={platform.platform()}",
        f"cwd={Path.cwd()}",
        f"hermes_home={get_hermes_home()}",
        f"argv={sys.argv!r}",
    ]

    if fields:
        for key, value in fields.items():
            lines.append(f"{key}={value!r}")

    missing_module = getattr(exc, "name", None) if isinstance(exc, ModuleNotFoundError) else None
    if missing_module:
        package_name = str(missing_module).split(".", 1)[0]
        lines.extend(
            [
                f"missing_module={missing_module}",
                f"missing_module_spec={_safe_find_spec(str(missing_module))!r}",
                f"missing_package_version={_safe_package_version(package_name)!r}",
            ]
        )

    python_path = os.getenv("PYTHONPATH")
    if python_path:
        lines.append(f"PYTHONPATH={python_path}")
    lines.append(f"sys_path_head={sys.path[:12]!r}")
    lines.append("traceback:\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
    return "\n".join(lines)


def log_exception_diagnostics(
    logger: logging.Logger,
    exc: BaseException,
    *,
    context: str,
    fields: Mapping[str, Any] | None = None,
) -> None:
    """Log diagnostics without allowing the diagnostic path to raise."""
    try:
        logger.error("%s", build_exception_diagnostics(exc, context=context, fields=fields))
    except Exception:
        logger.exception("Failed to build gateway exception diagnostics for %s", context)
