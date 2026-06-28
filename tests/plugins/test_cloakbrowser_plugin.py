import importlib.util
import sys
from pathlib import Path

import pytest


def _load_backend_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "plugins" / "cloakbrowser" / "backend.py"
    spec = importlib.util.spec_from_file_location("cloakbrowser_plugin_backend_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cloak_env(monkeypatch):
    monkeypatch.delenv("BROWSER_CDP_URL", raising=False)
    monkeypatch.setenv("CLOAKBROWSER_CDP_URL", "http://cloakbrowser:9222")
    monkeypatch.delenv("BROWSER_BACKEND", raising=False)


def test_is_plugin_enabled_with_cdp_url(cloak_env, monkeypatch):
    backend = _load_backend_module()
    assert backend.is_plugin_enabled() is True


def test_is_plugin_disabled_when_browser_cdp_override(cloak_env, monkeypatch):
    backend = _load_backend_module()
    monkeypatch.setenv("BROWSER_CDP_URL", "http://chrome:9222")
    assert backend.is_plugin_enabled() is False


def test_is_plugin_disabled_by_flag(cloak_env, monkeypatch):
    backend = _load_backend_module()
    monkeypatch.setenv("CLOAKBROWSER_PLUGIN_ENABLED", "0")
    assert backend.is_plugin_enabled() is False


def test_normalize_ref():
    backend = _load_backend_module()
    assert backend.normalize_ref("@e12") == "e12"
    assert backend.normalize_ref("E5") == "e5"


def test_count_snapshot_refs():
    backend = _load_backend_module()
    text = '- button "Go" [ref=e1]\n- link "Home" [ref=e2]\n- button "Go" [ref=e1]'
    assert backend.count_snapshot_refs(text) == 2


def test_is_thread_death_error():
    backend = _load_backend_module()
    assert backend.is_thread_death_error(RuntimeError("cannot switch to a different thread"))
    assert backend.is_thread_death_error(Exception("greenlet.error: Cannot switch"))
    assert not backend.is_thread_death_error(RuntimeError("timeout"))
