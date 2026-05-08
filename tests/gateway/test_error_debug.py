"""Tests for gateway.error_debug diagnostic logging helpers."""

import logging

from gateway.error_debug import build_exception_diagnostics, log_exception_diagnostics


def test_build_exception_diagnostics_includes_context_and_traceback():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        diag = build_exception_diagnostics(
            exc,
            context="unit-test",
            fields={"chat_id": "chat-123"},
        )

    assert "Gateway exception diagnostics (unit-test)" in diag
    assert "exception=ValueError: boom" in diag
    assert "chat_id='chat-123'" in diag
    assert "hermes_home=" in diag
    assert "sys_path_head=" in diag
    assert "traceback:" in diag


def test_build_exception_diagnostics_includes_missing_module_details():
    exc = ModuleNotFoundError("No module named 'missingpkg'")
    exc.name = "missingpkg"

    diag = build_exception_diagnostics(exc, context="imports")

    assert "missing_module=missingpkg" in diag
    assert "missing_module_spec=" in diag
    assert "missing_package_version=" in diag


def test_log_exception_diagnostics_emits_error_record(caplog):
    logger = logging.getLogger("tests.gateway.error_debug")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        try:
            raise RuntimeError("kapow")
        except RuntimeError as exc:
            log_exception_diagnostics(logger, exc, context="logging-test")

    assert any(
        "Gateway exception diagnostics (logging-test)" in record.getMessage()
        for record in caplog.records
    )