"""CLI surface (PRD 9.3, step 1 acceptance: `anybrain --help` lists commands)."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from anybrain import cli

P0_COMMANDS = ["init", "update", "doctor", "index", "neighbors", "lint", "anonymize", "feedback", "stats", "eval", "trace", "migrate"]
LATER_COMMANDS = ["content", "transcribe"]


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in P0_COMMANDS + LATER_COMMANDS:
        assert name in out


def test_no_command_prints_help_and_exits_2(capsys):
    assert cli.main([]) == 2
    assert "usage: anybrain" in capsys.readouterr().out


@pytest.mark.parametrize("name", LATER_COMMANDS)
def test_later_phase_commands_say_not_built(name, capsys):
    assert cli.main([name]) == 2
    assert "not built yet" in capsys.readouterr().err


def test_missing_vault_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.delenv("ANYBRAIN_VAULT", raising=False)
    assert cli.main(["index"]) == 2
    assert "ANYBRAIN_VAULT" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("anybrain") is None, reason="console script not installed")
def test_console_script_is_installed():
    result = subprocess.run(["anybrain", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "lint" in result.stdout


def test_python_version_is_supported():
    assert sys.version_info >= (3, 11)
