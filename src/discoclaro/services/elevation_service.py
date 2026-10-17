"""Escaneo con permisos de administrador (`discoclaro scan --admin`), lado del usuario.

Este módulo corre SIN permisos. Valida el pedido, le pide a Windows la ventana de
permisos para lanzar el punto de entrada fijo (`discoclaro.interfaces.elevated`) y lee
lo que ese proceso dejó. Nunca ejecuta algo que venga de un texto: la orden es siempre
la misma y solo cambia la letra de la unidad, que se valida contra los discos fijos.

Protocolo con el proceso elevado:

- Orden fija: `<python> -I -m discoclaro.interfaces.elevated scan <X:>`.
- El resultado vuelve en un JSON de campos fijos, dentro de la carpeta de datos del
  usuario. Aquí se valida estrictamente y lo que cuenta se confirma con la base, que se
  abre en solo lectura.
- El proceso elevado termina con alguno de los códigos `EXIT_*`.
"""

import json
import ntpath
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from discoclaro.adapters import package_integrity
from discoclaro.adapters.package_integrity import IntegrityCheck
from discoclaro.adapters.store import Store, StoreError
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Volume
from discoclaro.domain.unexplained import unexplained_of
from discoclaro.platform import windows
from discoclaro.services import scan_service
from discoclaro.services.scan_service import DATABASE_NAME, ScanError, ScanSummary

ENTRY_MODULE = "discoclaro.interfaces.elevated"
RESULT_NAME = "elevated-scan-result.json"
RESULT_VERSION = 1
MAX_RESULT_BYTES = 16 * 1024
MAX_DETAIL_CHARS = 300
MAX_COUNT = 10**12
DEFAULT_TIMEOUT_S = 3600.0
PACKAGE_NAME = "discoclaro"
MAX_SHOWN_MISMATCHES = 3

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_ARGUMENTS = 2
EXIT_NOT_ELEVATED = 3

# Errores que el proceso elevado puede informar; cualquier otro llega como inesperado.
REPORTABLE_KINDS = frozenset(
    {
        "not_fixed_disk",
        "not_a_folder",
        "cannot_open_database",
        "unsafe_data_location",
        "unexpected",
    }
)
_COUNT_FIELDS = ("snapshot_id", "folders", "files", "skipped_links", "nodes_saved")


def _check_installed_integrity() -> IntegrityCheck:
    """Compara los archivos instalados de discoclaro con los de su `RECORD` (T1.9)."""
    return package_integrity.check_installed_files(PACKAGE_NAME)


def _describe_mismatches(mismatches: tuple[str, ...]) -> str:
    """Las primeras rutas que no coinciden, para no desbordar la pantalla con todas."""
    shown = ", ".join(mismatches[:MAX_SHOWN_MISMATCHES])
    return shown + ("…" if len(mismatches) > MAX_SHOWN_MISMATCHES else "")


class ResultError(Exception):
    """El archivo de resultado falta, es demasiado grande o no tiene el formato fijo."""


def write_result(directory: Path, result: dict[str, object]) -> None:
    """Escribe el resultado en la carpeta de datos, con campos fijos.

    Revalida justo antes de escribir que ni la carpeta ni el archivo sean un enlace o
    tengan otro nombre (T1.9): si algo no cuadra, no escribe nada y deja que el
    `OSError` suba (`UnsafeDataLocation` es uno).
    """
    path = directory / RESULT_NAME
    windows.ensure_safe_to_write(directory)
    windows.ensure_safe_to_write(path)
    payload = json.dumps(result, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")


def read_result(directory: Path) -> dict[str, object]:
    """Lee y valida el resultado que dejó el proceso elevado.

    Lanza `ResultError` si falta, pesa demasiado, no es JSON o no cumple el formato.
    """
    try:
        with (directory / RESULT_NAME).open("rb") as handle:
            raw = handle.read(MAX_RESULT_BYTES + 1)
    except OSError as error:
        raise ResultError("the result file cannot be read") from error
    if len(raw) > MAX_RESULT_BYTES:
        raise ResultError("the result file is too big")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise ResultError("the result file is not valid JSON") from error
    return _validate(data)


def _is_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_COUNT


def _validate(data: object) -> dict[str, object]:
    """Exige exactamente los campos y tipos del protocolo; nada más."""
    if not isinstance(data, dict) or data.get("version") != RESULT_VERSION:
        raise ResultError("unknown result format")
    if data.get("ok") is True:
        fields = {"version", "ok", *_COUNT_FIELDS}
        if set(data) != fields or not all(_is_count(data[f]) for f in _COUNT_FIELDS):
            raise ResultError("malformed success result")
        if data["snapshot_id"] < 1:
            raise ResultError("malformed success result")
    elif data.get("ok") is False:
        detail = data.get("detail")
        if (
            set(data) != {"version", "ok", "kind", "detail"}
            or data["kind"] not in REPORTABLE_KINDS
            or not isinstance(detail, str)
            or len(detail) > MAX_DETAIL_CHARS
        ):
            raise ResultError("malformed failure result")
    else:
        raise ResultError("malformed result")
    return data


def run_admin_scan(
    target: str,
    *,
    data_dir: Path | None = None,
    runner: Callable[..., int] = windows.run_elevated,
    executable: str = sys.executable,
    volumes: Callable[[], list[Volume]] = list_volumes,
    is_admin: Callable[[], bool] = windows.is_admin,
    on_elevate: Callable[[str, str], None] | None = None,
    on_integrity_warning: Callable[[str], None] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    check_integrity: Callable[[], IntegrityCheck] = _check_installed_integrity,
    writable: Callable[[Path], bool] = package_integrity.is_writable_by_the_user,
) -> ScanSummary:
    """Escanea un volumen completo con permisos de administrador.

    Solo acepta volúmenes (`C:`), nunca carpetas. Si este proceso ya tiene permisos,
    escanea aquí mismo (ya es tarde para esta comprobación: lo que corre es lo que
    hay). Si no, antes de pedir nada compara lo instalado con su `RECORD` (T1.9): si
    algo no coincide, se niega a pedir el permiso (`ScanError` con clave
    `integrity_failed`) y nunca llega a llamar a Windows. Una instalación editable (la
    de desarrollo) no tiene con qué comparar sus archivos fuente y no se bloquea por
    eso, pero si además la carpeta del paquete se puede escribir, `on_integrity_warning`
    recibe esa carpeta antes de pedir el permiso. `on_elevate` recibe el volumen y la
    orden exacta, ya con sus comillas, justo antes de pedir el permiso.
    """
    volume = _volume_of(target, volumes())
    if is_admin():
        return scan_service.run_scan(volume.name, data_dir)

    check = check_integrity()
    if not check.ok:
        raise ScanError("integrity_failed", _describe_mismatches(check.mismatches))
    if (
        not check.editable
        and on_integrity_warning is not None
        and writable(check.base_dir)
    ):
        on_integrity_warning(str(check.base_dir))

    try:
        directory = windows.default_data_dir() if data_dir is None else data_dir
        directory.mkdir(parents=True, exist_ok=True)
        _forget_result(directory)
    except OSError as error:
        raise ScanError("cannot_open_database", str(error)) from error

    arguments = ["-I", "-m", ENTRY_MODULE, "scan", volume.name]
    if on_elevate is not None:
        on_elevate(volume.name, windows.command_line([executable, *arguments]))
    try:
        code = runner(executable, arguments, os.path.dirname(executable), timeout_s)
    except windows.ElevationDeclined as error:
        raise ScanError("elevation_declined", volume.name) from error
    except TimeoutError as error:
        raise ScanError("elevation_timeout", volume.name) from error
    except OSError as error:
        raise ScanError("elevation_failed", str(error)) from error

    if code not in (EXIT_OK, EXIT_FAILED):
        raise ScanError("elevation_failed", f"{code}")
    try:
        result = read_result(directory)
    except ResultError as error:
        raise ScanError("elevation_no_result", volume.name) from error
    finally:
        _forget_result(directory)
    if result["ok"] is not True:
        raise ScanError(str(result["kind"]), str(result["detail"]))
    return _summary_from_database(directory, volume, result)


def _volume_of(target: str, known: list[Volume]) -> Volume:
    """El volumen que nombra `target`; lanza `ScanError` si no es un volumen fijo."""
    path = scan_service.normalize_target(target)
    if ntpath.splitdrive(path)[1] != "\\":
        raise ScanError("admin_needs_volume", path)
    volume, _ = scan_service.locate(path, known)
    return volume


def _forget_result(directory: Path) -> None:
    """Borra el resultado, para no leer uno viejo o puesto de antemano."""
    (directory / RESULT_NAME).unlink(missing_ok=True)


def _summary_from_database(
    directory: Path, volume: Volume, result: dict[str, object]
) -> ScanSummary:
    """Arma el resumen con la foto que dejó el proceso elevado, leída de la base."""
    try:
        with Store.open_readonly(directory / DATABASE_NAME) as store:
            snapshot = store.get_snapshot(int(result["snapshot_id"]))
    except (OSError, sqlite3.Error, StoreError) as error:
        raise ScanError("cannot_open_database", str(error)) from error
    if snapshot is None or snapshot.volume != volume.name or not snapshot.is_admin:
        raise ScanError("elevation_no_result", volume.name)
    return ScanSummary(
        target=volume.root,
        is_volume=True,
        snapshot=snapshot,
        folders=int(result["folders"]),
        files=int(result["files"]),
        skipped_links=int(result["skipped_links"]),
        system_files=(),
        nodes_saved=int(result["nodes_saved"]),
        unexplained=unexplained_of(snapshot),
    )
