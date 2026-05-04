"""Regression tests for TTS provider setup and schema surfaces."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


def test_tts_provider_setup_choices_include_neutts_air(monkeypatch):
    from hermes_cli.setup import _setup_tts_provider

    captured = {}

    def fake_choice(question, choices, default=0):
        captured["question"] = question
        captured["choices"] = choices
        return len(choices) - 1

    monkeypatch.setattr("hermes_cli.setup.prompt_choice", fake_choice)
    monkeypatch.setattr("hermes_cli.setup.prompt_yes_no", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        "hermes_cli.setup.get_nous_subscription_features",
        lambda config: SimpleNamespace(nous_auth_present=False),
    )
    monkeypatch.setattr("hermes_cli.setup.managed_nous_tools_enabled", lambda: False)

    _setup_tts_provider({"tts": {"provider": "edge"}})

    assert captured["question"] == "Select TTS provider:"
    assert any("NeuTTS Air" in choice for choice in captured["choices"])


def test_web_server_tts_schema_lists_neutts_air():
    web_server_path = Path(__file__).resolve().parents[2] / "hermes_cli" / "web_server.py"
    module = ast.parse(web_server_path.read_text(encoding="utf-8"))

    overrides = None
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SCHEMA_OVERRIDES":
                    overrides = ast.literal_eval(node.value)
                    break
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "_SCHEMA_OVERRIDES":
                overrides = ast.literal_eval(node.value)
        if overrides is not None:
            break

    assert overrides is not None
    assert "neutts-air" in overrides["tts.provider"]["options"]


def test_tools_catalog_lists_neutts_air():
    from hermes_cli.tools_config import TOOL_CATEGORIES

    providers = TOOL_CATEGORIES["tts"]["providers"]
    assert any(provider.get("tts_provider") == "neutts-air" for provider in providers)


def test_tts_label_maps_neutts_air():
    from hermes_cli.nous_subscription import _tts_label

    assert _tts_label("neutts-air") == "NeuTTS Air"