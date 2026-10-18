"""Prueba de `python -m discoclaro` (T2.8): no toca el disco, solo `--version`."""

import runpy

import pytest


def test_running_as_a_module_calls_main_and_uses_its_exit_code(monkeypatch):
    monkeypatch.setattr("sys.argv", ["discoclaro", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("discoclaro", run_name="__main__")
    assert exc.value.code == 0
