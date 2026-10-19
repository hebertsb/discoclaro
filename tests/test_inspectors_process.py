"""Pruebas del corredor de comandos compartido por los inspectores (T2C.1):
`run` siempre se reemplaza, nunca se ejecuta nada de verdad."""

import subprocess
import types

from discoclaro.adapters.inspectors._process import resolve_executable, run_command


def fake_run(stdout="", returncode=0, raises=None):
    def run(command, **kwargs):
        if raises is not None:
            raise raises
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert "shell" not in kwargs
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)

    return run


def test_run_command_returns_the_output_on_success():
    assert run_command(["x"], run=fake_run(stdout="hello")) == "hello"


def test_run_command_returns_none_on_a_nonzero_exit_code():
    assert run_command(["x"], run=fake_run(stdout="oops", returncode=1)) is None


def test_run_command_returns_none_when_the_executable_is_missing():
    assert run_command(["x"], run=fake_run(raises=FileNotFoundError())) is None


def test_run_command_returns_none_on_a_timeout():
    raises = subprocess.TimeoutExpired(cmd=["x"], timeout=15.0)
    assert run_command(["x"], run=fake_run(raises=raises)) is None


def test_run_command_returns_none_when_the_output_is_too_big():
    huge = "x" * 1_000_001
    assert run_command(["x"], run=fake_run(stdout=huge)) is None


def test_run_command_accepts_output_right_at_the_limit():
    exactly = "x" * 1_000_000
    assert run_command(["x"], run=fake_run(stdout=exactly)) == exactly


def test_resolve_executable_returns_the_full_path():
    assert resolve_executable("docker", which=lambda name: f"C:\\{name}.exe") == (
        "C:\\docker.exe"
    )


def test_resolve_executable_returns_none_when_not_found():
    assert resolve_executable("docker", which=lambda name: None) is None


def test_the_real_adapters_are_the_defaults_without_calling_them():
    import inspect
    import shutil

    assert inspect.signature(run_command).parameters["run"].default is subprocess.run
    assert (
        inspect.signature(resolve_executable).parameters["which"].default
        is shutil.which
    )


def test_run_command_never_uses_a_shell():
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(stdout="", returncode=0)

    run_command(["x"], run=run)
    assert "shell" not in calls[0]
