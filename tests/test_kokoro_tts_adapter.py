from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).resolve().parents[1] / ".services" / "kokoro-tts" / "app" / "main.py"


def _load_module(monkeypatch):
    fake_kokoro = types.ModuleType("kokoro_onnx")
    fake_kokoro.Kokoro = object
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro)

    fake_soundfile = types.ModuleType("soundfile")
    fake_soundfile.write = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    module_name = "kokoro_tts_main_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_text_rewrites_english_decimals(monkeypatch):
    module = _load_module(monkeypatch)

    assert module._normalize_text("Pi is 3.14.", lang="en-us") == "Pi is 3 point 14."
    assert module._normalize_text("Try 0.5 mg and 12.75 ml.", lang="en-gb") == (
        "Try 0 point 5 mg and 12 point 75 ml."
    )


def test_normalize_text_leaves_non_decimals_and_non_english(monkeypatch):
    module = _load_module(monkeypatch)

    assert module._normalize_text("Hello. Next sentence.", lang="en-us") == "Hello. Next sentence."
    assert module._normalize_text("La valeur est 3.14.", lang="fr-fr") == "La valeur est 3.14."


def test_tts_normalizes_decimal_text_before_render(monkeypatch):
    module = _load_module(monkeypatch)
    captured: dict[str, object] = {}

    def fake_render(text: str, *, voice: str, lang: str, speed: float) -> bytes:
        captured["text"] = text
        captured["voice"] = voice
        captured["lang"] = lang
        captured["speed"] = speed
        return b"wav-bytes"

    monkeypatch.setattr(module, "_render_wav_bytes", fake_render)
    client = TestClient(module.app)

    response = client.post(
        "/tts",
        data={"text": "Value is 3.14 today.", "lang": "en-us", "voice": "af_sky", "speed": "1.0"},
    )

    assert response.status_code == 200
    assert response.content == b"wav-bytes"
    assert captured == {
        "text": "Value is 3 point 14 today.",
        "voice": "af_sky",
        "lang": "en-us",
        "speed": 1.0,
    }


def test_tts_keeps_non_english_decimal_text_raw(monkeypatch):
    module = _load_module(monkeypatch)
    captured: dict[str, object] = {}

    def fake_render(text: str, *, voice: str, lang: str, speed: float) -> bytes:
        captured["text"] = text
        return b"wav-bytes"

    monkeypatch.setattr(module, "_render_wav_bytes", fake_render)
    client = TestClient(module.app)

    response = client.post(
        "/tts",
        data={"text": "La valeur est 3.14.", "lang": "fr-fr"},
    )

    assert response.status_code == 200
    assert captured["text"] == "La valeur est 3.14."


def test_individual_pronunciation_update_persists_and_affects_tts(monkeypatch, tmp_path):
    monkeypatch.setenv("KOKORO_PRONUNCIATIONS_PATH", str(tmp_path / "pronunciations.json"))
    module = _load_module(monkeypatch)
    captured: dict[str, object] = {}

    def fake_render(text: str, *, voice: str, lang: str, speed: float) -> bytes:
        captured["text"] = text
        return b"wav-bytes"

    monkeypatch.setattr(module, "_render_wav_bytes", fake_render)
    client = TestClient(module.app)

    update_response = client.post(
        "/pronunciations",
        json={"phrase": "Hermes", "pronunciation": "Hur-meez"},
    )

    assert update_response.status_code == 200
    assert (tmp_path / "pronunciations.json").read_text() == '{\n  "Hermes": "Hur-meez"\n}\n'

    tts_response = client.post("/tts", data={"text": "Hermes is online.", "lang": "en-us"})

    assert tts_response.status_code == 200
    assert captured["text"] == "Hur-meez is online."


def test_bulk_pronunciation_update_persists_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("KOKORO_PRONUNCIATIONS_PATH", str(tmp_path / "pronunciations.json"))
    module = _load_module(monkeypatch)
    client = TestClient(module.app)

    response = client.post(
        "/pronunciations/bulk",
        json={
            "entries": [
                {"phrase": "Hermes", "pronunciation": "Hur-meez"},
                {"phrase": "Kokoro", "pronunciation": "Koh-koh-roh"},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert module._load_pronunciations() == {
        "Hermes": "Hur-meez",
        "Kokoro": "Koh-koh-roh",
    }


def test_edit_pronunciation_can_rename_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv("KOKORO_PRONUNCIATIONS_PATH", str(tmp_path / "pronunciations.json"))
    module = _load_module(monkeypatch)
    module._save_pronunciations({"Hermes": "Hur-meez"})
    client = TestClient(module.app)

    response = client.patch(
        "/pronunciations/Hermes",
        json={"phrase": "Hermes Agent", "pronunciation": "Hur-meez Ay-jent"},
    )

    assert response.status_code == 200
    assert module._load_pronunciations() == {"Hermes Agent": "Hur-meez Ay-jent"}


def test_delete_pronunciation_removes_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("KOKORO_PRONUNCIATIONS_PATH", str(tmp_path / "pronunciations.json"))
    module = _load_module(monkeypatch)
    module._save_pronunciations({"Hermes": "Hur-meez"})
    client = TestClient(module.app)

    response = client.delete("/pronunciations/Hermes")

    assert response.status_code == 200
    assert module._load_pronunciations() == {}