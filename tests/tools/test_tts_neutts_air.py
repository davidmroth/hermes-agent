"""Tests for the NeuTTS Air sidecar provider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def test_resolve_neutts_air_base_url_trims_tts_suffix(monkeypatch):
    from tools.tts_tool import _resolve_neutts_air_base_url

    monkeypatch.delenv("NEUTTS_AIR_BASE_URL", raising=False)

    cfg = {"neutts-air": {"base_url": "http://neutts-air:8000/tts"}}

    assert _resolve_neutts_air_base_url(cfg) == "http://neutts-air:8000"


def test_generate_neutts_air_posts_to_sidecar_and_writes_wav(tmp_path):
    from tools.tts_tool import _generate_neutts_air

    output_path = str(tmp_path / "speech.wav")
    response = Mock(status_code=200, content=b"RIFFfakewav")

    with patch("requests.post", return_value=response) as mock_post:
        result = _generate_neutts_air(
            "Hello",
            output_path,
            {"neutts-air": {"base_url": "http://neutts-air:8000"}},
        )

    assert result == output_path
    assert Path(output_path).read_bytes() == b"RIFFfakewav"
    assert mock_post.call_args.args[0] == "http://neutts-air:8000/tts"


def test_check_tts_requirements_uses_neutts_air_health(monkeypatch):
    from tools.tts_tool import check_tts_requirements

    monkeypatch.setattr(
        "tools.tts_tool._load_tts_config",
        lambda: {"provider": "neutts-air", "neutts-air": {"base_url": "http://neutts-air:8000"}},
    )
    monkeypatch.setattr("tools.tts_tool._check_neutts_air_available", lambda cfg: True)

    assert check_tts_requirements() is True


def test_text_to_speech_tool_dispatches_to_neutts_air(tmp_path, monkeypatch):
    from tools.tts_tool import text_to_speech_tool

    output_path = str(tmp_path / "speech.wav")

    def fake_generate(text, out, cfg):
        Path(out).write_bytes(b"RIFFfakewav")
        return out

    monkeypatch.setattr(
        "tools.tts_tool._load_tts_config",
        lambda: {"provider": "neutts-air", "neutts-air": {"base_url": "http://neutts-air:8000"}},
    )
    monkeypatch.setattr("tools.tts_tool._generate_neutts_air", fake_generate)

    result = json.loads(text_to_speech_tool(text="Hello", output_path=output_path))

    assert result["success"] is True
    assert result["provider"] == "neutts-air"
    assert Path(result["file_path"]).exists()


def test_generate_neutts_air_surfaces_http_error(tmp_path):
    from tools.tts_tool import _generate_neutts_air

    response = Mock(status_code=401, text="nope")
    response.json.return_value = {"detail": "auth failed"}

    with patch("requests.post", return_value=response):
        with pytest.raises(RuntimeError, match=r"NeuTTS Air sidecar error \(HTTP 401\): auth failed"):
            _generate_neutts_air(
                "Hello",
                str(tmp_path / "speech.wav"),
                {"neutts-air": {"base_url": "http://neutts-air:8000"}},
            )