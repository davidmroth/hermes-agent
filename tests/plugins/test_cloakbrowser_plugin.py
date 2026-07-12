import importlib.util
import queue
import sys
import threading
import unittest.mock as mock
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


def _make_worker(backend, cdp_url="http://fake:9222"):
    """Build a _BrowserWorker with a live queue/thread but without Playwright.

    Uses a minimal queue-draining loop so tests don't need playwright installed.
    """
    worker = object.__new__(backend._BrowserWorker)
    worker._cdp_url = cdp_url
    worker._queue = queue.Queue()
    worker._playwright = mock.MagicMock()
    worker._browser = None
    worker._pages = {}
    worker._console_capture = False
    worker._playwright_broken = False
    worker._ready = threading.Event()

    def _simple_loop():
        worker._ready.set()
        while True:
            item = worker._queue.get()
            if item is None:
                break
            fn, result_box, done = item
            try:
                result_box.append(fn())
            except Exception as exc:
                result_box.append(exc)
            finally:
                done.set()

    worker._thread = threading.Thread(target=_simple_loop, daemon=True, name="test-worker")
    worker._thread.start()
    worker._ready.wait(timeout=5)
    return worker


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


def test_plugin_yaml_is_bundled_backend():
    """Bundled kind=backend auto-loads without plugins.enabled."""
    import yaml

    manifest = Path(__file__).resolve().parents[2] / "plugins" / "cloakbrowser" / "plugin.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert data.get("kind") == "backend"


def test_core_cloakbrowser_mode_disabled(monkeypatch):
    """Core browser_tool must not route to non-thread-safe helpers."""
    monkeypatch.setenv("BROWSER_BACKEND", "cloakbrowser")
    monkeypatch.setenv("CLOAKBROWSER_CDP_URL", "http://cloakbrowser:9222")
    from tools.browser_cloakbrowser import is_cloakbrowser_mode

    assert is_cloakbrowser_mode() is False


# ---------------------------------------------------------------------------
# _BrowserWorker recovery tests
# ---------------------------------------------------------------------------

def test_submit_raises_when_thread_dead():
    """_submit should raise immediately (not hang) when the worker thread has died."""
    backend = _load_backend_module()
    worker = _make_worker(backend)
    # Kill the thread by sending poison pill, then wait for it to exit.
    worker._queue.put(None)
    worker._thread.join(timeout=5)
    assert not worker._thread.is_alive()

    with pytest.raises(RuntimeError, match="worker thread has exited"):
        worker._submit(lambda: "x", timeout=1)


def test_is_alive_false_when_playwright_broken():
    backend = _load_backend_module()
    worker = _make_worker(backend)
    assert worker.is_alive() is True
    worker._playwright_broken = True
    assert worker.is_alive() is False
    worker._queue.put(None)
    worker._thread.join(timeout=5)


def test_reset_connection_survives_greenlet_error_in_cleanup():
    """_reset_connection must clear state even when page.close() raises a greenlet error."""
    backend = _load_backend_module()
    worker = _make_worker(backend)

    # Add a fake page session whose close() raises a greenlet-style error.
    fake_page = mock.MagicMock()
    fake_page.close.side_effect = RuntimeError("cannot switch to a different thread")
    worker._pages["t1"] = backend._PageSession(page=fake_page)
    worker._browser = mock.MagicMock()
    worker._browser.close.side_effect = RuntimeError("which happens to have exited")

    worker._reset_connection()

    # State must be cleared regardless of the errors.
    assert worker._pages == {}
    assert worker._browser is None

    worker._queue.put(None)
    worker._thread.join(timeout=5)


def test_invoke_retries_after_greenlet_error():
    """invoke() retries after the first greenlet error once reset succeeds."""
    backend = _load_backend_module()
    worker = _make_worker(backend)

    call_count = {"n": 0}

    def op_navigate(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("cannot switch to a different thread")
        return {"success": True, "url": "http://ok"}

    worker._op_navigate = op_navigate

    result = worker.invoke("navigate", url="http://example.com")
    assert result == {"success": True, "url": "http://ok"}
    assert call_count["n"] == 2

    worker._queue.put(None)
    worker._thread.join(timeout=5)


def test_invoke_restarts_playwright_when_retry_also_fails():
    """invoke() calls _restart_playwright() when both attempts hit greenlet errors."""
    backend = _load_backend_module()
    worker = _make_worker(backend)

    greenlet_err = RuntimeError("cannot switch to a different thread")
    call_count = {"n": 0}

    def op_navigate(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise greenlet_err
        return {"success": True, "url": "http://recovered"}

    worker._op_navigate = op_navigate

    restart_called = {"called": False}
    original_restart = worker._restart_playwright

    def fake_restart():
        restart_called["called"] = True
        # Simulate successful Playwright restart.
        worker._playwright = mock.MagicMock()
        worker._playwright_broken = False

    worker._restart_playwright = fake_restart

    result = worker.invoke("navigate", url="http://example.com")
    assert restart_called["called"] is True
    assert result == {"success": True, "url": "http://recovered"}

    worker._queue.put(None)
    worker._thread.join(timeout=5)


def test_get_worker_replaces_dead_worker(monkeypatch):
    """_get_worker() creates a new worker when the existing one is dead/broken."""
    backend = _load_backend_module()

    # Patch _BrowserWorker.__init__ so we don't actually start Playwright.
    init_count = {"n": 0}

    class FakeWorker:
        def __init__(self, cdp_url):
            init_count["n"] += 1
            self._playwright_broken = False
            # Use the main test thread so is_alive() stays True while the
            # test runs (a daemon thread running lambda: None exits instantly).
            self._thread = threading.main_thread()

        def is_alive(self):
            return self._thread.is_alive() and not self._playwright_broken
    monkeypatch.setattr(backend, "_BrowserWorker", FakeWorker)
    monkeypatch.setattr(backend, "_worker", None)
    monkeypatch.setattr(backend, "_worker_lock", threading.Lock())

    w1 = backend._get_worker()
    assert init_count["n"] == 1
    # Same worker returned on second call.
    w2 = backend._get_worker()
    assert w1 is w2
    assert init_count["n"] == 1

    # Mark it as broken — _get_worker() should replace it.
    w1._playwright_broken = True
    w3 = backend._get_worker()
    assert w3 is not w1
    assert init_count["n"] == 2
