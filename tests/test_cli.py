"""Pruebas de la CLI. El servicio de escaneo se reemplaza por uno falso."""

import dataclasses
import importlib
import io
import json
import tomllib
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discoclaro import __version__, i18n
from discoclaro.domain.formatting import format_table
from discoclaro.domain.models import Node, ScanProgress, Snapshot, SystemFile
from discoclaro.domain.unexplained import compute_unexplained
from discoclaro.i18n import t
from discoclaro.interfaces import cli
from discoclaro.interfaces.cli import COMMANDS, main
from discoclaro.services import report_service
from discoclaro.services.report_service import Report, ReportError
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

    def run_scan(target, **kwargs):
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


@pytest.mark.parametrize("command", ["growth"])
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


def with_duplicates(gib):
    base = make_summary()
    snapshot = dataclasses.replace(base.snapshot, duplicate_bytes=int(gib * GIB))
    return dataclasses.replace(base, snapshot=snapshot)


def test_hard_link_duplicates_get_their_own_line_after_the_space_line(
    monkeypatch, capsys
):
    fake_run_scan(monkeypatch, with_duplicates(21.5))
    assert main(["scan", "C:"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2].startswith("Escaneado:")
    assert lines[3] == (
        "Enlaces duros (el mismo contenido con otro nombre): 21,5 GiB; "
        "cada carpeta los cuenta enteros y el total del disco, una sola vez."
    )
    assert lines[4].startswith("Archivos de sistema")


def test_the_hard_link_line_is_translated(monkeypatch, capsys):
    i18n.set_language("en")
    fake_run_scan(monkeypatch, with_duplicates(21.5))
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[3] == (
        "Hard links (the same content under another name): 21.5 GiB; "
        "each folder counts them in full and the disk total only once."
    )


def test_there_is_no_hard_link_line_when_there_are_none(monkeypatch, capsys):
    fake_run_scan(monkeypatch, make_summary())
    assert main(["scan", "C:"]) == 0
    assert "Enlaces duros" not in capsys.readouterr().out


def big_unexplained():
    return compute_unexplained(300 * GIB, 250 * GIB, int(6.5 * GIB))


def test_a_big_unexplained_space_adds_the_warning_that_suggests_admin(
    monkeypatch, capsys
):
    fake_run_scan(monkeypatch, make_summary(unexplained=big_unexplained()))
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[-3:] == [
        "Sin explicar: 43,5 GiB (14,5 % de lo usado).",
        "Atención: no se pudo explicar 43,5 GiB. Puede haber carpetas que solo un "
        "administrador puede abrir: prueba con `discoclaro scan C: --admin`.",
        "Foto guardada: n.º 7 (4.321 carpetas guardadas).",
    ]


def test_the_warning_is_translated(monkeypatch, capsys):
    i18n.set_language("en")
    fake_run_scan(monkeypatch, make_summary(unexplained=big_unexplained()))
    assert main(["scan", "C:"]) == 0
    assert capsys.readouterr().out.splitlines()[-3:-1] == [
        "Unexplained: 43.5 GiB (14.5% of used).",
        "Warning: 43.5 GiB could not be explained. Some folders may only be readable "
        "by an administrator: try `discoclaro scan C: --admin`.",
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
            "system_files_bytes": 0,
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


def test_resumed_folders_line_appears_right_after_the_done_line(monkeypatch, capsys):
    fake_run_scan(monkeypatch, dataclasses.replace(make_summary(), resumed_folders=3))
    assert main(["scan", "C:"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Escaneo de C: terminado en 3 min 12 s."
    assert lines[1] == "Se retomaron 3 carpetas de primer nivel de una pausa anterior."
    assert lines[2].startswith("Carpetas:")


def test_no_resumed_folders_line_when_nothing_was_resumed(monkeypatch, capsys):
    fake_run_scan(monkeypatch, make_summary())
    assert main(["scan", "C:"]) == 0
    assert "retomaron" not in capsys.readouterr().out


def test_scan_help_mentions_the_restart_option(capsys):
    with pytest.raises(SystemExit):
        main(["scan", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "--restart" in shown
    assert t("cli.scan.restart_help") in shown


def test_restart_is_passed_through_to_the_normal_scan(monkeypatch, capsys):
    calls = []

    def run_scan(target, **kwargs):
        calls.append(kwargs.get("restart"))
        return make_summary()

    monkeypatch.setattr(cli.scan_service, "run_scan", run_scan)
    assert main(["scan", "C:", "--restart"]) == 0
    assert calls == [True]
    capsys.readouterr()


def test_without_the_flag_restart_defaults_to_false(monkeypatch, capsys):
    calls = []

    def run_scan(target, **kwargs):
        calls.append(kwargs.get("restart"))
        return make_summary()

    monkeypatch.setattr(cli.scan_service, "run_scan", run_scan)
    assert main(["scan", "C:"]) == 0
    assert calls == [False]
    capsys.readouterr()


def test_restart_is_passed_through_to_the_admin_scan(monkeypatch, capsys):
    calls = []

    def run_admin_scan(target, *, on_elevate=None, on_integrity_warning=None, **kw):
        calls.append(kw.get("restart"))
        return make_summary()

    monkeypatch.setattr(cli.elevation_service, "run_admin_scan", run_admin_scan)
    assert main(["scan", "C:", "--admin", "--restart"]) == 0
    assert calls == [True]
    capsys.readouterr()


def test_a_keyboard_interrupt_prints_a_clean_message_and_exits_130(monkeypatch, capsys):
    fake_run_scan(monkeypatch, error=KeyboardInterrupt())
    assert main(["scan", "C:"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == "Escaneo cancelado por el usuario; no se guardó ninguna foto.\n"
    )


def test_the_keyboard_interrupt_message_is_translated(monkeypatch, capsys):
    i18n.set_language("en")
    fake_run_scan(monkeypatch, error=KeyboardInterrupt())
    assert main(["scan", "C:"]) == 130
    assert capsys.readouterr().err == (
        "Scan cancelled by the user; no snapshot was saved.\n"
    )


def test_pausing_prints_the_resume_command_and_exits_130(monkeypatch, capsys):
    def run_scan(target, *, on_progress=None, on_checkpoint=None, restart=False):
        raise cli.ScanPaused

    monkeypatch.setattr(cli.scan_service, "run_scan", run_scan)
    assert main(["scan", "D:\\datos"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Escaneo pausado. Corré `discoclaro scan D:\\datos` para seguir donde se "
        "quedó.\n"
    )


def test_progress_reporter_only_prints_when_stdout_is_a_tty(monkeypatch, capsys):
    reporter = cli._ProgressReporter()
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    reporter.on_progress(ScanProgress(1, 1, 1, "C:\\x"))
    assert capsys.readouterr().out == ""


def test_progress_reporter_prints_a_line_when_interactive(monkeypatch, capsys):
    reporter = cli._ProgressReporter()
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    reporter.on_progress(ScanProgress(312, 1000, 5 * GIB, "C:\\Windows"))
    out = capsys.readouterr().out
    assert out.startswith("\r")
    assert "312" in out
    assert "C:\\Windows" in out


def test_progress_reporter_throttles_by_time(monkeypatch, capsys):
    reporter = cli._ProgressReporter()
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    times = iter([0.0, 0.05, 0.2])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(times))
    reporter.on_progress(ScanProgress(1, 1, 1, "a"))
    reporter.on_progress(ScanProgress(2, 2, 2, "b"))
    reporter.on_progress(ScanProgress(3, 3, 3, "c"))
    assert capsys.readouterr().out.count("\r") == 2


def test_progress_reporter_finish_prints_a_newline_only_if_something_was_shown(
    monkeypatch, capsys
):
    reporter = cli._ProgressReporter()
    reporter.finish()
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    reporter.on_progress(ScanProgress(1, 1, 1, "a"))
    capsys.readouterr()
    reporter.finish()
    assert capsys.readouterr().out == "\n"


def test_on_checkpoint_raises_scan_paused_when_the_pause_key_was_pressed(monkeypatch):
    reporter = cli._ProgressReporter()
    monkeypatch.setattr(cli.windows, "pause_requested", lambda: True)
    with pytest.raises(cli.ScanPaused):
        reporter.on_checkpoint()


def test_on_checkpoint_does_nothing_without_the_pause_key(monkeypatch):
    reporter = cli._ProgressReporter()
    monkeypatch.setattr(cli.windows, "pause_requested", lambda: False)
    reporter.on_checkpoint()


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


COMMAND = "R:\\python\\python.exe -I -m discoclaro.interfaces.elevated scan C:"


def fake_admin_scan(
    monkeypatch, summary=None, error=None, announce=(("C:", COMMAND)), warning=None
):
    """Reemplaza el servicio de administrador; devuelve los destinos recibidos."""
    calls = []

    def run_admin_scan(target, *, on_elevate=None, on_integrity_warning=None, **kwargs):
        calls.append(target)
        if on_integrity_warning and warning:
            on_integrity_warning(warning)
        if on_elevate and announce:
            on_elevate(*announce)
        if error:
            raise error
        return summary

    monkeypatch.setattr(cli.elevation_service, "run_admin_scan", run_admin_scan)
    return calls


def test_scan_help_mentions_the_admin_option(capsys):
    with pytest.raises(SystemExit):
        main(["scan", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "--admin" in shown
    assert t("cli.scan.admin_help") in shown


def test_admin_uses_the_administrator_service_and_not_the_normal_one(
    monkeypatch, capsys
):
    normal = fake_run_scan(monkeypatch, make_summary())
    admin = fake_admin_scan(monkeypatch, make_summary(system_files=()))
    assert main(["scan", "c:", "--admin"]) == 0
    assert admin == ["c:"]
    assert normal == []
    assert "Archivos de sistema, medidos aparte: 6,5 GiB" in capsys.readouterr().out


def test_without_admin_the_administrator_service_is_never_used(monkeypatch, capsys):
    normal = fake_run_scan(monkeypatch, make_summary())
    admin = fake_admin_scan(monkeypatch, make_summary())
    assert main(["scan", "C:"]) == 0
    assert normal == ["C:"]
    assert admin == []
    capsys.readouterr()


def test_the_exact_command_is_shown_before_the_summary(monkeypatch, capsys):
    fake_admin_scan(monkeypatch, make_summary())
    assert main(["scan", "C:", "--admin"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "Se pedirá permiso de administrador para escanear C: en modo solo lectura. "
        "Windows mostrará su propia ventana de permisos."
    )
    assert lines[1] == f"Orden que recibirá Windows: {COMMAND}"
    assert lines[2].startswith("Escaneo de C: terminado")


def test_the_announcement_is_translated_and_cleaned(monkeypatch, capsys):
    i18n.set_language("en")
    fake_admin_scan(
        monkeypatch, make_summary(), announce=("C:", "x.exe \x1b[31mmalo\nfalso")
    )
    assert main(["scan", "C:", "--admin"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Administrator permission will be requested to scan C:")
    assert lines[1] == "Command Windows will receive: x.exe ?[31mmalo?falso"


def test_a_writable_install_is_announced_before_the_elevation_announcement(
    monkeypatch, capsys
):
    fake_admin_scan(monkeypatch, make_summary(), warning="C:\\Users\\x\\venv")
    assert main(["scan", "C:", "--admin"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "Aviso: la carpeta donde está instalado discoclaro (C:\\Users\\x\\venv) se "
        "puede escribir con tu usuario. Es normal en una instalación de desarrollo "
        "o en un entorno virtual; en una instalación protegida no debería pasar."
    )
    assert lines[1].startswith("Se pedirá permiso de administrador")


def test_the_writable_warning_is_translated_and_cleaned(monkeypatch, capsys):
    i18n.set_language("en")
    fake_admin_scan(monkeypatch, make_summary(), warning="C:\\x\x1b[31m\nfalso")
    assert main(["scan", "C:", "--admin"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "Notice: the folder where discoclaro is installed (C:\\x?[31m?falso) can "
        "be written to by your user. This is normal for a development install or "
        "a virtual environment; it should not happen on a protected install."
    )


def test_no_writable_warning_line_when_nothing_was_reported(monkeypatch, capsys):
    fake_admin_scan(monkeypatch, make_summary())
    assert main(["scan", "C:", "--admin"]) == 0
    assert "Aviso" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("kind", "template"),
    [
        (
            "admin_needs_volume",
            "Con --admin solo se escanean discos completos (por ejemplo C:), "
            "no carpetas: {}",
        ),
        (
            "elevation_declined",
            "Se rechazó el permiso de administrador para {}. No se escaneó nada.",
        ),
        (
            "elevation_timeout",
            "El escaneo de {} con permisos de administrador tardó demasiado y se "
            "dejó de esperar; puede seguir corriendo en segundo plano.",
        ),
        ("elevation_failed", "No se pudo escanear con permisos de administrador: {}"),
        (
            "elevation_no_result",
            "El escaneo de {} con permisos de administrador no dejó un resultado "
            "válido.",
        ),
        (
            "unexpected",
            "Ocurrió un error inesperado en el escaneo con permisos de "
            "administrador: {}",
        ),
        (
            "integrity_failed",
            "No se pidió el permiso de administrador: lo instalado no coincide con "
            "lo esperado ({}). Reinstala discoclaro y vuelve a intentarlo.",
        ),
        (
            "unsafe_data_location",
            "No se escribió nada: {} es un enlace o tiene más de un nombre, así "
            "que no es seguro escribir ahí. Revísalo a mano.",
        ),
    ],
)
def test_administrator_errors_go_to_stderr_with_a_cleaned_detail(
    monkeypatch, capsys, kind, template
):
    fake_admin_scan(
        monkeypatch, error=ScanError(kind, "C:\x1b[31m\nfalso"), announce=()
    )
    assert main(["scan", "C:", "--admin"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == template.format("C:?[31m?falso") + "\n"


def make_report(**changes):
    snapshot = Snapshot(
        taken_at=datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        volume="C:",
        total_bytes=500 * GIB,
        used_bytes=300 * GIB,
        free_bytes=200 * GIB,
        scanned_bytes=290 * GIB,
        system_files_bytes=int(6.5 * GIB),
        unreadable_bytes=0,
        unreadable_count=0,
        duration_s=68.0,
        is_admin=False,
        id=3,
    )
    fields = {
        "snapshot": snapshot,
        "top_folders": (
            Node("C:\\Windows", "C:\\", 10 * GIB, 100, 1),
            Node("C:\\Users", "C:\\", 2 * GIB, 20, 1),
        ),
        "total_folders": 500,
        "unexplained": compute_unexplained(300 * GIB, 290 * GIB, int(6.5 * GIB)),
    }
    return Report(**{**fields, **changes})


def fake_run_report(monkeypatch, report=None, error=None):
    """Reemplaza el servicio de informe; devuelve (destino, top) de cada llamada."""
    calls = []

    def run_report(target, *, top_n=report_service.DEFAULT_TOP_N):
        calls.append((target, top_n))
        if error:
            raise error
        return report

    monkeypatch.setattr(cli.report_service, "run_report", run_report)
    return calls


def test_report_help_describes_the_target_from_the_catalog(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["report", "--help"])
    assert exc.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert t("cli.report.target_name") in shown
    assert t("cli.report.target_help") in shown
    assert "--top" in shown
    assert t("cli.report.top_help") in shown
    assert "--json" in shown
    assert t("cli.report.json_help") in shown


def test_report_passes_the_target_as_typed_and_the_default_top(monkeypatch, capsys):
    calls = fake_run_report(monkeypatch, make_report())
    assert main(["report", "c:"]) == 0
    assert calls == [("c:", report_service.DEFAULT_TOP_N)]
    capsys.readouterr()


def test_report_with_no_target_passes_none(monkeypatch, capsys):
    calls = fake_run_report(monkeypatch, make_report())
    assert main(["report"]) == 0
    assert calls == [(None, report_service.DEFAULT_TOP_N)]
    capsys.readouterr()


def test_report_top_is_passed_through(monkeypatch, capsys):
    calls = fake_run_report(monkeypatch, make_report())
    assert main(["report", "C:", "--top", "5"]) == 0
    assert calls == [("C:", 5)]
    capsys.readouterr()


def test_report_prints_the_table_in_spanish(monkeypatch, capsys):
    report = make_report()
    fake_run_report(monkeypatch, report)
    assert main(["report", "C:"]) == 0
    lines = capsys.readouterr().out.splitlines()
    taken_at = report.snapshot.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    assert lines[0] == f"Carpetas más grandes de C:, foto del {taken_at} (n.º 3)."
    rows = [["C:\\Windows", "10,0 GiB", "100"], ["C:\\Users", "2,0 GiB", "20"]]
    table = format_table(["Carpeta", "Tamaño", "Archivos"], rows, align=("l", "r", "r"))
    assert lines[1 : 1 + len(table)] == table
    rest = lines[1 + len(table) :]
    assert rest == [
        "Se muestran 2 de 500 carpetas guardadas.",
        "Sin explicar: 3,5 GiB (1,2 % de lo usado).",
    ]


def test_report_prints_the_table_in_english(monkeypatch, capsys):
    i18n.set_language("en")
    report = make_report()
    fake_run_report(monkeypatch, report)
    assert main(["report", "C:"]) == 0
    lines = capsys.readouterr().out.splitlines()
    taken_at = report.snapshot.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    assert lines[0] == f"Largest folders of C:, snapshot of {taken_at} (no. 3)."
    rows = [["C:\\Windows", "10.0 GiB", "100"], ["C:\\Users", "2.0 GiB", "20"]]
    table = format_table(["Folder", "Size", "Files"], rows, align=("l", "r", "r"))
    assert lines[1 : 1 + len(table)] == table
    rest = lines[1 + len(table) :]
    assert rest == [
        "Showing 2 of 500 folders stored.",
        "Unexplained: 3.5 GiB (1.2% of used).",
    ]


def test_report_with_no_folders_still_shows_the_table_header(monkeypatch, capsys):
    report = make_report(top_folders=(), total_folders=0)
    fake_run_report(monkeypatch, report)
    assert main(["report", "C:"]) == 0
    lines = capsys.readouterr().out.splitlines()
    table = format_table(["Carpeta", "Tamaño", "Archivos"], [], align=("l", "r", "r"))
    assert lines[1 : 1 + len(table)] == table
    assert lines[1 + len(table)] == "Se muestran 0 de 0 carpetas guardadas."


def test_report_json_has_the_expected_fields(monkeypatch, capsys):
    report = make_report()
    fake_run_report(monkeypatch, report)
    assert main(["report", "C:", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["target"] == "C:"
    assert data["snapshot_id"] == 3
    assert data["total_folders"] == 500
    assert data["scanned_bytes"] == 290 * GIB
    assert data["duplicate_bytes"] == 0
    assert [folder["path"] for folder in data["top_folders"]] == [
        "C:\\Windows",
        "C:\\Users",
    ]
    assert data["top_folders"][0] == {
        "path": "C:\\Windows",
        "size_bytes": 10 * GIB,
        "n_files": 100,
        "depth": 1,
    }
    expected = compute_unexplained(300 * GIB, 290 * GIB, int(6.5 * GIB))
    assert data["unexplained"] == {
        "size_bytes": expected.size_bytes,
        "percent_of_used": pytest.approx(expected.percent_of_used),
        "exceeds_threshold": expected.exceeds_threshold,
    }


def test_report_json_with_no_unexplained_space_is_null(monkeypatch, capsys):
    folder_snapshot = dataclasses.replace(make_report().snapshot, volume="D:\\data")
    report = make_report(snapshot=folder_snapshot, unexplained=None)
    fake_run_report(monkeypatch, report)
    assert main(["report", "D:\\data", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["unexplained"] is None


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "report_not_fixed_disk",
            "Esta ruta no está en un disco fijo, así que no se pudo buscar una foto "
            "de ella: ",
        ),
        (
            "report_no_snapshot_for_target",
            "No hay ninguna foto guardada de ",
        ),
    ],
)
def test_report_errors_go_to_stderr_with_a_safe_target_and_exit_code_1(
    monkeypatch, capsys, kind, expected
):
    fake_run_report(monkeypatch, error=ReportError(kind, "D:\\x\x1b[31m\nfalso"))
    assert main(["report", "D:\\x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(expected)
    assert "D:\\x?[31m?falso" in captured.err


def test_report_with_no_snapshots_at_all_is_reported(monkeypatch, capsys):
    fake_run_report(monkeypatch, error=ReportError("report_no_snapshots", ""))
    assert main(["report"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "No hay ninguna foto guardada todavía. Corre `discoclaro scan <disco>` "
        "primero.\n"
    )


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
