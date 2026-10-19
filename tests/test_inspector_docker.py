"""Pruebas del inspector de Docker (T2C.1/T2C.2): `resolve`/`run`/`vhdx_path`/
`file_size`/`used_bytes`/`run_in_wsl` siempre se reemplazan, nunca se ejecuta
ni se toca nada de verdad."""

import types

from discoclaro.adapters.inspectors import docker as docker_inspector

REAL_DF_OUTPUT = (
    "Filesystem           1K-blocks      Used Available Use% Mounted on\n"
    "/dev/sdd                132003     66738     54983  55% /\n"
    "/dev/sde             1055762868  29015000 973044396   3%"
    " /mnt/docker-desktop-disk\n"
    "C:\\                  497775612 298929920 198845692  60% /mnt/host/c\n"
)

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


def fake_run_in_wsl(stdout=REAL_DF_OUTPUT, returncode=0, raises=None):
    def run_in_wsl(distro, command):
        if raises is not None:
            raise raises
        assert distro == "docker-desktop"
        assert command == ["df", "-k"]
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)

    return run_in_wsl


def test_parse_df_used_bytes_reads_the_real_1k_block_shape():
    used = docker_inspector._parse_df_used_bytes(
        REAL_DF_OUTPUT, "/mnt/docker-desktop-disk"
    )
    assert used == 29015000 * 1024


def test_parse_df_used_bytes_is_none_without_a_matching_mount():
    assert (
        docker_inspector._parse_df_used_bytes(REAL_DF_OUTPUT, "/no/such/mount") is None
    )


def test_parse_df_used_bytes_is_none_on_a_malformed_used_column():
    output = (
        "Filesystem 1K-blocks Used Available Use% Mounted on\nx not-a-number y 1% /m\n"
    )
    assert docker_inspector._parse_df_used_bytes(output, "/m") is None


def test_wsl_used_bytes_parses_the_real_shape():
    used = docker_inspector._wsl_used_bytes(run_in_wsl=fake_run_in_wsl())
    assert used == 29015000 * 1024


def test_wsl_used_bytes_is_none_when_wsl_is_not_installed():
    result = docker_inspector._wsl_used_bytes(
        run_in_wsl=fake_run_in_wsl(raises=FileNotFoundError())
    )
    assert result is None


def test_wsl_used_bytes_is_none_when_the_command_fails():
    result = docker_inspector._wsl_used_bytes(run_in_wsl=fake_run_in_wsl(returncode=1))
    assert result is None


def test_wsl_used_bytes_is_none_when_the_output_is_too_big():
    result = docker_inspector._wsl_used_bytes(
        run_in_wsl=fake_run_in_wsl(stdout="x" * 1_000_001)
    )
    assert result is None


def test_compaction_gap_combines_the_real_file_size_and_the_real_usage():
    gap = docker_inspector.compaction_gap(
        vhdx_path=lambda: "C:\\docker_data.vhdx",
        file_size=lambda path: 30 * 1024**3,
        used_bytes=lambda: 27 * 1024**3,
    )
    assert gap.path == "C:\\docker_data.vhdx"
    assert gap.file_bytes == 30 * 1024**3
    assert gap.used_bytes == 27 * 1024**3
    assert gap.gap_bytes == 3 * 1024**3


def test_compaction_gap_is_none_when_docker_does_not_use_wsl2():
    gap = docker_inspector.compaction_gap(
        vhdx_path=lambda: None,
        file_size=lambda path: 1,
        used_bytes=lambda: 1,
    )
    assert gap is None


def test_compaction_gap_is_none_when_the_file_size_is_unknown():
    gap = docker_inspector.compaction_gap(
        vhdx_path=lambda: "C:\\docker_data.vhdx",
        file_size=lambda path: None,
        used_bytes=lambda: 1,
    )
    assert gap is None


def test_compaction_gap_is_none_when_the_usage_is_unknown():
    gap = docker_inspector.compaction_gap(
        vhdx_path=lambda: "C:\\docker_data.vhdx",
        file_size=lambda path: 1,
        used_bytes=lambda: None,
    )
    assert gap is None
