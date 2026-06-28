"""CloakBrowser backend — thread-safe Playwright over CDP."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tools.registry import tool_error

logger = logging.getLogger(__name__)

_SNAPSHOT_MAX_CHARS = 80_000
_THREAD_DEATH_MARKERS = (
    "cannot switch to a different thread",
    "which happens to have exited",
    "greenlet.error",
    "greenlet",
)
_REF_PATTERN = re.compile(r"\[ref=(e\d+)\]", re.IGNORECASE)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def get_cdp_url() -> str:
    url = os.getenv("CLOAKBROWSER_CDP_URL", "").strip().rstrip("/")
    return url or "http://127.0.0.1:9222"


def is_plugin_enabled() -> bool:
    if os.getenv("BROWSER_CDP_URL", "").strip():
        return False
    if not _env_flag("CLOAKBROWSER_PLUGIN_ENABLED", default=True):
        return False
    backend = os.getenv("BROWSER_BACKEND", "").strip().lower()
    if backend and backend not in {"cloakbrowser", ""}:
        return False
    return bool(os.getenv("CLOAKBROWSER_CDP_URL", "").strip()) or backend == "cloakbrowser"


def check_available() -> bool:
    if not is_plugin_enabled():
        return False
    try:
        url = f"{get_cdp_url()}/json/version"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def normalize_ref(ref: str) -> str:
    clean = ref.strip().lstrip("@")
    match = re.fullmatch(r"e(\d+)", clean, re.IGNORECASE)
    if match:
        return f"e{match.group(1)}"
    return clean


def count_snapshot_refs(snapshot: str) -> int:
    return len(set(_REF_PATTERN.findall(snapshot or "")))


def is_thread_death_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _THREAD_DEATH_MARKERS)


@dataclass
class _ConsoleState:
    messages: List[dict] = field(default_factory=list)
    js_errors: List[dict] = field(default_factory=list)

    def append_console(self, msg: Any) -> None:
        try:
            self.messages.append(
                {
                    "type": getattr(msg, "type", "log"),
                    "text": getattr(msg, "text", str(msg)),
                }
            )
        except Exception:
            self.messages.append({"type": "log", "text": str(msg)})

    def append_error(self, err: Any) -> None:
        self.js_errors.append({"text": str(err)})

    def drain(self, clear: bool) -> tuple[list, list]:
        msgs, errs = list(self.messages), list(self.js_errors)
        if clear:
            self.messages.clear()
            self.js_errors.clear()
        return msgs, errs


@dataclass
class _PageSession:
    page: Any
    console: _ConsoleState = field(default_factory=_ConsoleState)


class _BrowserWorker:
    """Runs all Playwright sync API calls on one dedicated thread."""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url.rstrip("/")
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop,
            name="cloakbrowser-plugin-worker",
            daemon=True,
        )
        self._ready = threading.Event()
        self._playwright: Any = None
        self._browser: Any = None
        self._pages: Dict[str, _PageSession] = {}
        self._console_capture = _env_flag("CLOAKBROWSER_CONSOLE_CAPTURE", default=True)
        self._thread.start()
        if not self._ready.wait(timeout=60):
            raise RuntimeError("CloakBrowser worker thread failed to start")

    def _loop(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._ready.set()
            while True:
                item = self._queue.get()
                if item is None:
                    break
                fn, result_box, done = item
                try:
                    result_box.append(fn())
                except Exception as exc:
                    result_box.append(exc)
                finally:
                    done.set()
        except Exception as exc:
            logger.error("CloakBrowser worker failed to start: %s", exc)
            self._ready.set()

    def _submit(self, fn: Callable[[], Any], timeout: float = 120.0) -> Any:
        if threading.current_thread() is self._thread:
            return fn()
        result_box: list[Any] = []
        done = threading.Event()
        self._queue.put((fn, result_box, done))
        if not done.wait(timeout=timeout):
            raise TimeoutError("CloakBrowser operation timed out")
        outcome = result_box[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _reset_connection(self) -> None:
        def work() -> None:
            for session in list(self._pages.values()):
                try:
                    session.page.close()
                except Exception:
                    pass
            self._pages.clear()
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

        self._submit(work, timeout=30)

    def invoke(self, op: str, retry_on_thread_death: bool = True, **kwargs: Any) -> Any:
        def work() -> Any:
            handler = getattr(self, f"_op_{op}")
            return handler(**kwargs)

        try:
            return self._submit(work, timeout=float(kwargs.pop("_timeout", 120)))
        except Exception as exc:
            if retry_on_thread_death and is_thread_death_error(exc):
                logger.warning("CloakBrowser thread/greenlet error — resetting CDP connection: %s", exc)
                self._reset_connection()
                return self._submit(work, timeout=120)
            raise

    def _connect(self) -> Any:
        if self._browser is not None:
            return self._browser
        version_url = f"{self._cdp_url}/json/version"
        version = json.loads(urllib.request.urlopen(version_url, timeout=10).read())
        ws_url = version.get("webSocketDebuggerUrl") or self._cdp_url
        logger.info("CloakBrowser plugin connecting to %s", ws_url)
        self._browser = self._playwright.chromium.connect_over_cdp(ws_url)
        return self._browser

    def _attach_console_listeners(self, page: Any, state: _ConsoleState) -> None:
        if not self._console_capture:
            return

        def on_console(msg: Any) -> None:
            state.append_console(msg)

        def on_error(err: Any) -> None:
            state.append_error(err)

        try:
            page.on("console", on_console)
            page.on("pageerror", on_error)
        except Exception as exc:
            logger.debug("Could not attach console listeners: %s", exc)

    def _get_session(self, task_id: Optional[str]) -> _PageSession:
        key = task_id or "default"
        if key not in self._pages:
            browser = self._connect()
            page = browser.new_page()
            state = _PageSession(page=page)
            self._attach_console_listeners(page, state.console)
            self._pages[key] = state
        return self._pages[key]

    def _resolve_locator(self, page: Any, ref: str) -> Any:
        clean = normalize_ref(ref)
        strategies: List[Callable[[], Any]] = []

        if re.fullmatch(r"e\d+", clean, re.IGNORECASE):
            ref_id = clean.lower()
            strategies.extend(
                [
                    lambda: page.locator(f"aria-ref={ref_id}"),
                    lambda: page.locator(f'[aria-ref="{ref_id}"]'),
                    lambda: page.locator(f"internal:aria-ref={ref_id}"),
                    lambda: page.locator(f"#{ref_id}"),
                ]
            )

        if clean:
            strategies.append(lambda: page.locator(clean))

        for role, name in (
            ("combobox", "Search"),
            ("searchbox", None),
            ("textbox", None),
        ):
            if name:
                strategies.append(lambda r=role, n=name: page.get_by_role(r, name=n))
            else:
                strategies.append(lambda r=role: page.get_by_role(r))

        last_error: Optional[Exception] = None
        for factory in strategies:
            try:
                locator = factory()
                if locator.count() > 0:
                    return locator.first
            except Exception as exc:
                last_error = exc
                continue

        raise ValueError(
            f"Could not resolve ref {ref!r}"
            + (f": {last_error}" if last_error else "")
        )

    def _snapshot_text(self, page: Any, full: bool, user_task: Optional[str]) -> tuple[str, int]:
        try:
            snapshot = page.locator("body").aria_snapshot()
        except Exception:
            snapshot = page.locator("body").evaluate("el => el.innerText") or ""

        from tools.browser_tool import (
            SNAPSHOT_SUMMARIZE_THRESHOLD,
            _extract_relevant_content,
            _truncate_snapshot,
        )

        if len(snapshot) > SNAPSHOT_SUMMARIZE_THRESHOLD:
            if user_task:
                snapshot = _extract_relevant_content(snapshot, user_task)
            else:
                snapshot = _truncate_snapshot(snapshot)

        if len(snapshot) > _SNAPSHOT_MAX_CHARS:
            snapshot = snapshot[:_SNAPSHOT_MAX_CHARS] + "\n… [truncated]"

        return snapshot, count_snapshot_refs(snapshot)

    def _op_navigate(self, url: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        session.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        result: dict = {
            "success": True,
            "url": session.page.url,
            "title": session.page.title(),
        }
        if _env_flag("CLOAKBROWSER_NAVIGATE_SNAPSHOT", default=True):
            snapshot, refs = self._snapshot_text(session.page, full=False, user_task=None)
            result["snapshot"] = snapshot
            result["element_count"] = refs
        return result

    def _op_snapshot(
        self,
        full: bool = False,
        task_id: Optional[str] = None,
        user_task: Optional[str] = None,
    ) -> dict:
        session = self._get_session(task_id)
        snapshot, refs = self._snapshot_text(session.page, full=full, user_task=user_task)
        return {"success": True, "snapshot": snapshot, "element_count": refs}

    def _op_click(self, ref: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        locator = self._resolve_locator(session.page, ref)
        locator.click(timeout=10000)
        return {"success": True, "clicked": normalize_ref(ref), "url": session.page.url}

    def _op_type(self, ref: str, text: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        locator = self._resolve_locator(session.page, ref)
        locator.fill("", timeout=10000)
        locator.type(text, delay=30, timeout=30000)
        return {"success": True, "typed": text, "element": normalize_ref(ref)}

    def _op_scroll(self, direction: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        amount = 500
        scrolls = {
            "down": f"window.scrollBy(0, {amount})",
            "up": f"window.scrollBy(0, -{amount})",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }
        expr = scrolls.get(direction)
        if not expr:
            raise ValueError(f"Unknown scroll direction: {direction}")
        session.page.evaluate(expr)
        return {"success": True, "scrolled": direction}

    def _op_back(self, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        session.page.go_back(timeout=30000, wait_until="domcontentloaded")
        return {"success": True, "url": session.page.url}

    def _op_press(self, key: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        session.page.keyboard.press(key)
        return {"success": True, "pressed": key}

    def _op_close(self, task_id: Optional[str] = None) -> dict:
        key = task_id or "default"
        session = self._pages.pop(key, None)
        if session:
            try:
                session.page.close()
            except Exception:
                pass
            return {"success": True, "closed": True}
        return {"success": True, "closed": False}

    def _op_get_images(self, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        images = session.page.evaluate(
            """() => Array.from(document.querySelectorAll('img')).map(img => ({
                src: img.src || img.getAttribute('src') || '',
                alt: img.alt || img.getAttribute('alt') || ''
            }))"""
        )
        filtered = [img for img in images if img.get("src") and not img["src"].startswith("data:")]
        return {"success": True, "images": filtered[:50], "count": len(filtered)}

    def _op_console(
        self,
        clear: bool = False,
        expression: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict:
        session = self._get_session(task_id)
        if expression:
            result = session.page.evaluate(expression)
            return {
                "success": True,
                "result": result,
                "result_type": type(result).__name__,
            }

        msgs, errs = session.console.drain(clear=clear)
        return {
            "success": True,
            "console_messages": msgs,
            "js_errors": errs,
            "total_messages": len(msgs),
            "total_errors": len(errs),
        }

    def _op_eval(self, expression: str, task_id: Optional[str] = None) -> dict:
        session = self._get_session(task_id)
        result = session.page.evaluate(expression)
        return {
            "success": True,
            "result": result,
            "result_type": type(result).__name__,
        }

    def _op_vision(
        self,
        question: str,
        annotate: bool = False,
        task_id: Optional[str] = None,
    ) -> dict:
        session = self._get_session(task_id)
        screenshot_bytes = session.page.screenshot(full_page=False)

        from hermes_constants import get_hermes_home

        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(
            screenshots_dir / f"cloakbrowser_plugin_{uuid.uuid4().hex[:8]}.png"
        )
        with open(screenshot_path, "wb") as handle:
            handle.write(screenshot_bytes)

        import base64

        img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        annotation_context = ""
        if annotate:
            snap, _ = self._snapshot_text(session.page, full=False, user_task=None)
            annotation_context = f"\n\nAccessibility tree:\n{snap[:3000]}"

        from agent.auxiliary_client import call_llm
        from agent.redact import redact_sensitive_text
        from hermes_cli.config import cfg_get, load_config

        annotation_context = redact_sensitive_text(annotation_context)
        vision_prompt = f"Analyze this browser screenshot and answer: {question}{annotation_context}"

        try:
            cfg = load_config()
            vision_cfg = cfg_get(cfg, "auxiliary", "vision", default={})
            vision_timeout = float(vision_cfg.get("timeout", 120))
            vision_temperature = float(vision_cfg.get("temperature", 0.1))
        except Exception:
            vision_timeout = 120.0
            vision_temperature = 0.1

        response = call_llm(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            task="vision",
            temperature=vision_temperature,
            timeout=vision_timeout,
        )
        analysis = (response.choices[0].message.content or "").strip() if response.choices else ""
        analysis = redact_sensitive_text(analysis)
        return {"success": True, "analysis": analysis, "screenshot_path": screenshot_path}

    def cleanup_task(self, task_id: Optional[str]) -> None:
        try:
            self.invoke("close", task_id=task_id, retry_on_thread_death=False)
        except Exception as exc:
            logger.debug("CloakBrowser cleanup for %s: %s", task_id, exc)

    def shutdown(self) -> None:
        def work() -> None:
            for session in list(self._pages.values()):
                try:
                    session.page.close()
                except Exception:
                    pass
            self._pages.clear()
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

        try:
            self._submit(work, timeout=15)
        except Exception:
            pass


_worker: Optional[_BrowserWorker] = None
_worker_lock = threading.Lock()


def _get_worker() -> _BrowserWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _BrowserWorker(get_cdp_url())
        return _worker


def run_op(op: str, **kwargs: Any) -> str:
    try:
        payload = _get_worker().invoke(op, **kwargs)
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as exc:
        return tool_error(str(exc), success=False)


def cleanup_task(task_id: Optional[str]) -> None:
    if _worker is not None:
        _worker.cleanup_task(task_id)


def cleanup_all() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.shutdown()
            _worker = None
