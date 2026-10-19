"""Pruebas del inspector de uv (T2C.1): `resolve`/`run`/`measure` siempre se
reemplazan, nunca se ejecuta ni se mide nada de verdad."""

from discoclaro.adapters.inspectors import uv as uv_inspector
from discoclaro.domain.insights import InsightItem


def fake(resolved="C:\\uv.exe", output="C:\\Users\\x\\AppData\\Local\\uv\\cache\n"):
    return (lambda name: resolved), (lambda command: output)


def test_inspect_measures_the_real_cache_directory():
    resolve, run = fake()
    insight = uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 2048)
    assert insight.program == "uv"
    assert insight.items == (InsightItem(name="cache", size_bytes=2048),)
    assert insight.total_bytes == 2048


def test_inspect_passes_the_resolved_executable_and_the_right_arguments():
    calls = []

    def run(command):
        calls.append(command)
        return "C:\\cache\n"

    uv_inspector.inspect(
        resolve=lambda name: "C:\\uv.exe", run=run, measure=lambda p: 1
    )
    assert calls == [["C:\\uv.exe", "cache", "dir"]]


def test_inspect_measures_the_path_with_whitespace_stripped():
    measured = []
    resolve, run = fake(output="  C:\\cache\n\n")
    uv_inspector.inspect(resolve=resolve, run=run, measure=measured.append)
    assert measured == ["C:\\cache"]


def test_inspect_is_none_when_uv_is_not_installed():
    resolve, run = fake(resolved=None)
    assert uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_when_the_command_fails():
    resolve, run = fake(output=None)
    assert uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_on_empty_output():
    resolve, run = fake(output="\n")
    assert uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 1) is None


def test_inspect_is_none_when_the_folder_cannot_be_measured():
    resolve, run = fake()
    assert (
        uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: None) is None
    )


def test_inspect_is_none_when_the_cache_is_empty():
    resolve, run = fake()
    assert uv_inspector.inspect(resolve=resolve, run=run, measure=lambda p: 0) is None
