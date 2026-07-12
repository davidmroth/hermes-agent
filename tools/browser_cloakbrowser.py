"""CloakBrowser backend — DEPRECATED core helpers.

.. warning::

   Do not use this module for gateway browser tools. The supported path is
   ``plugins/cloakbrowser`` with ``CLOAKBROWSER_CDP_URL`` (thread-safe Playwright
   worker). ``is_cloakbrowser_mode()`` always returns False so ``browser_tool``
   never routes here.

Historical notes (cloakserve CDP)::

    docker run -d --name cloak -p 127.0.0.1:9222:9222 cloakhq/cloakbrowser cloakserve
    # Prefer: CLOAKBROWSER_CDP_URL=http://cloakbrowser:9222 + plugins/cloakbrowser
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import urllib.request
import uuid
from typing import Any, Dict, Optional

from tools.registry import tool_error

logger = logging.getLogger(__name__)

_SNAPSHOT_MAX_CHARS = 80_000


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logger.debug("Ignoring invalid boolean env %s=%r", name, raw)
    return None


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_cloakbrowser_cdp_url() -> str:
    """Return the configured CloakBrowser CDP URL, or default."""
    url = _env_str("CLOAKBROWSER_CDP_URL", "")
    if url:
        return url.rstrip("/")
    return "http://127.0.0.1:9222"


def is_cloakbrowser_mode() -> bool:
    """Deprecated core routing — always False.

    CloakBrowser is owned by ``plugins/cloakbrowser`` (set ``CLOAKBROWSER_CDP_URL``).
    The built-in Playwright helpers in this module are not thread-safe under the
    gateway thread pool and must not be selected via ``BROWSER_BACKEND``.
    """
    backend = _env_str("BROWSER_BACKEND", "").lower()
    if backend == "cloakbrowser":
        logger.warning(
            "BROWSER_BACKEND=cloakbrowser is ignored for core routing; "
            "use CLOAKBROWSER_CDP_URL so plugins/cloakbrowser owns browser_* tools"
        )
    return False


def check_cloakbrowser_available() -> bool:
    """Verify the CloakBrowser CDP server is reachable."""
    try:
        url = f"{get_cloakbrowser_cdp_url()}/json/version"
        resp = urllib.request.urlopen(url, timeout=5)
        return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Browser lifecycle via CDP
# ---------------------------------------------------------------------------
# We connect to the cloakserve CDP endpoint using Playwright's
# ``connect_over_cdp()``.  Each task gets its own page (tab).
# The browser itself runs in a separate Docker container.

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:
    sync_playwright = None  # type: ignore[assignment]
    _PW_AVAILABLE = False

_pages: Dict[str, Any] = {}
_pages_lock = threading.Lock()
_browser: Any = None
_browser_lock = threading.Lock()


def _get_playwright() -> Any:
    if not _PW_AVAILABLE:
        raise RuntimeError("playwright package not installed. Run: pip install playwright")
    return sync_playwright().start()


def _ensure_browser():
    """Connect to the CloakBrowser CDP server (singleton per process)."""
    global _browser
    if _browser is not None:
        return _browser

    with _browser_lock:
        if _browser is not None:
            return _browser

        try:
            pw = _get_playwright()
            cdp_url = get_cloakbrowser_cdp_url()

            version_url = f"{cdp_url}/json/version"
            version = json.loads(urllib.request.urlopen(version_url, timeout=10).read())
            ws_url = version.get("webSocketDebuggerUrl", "")

            if not ws_url:
                ws_url = cdp_url

            logger.info("Connecting to CloakBrowser CDP at %s", ws_url)
            _browser = pw.chromium.connect_over_cdp(ws_url)
            logger.info("CloakBrowser connected successfully")

        except Exception as exc:
            logger.error("Failed to connect to CloakBrowser: %s", exc)
            raise

    return _browser


def _get_page(task_id: Optional[str]) -> Any:
    """Get or create a page (tab) for the given task."""
    task_id = task_id or "default"
    with _pages_lock:
        if task_id not in _pages:
            browser = _ensure_browser()
            page = browser.new_page()
            _pages[task_id] = page
            logger.debug("Created new CloakBrowser page for task %s", task_id)
        return _pages[task_id]


def _close_page(task_id: Optional[str]) -> bool:
    """Close and remove the page for the given task."""
    task_id = task_id or "default"
    with _pages_lock:
        page = _pages.pop(task_id, None)
        if page:
            try:
                page.close()
                logger.debug("Closed CloakBrowser page for task %s", task_id)
                return True
            except Exception as exc:
                logger.debug("Error closing CloakBrowser page for task %s: %s", task_id, exc)
        return False


def _close_all_pages():
    """Close all pages (called on cleanup)."""
    with _pages_lock:
        for task_id, page in list(_pages.items()):
            try:
                page.close()
            except Exception:
                pass
        _pages.clear()


def _close_browser():
    """Close the browser connection (called on cleanup)."""
    global _browser
    with _browser_lock:
        if _browser:
            try:
                _browser.close()
                logger.info("CloakBrowser disconnected")
            except Exception as exc:
                logger.debug("Error closing CloakBrowser: %s", exc)
            _browser = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def cloakbrowser_navigate(url: str, task_id: Optional[str] = None) -> str:
    """Navigate to a URL via CloakBrowser."""
    try:
        page = _get_page(task_id)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return json.dumps({"success": True, "url": page.url, "title": page.title()})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_snapshot(full: bool = False, task_id: Optional[str] = None,
                          user_task: Optional[str] = None) -> str:
    """Get accessibility tree snapshot from CloakBrowser."""
    try:
        page = _get_page(task_id)
        try:
            snapshot = page.locator("body").aria_snapshot()
        except Exception:
            snapshot = page.locator("body").evaluate("el => el.outerHTML")

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

        import re
        refs = re.findall(r'\[e\d+\]', snapshot)
        refs_count = len(set(refs)) if refs else 0

        return json.dumps({"success": True, "snapshot": snapshot, "element_count": refs_count})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_click(ref: str, task_id: Optional[str] = None) -> str:
    """Click an element by ref via CloakBrowser."""
    try:
        page = _get_page(task_id)
        clean_ref = ref.lstrip("@")
        locator = page.locator(f"[ref={clean_ref}]")
        if not locator.count():
            locator = page.get_by_role("link", name=clean_ref) if "href" in clean_ref.lower() else page.locator(clean_ref)
        locator.first.click(timeout=5000)
        return json.dumps({"success": True, "clicked": clean_ref, "url": page.url})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_type(ref: str, text: str, task_id: Optional[str] = None) -> str:
    """Type text into an element by ref via CloakBrowser."""
    try:
        page = _get_page(task_id)
        clean_ref = ref.lstrip("@")
        locator = page.locator(f"[ref={clean_ref}]")
        if not locator.count():
            locator = page.locator(clean_ref)
        locator.first.fill("")
        locator.first.type(text, delay=50)
        return json.dumps({"success": True, "typed": text, "element": clean_ref})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_scroll(direction: str, task_id: Optional[str] = None) -> str:
    """Scroll the page via CloakBrowser."""
    try:
        page = _get_page(task_id)
        amount = 500
        scrolls = {
            "down": f"window.scrollBy(0, {amount})",
            "up": f"window.scrollBy(0, -{amount})",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }
        expr = scrolls.get(direction)
        if not expr:
            return tool_error(f"Unknown scroll direction: {direction}", success=False)
        page.evaluate(expr)
        return json.dumps({"success": True, "scrolled": direction})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_back(task_id: Optional[str] = None) -> str:
    """Navigate back via CloakBrowser."""
    try:
        page = _get_page(task_id)
        page.go_back(timeout=30000, wait_until="domcontentloaded")
        return json.dumps({"success": True, "url": page.url})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_press(key: str, task_id: Optional[str] = None) -> str:
    """Press a keyboard key via CloakBrowser."""
    try:
        page = _get_page(task_id)
        page.keyboard.press(key)
        return json.dumps({"success": True, "pressed": key})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_close(task_id: Optional[str] = None) -> str:
    """Close the CloakBrowser page/session."""
    try:
        closed = _close_page(task_id)
        return json.dumps({"success": True, "closed": closed})
    except Exception as e:
        return json.dumps({"success": True, "closed": True, "warning": str(e)})


def cloakbrowser_get_images(task_id: Optional[str] = None) -> str:
    """Get images on the current page via CloakBrowser."""
    try:
        page = _get_page(task_id)
        images = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img')).map(img => ({
                src: img.src || img.getAttribute('src') || '',
                alt: img.alt || img.getAttribute('alt') || ''
            }));
        }""")
        filtered = [img for img in images if img.get("src") and not img["src"].startswith("data:")]
        return json.dumps({"success": True, "images": filtered[:50], "count": len(filtered)})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_vision(question: str, annotate: bool = False,
                        task_id: Optional[str] = None) -> str:
    """Take a screenshot and analyze it with vision AI via CloakBrowser."""
    try:
        page = _get_page(task_id)
        screenshot_bytes = page.screenshot(full_page=False)

        from hermes_constants import get_hermes_home
        screenshots_dir = get_hermes_home() / "browser_screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = str(screenshots_dir / f"cloakbrowser_screenshot_{uuid.uuid4().hex[:8]}.png")

        with open(screenshot_path, "wb") as f:
            f.write(screenshot_bytes)

        img_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        annotation_context = ""
        if annotate:
            try:
                snap_data = cloakbrowser_snapshot(task_id=task_id)
                snap = json.loads(snap_data)
                annotation_context = f"\n\nAccessibility tree:\n{snap.get('snapshot', '')[:3000]}"
            except Exception:
                pass

        from agent.redact import redact_sensitive_text
        annotation_context = redact_sensitive_text(annotation_context)

        from agent.auxiliary_client import call_llm
        from hermes_cli.config import cfg_get, load_config

        vision_prompt = f"Analyze this browser screenshot and answer: {question}{annotation_context}"

        try:
            _cfg = load_config()
            _vision_cfg = cfg_get(_cfg, "auxiliary", "vision", default={})
            _vision_timeout = float(_vision_cfg.get("timeout", 120))
            _vision_temperature = float(_vision_cfg.get("temperature", 0.1))
        except Exception:
            _vision_timeout = 120.0
            _vision_temperature = 0.1

        response = call_llm(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            task="vision",
            temperature=_vision_temperature,
            timeout=_vision_timeout,
        )
        analysis = (response.choices[0].message.content or "").strip() if response.choices else ""
        analysis = redact_sensitive_text(analysis)

        return json.dumps({"success": True, "analysis": analysis, "screenshot_path": screenshot_path})
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_console(clear: bool = False, task_id: Optional[str] = None) -> str:
    """Get console output from CloakBrowser."""
    try:
        return json.dumps({
            "success": True, "console_messages": [], "js_errors": [],
            "total_messages": 0, "total_errors": 0,
            "note": "Console log capture requires pre-registered listeners. "
                    "Use browser_snapshot or browser_vision to inspect page state.",
        })
    except Exception as e:
        return tool_error(str(e), success=False)


def cloakbrowser_eval(expression: str, task_id: Optional[str] = None) -> str:
    """Evaluate JavaScript in the page context via CloakBrowser."""
    try:
        page = _get_page(task_id)
        result = page.evaluate(expression)
        return json.dumps({"success": True, "result": result, "result_type": type(result).__name__},
                          ensure_ascii=False, default=str)
    except Exception as e:
        return tool_error(str(e), success=False)


# ---------------------------------------------------------------------------
# Cleanup hooks
# ---------------------------------------------------------------------------

def cloakbrowser_soft_cleanup(task_id: Optional[str] = None) -> bool:
    """Release the in-memory page without closing the browser."""
    return _close_page(task_id)


def cloakbrowser_cleanup_all():
    """Close all pages and the browser connection."""
    _close_all_pages()
    _close_browser()
