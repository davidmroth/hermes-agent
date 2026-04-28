"""Tests for warn_deprecated_cwd_env_vars() migration warning."""

import pytest


def _write_env_file(tmp_path, content: str) -> None:
    env_path = tmp_path / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(content, encoding="utf-8")


class TestDeprecatedCwdWarning:
    """Warn when MESSAGING_CWD or TERMINAL_CWD is set in .env."""

    def test_messaging_cwd_triggers_warning(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        _write_env_file(tmp_path, "MESSAGING_CWD=/some/path\n")

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        warn_deprecated_cwd_env_vars(config={})

        captured = capsys.readouterr()
        assert "MESSAGING_CWD" in captured.err
        assert "deprecated" in captured.err.lower()
        assert "config.yaml" in captured.err

    def test_terminal_cwd_triggers_warning_when_config_placeholder(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("MESSAGING_CWD", raising=False)
        _write_env_file(tmp_path, "TERMINAL_CWD=/project\n")

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        # config has placeholder cwd → TERMINAL_CWD likely from .env
        warn_deprecated_cwd_env_vars(config={"terminal": {"cwd": "."}})

        captured = capsys.readouterr()
        assert "TERMINAL_CWD" in captured.err
        assert "deprecated" in captured.err.lower()

    def test_no_warning_when_config_has_explicit_cwd(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("MESSAGING_CWD", raising=False)
        _write_env_file(tmp_path, "TERMINAL_CWD=/project\n")

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        # config has explicit cwd → TERMINAL_CWD could be from config bridge
        warn_deprecated_cwd_env_vars(config={"terminal": {"cwd": "/project"}})

        captured = capsys.readouterr()
        assert "TERMINAL_CWD" not in captured.err

    def test_no_warning_when_env_clean(self, monkeypatch, capsys, tmp_path):
        monkeypatch.delenv("MESSAGING_CWD", raising=False)
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _write_env_file(tmp_path, "")

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        warn_deprecated_cwd_env_vars(config={})

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_both_deprecated_vars_warn(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _write_env_file(
            tmp_path,
            "MESSAGING_CWD=/msg/path\nTERMINAL_CWD=/term/path\n",
        )

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        warn_deprecated_cwd_env_vars(config={})

        captured = capsys.readouterr()
        assert "MESSAGING_CWD" in captured.err
        assert "TERMINAL_CWD" in captured.err

    def test_terminal_cwd_env_does_not_warn_when_not_present_in_hermes_env_file(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("TERMINAL_CWD", "/workspace")
        monkeypatch.delenv("MESSAGING_CWD", raising=False)

        (tmp_path / ".env").write_text("OPENAI_API_KEY=***\n", encoding="utf-8")

        from hermes_cli.config import warn_deprecated_cwd_env_vars
        warn_deprecated_cwd_env_vars(config={"terminal": {"cwd": "."}})

        captured = capsys.readouterr()
        assert "TERMINAL_CWD" not in captured.err
