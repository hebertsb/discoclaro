"""Pruebas del inspector de pip (T2C.1): `resolve`/`run` siempre se
reemplazan, nunca se ejecuta nada de verdad."""

from discoclaro.adapters.inspectors import pip as pip_inspector

REAL_OUTPUT = """Package index page cache location (pip v23.3+): c:\\x\\http-v2
Package index page cache location (older pips): c:\\x\\http
Package index page cache size: 40.2 MB
Number of HTTP files: 216
Locally built wheels location: c:\\x\\wheels
Locally built wheels size: 0 bytes
Number of locally built wheels: 0
"""


def fake(resolved="C:\\pip.exe", output=REAL_OUTPUT):
    return (lambda name: resolved), (lambda command: output)


def test_inspect_parses_the_real_output_shape():
    resolve, run = fake()
    insight = pip_inspector.inspect(resolve=resolve, run=run)
    assert insight.program == "pip"
    by_name = {item.name: item.size_bytes for item in insight.items}
    assert by_name["index"] == round(40.2 * 1024**2)
    assert by_name["wheels"] == 0


def test_inspect_passes_the_resolved_executable_and_the_right_arguments():
    calls = []

    def run(command):
        calls.append(command)
        return REAL_OUTPUT

    pip_inspector.inspect(resolve=lambda name: "C:\\pip.exe", run=run)
    assert calls == [["C:\\pip.exe", "cache", "info"]]


def test_inspect_is_none_when_pip_is_not_installed():
    resolve, run = fake(resolved=None)
    assert pip_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_when_the_command_fails():
    resolve, run = fake(output=None)
    assert pip_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_on_completely_unrelated_output():
    resolve, run = fake(output="not even close to the real format\n")
    assert pip_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_on_empty_output():
    resolve, run = fake(output="")
    assert pip_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_skips_a_line_with_an_unparseable_size():
    output = "Package index page cache size: not-a-number\n"
    resolve, run = fake(output=output)
    assert pip_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_handles_only_one_of_the_two_lines_present():
    output = "Package index page cache size: 1.0 GB\n"
    resolve, run = fake(output=output)
    insight = pip_inspector.inspect(resolve=resolve, run=run)
    assert [item.name for item in insight.items] == ["index"]
    assert insight.items[0].size_bytes == 1024**3
