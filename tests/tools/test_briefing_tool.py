"""Tests for tools/briefing_tool.py."""

from __future__ import annotations

import json

import httpx

from tools.briefing_tool import check_briefing_requirements, create_briefing_tool


_REAL_HTTPX_CLIENT = httpx.Client


def _client_factory(transport: httpx.MockTransport):
    def _factory(*args, **kwargs):
        return _REAL_HTTPX_CLIENT(transport=transport, **kwargs)

    return _factory


def test_create_briefing_waits_for_completion_and_returns_preview(monkeypatch):
    captured_request = {}

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
            captured_request["headers"] = dict(request.headers)
            captured_request["json"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(202, json=accepted)
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        "tools.briefing_tool.load_config",
        lambda: {
            "briefing": {
                "renderer_base_url": "http://renderer.test",
                "request_timeout_seconds": 5,
                "poll_interval_seconds": 0,
                "max_wait_seconds": 15,
            }
        },
    )
    monkeypatch.setattr("tools.briefing_tool.httpx.Client", _client_factory(httpx.MockTransport(handler)))
    monkeypatch.setenv("BRIEFING_RENDERER_SERVICE_TOKEN", "token-123")

    result = json.loads(
        create_briefing_tool(
            {
                "title": "Shipping Risk Briefing",
                "topic": "North Atlantic shipping disruption risk",
                "summary": "A concise risk snapshot for operators.",
                "sections": [
                    {
                        "id": "risk",
                        "title": "Immediate Risk",
                        "narration": "Ports are congested and delays are extending into next week.",
                        "body": ["Congestion is highest in northern hubs."],
                        "metrics": [{"id": "delay", "label": "Median delay", "value": "36h", "trend": "+8h WoW"}],
                        "citations": [{"id": "c1", "label": "Port update", "source_id": "s1"}],
                    }
                ],
                "sources": [
                    {
                        "id": "s1",
                        "title": "Port update",
                        "publisher": "Lloyd's List",
                        "url": "https://example.com/port-update",
                    }
                ],
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["job_id"] == "job-123"
    assert result["result"]["webui_preview_path"] == "/briefings/job-123"
    assert result["result"]["audio_url"] == "http://renderer.test/v1/briefings/job-123/assets/narration.wav"
    assert captured_request["headers"]["authorization"] == "Bearer token-123"
    assert captured_request["json"]["briefing_id"].startswith("shipping-risk-briefing-")
    assert captured_request["json"]["generated_by"] == "hermes"


def test_create_briefing_returns_clear_auth_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/briefings":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        if request.method == "GET" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        "tools.briefing_tool.load_config",
        lambda: {"briefing": {"renderer_base_url": "http://renderer.test", "request_timeout_seconds": 5}},
    )
    monkeypatch.setattr("tools.briefing_tool.httpx.Client", _client_factory(httpx.MockTransport(handler)))
    monkeypatch.delenv("BRIEFING_RENDERER_SERVICE_TOKEN", raising=False)

    result = json.loads(
        create_briefing_tool(
            {
                "title": "Risk Briefing",
                "topic": "Logistics",
                "sections": [{"id": "overview", "title": "Overview", "narration": "Delays are worsening."}],
            }
        )
    )

    assert result["success"] is False
    assert "BRIEFING_RENDERER_SERVICE_TOKEN" in result["error"]


def test_check_briefing_requirements_uses_health_probe(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        "tools.briefing_tool.load_config",
        lambda: {"briefing": {"renderer_base_url": "http://renderer.test", "request_timeout_seconds": 5}},
    )
    monkeypatch.setattr("tools.briefing_tool.httpx.Client", _client_factory(httpx.MockTransport(handler)))

    assert check_briefing_requirements() is True