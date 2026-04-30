import logging

from gateway.error_debug import build_exception_diagnostics, log_exception_diagnostics


def _raise_missing_module():
    raise ModuleNotFoundError("No module named 'fire'", name="fire")


def test_build_exception_diagnostics_includes_missing_module_context():
    try:
        _raise_missing_module()
    except ModuleNotFoundError as exc:
        diagnostics = build_exception_diagnostics(
            exc,
            context="gateway_agent_turn",
            fields={"platform": "webchat", "chat_id": "conv-1"},
        )

    assert "Gateway exception diagnostics (gateway_agent_turn)" in diagnostics
    assert "exception=ModuleNotFoundError: No module named 'fire'" in diagnostics
    assert "missing_module=fire" in diagnostics
    assert "missing_module_spec=" in diagnostics
    assert "missing_package_version=" in diagnostics
    assert "platform='webchat'" in diagnostics
    assert "chat_id='conv-1'" in diagnostics
    assert "traceback:" in diagnostics
    assert "_raise_missing_module" in diagnostics


def test_log_exception_diagnostics_writes_single_error_record(caplog):
    logger = logging.getLogger("tests.gateway.error_debug")

    try:
        _raise_missing_module()
    except ModuleNotFoundError as exc:
        with caplog.at_level(logging.ERROR, logger="tests.gateway.error_debug"):
            log_exception_diagnostics(logger, exc, context="platform_message_handler")

    assert "Gateway exception diagnostics (platform_message_handler)" in caplog.text
    assert "missing_module=fire" in caplog.text
