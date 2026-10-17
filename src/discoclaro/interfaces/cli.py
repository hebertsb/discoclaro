"""Línea de comandos de discoclaro.

Capa de entrada delgada: lee los argumentos, llama a un servicio e imprime el
resultado con los textos del catálogo. Aquí no va lógica de negocio.
"""

import argparse
import json
import sys
from collections.abc import Sequence

from discoclaro import __version__
from discoclaro.domain.formatting import (
    format_bytes,
    format_decimal,
    format_duration,
    format_int,
    format_table,
    safe_text,
)
from discoclaro.domain.models import Node
from discoclaro.domain.unexplained import Unexplained
from discoclaro.i18n import t
from discoclaro.services import elevation_service, report_service, scan_service
from discoclaro.services.report_service import Report, ReportError
from discoclaro.services.scan_service import ScanError, ScanSummary

COMMANDS = ("scan", "report", "growth")


def build_parser() -> argparse.ArgumentParser:
    """Arma el analizador de argumentos: `--version` y los subcomandos."""
    parser = argparse.ArgumentParser(
        prog="discoclaro", description=t("cli.description")
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        subparser = subparsers.add_parser(name, help=t(f"cli.commands.{name}"))
        if name == "scan":
            subparser.add_argument(
                "target",
                metavar=t("cli.scan.target_name"),
                help=t("cli.scan.target_help"),
            )
            subparser.add_argument(
                "--admin", action="store_true", help=t("cli.scan.admin_help")
            )
        elif name == "report":
            subparser.add_argument(
                "target",
                nargs="?",
                metavar=t("cli.report.target_name"),
                help=t("cli.report.target_help"),
            )
            subparser.add_argument(
                "--top",
                type=int,
                default=report_service.DEFAULT_TOP_N,
                metavar="N",
                help=t("cli.report.top_help"),
            )
            subparser.add_argument(
                "--json", action="store_true", help=t("cli.report.json_help")
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del comando `discoclaro`; devuelve el código de salida."""
    args = build_parser().parse_args(argv)
    _make_output_robust()
    if args.command == "scan":
        return _scan(args.target, args.admin)
    if args.command == "report":
        return _report(args.target, args.top, args.json)
    return 0


def _make_output_robust() -> None:
    """Evita que un carácter que la consola no sabe escribir tumbe el programa."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


def _announce_elevation(volume: str, command: str) -> None:
    """Dice qué se va a pedir y la orden exacta, antes de la ventana de Windows."""
    print(t("admin.announce", target=safe_text(volume)))
    print(t("admin.command", command=safe_text(command)))


def _scan(target: str, admin: bool = False) -> int:
    """Escanea `target`, guarda la foto y muestra el resumen."""
    try:
        if admin:
            summary = elevation_service.run_admin_scan(
                target, on_elevate=_announce_elevation
            )
        else:
            summary = scan_service.run_scan(target)
    except ScanError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    for line in _summary_lines(summary):
        print(line)
    return 0


def _report(target: str | None, top_n: int, as_json: bool) -> int:
    """Arma el informe de una foto ya guardada y lo muestra en tabla o en JSON."""
    try:
        report = report_service.run_report(target, top_n=top_n)
    except ReportError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(_report_to_json(report), ensure_ascii=False, indent=2))
    else:
        for line in _report_lines(report):
            print(line)
    return 0


def _size(value: int) -> str:
    return format_bytes(value, t("format.decimal_separator"))


def _number(value: int) -> str:
    return format_int(value, t("format.thousands_separator"))


def _summary_lines(summary: ScanSummary) -> list[str]:
    """Las líneas del resumen de un escaneo, en el idioma actual."""
    snapshot = summary.snapshot
    shown = snapshot.volume if summary.is_volume else summary.target
    space_key = "scan.space_volume" if summary.is_volume else "scan.space_folder"
    lines = [
        t(
            "scan.done",
            target=safe_text(shown),
            duration=format_duration(
                snapshot.duration_s, t("format.decimal_separator")
            ),
        ),
        t(
            "scan.counts",
            folders=_number(summary.folders),
            files=_number(summary.files),
            links=_number(summary.skipped_links),
        ),
        t(
            space_key,
            scanned=_size(snapshot.scanned_bytes),
            used=_size(snapshot.used_bytes),
            total=_size(snapshot.total_bytes),
            free=_size(snapshot.free_bytes),
        ),
    ]
    if snapshot.duplicate_bytes > 0:
        lines.append(t("scan.duplicates", size=_size(snapshot.duplicate_bytes)))
    if snapshot.system_files_bytes > 0:
        lines.append(t("scan.system_files", size=_size(snapshot.system_files_bytes)))
    if snapshot.unreadable_count:
        lines.append(t("scan.unreadable", count=_number(snapshot.unreadable_count)))
    if summary.unexplained is not None:
        lines.extend(
            _unexplained_lines(summary.unexplained, snapshot.is_admin, snapshot.volume)
        )
    lines.append(t("scan.saved", id=snapshot.id, nodes=_number(summary.nodes_saved)))
    return lines


def _report_lines(report: Report) -> list[str]:
    """Las líneas del informe: título, tabla de carpetas y espacio sin explicar."""
    snapshot = report.snapshot
    taken_at = snapshot.taken_at.astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        t(
            "report.title",
            target=safe_text(snapshot.volume),
            taken_at=taken_at,
            id=snapshot.id,
        )
    ]
    headers = [
        t("report.column_path"),
        t("report.column_size"),
        t("report.column_files"),
    ]
    rows = [
        [safe_text(node.path), _size(node.size_bytes), _number(node.n_files)]
        for node in report.top_folders
    ]
    lines.extend(format_table(headers, rows, align=("l", "r", "r")))
    lines.append(
        t(
            "report.shown",
            shown=_number(len(report.top_folders)),
            total=_number(report.total_folders),
        )
    )
    if report.unexplained is not None:
        lines.extend(
            _unexplained_lines(report.unexplained, snapshot.is_admin, snapshot.volume)
        )
    return lines


def _report_to_json(report: Report) -> dict[str, object]:
    """El informe como datos simples y sin traducir, para `--json`."""
    snapshot = report.snapshot
    unexplained = report.unexplained
    return {
        "target": snapshot.volume,
        "snapshot_id": snapshot.id,
        "taken_at": snapshot.taken_at.isoformat(),
        "is_admin": snapshot.is_admin,
        "total_bytes": snapshot.total_bytes,
        "used_bytes": snapshot.used_bytes,
        "free_bytes": snapshot.free_bytes,
        "scanned_bytes": snapshot.scanned_bytes,
        "duplicate_bytes": snapshot.duplicate_bytes,
        "system_files_bytes": snapshot.system_files_bytes,
        "unreadable_count": snapshot.unreadable_count,
        "total_folders": report.total_folders,
        "top_folders": [_node_to_json(node) for node in report.top_folders],
        "unexplained": None
        if unexplained is None
        else {
            "size_bytes": unexplained.size_bytes,
            "percent_of_used": unexplained.percent_of_used,
            "exceeds_threshold": unexplained.exceeds_threshold,
        },
    }


def _node_to_json(node: Node) -> dict[str, object]:
    return {
        "path": node.path,
        "size_bytes": node.size_bytes,
        "n_files": node.n_files,
        "depth": node.depth,
    }


def _unexplained_lines(
    unexplained: Unexplained, is_admin: bool, volume: str
) -> list[str]:
    """El espacio sin explicar y, si pasa el umbral, el aviso que corresponde.

    No se muestra nada si no sobra espacio: un resultado negativo no es un problema.
    """
    if unexplained.size_bytes <= 0:
        return []
    size = _size(unexplained.size_bytes)
    percent = format_decimal(unexplained.percent_of_used, t("format.decimal_separator"))
    lines = [t("scan.unexplained", size=size, percent=percent)]
    if unexplained.exceeds_threshold:
        key = (
            "scan.unexplained_warning_admin" if is_admin else "scan.unexplained_warning"
        )
        lines.append(t(key, size=size, volume=safe_text(volume)))
    return lines
