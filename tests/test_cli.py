"""Pruebas del esqueleto de la línea de comandos."""

import importlib
import tomllib
from pathlib import Path

import pytest

from discoclaro import __version__
from discoclaro.interfaces.cli import COMMANDS, main


def test_version_prints_name_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"discoclaro {__version__}"


@pytest.mark.parametrize("command", COMMANDS)
def test_empty_subcommands_exit_cleanly(command, capsys):
    assert main([command]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("argv", [[], ["no-existe"]])
def test_missing_or_unknown_subcommand_is_an_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    capsys.readouterr()


def test_console_script_points_to_main():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    module_name, _, attribute = data["project"]["scripts"]["discoclaro"].partition(":")
    assert getattr(importlib.import_module(module_name), attribute) is main
