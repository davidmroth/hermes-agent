#!/usr/bin/env python3
"""Create rendered multimedia briefings via the briefing renderer service."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from hermes_cli.config import load_config
from hermes_constants import is_container
from tools.registry import registry, tool_error, tool_result


_SLUG_RE = re.compile(r"[^a-z0-9]+")


class SourceRef(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    url: str = Field(min_length=1)
    accessed_at: str | None = None
    excerpt: str | None = None


class CitationRef(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    note: str | None = None


class MetricCard(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    trend: str | None = None


class IllustrationBlock(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    kind: str = Field(default="illustration")


class SectionInput(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    narration: str = Field(min_length=1)
    body: list[str] = Field(default_factory=list)
    metrics: list[MetricCard] = Field(default_factory=list)
    illustrations: list[IllustrationBlock] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)

    @field_validator("body", mode="before")
    @classmethod
    def _coerce_body(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value


class BriefingRequest(BaseModel):
    briefing_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    summary: str | None = None
    locale: str = Field(default="en-US", min_length=2)
    generated_by: str = Field(default="hermes", min_length=1)
    sections: list[SectionInput] = Field(min_length=1)
    sources: list[SourceRef] = Field(default_factory=list)

    @field_validator("sections", mode="before")
    @classmethod
    def _coerce_sections(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return [value]
        return value

    @field_validator("sources", mode="before")
    @classmethod
    def _coerce_sources(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return value


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
            "briefing_id": {
                "type": "string",
                "description": "Optional stable ID for the briefing. If omitted, Hermes generates one from the title and timestamp."
            },
            "title": {
                "type": "string",
                "description": "Short human-readable title for the briefing."
            },
            "topic": {
                "type": "string",
                "description": "The main topic or question the briefing addresses."
            },
            "summary": {
                "type": "string",
                "description": "Optional short executive summary shown above the rendered briefing."
            },
            "locale": {
                "type": "string",
                "description": "Locale for narration and display, for example en-US. Defaults to en-US."
            },
            "generated_by": {
                "type": "string",
                "description": "Optional generator label. Defaults to hermes."
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
                            "description": "Spoken-language narration for this section. Write complete sentences, not fragments."
                        },
                        "body": {
                            "type": "array",
                            "description": "Optional supporting paragraphs or bullet-like lines.",
                            "items": {"type": "string"}
                        },
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "value": {"type": "string"},
                                    "trend": {"type": "string"}
                                },
                                "required": ["id", "label", "value"]
                            }
                        },
                        "illustrations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "title": {"type": "string"},
                                    "caption": {"type": "string"},
                                    "kind": {
                                        "type": "string",
                                        "description": "One of illustration, map, or chart."
                                    }
                                },
                                "required": ["id", "title", "caption"]
                            }
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
                                        "description": "Must match a source id from the top-level sources list."
                                    },
                                    "note": {"type": "string"}
                                },
                                "required": ["id", "label", "source_id"]
                            }
                        }
                    },
                    "required": ["id", "title", "narration"]
                }
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
                        "excerpt": {"type": "string"}
                    },
                    "required": ["id", "title", "publisher", "url"]
                }
            },
            "wait_for_completion": {
                "type": "boolean",
                "description": "Wait for the renderer job to finish before returning. Defaults to true."
            },
            "max_wait_seconds": {
                "type": "number",
                "description": "Optional maximum time to wait when wait_for_completion is true. Defaults to briefing.max_wait_seconds from config."
            },
            "poll_interval_seconds": {
                "type": "number",
                "description": "Optional poll interval while waiting for completion. Defaults to briefing.poll_interval_seconds from config."
            }
        },
        "required": ["title", "topic", "sections"]
    }
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
    cfg = _briefing_config()
    raw = cfg.get("request_timeout_seconds", 20)
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


def _headers() -> dict[str, str]:
    token = _resolve_service_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _absolute_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _asset_url(base_url: str, job_id: str, asset_path: str) -> str:
    normalized = asset_path.lstrip("/")
    return _absolute_url(base_url, f"/v1/briefings/{job_id}/assets/{normalized}")


def _normalize_request_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in args.items()
        if key not in {"wait_for_completion", "max_wait_seconds", "poll_interval_seconds"}
    }
    if not payload.get("briefing_id"):
        payload["briefing_id"] = _auto_briefing_id(str(payload.get("title") or "briefing"))
    if not payload.get("generated_by"):
        payload["generated_by"] = "hermes"
    if not payload.get("locale"):
        payload["locale"] = "en-US"
    return payload


def _build_summary(result: dict[str, Any], base_url: str) -> dict[str, Any]:
    job_id = result["job_id"]
    assets = result.get("assets", [])
    asset_urls = {
        asset["role"]: _asset_url(base_url, job_id, asset["path"])
        for asset in assets
        if asset.get("role") and asset.get("path")
    }
    return {
        "briefing_id": result.get("briefing_id"),
        "title": result.get("title"),
        "topic": result.get("topic"),
        "summary": result.get("summary"),
        "generated_at": result.get("generated_at"),
        "sections_count": len(result.get("sections") or []),
        "source_count": len(result.get("sources") or []),
        "validation": result.get("validation") or {"valid": True, "warnings": [], "errors": []},
        "manifest_url": _asset_url(base_url, job_id, result.get("manifest_path") or "briefing.json"),
        "audio_url": _asset_url(base_url, job_id, result["audio_path"]),
        "standalone_html_url": _asset_url(base_url, job_id, result["standalone_html_path"]),
        "asset_urls": asset_urls,
        "webui_preview_path": f"/briefings/{job_id}",
    }


def _raise_for_error_response(response: httpx.Response, default_message: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            raise RuntimeError(
                "Briefing renderer rejected the request. Set BRIEFING_RENDERER_SERVICE_TOKEN in ~/.hermes/.env if auth is enabled."
            ) from exc
        try:
            payload = exc.response.json()
        except ValueError:
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise RuntimeError(detail or default_message) from exc


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
    try:
        request = BriefingRequest.model_validate(payload)
    except ValidationError as exc:
        return tool_error(
            "Invalid briefing payload.",
            success=False,
            validation_errors=exc.errors(),
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
                json=request.model_dump(mode="json"),
                headers=_headers(),
            )
            _raise_for_error_response(create_response, "Briefing renderer failed to create the job.")
            accepted = create_response.json()

            result = {
                "success": True,
                "status": accepted.get("status", "processing"),
                "job_id": accepted["job_id"],
                "briefing_id": request.briefing_id,
                "title": request.title,
                "topic": request.topic,
                "renderer_base_url": base_url,
                "status_url": _absolute_url(base_url, accepted["status_url"]),
                "result_url": _absolute_url(base_url, accepted["result_url"]),
                "webui_preview_path": f"/briefings/{accepted['job_id']}",
            }

            if not wait_for_completion:
                return tool_result(result)

            deadline = time.monotonic() + max_wait_seconds
            while True:
                status_response = client.get(result["status_url"], headers=_headers())
                _raise_for_error_response(status_response, "Briefing renderer job status lookup failed.")
                status_payload = status_response.json()
                result["status"] = status_payload.get("status", result["status"])
                result["validation"] = status_payload.get("validation")
                if status_payload.get("error"):
                    result["error"] = status_payload["error"]

                if result["status"] == "completed":
                    result_response = client.get(result["result_url"], headers=_headers())
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