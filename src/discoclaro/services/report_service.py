"""Caso de uso "informe": las carpetas más grandes de una foto ya guardada.

Es de solo lectura y no vuelve a recorrer el disco: solo lee la base que dejó un
`scan` anterior. Si no se da un destino, usa la foto más reciente de cualquiera que
se haya escaneado antes.
"""

import ntpath
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.adapters.store import Store, StoreError
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.categories import CategoryTotal, category_totals
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.domain.rules import Rule
from discoclaro.domain.unexplained import (
    DEFAULT_THRESHOLD_PERCENT,
    Unexplained,
    unexplained_of,
)
from discoclaro.platform import windows
from discoclaro.services.scan_service import (
    DATABASE_NAME,
    ScanError,
    locate,
    normalize_target,
)

DEFAULT_TOP_N = 20


class ReportError(Exception):
    """No se pudo armar el informe; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str) -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class Report:
    """Lo que se muestra de una foto guardada: su resumen y sus carpetas más grandes.

    `by_category` es `None` si no se pidió (el modo normal, por carpeta); si se pidió
    con `--by-category`, trae los totales por categoría en vez de las carpetas.
    """

    snapshot: Snapshot
    top_folders: tuple[Node, ...]
    total_folders: int
    unexplained: Unexplained | None
    by_category: tuple[CategoryTotal, ...] | None = None


def _resolved_name(target: str, volumes: Callable[[], list[Volume]]) -> str:
    """El nombre bajo el que se guardaría la foto de `target`: el volumen o la ruta."""
    try:
        volume, path = locate(normalize_target(target), volumes())
    except ScanError as error:
        raise ReportError("report_not_fixed_disk", error.target) from error
    return volume.name if ntpath.splitdrive(path)[1] == "\\" else path


_report_cache: dict[tuple[int, int, float, bool], Report] = {}


def _pick_default_snapshot(snapshots: list[Snapshot]) -> Snapshot:
    volume_scans = [s for s in snapshots if s.is_volume_scan]
    return volume_scans[0] if volume_scans else snapshots[0]


def build_report(
    target: str | None,
    store: Store,
    *,
    volumes: Callable[[], list[Volume]] = list_volumes,
    top_n: int = DEFAULT_TOP_N,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
    by_category: bool = False,
    rules: Callable[[], Sequence[Rule]] = load_builtin_catalog,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
) -> Report:
    """El informe de la foto de `target`, o de la más reciente de todas si falta.

    Lanza `ReportError` si no hay ninguna foto guardada (de ese destino, o de ningún
    destino). No toca el disco si `target` es `None`. `rules` y `environment` solo se
    llaman cuando `by_category` es cierto (T2.4): leer el catálogo y las variables de
    entorno reales no hace falta para el informe normal, por carpeta.
    """
    if target is None:
        snapshots = store.snapshots()
        if not snapshots:
            raise ReportError("report_no_snapshots", "")
        snapshot = _pick_default_snapshot(snapshots)
    else:
        name = _resolved_name(target, volumes)
        found = store.latest_snapshot(name)
        if found is None:
            raise ReportError("report_no_snapshot_for_target", target)
        snapshot = found

    use_cache = (
        snapshot.id is not None
        and rules is load_builtin_catalog
        and environment is windows.environment_variables
    )
    cache_key = (snapshot.id or 0, top_n, threshold_percent, by_category)
    if use_cache and cache_key in _report_cache:
        return _report_cache[cache_key]

    nodes = store.nodes(snapshot.id)
    top = tuple(sorted(nodes, key=lambda node: node.size_bytes, reverse=True))
    by_category_totals = None
    if by_category:
        by_category_totals = category_totals(nodes, rules(), environment())[
            : max(top_n, 0)
        ]
    result = Report(
        snapshot=snapshot,
        top_folders=top[: max(top_n, 0)],
        total_folders=len(nodes),
        unexplained=unexplained_of(snapshot, threshold_percent),
        by_category=by_category_totals,
    )
    if use_cache:
        _report_cache[cache_key] = result
    return result


def run_report(
    target: str | None,
    data_dir: Path | None = None,
    *,
    top_n: int = DEFAULT_TOP_N,
    by_category: bool = False,
) -> Report:
    """Arma el informe leyendo la base propia de discoclaro, sin volver a escanear.

    La base vive en `%LOCALAPPDATA%\\discoclaro`, o en `data_dir` si se da (para
    probar).
    """
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise ReportError("report_no_snapshots", "")
    try:
        store = Store.open_readonly(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise ReportError("cannot_open_database", str(error)) from error
    with store:
        return build_report(target, store, top_n=top_n, by_category=by_category)
