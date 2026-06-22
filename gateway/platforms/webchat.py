"""WebChat platform — Hermes adapter shipped from the WebUI repo.

The implementation lives in the sibling WebUI checkout::

    <webui>/plugin/adapter.py

Mounted into the gateway container at::

    ~/.hermes/plugins/webchat-platform/

This module re-exports the adapter for gateway runner hooks that still
import from ``gateway.platforms.webchat`` until those paths move to generic
platform extension hooks.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_ADAPTER_MODULE: ModuleType | None = None


def _webui_plugin_adapter_paths() -> list[Path]:
    """Return candidate ``adapter.py`` paths, highest priority first."""
    paths: list[Path] = []

    env_path = os.getenv("WEBUI_PLUGIN_PATH", "").strip()
    if env_path:
        paths.append(Path(env_path) / "adapter.py")

    repo_root = Path(__file__).resolve().parents[2]
    paths.append(repo_root.parent / "webui" / "plugin" / "adapter.py")

    try:
        from hermes_constants import get_hermes_home

        paths.append(get_hermes_home() / "plugins" / "webchat-platform" / "adapter.py")
    except Exception:
        pass

    return paths


def _adapter_module() -> ModuleType:
    global _ADAPTER_MODULE
    if _ADAPTER_MODULE is not None:
        return _ADAPTER_MODULE

    adapter_path = next((p for p in _webui_plugin_adapter_paths() if p.is_file()), None)
    if adapter_path is None:
        searched = ", ".join(str(p) for p in _webui_plugin_adapter_paths())
        raise ImportError(
            "WebChat adapter not found. Install the WebUI plugin at "
            "~/.hermes/plugins/webchat-platform/ or set WEBUI_PLUGIN_PATH. "
            f"Searched: {searched}"
        )

    module_name = "hermes_webchat_platform_adapter"
    spec = importlib.util.spec_from_file_location(module_name, adapter_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load WebChat adapter from {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    try:
        from hermes_cli.plugins import discover_plugins

        discover_plugins()
    except Exception:
        pass

    _ADAPTER_MODULE = module
    return module


def __getattr__(name: str) -> Any:
    return getattr(_adapter_module(), name)
