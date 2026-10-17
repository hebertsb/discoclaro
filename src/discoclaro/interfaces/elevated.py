"""Punto de entrada FIJO del escaneo con permisos de administrador.

Solo lo lanza `discoclaro scan --admin` (ver `services.elevation_service`):

    <python> -I -m discoclaro.interfaces.elevated scan <X:>

Acepta exactamente eso, nada más: ni comandos ni rutas libres. Corre con permisos de
administrador y no se fía de quien lo lanzó, así que revalida aquí dentro: que de
verdad esté elevado, que el argumento sea una letra de unidad y que el disco sea fijo.
La carpeta de datos la calcula él, no la recibe. Solo lee el disco; lo único que escribe
es la base de discoclaro y un resultado con campos fijos.
"""

import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from discoclaro.platform import windows
from discoclaro.services import elevation_service, scan_service
from discoclaro.services.elevation_service import (
    EXIT_BAD_ARGUMENTS,
    EXIT_FAILED,
    EXIT_NOT_ELEVATED,
    EXIT_OK,
    MAX_DETAIL_CHARS,
    REPORTABLE_KINDS,
    RESULT_VERSION,
)
from discoclaro.services.scan_service import ScanError

_VOLUME = re.compile(r"[A-Za-z]:")


def parse_arguments(argv: Sequence[str]) -> str | None:
    """La letra de unidad (`C:`) si los argumentos son exactamente `scan <X:>`."""
    if len(argv) == 2 and argv[0] == "scan" and _VOLUME.fullmatch(argv[1]):
        return argv[1]
    return None


def main(
    argv: Sequence[str] | None = None,
    *,
    is_admin: Callable[[], bool] = windows.is_admin,
    data_dir: Callable[[], Path] = windows.default_data_dir,
    scan: Callable[[str, Path], scan_service.ScanSummary] = scan_service.run_scan,
    write: Callable[[Path, dict[str, object]], None] = elevation_service.write_result,
) -> int:
    """Escanea un volumen y deja el resultado; devuelve uno de los códigos `EXIT_*`."""
    volume = parse_arguments(sys.argv[1:] if argv is None else argv)
    if volume is None:
        return EXIT_BAD_ARGUMENTS
    if not is_admin():
        return EXIT_NOT_ELEVATED
    try:
        directory = data_dir()
    except OSError:
        return EXIT_FAILED

    result: dict[str, object] = {"version": RESULT_VERSION}
    try:
        summary = scan(volume, directory)
    except ScanError as error:
        kind = error.kind if error.kind in REPORTABLE_KINDS else "unexpected"
        result |= {"ok": False, "kind": kind, "detail": error.target[:MAX_DETAIL_CHARS]}
    except Exception as error:  # noqa: BLE001 - el usuario debe saber qué pasó
        result |= {"ok": False, "kind": "unexpected", "detail": type(error).__name__}
    else:
        result |= {
            "ok": True,
            "snapshot_id": summary.snapshot.id,
            "folders": summary.folders,
            "files": summary.files,
            "skipped_links": summary.skipped_links,
            "nodes_saved": summary.nodes_saved,
        }
    try:
        write(directory, result)
    except OSError:
        return EXIT_FAILED
    return EXIT_OK if result["ok"] else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
