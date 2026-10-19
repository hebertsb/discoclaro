"""Pruebas del inspector de Docker (T2C.1): `resolve`/`run` siempre se
reemplazan, nunca se ejecuta nada de verdad."""

from discoclaro.adapters.inspectors import docker as docker_inspector

REAL_OUTPUT = (
    '{"Active":"4","Reclaimable":"17.02GB (75%)","Size":"22.47GB",'
    '"TotalCount":"10","Type":"Images"}\n'
    '{"Active":"0","Reclaimable":"2.11GB (100%)","Size":"2.11GB",'
    '"TotalCount":"5","Type":"Containers"}\n'
    '{"Active":"3","Reclaimable":"201.9MB (3%)","Size":"5.639GB",'
    '"TotalCount":"4","Type":"Local Volumes"}\n'
    '{"Active":"0","Reclaimable":"0B","Size":"0B","TotalCount":"0",'
    '"Type":"Build Cache"}\n'
)


def fake(resolved="C:\\docker.exe", output=REAL_OUTPUT):
    return (lambda name: resolved), (lambda command: output)


def test_inspect_parses_the_real_output_shape():
    resolve, run = fake()
    insight = docker_inspector.inspect(resolve=resolve, run=run)
    assert insight.program == "docker"
    by_name = {item.name: item.size_bytes for item in insight.items}
    assert by_name["Images"] == round(17.02 * 1000**3)
    assert by_name["Containers"] == round(2.11 * 1000**3)
    assert by_name["Local Volumes"] == round(201.9 * 1000**2)
    assert "Build Cache" not in by_name


def test_inspect_passes_the_resolved_executable_and_the_right_arguments():
    calls = []

    def run(command):
        calls.append(command)
        return REAL_OUTPUT

    docker_inspector.inspect(resolve=lambda name: "C:\\docker.exe", run=run)
    assert calls == [["C:\\docker.exe", "system", "df", "--format", "json"]]


def test_inspect_is_none_when_docker_is_not_installed():
    resolve, run = fake(resolved=None)
    assert docker_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_when_the_daemon_is_not_running():
    resolve, run = fake(output=None)
    assert docker_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_when_nothing_is_reclaimable():
    output = (
        '{"Active":"0","Reclaimable":"0B","Size":"0B","TotalCount":"0",'
        '"Type":"Images"}\n'
    )
    resolve, run = fake(output=output)
    assert docker_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_skips_a_line_that_is_not_valid_json():
    output = REAL_OUTPUT + "not json at all\n"
    resolve, run = fake(output=output)
    insight = docker_inspector.inspect(resolve=resolve, run=run)
    assert len(insight.items) == 3


def test_inspect_skips_a_line_that_is_a_json_array_not_an_object():
    output = "[1, 2, 3]\n" + REAL_OUTPUT
    resolve, run = fake(output=output)
    insight = docker_inspector.inspect(resolve=resolve, run=run)
    assert len(insight.items) == 3


def test_inspect_skips_a_row_missing_the_reclaimable_field():
    output = '{"Type":"Images"}\n'
    resolve, run = fake(output=output)
    assert docker_inspector.inspect(resolve=resolve, run=run) is None


def test_inspect_is_none_on_empty_output():
    resolve, run = fake(output="")
    assert docker_inspector.inspect(resolve=resolve, run=run) is None
