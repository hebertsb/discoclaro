"""Pruebas de la CLI. El servicio de escaneo se reemplaza por uno falso."""

import dataclasses
import importlib
import io
import tomllib
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discoclaro import __version__, i18n
from discoclaro.domain.models import Snapshot, SystemFile
from discoclaro.domain.unexplained import compute_unexplained
from discoclaro.i18n import t
from discoclaro.interfaces import cli
from discoclaro.interfaces.cli import COMMANDS, main
from discoclaro.services.scan_service import ScanError, ScanSummary

GIB = 1024**3


@pytest.fixture(autouse=True)
def restore_language():
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


def make_summary(**changes):
    snapshot = Snapshot(
        taken_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        volume="C:",
        total_bytes=500 * GIB,
        used_bytes=300 * GIB,
        free_bytes=200 * GIB,
        scanned_bytes=290 * GIB,
        system_files_bytes=int(6.5 * GIB),
        unreadable_bytes=0,
        unreadable_count=1842,
        duration_s=192.0,
        is_admin=False,
        id=7,
    )
    fields = {
        "target": "C:\\",
        "is_volume": True,
        "snapshot": snapshot,
        "folders": 312_456,
        "files": 1_203_998,
        "skipped_links": 24,
        "system_files": (SystemFile("C:\\hiberfil.sys", int(6.5 * GIB)),),
        "nodes_saved": 4321,
        "unexplained": compute_unexplained(300 * GIB, 290 * GIB, int(6.5 * GIB)),
    }
    return ScanSummary(**{**fields, **changes})


def fake_run_scan(monkeypatch, summary=None, error=None):
    """Reemplaza el servicio de escaneo; devuelve la lista de destinos recibidos."""
    calls = []

    def run_scan(target):
        calls.append(target)
        if error:
            raise error
        return summary

    monkeypatch.setattr(cli.scan_service, "run_scan", run_scan)
    return calls


def test_version_prints_name_and_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"discoclaro {__version__}"


@pytest.mark.parametrize("command", ["report", "growth"])
def test_commands_not_built_yet_exit_cleanly(command, capsys):
    assert main([command]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("argv", [[], ["no-existe"], ["scan"]])
def test_missing_or_unknown_subcommand_or_target_is_an_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    capsys.readouterr()


def test_help_texts_come_from_the_catalog(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert t("cli.description") in shown
    for command in COMMANDS:
        assert t(f"cli.commands.{command}") in shown


def test_scan_help_describes_the_target_from_the_catalog(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["scan", "--help"])
    assert exc.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert t("cli.scan.target_name") in shown
    assert t("cli.scan.target_help") in shown


def test_scan_passes_the_target_exactly_as_typed(monkeypatch, capsys):
    calls = fake_run_scan(monkeypatch, make_summary())
    assert main(["scan", "c:"]) == 0
    assert calls == ["c:"]
    capsys.readouterr()


def test_scan_prints_the_summary_in_spanish(monkeypatch, capsys):
    fake_run_scan(monkeypatch, make_summary())
    assert main(["scan", "C:"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "Escaneo de C: terminado en 3 min 12 s.",
        "Carpetas: 312.456 · Archivos: 1.203.998 · Enlaces omitidos: 24",
        "Escaneado: 290,0 GiB de 300,0 GiB usados "
        "(volumen de 500,0 GiB, 200,0 GiB libres).",
        "Archivos de sistema, medidos aparte: 6,5 GiB",
        "Rutas sin acceso: 1.842",
        "Sin explicar: 3,5 GiB (1,2 % de lo usado).",
        "Foto guardada: n.º 7 (4.321 carpetas guardadas).",
    ]


def test_scan_prints_the_summary_in_english(monkeypatch, capsys):
    i18n.set_language("en")
    fake_run_scan(monkeypatch, make_summary())
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Scan of C: finished in 3 min 12 s.",
        "Folders: 312,456 · Files: 1,203,998 · Links skipped: 24",
        "Scanned: 290.0 GiB of 300.0 GiB used (volume of 500.0 GiB, 200.0 GiB free).",
        "System files, measured separately: 6.5 GiB",
        "Paths without access: 1,842",
        "Unexplained: 3.5 GiB (1.2% of used).",
        "Snapshot saved: no. 7 (4,321 folders stored).",
    ]


def big_unexplained():
    return compute_unexplained(300 * GIB, 250 * GIB, int(6.5 * GIB))


def test_a_big_unexplained_space_adds_the_warning_to_open_an_administrator_terminal(
    monkeypatch, capsys
):
    fake_run_scan(monkeypatch, make_summary(unexplained=big_unexplained()))
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[-3:] == [
        "Sin explicar: 43,5 GiB (14,5 % de lo usado).",
        "Atención: no se pudo explicar 43,5 GiB. Puede haber carpetas que solo un "
        "administrador puede abrir: abre una terminal como administrador y vuelve "
        "a escanear.",
        "Foto guardada: n.º 7 (4.321 carpetas guardadas).",
    ]


def test_the_warning_is_translated(monkeypatch, capsys):
    i18n.set_language("en")
    fake_run_scan(monkeypatch, make_summary(unexplained=big_unexplained()))
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[-3:-1] == [
        "Unexplained: 43.5 GiB (14.5% of used).",
        "Warning: 43.5 GiB could not be explained. Some folders may only be readable "
        "by an administrator: open a terminal as administrator and scan again.",
    ]


def test_if_it_was_already_scanned_as_administrator_the_warning_says_so(
    monkeypatch, capsys
):
    base = make_summary(unexplained=big_unexplained())
    summary = dataclasses.replace(
        base, snapshot=dataclasses.replace(base.snapshot, is_admin=True)
    )
    fake_run_scan(monkeypatch, summary)
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[-2] == (
        "Atención: aun con permisos de administrador no se pudo explicar 43,5 GiB. "
        "Puede ser espacio propio del sistema de archivos, como metadatos o copias "
        "de volumen."
    )


@pytest.mark.parametrize("scanned_gib", [293.5, 310])
def test_nothing_is_said_when_no_space_is_left_unexplained(
    monkeypatch, capsys, scanned_gib
):
    unexplained = compute_unexplained(300 * GIB, int(scanned_gib * GIB), int(6.5 * GIB))
    fake_run_scan(monkeypatch, make_summary(unexplained=unexplained))
    assert main(["scan", "C:"]) == 0
    shown = capsys.readouterr().out
    assert "Sin explicar" not in shown
    assert "Atención" not in shown


def test_a_folder_summary_uses_the_folder_wording_and_omits_empty_lines(
    monkeypatch, capsys
):
    snapshot = make_summary().snapshot
    folder_snapshot = Snapshot(
        **{
            **{name: getattr(snapshot, name) for name in snapshot.__slots__},
            "volume": "D:\\data",
            "unreadable_count": 0,
            "scanned_bytes": 10 * GIB,
        }
    )
    summary = make_summary(
        target="D:\\data",
        is_volume=False,
        snapshot=folder_snapshot,
        system_files=(),
        skipped_links=0,
        unexplained=None,
    )
    fake_run_scan(monkeypatch, summary)
    assert main(["scan", "D:\\data"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Escaneo de D:\\data terminado en 3 min 12 s.",
        "Carpetas: 312.456 · Archivos: 1.203.998 · Enlaces omitidos: 0",
        "Escaneado: 10,0 GiB "
        "(el volumen usa 300,0 GiB de 500,0 GiB y tiene 200,0 GiB libres).",
        "Foto guardada: n.º 7 (4.321 carpetas guardadas).",
    ]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "not_fixed_disk",
            "Solo se pueden escanear discos fijos y esta ruta no está en uno: ",
        ),
        ("not_a_folder", "No existe o no es una carpeta: "),
        ("cannot_open_database", "No se pudo abrir la base de datos de discoclaro: "),
    ],
)
def test_errors_go_to_stderr_with_a_safe_target_and_exit_code_1(
    monkeypatch, capsys, kind, expected
):
    fake_run_scan(monkeypatch, error=ScanError(kind, "D:\\x\x1b[31m\nfalso"))
    assert main(["scan", "D:\\x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected + "D:\\x?[31m?falso\n"


def test_output_never_crashes_on_characters_the_console_cannot_write(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    plain = io.StringIO()
    monkeypatch.setattr(cli, "sys", types.SimpleNamespace(stdout=stream, stderr=plain))
    cli._make_output_robust()
    stream.write("ñandú 📁")
    stream.flush()
    assert stream.buffer.getvalue() == b"?and? ?"


def test_console_script_points_to_main():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    module_name, _, attribute = data["project"]["scripts"]["discoclaro"].partition(":")
    assert getattr(importlib.import_module(module_name), attribute) is main
