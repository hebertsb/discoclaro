"""Línea de comandos de discoclaro.

Capa de entrada delgada: lee los argumentos y, cuando existan los servicios,
solo los llama. Aquí no va lógica.
"""

import argparse
from collections.abc import Sequence

from discoclaro import __version__
from discoclaro.i18n import t

COMMANDS = ("scan", "report", "growth")


def build_parser() -> argparse.ArgumentParser:
    """Arma el analizador de argumentos: `--version` y los subcomandos vacíos."""
    parser = argparse.ArgumentParser(
        prog="discoclaro", description=t("cli.description")
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        subparsers.add_parser(name, help=t(f"cli.commands.{name}"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del comando `discoclaro`; devuelve el código de salida."""
    build_parser().parse_args(argv)
    return 0
