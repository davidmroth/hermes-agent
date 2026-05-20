"""Tests for the bundled briefing service plugin."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx


_REAL_HTTPX_CLIENT = httpx.Client


def _load_plugin_module():
    repo_root = Path(__file__).resolve().parents[2]
    plugin_path = repo_root / ".services" / "briefing-service" / "plugin" / "__init__.py"
    spec = importlib.util.spec_from_file_location("briefing_service_plugin_under_test", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _client_factory(transport: httpx.MockTransport):
    def _factory(*args, **kwargs):
        return _REAL_HTTPX_CLIENT(transport=transport, **kwargs)

    return _factory


def test_create_briefing_returns_briefing_url_without_waiting_by_default(monkeypatch):
    plugin = _load_plugin_module()
    captured_request = {}

    accepted = {
        "job_id": "job-123",
        "status": "processing",
        "status_url": "/v1/briefings/job-123",
        "result_url": "/v1/briefings/job-123/result",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/briefings":
            captured_request["headers"] = dict(request.headers)
            captured_request["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(202, json=accepted)
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        plugin,
        "load_config",
        lambda: {
            "briefing": {
                "renderer_base_url": "http://renderer.test",
                "request_timeout_seconds": 5,
                "poll_interval_seconds": 0,
                "max_wait_seconds": 15,
            }
        },
    )
    monkeypatch.setattr(plugin.httpx, "Client", _client_factory(httpx.MockTransport(handler)))
    monkeypatch.setenv("BRIEFING_RENDERER_SERVICE_TOKEN", "token-123")
    monkeypatch.setenv("WEBCHAT_PUBLIC_BASE_URL", "https://briefings.example.com")

    result = json.loads(
        plugin.create_briefing_tool(
            {
                "title": "Shipping Risk Briefing",
                "topic": "North Atlantic shipping disruption risk",
                "summary": "A concise risk snapshot for operators.",
                "sections": [
                    {
                        "id": "risk",
                        "title": "Immediate Risk",
                        "narration": "Ports are congested and delays are extending into next week.",
                    }
                ],
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "processing"
    assert result["job_id"] == "job-123"
    assert result["webui_standalone_html_url"] == "https://briefings.example.com/briefings/job-123"
    assert result["briefing_path"] == "/briefings/job-123"
    assert result["briefing_url"] == "https://briefings.example.com/briefings/job-123"
    assert "webui_preview_url" not in result
    assert "webui_asset_base_url" not in result
    assert "Open briefing_url" in result["message"]
    assert captured_request["headers"]["authorization"] == "Bearer token-123"
    assert captured_request["json"]["briefing_id"].startswith("shipping-risk-briefing-")


def test_create_briefing_waits_for_completion_when_requested(monkeypatch):
    plugin = _load_plugin_module()

    accepted = {
        "job_id": "job-123",
        "status": "processing",
        "status_url": "/v1/briefings/job-123",
        "result_url": "/v1/briefings/job-123/result",
    }
    completed_status = {
        "job_id": "job-123",
        "briefing_id": "shipping-risk-20260502-120000",
        "status": "completed",
        "created_at": "2026-05-02T12:00:00+00:00",
        "completed_at": "2026-05-02T12:00:01+00:00",
        "manifest_path": "briefing.json",
        "asset_count": 4,
        "validation": {"valid": True, "warnings": [], "errors": []},
    }
    result_payload = {
        "schema_version": "briefing-renderer/v1",
        "render_mode": "synthetic-v1",
        "job_id": "job-123",
        "briefing_id": "shipping-risk-20260502-120000",
        "title": "Shipping Risk Briefing",
        "topic": "North Atlantic shipping disruption risk",
        "summary": "A concise risk snapshot for operators.",
        "generated_at": "2026-05-02T12:00:01+00:00",
        "locale": "en-US",
        "generated_by": "hermes",
        "manifest_path": "briefing.json",
        "asset_base_path": "/v1/briefings/job-123/assets",
        "standalone_html_path": "briefing.html",
        "audio_path": "narration.wav",
        "sections": [{"id": "risk", "title": "Risk", "narration": "Ports are congested.", "body": [], "metrics": [], "illustrations": [], "citations": [], "sentences": [], "start": 0.0, "end": 4.0}],
        "sources": [{"id": "s1", "title": "Port update", "publisher": "Lloyd's List", "url": "https://example.com/port-update"}],
        "timeline_cues": [],
        "assets": [
            {"role": "audio", "path": "narration.wav", "content_type": "audio/wav", "size_bytes": 12, "sha256": "a", "cache_control": "private, max-age=300"},
            {"role": "standalone_html", "path": "briefing.html", "content_type": "text/html", "size_bytes": 12, "sha256": "b", "cache_control": "private, max-age=300"},
        ],
        "validation": {"valid": True, "warnings": [], "errors": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/briefings/job-123":
            return httpx.Response(200, json=completed_status)
        if request.method == "GET" and request.url.path == "/v1/briefings/job-123/result":
            return httpx.Response(200, json=result_payload)
        if request.method == "POST" and request.url.path == "/v1/briefings":
            return httpx.Response(202, json=accepted)
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        plugin,
        "load_config",
        lambda: {
            "briefing": {
                "renderer_base_url": "http://renderer.test",
                "request_timeout_seconds": 5,
                "poll_interval_seconds": 0,
                "max_wait_seconds": 15,
            }
        },
    )
    monkeypatch.setattr(plugin.httpx, "Client", _client_factory(httpx.MockTransport(handler)))
    monkeypatch.setenv("WEBCHAT_PUBLIC_BASE_URL", "https://briefings.example.com")

    result = json.loads(
        plugin.create_briefing_tool(
            {
                "title": "Shipping Risk Briefing",
                "topic": "North Atlantic shipping disruption risk",
                "sections": [
                    {
                        "id": "risk",
                        "title": "Immediate Risk",
                        "narration": "Ports are congested and delays are extending into next week.",
                    }
                ],
                "wait_for_completion": True,
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["briefing_url"] == "https://briefings.example.com/briefings/job-123"
    assert result["result"]["webui_standalone_html_url"] == "https://briefings.example.com/briefings/job-123"
    assert "webui_preview_url" not in result["result"]
    assert "asset_urls" not in result["result"]
    assert "audio_url" not in result["result"]
    assert "manifest_url" not in result["result"]