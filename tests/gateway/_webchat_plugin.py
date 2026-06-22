"""Install the WebUI-shipped WebChat platform plugin for gateway tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_webchat_plugin_src() -> Optional[Path]:
    """Return the WebUI ``plugin/`` directory if present on disk."""
    env_path = os.getenv("WEBUI_PLUGIN_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
        if (candidate / "plugin.yaml").is_file():
            return candidate
        if (candidate / "adapter.py").is_file():
            return candidate.parent if candidate.name == "adapter.py" else candidate

    sibling = PROJECT_ROOT.parent / "webui" / "plugin"
    if (sibling / "plugin.yaml").is_file():
        return sibling
    return None


def install_webchat_plugin(hermes_home: Path) -> Path:
    """Copy the WebUI plugin into an isolated HERMES_HOME and enable it."""
    src = resolve_webchat_plugin_src()
    if src is None:
        pytest.skip(
            "WebUI plugin not found. Set WEBUI_PLUGIN_PATH or checkout "
            "../webui/plugin next to hermes-agent."
        )

    dst = hermes_home / "plugins" / "webchat-platform"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    config_path = hermes_home / "config.yaml"
    data = {}
    if config_path.is_file():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    plugins = data.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if "webchat-platform" not in enabled:
        enabled.append("webchat-platform")
    plugins["enabled"] = enabled
    config_path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
    return dst


def reset_plugin_discovery() -> None:
    """Force plugin + adapter shim caches to reload after install."""
    from hermes_cli.plugins import discover_plugins

    discover_plugins(force=True)
    import gateway.platforms.webchat as webchat_shim

    webchat_shim._ADAPTER_MODULE = None
