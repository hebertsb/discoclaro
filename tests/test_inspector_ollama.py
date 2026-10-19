"""Pruebas del inspector de Ollama (T2C.1): `resolve`/`run` siempre se
reemplazan, nunca se ejecuta nada de verdad."""

from discoclaro.adapters.inspectors import ollama as ollama_inspector

REAL_SHAPE = (
    "NAME              ID              SIZE      MODIFIED    \n"
    "llama3:latest     365c0bd3c000    4.7 GB    2 days ago  \n"
    "phi3:mini         abcdef123456    2.2 GB    3 weeks ago \n"
)

EMPTY_SHAPE = "NAME    ID    SIZE    MODIFIED \n"


def fake(resolved="C:\\ollama.exe", output=REAL_SHAPE):
    return (lambda name: resolved), (lambda command: output)


def test_inspect_parses_the_real_output_shape():
    resolve, run = fake()
    insight = ollama_inspector.inspect(resolve=resolve, run=run)
    assert insight.program == "ollama"
    by_name = {item.name: item.size_bytes for item in insight.items}
    assert by_name["llama3:latest"] == round(4.7 * 1024**3)
    assert by_name["phi3:mini"] == round(2.2 * 1024**3)


def test_inspect_passes_the_resolved_executable_and_the_right_arguments():
    calls = []

    def run(command):
        calls.append(command)
        return REAL_SHAPE

    ollama_inspector.inspect(resolve=lambda name: "C:\\ollama.exe", run=run)
    assert calls == [["C:\\ollama.exe", "list"]]


def test_inspect_is_none_when_ollama_is_not_installed():
    resolve, run = fake(resolved=None)
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_when_the_command_fails():
    resolve, run = fake(output=None)
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_with_no_models_installed():
    resolve, run = fake(output=EMPTY_SHAPE)
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_on_empty_output():
    resolve, run = fake(output="")
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_when_the_header_does_not_have_the_expected_columns():
    resolve, run = fake(output="SOMETHING ELSE ENTIRELY\n")
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_skips_a_row_with_an_unparseable_size():
    output = REAL_SHAPE.replace("4.7 GB", "not-a-size")
    resolve, run = fake(output=output)
    insight = ollama_inspector.inspect(resolve=resolve, run=run)
    assert [item.name for item in insight.items] == ["phi3:mini"]


def test_inspect_skips_a_truncated_row():
    output = "NAME    ID    SIZE    MODIFIED \nllama3\n"
    resolve, run = fake(output=output)
    assert ollama_inspector.inspect(resolve=resolve, run=run) is None
