"""Renderer-backed briefing tool."""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

from hermes_cli.config import load_config
from hermes_constants import is_container
from tools.registry import registry, tool_error, tool_result


_SLUG_RE = re.compile(r"[^a-z0-9]+")


CREATE_BRIEFING_SCHEMA = {
    "name": "create_briefing",
    "description": (
        "Render a structured multimedia briefing into synchronized audio and HTML assets. "
        "Use this after researching the topic and assembling concrete sections with narration, citations, and sources. "
        "By default it waits for completion and returns renderer URLs plus the WebUI preview path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Optional stable renderer job id. If omitted, Hermes derives one deterministically from briefing_id.",
            },
            "briefing_id": {
                "type": "string",
                "description": "Optional stable ID for the briefing. If omitted, Hermes generates one from the title and timestamp.",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable title for the briefing.",
            },
            "topic": {
                "type": "string",
                "description": "The main topic or question the briefing addresses.",
            },
            "summary": {
                "type": "string",
                "description": "Optional short executive summary shown above the rendered briefing.",
            },
            "locale": {
                "type": "string",
                "description": "Locale for narration and display, for example en-US. Defaults to en-US.",
            },
            "generated_by": {
                "type": "string",
                "description": "Optional generator label. Defaults to hermes.",
            },
            "sections": {
                "type": "array",
                "description": "Ordered briefing sections. Each section must include a spoken narration string.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Stable section id."},
                        "title": {"type": "string", "description": "Section heading."},
                        "narration": {
                            "type": "string",
                            "description": "Spoken-language narration for this section. Write complete sentences, not fragments.",
                        },
                        "body": {
                            "type": "array",
                            "description": "Optional supporting paragraphs or bullet-like lines.",
                            "items": {"type": "string"},
                        },
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "value": {"type": "string"},
                                    "trend": {"type": "string"},
                                },
                                "required": ["id", "label", "value"],
                            },
                        },
                        "illustrations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "caption": {"type": "string"},
                                    "kind": {"type": "string", "description": "One of illustration, map, or chart."},
                                },
                                "required": ["id", "title", "caption"],
                            },
                        },
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "source_id": {
                                        "type": "string",
                                        "description": "Must match a source id from the top-level sources list.",
                                    },
                                    "note": {"type": "string"},
                                },
                                "required": ["id", "label", "source_id"],
                            },
                        },
                    },
                    "required": ["id", "title", "narration"],
                },
            },
            "sources": {
                "type": "array",
                "description": "Source records referenced by the briefing and citations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "publisher": {"type": "string"},
                        "url": {"type": "string"},
                        "accessed_at": {"type": "string"},
                        "excerpt": {"type": "string"},
                    },
                    "required": ["id", "title", "publisher", "url"],
                },
            },
            "wait_for_completion": {
                "type": "boolean",
                "description": "Wait for the renderer job to finish before returning. Defaults to true.",
            },
            "max_wait_seconds": {
                "type": "number",
                "description": "Optional maximum time to wait when wait_for_completion is true. Defaults to briefing.max_wait_seconds from config.",
            },
            "poll_interval_seconds": {
                "type": "number",
                "description": "Optional poll interval while waiting for completion. Defaults to briefing.poll_interval_seconds from config.",
            },
        },
        "required": ["title", "topic", "sections"],
    },
}


def _briefing_config() -> dict[str, Any]:
    return (load_config() or {}).get("briefing", {}) or {}


def _resolve_renderer_base_url() -> str:
    cfg = _briefing_config()
    configured = str(cfg.get("renderer_base_url") or "").strip()
    if configured:
        return configured.rstrip("/")
    env_override = os.getenv("BRIEFING_RENDERER_BASE_URL", "").strip()
    if env_override:
        return env_override.rstrip("/")
    if is_container():
        return "http://briefing:8080"
    return "http://127.0.0.1:9910"


def _resolve_service_token() -> str:
    return os.getenv("BRIEFING_RENDERER_SERVICE_TOKEN", "").strip()


def _resolve_request_timeout_seconds() -> float:
    raw = _briefing_config().get("request_timeout_seconds", 20)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 20.0


def _resolve_max_wait_seconds(args: dict[str, Any]) -> float:
    raw = args.get("max_wait_seconds")
    if raw is None:
        raw = _briefing_config().get("max_wait_seconds", 90)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 90.0


def _resolve_poll_interval_seconds(args: dict[str, Any]) -> float:
    raw = args.get("poll_interval_seconds")
    if raw is None:
        raw = _briefing_config().get("poll_interval_seconds", 1.0)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def _slugify(text: str) -> str:
    lowered = (text or "briefing").strip().lower()
    lowered = _SLUG_RE.sub("-", lowered).strip("-")
    return lowered or "briefing"


def _auto_briefing_id(title: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{_slugify(title)}-{timestamp}"


def _derive_job_id(briefing_id: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"briefing:{briefing_id}").hex


def _headers() -> dict[str, str]:
    token = _resolve_service_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _absolute_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _asset_url(base_url: str, job_id: str, asset_path: str) -> str:
    return _absolute_url(base_url, f"/v1/briefings/{job_id}/assets/{asset_path.lstrip('/')}")


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return []


def _validation_error(loc: tuple[str, ...], msg: str) -> dict[str, Any]:
    return {"loc": list(loc), "msg": msg}


def _validate_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not str(payload.get("title") or "").strip():
        errors.append(_validation_error(("title",), "Field required"))
    if not str(payload.get("topic") or "").strip():
        errors.append(_validation_error(("topic",), "Field required"))

    sections = _coerce_list(payload.get("sections"))
    if not sections:
        errors.append(_validation_error(("sections",), "At least one section is required"))
        return errors

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(_validation_error(("sections", str(index)), "Section must be an object"))
            continue
        for field in ("id", "title", "narration"):
            if not str(section.get(field) or "").strip():
                errors.append(_validation_error(("sections", str(index), field), "Field required"))
    return errors


def _normalize_request_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in args.items()
        if key not in {"wait_for_completion", "max_wait_seconds", "poll_interval_seconds"}
    }
    payload["sections"] = _coerce_list(payload.get("sections"))
    payload["sources"] = _coerce_list(payload.get("sources"))
    if not payload.get("briefing_id"):
        payload["briefing_id"] = _auto_briefing_id(str(payload.get("title") or "briefing"))
    if not payload.get("job_id"):
        payload["job_id"] = _derive_job_id(str(payload["briefing_id"]))
    if not payload.get("generated_by"):
        payload["generated_by"] = "hermes"
    if not payload.get("locale"):
        payload["locale"] = "en-US"
    return payload


def _build_summary(result: dict[str, Any], base_url: str) -> dict[str, Any]:
    job_id = str(result["job_id"])
    assets = result.get("assets", []) or []
    asset_urls = {
        asset["role"]: _asset_url(base_url, job_id, asset["path"])
        for asset in assets
        if asset.get("role") and asset.get("path")
    }
    summary = {
        "briefing_id": result.get("briefing_id"),
        "title": result.get("title"),
        "topic": result.get("topic"),
        "summary": result.get("summary"),
        "generated_at": result.get("generated_at"),
        "sections_count": len(result.get("sections") or []),
        "source_count": len(result.get("sources") or []),
        "validation": result.get("validation") or {"valid": True, "warnings": [], "errors": []},
        "asset_urls": asset_urls,
        "webui_manifest_path": f"/briefings/{job_id}/manifest",
        "webui_asset_base_path": f"/briefings/{job_id}/assets",
        "webui_preview_path": f"/briefings/{job_id}/player",
        "webui_standalone_html_path": f"/briefings/{job_id}",
    }
    published_bundle = result.get("published_bundle")
    if isinstance(published_bundle, dict):
        summary["published_bundle"] = {
            "provider": published_bundle.get("provider"),
            "published_at": published_bundle.get("published_at"),
            "webui_path": published_bundle.get("webui_path") or f"/briefings/{job_id}",
            "webui_player_path": published_bundle.get("webui_player_path") or f"/briefings/{job_id}/player",
            "webui_manifest_path": published_bundle.get("webui_manifest_path") or f"/briefings/{job_id}/manifest",
            "webui_asset_base_path": published_bundle.get("webui_asset_base_path") or f"/briefings/{job_id}/assets",
        }
    manifest_path = result.get("manifest_path")
    audio_path = result.get("audio_path")
    standalone_html_path = result.get("standalone_html_path")
    if manifest_path:
        summary["manifest_url"] = _asset_url(base_url, job_id, manifest_path)
    if audio_path:
        summary["audio_url"] = _asset_url(base_url, job_id, audio_path)
    if standalone_html_path:
        summary["standalone_html_url"] = _asset_url(base_url, job_id, standalone_html_path)
    return summary


def _raise_for_error_response(response: httpx.Response, default_message: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            raise RuntimeError(
                "Briefing renderer rejected the request. Set BRIEFING_RENDERER_SERVICE_TOKEN in ~/.hermes/.env if auth is enabled."
            ) from exc
        detail = None
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("error")
        raise RuntimeError(str(detail or default_message)) from exc


def check_briefing_requirements() -> bool:
    base_url = _resolve_renderer_base_url()
    timeout = min(_resolve_request_timeout_seconds(), 2.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(_absolute_url(base_url, "/health"))
        response.raise_for_status()
        payload = response.json()
        return payload.get("status") == "ok"
    except Exception:
        return False


def create_briefing_tool(args: dict[str, Any], **_kw) -> str:
    payload = _normalize_request_payload(args)
    validation_errors = _validate_payload(payload)
    if validation_errors:
        return tool_error(
            "Invalid briefing payload.",
            success=False,
            validation_errors=validation_errors,
        )

    base_url = _resolve_renderer_base_url()
    timeout = _resolve_request_timeout_seconds()
    wait_for_completion = bool(args.get("wait_for_completion", True))
    max_wait_seconds = _resolve_max_wait_seconds(args)
    poll_interval_seconds = _resolve_poll_interval_seconds(args)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            create_response = client.post(
                _absolute_url(base_url, "/v1/briefings"),
                json=payload,
                headers=_headers(),
            )
            _raise_for_error_response(create_response, "Briefing renderer failed to create the job.")
            accepted = create_response.json()

            job_id = str(accepted.get("job_id") or payload["job_id"])
            status_url = _absolute_url(base_url, accepted.get("status_url") or f"/v1/briefings/{job_id}")
            result_url = _absolute_url(base_url, accepted.get("result_url") or f"/v1/briefings/{job_id}/result")

            result = {
                "success": True,
                "status": accepted.get("status", "processing"),
                "job_id": job_id,
                "briefing_id": payload["briefing_id"],
                "title": payload["title"],
                "topic": payload["topic"],
                "renderer_base_url": base_url,
                "status_url": status_url,
                "result_url": result_url,
                "webui_preview_path": f"/briefings/{job_id}/player",
                "webui_standalone_html_path": f"/briefings/{job_id}",
            }

            if not wait_for_completion:
                return tool_result(result)

            deadline = time.monotonic() + max_wait_seconds
            while True:
                status_response = client.get(status_url, headers=_headers())
                _raise_for_error_response(status_response, "Briefing renderer job status lookup failed.")
                status_payload = status_response.json()
                result["status"] = status_payload.get("status", result["status"])
                if status_payload.get("validation") is not None:
                    result["validation"] = status_payload.get("validation")
                if status_payload.get("error"):
                    result["error"] = status_payload["error"]

                if result["status"] == "completed":
                    result_response = client.get(result_url, headers=_headers())
                    _raise_for_error_response(result_response, "Briefing renderer result lookup failed.")
                    result["result"] = _build_summary(result_response.json(), base_url)
                    return tool_result(result)

                if result["status"] == "failed":
                    return tool_result(result)

                if time.monotonic() >= deadline:
                    result["poll_after_seconds"] = poll_interval_seconds
                    return tool_result(result)

                if poll_interval_seconds > 0:
                    time.sleep(poll_interval_seconds)

    except RuntimeError as exc:
        return tool_error(str(exc), success=False, renderer_base_url=base_url)
    except httpx.HTTPError as exc:
        return tool_error(
            f"Briefing renderer request failed: {exc}",
            success=False,
            renderer_base_url=base_url,
        )


registry.register(
    name="create_briefing",
    toolset="briefing",
    schema=CREATE_BRIEFING_SCHEMA,
    handler=create_briefing_tool,
    check_fn=check_briefing_requirements,
    requires_env=["BRIEFING_RENDERER_SERVICE_TOKEN"],
    description="Render a structured multimedia briefing via the briefing renderer service.",
    emoji="🗞️",
)