"""Pruebas del inspector de npm (T2C.1): `resolve`/`run`/`measure` siempre se
reemplazan, nunca se ejecuta ni se mide nada de verdad."""

from discoclaro.adapters.inspectors import npm as npm_inspector
from discoclaro.domain.insights import InsightItem


def fake(resolved="C:\\npm.cmd", output="D:\\.npm-cache\n"):
    return (lambda name: resolved), (lambda command: output)


def test_inspect_measures_the_real_cache_directory():
    resolve, run = fake()
    insight = npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 4096)
    assert insight.program == "npm"
    assert insight.items == (InsightItem(name="cache", size_bytes=4096),)


def test_inspect_passes_the_resolved_executable_and_the_right_arguments():
    calls = []

    def run(command):
        calls.append(command)
        return "D:\\.npm-cache\n"

    npm_inspector.inspect(
        resolve=lambda name: "C:\\npm.cmd", run=run, measure=lambda p: 1
    )
    assert calls == [["C:\\npm.cmd", "config", "get", "cache"]]


def test_inspect_never_calls_cache_verify():
    """`npm cache verify` hace garbage collection de verdad (comprobado real,
    T2C.1): el inspector nunca debe armar ese comando."""
    calls = []

    def run(command):
        calls.append(command)
        return "D:\\.npm-cache\n"

    npm_inspector.inspect(
        resolve=lambda name: "C:\\npm.cmd", run=run, measure=lambda p: 1
    )
    assert all("verify" not in part for call in calls for part in call)


def test_inspect_is_none_when_npm_is_not_installed():
    resolve, run = fake(resolved=None)
    assert npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_when_the_command_fails():
    resolve, run = fake(output=None)
    assert npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_on_empty_output():
    resolve, run = fake(output="\n")
    assert npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_when_the_folder_cannot_be_measured():
    resolve, run = fake()
    result = npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: None)
    assert result is None


def test_inspect_is_none_when_the_cache_is_empty():
    resolve, run = fake()
    assert npm_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 0) is None
