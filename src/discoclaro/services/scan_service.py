"""Caso de uso "escanear": junta volúmenes, escáner, archivos de sistema y base.

Recibe los adaptadores como parámetros, con los reales por defecto, para probarlo sin
tocar discos. No sabe si lo llama la CLI o la API.

Un destino que es la raíz de un volumen (`C:`) es un escaneo completo: sus archivos de
sistema se miden aparte y la foto se guarda con el nombre del volumen. Cualquier otra
ruta es un escaneo parcial: la foto se guarda con la propia ruta como nombre y con las
cifras del volumen que la contiene, para que nunca se mezcle con las de un volumen.
"""

import dataclasses
import ntpath
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from discoclaro.adapters.scanner import scan_tree
from discoclaro.adapters.store import Store, StoreError
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Node, ScanResult, Snapshot, SystemFile, Volume
from discoclaro.domain.unexplained import (
    DEFAULT_THRESHOLD_PERCENT,
    Unexplained,
    unexplained_of,
)
from discoclaro.platform import windows

# Control de tamaño de la base (sección 5.3 del plan): solo se guardan las carpetas de
# poca profundidad o grandes. La raíz siempre entra.
KEEP_MAX_DEPTH = 3
KEEP_MIN_BYTES = 50 * 1024 * 1024

DATABASE_NAME = "discoclaro.db"
_BARE_DRIVE = re.compile(r"[A-Za-z]:")


class ScanError(Exception):
    """Un escaneo que no se puede hacer; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str) -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Lo que dejó un escaneo, para mostrárselo al usuario."""

    target: str
    is_volume: bool
    snapshot: Snapshot
    folders: int
    files: int
    skipped_links: int
    system_files: tuple[SystemFile, ...]
    nodes_saved: int
    unexplained: Unexplained | None


def keep_node(node: Node) -> bool:
    """Si una carpeta se guarda en la foto: poco profunda o grande."""
    return node.depth <= KEEP_MAX_DEPTH or node.size_bytes >= KEEP_MIN_BYTES


def normalize_target(target: str) -> str:
    """Ruta absoluta y normalizada; `C:` sin barra se entiende como la raíz de C.

    En Windows, `C:` a secas significa "la carpeta actual del disco C", que casi nunca
    es lo que se quiere al escribir un volumen.
    """
    if _BARE_DRIVE.fullmatch(target):
        target += "\\"
    return ntpath.abspath(target)


def locate(path: str, known: list[Volume]) -> tuple[Volume, str]:
    """El volumen de `path` y la ruta con la unidad como la nombra el sistema.

    Lanza `ScanError` si la ruta no está en uno de los discos fijos conocidos.
    """
    drive, rest = ntpath.splitdrive(path)
    volume = next((v for v in known if v.name.lower() == drive.lower()), None)
    if volume is None:
        raise ScanError("not_fixed_disk", path)
    return volume, volume.name + rest


def scan_target(
    target: str,
    store: Store,
    *,
    volumes: Callable[[], list[Volume]] = list_volumes,
    scanner: Callable[..., ScanResult] = scan_tree,
    system_files: Callable[[str], list[SystemFile]] = windows.measure_system_files,
    is_admin: Callable[[], bool] = windows.is_admin,
    is_folder: Callable[[str], bool] = os.path.isdir,
    canonical_path: Callable[[str], str] = os.path.realpath,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    keep: Callable[[Node], bool] = keep_node,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
) -> ScanSummary:
    """Escanea `target`, guarda la foto en `store` y devuelve el resumen.

    Lanza `ScanError` si el destino no está en un disco fijo o no es una carpeta. Si el
    escaneo falla o se interrumpe, no se guarda nada.
    """
    known = volumes()
    volume, path = locate(normalize_target(target), known)
    if not is_folder(path):
        raise ScanError("not_a_folder", path)
    # Con las mayúsculas reales y resolviendo los enlaces de la propia raíz, la misma
    # carpeta tiene siempre el mismo nombre en la base, la escriban como la escriban.
    # Si el resultado cae fuera de los discos fijos, se rechaza.
    volume, path = locate(canonical_path(path), known)
    is_volume = ntpath.splitdrive(path)[1] == "\\"

    taken_at = now()
    started = monotonic()
    try:
        result = scanner(
            path, skip_root_files=windows.SYSTEM_FILE_NAMES if is_volume else ()
        )
    except (FileNotFoundError, NotADirectoryError) as error:
        raise ScanError("not_a_folder", path) from error
    found = tuple(system_files(volume.root)) if is_volume else ()
    duration = monotonic() - started

    snapshot = Snapshot(
        taken_at=taken_at,
        volume=volume.name if is_volume else path,
        total_bytes=volume.total_bytes,
        used_bytes=volume.used_bytes,
        free_bytes=volume.free_bytes,
        scanned_bytes=result.scanned_bytes,
        system_files_bytes=sum(file.size_bytes for file in found),
        unreadable_bytes=0,
        unreadable_count=result.unreadable_count,
        duration_s=duration,
        is_admin=is_admin(),
        duplicate_bytes=result.duplicate_bytes,
    )
    kept = [node for node in result.nodes if keep(node)]
    snapshot_id = store.save_snapshot(snapshot, kept)
    saved = dataclasses.replace(snapshot, id=snapshot_id)
    return ScanSummary(
        target=path,
        is_volume=is_volume,
        snapshot=saved,
        folders=len(result.nodes),
        files=result.n_files,
        skipped_links=result.skipped_links,
        system_files=found,
        nodes_saved=len(kept),
        unexplained=unexplained_of(saved, threshold_percent),
    )


def run_scan(target: str, data_dir: Path | None = None) -> ScanSummary:
    """Escanea `target` y guarda la foto en la base propia de discoclaro.

    La base vive en `%LOCALAPPDATA%\\discoclaro`, o en `data_dir` si se da (para
    probar). Se crea la carpeta si falta.
    """
    try:
        directory = windows.default_data_dir() if data_dir is None else data_dir
        directory.mkdir(parents=True, exist_ok=True)
        store = Store.open(directory / DATABASE_NAME)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise ScanError("cannot_open_database", str(error)) from error
    with store:
        return scan_target(target, store)
