"""Línea de comandos de discoclaro.

Capa de entrada delgada: lee los argumentos, llama a un servicio e imprime el
resultado con los textos del catálogo. Aquí no va lógica de negocio.
"""

import argparse
import sys
from collections.abc import Sequence

from discoclaro import __version__
from discoclaro.domain.formatting import (
    format_bytes,
    format_duration,
    format_int,
    safe_text,
)
from discoclaro.i18n import t
from discoclaro.services import scan_service
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del comando `discoclaro`; devuelve el código de salida."""
    args = build_parser().parse_args(argv)
    _make_output_robust()
    if args.command == "scan":
        return _scan(args.target)
    return 0


def _make_output_robust() -> None:
    """Evita que un carácter que la consola no sabe escribir tumbe el programa."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


def _scan(target: str) -> int:
    """Escanea `target`, guarda la foto y muestra el resumen."""
    try:
        summary = scan_service.run_scan(target)
    except ScanError as error:
        message = t(f"errors.{error.kind}", target=safe_text(error.target))
        print(message, file=sys.stderr)
        return 1
    for line in _summary_lines(summary):
        print(line)
    return 0


def _summary_lines(summary: ScanSummary) -> list[str]:
    """Las líneas del resumen de un escaneo, en el idioma actual."""
    decimal = t("format.decimal_separator")
    thousands = t("format.thousands_separator")

    def size(value: int) -> str:
        return format_bytes(value, decimal)

    def number(value: int) -> str:
        return format_int(value, thousands)

    snapshot = summary.snapshot
    shown = snapshot.volume if summary.is_volume else summary.target
    space_key = "scan.space_volume" if summary.is_volume else "scan.space_folder"
    lines = [
        t(
            "scan.done",
            target=safe_text(shown),
            duration=format_duration(snapshot.duration_s, decimal),
        ),
        t(
            "scan.counts",
            folders=number(summary.folders),
            files=number(summary.files),
            links=number(summary.skipped_links),
        ),
        t(
            space_key,
            scanned=size(snapshot.scanned_bytes),
            used=size(snapshot.used_bytes),
            total=size(snapshot.total_bytes),
            free=size(snapshot.free_bytes),
        ),
    ]
    if summary.system_files:
        lines.append(t("scan.system_files", size=size(snapshot.system_files_bytes)))
    if snapshot.unreadable_count:
        lines.append(t("scan.unreadable", count=number(snapshot.unreadable_count)))
    lines.append(t("scan.saved", id=snapshot.id, nodes=number(summary.nodes_saved)))
    return lines
