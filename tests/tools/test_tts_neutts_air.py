"""Tests for the NeuTTS Air sidecar provider in tools/tts_tool.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestGenerateNeuTtsAir:
    def test_posts_text_to_sidecar_and_writes_wav(self, tmp_path):
        from tools.tts_tool import _generate_neutts_air

        response = MagicMock(status_code=200, content=b"RIFFfakewav", text="")
        response.json.side_effect = ValueError("not-json")

        with patch("requests.post", return_value=response) as mock_post:
            output_path = str(tmp_path / "sample.wav")
            result = _generate_neutts_air(
                "Hello from Hermes",
                output_path,
                {"neutts-air": {"base_url": "http://neutts-air:8000"}},
            )

        assert result == output_path
        assert Path(output_path).read_bytes() == b"RIFFfakewav"
        assert mock_post.call_args.args[0] == "http://neutts-air:8000/tts"
        assert mock_post.call_args.kwargs["data"] == {"text": "Hello from Hermes"}
        assert mock_post.call_args.kwargs["files"] is None


class TestDispatcherNeuTtsAir:
    def test_text_to_speech_tool_returns_neutts_air_result(self, monkeypatch, tmp_path):
        from tools.tts_tool import text_to_speech_tool

        def fake_generate(text, output_path, tts_config):
            Path(output_path).write_bytes(b"RIFFgenerated")
            return output_path

        monkeypatch.setattr(
            "tools.tts_tool._load_tts_config",
            lambda: {"provider": "neutts-air", "neutts-air": {"base_url": "http://neutts-air:8000"}},
        )
        monkeypatch.setattr("tools.tts_tool._generate_neutts_air", fake_generate)

        result = json.loads(
            text_to_speech_tool("Speak", output_path=str(tmp_path / "reply.wav"))
        )

        assert result["success"] is True
        assert result["provider"] == "neutts-air"
        assert result["file_path"].endswith("reply.wav")


class TestCheckTtsRequirementsNeuTtsAir:
    def test_selected_sidecar_provider_uses_health_probe(self, monkeypatch):
        from tools.tts_tool import check_tts_requirements

        monkeypatch.setattr(
            "tools.tts_tool._load_tts_config",
            lambda: {"provider": "neutts-air", "neutts-air": {"base_url": "http://neutts-air:8000"}},
        )

        with patch("requests.get", return_value=MagicMock(status_code=200)) as mock_get:
            assert check_tts_requirements() is True

        assert mock_get.call_args.args[0] == "http://neutts-air:8000/health"

    def test_unhealthy_sidecar_returns_false(self, monkeypatch):
        from tools.tts_tool import check_tts_requirements

        monkeypatch.setattr(
            "tools.tts_tool._load_tts_config",
            lambda: {"provider": "neutts-air", "neutts-air": {"base_url": "http://neutts-air:8000"}},
        )

        with patch("requests.get", side_effect=RuntimeError("down")):
            assert check_tts_requirements() is False