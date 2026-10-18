"""Caso de uso "crecimiento": compara dos fotos del mismo destino (T2.5).

Es de solo lectura y no vuelve a escanear: usa dos fotos ya guardadas. La más
reciente es "after"; "before" es la más reciente que sea de al menos `since` de
antigüedad respecto a "after". Si no se da un destino, usa el de la foto más
reciente de cualquiera que se haya escaneado antes (igual que `report`).
"""

import ntpath
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.adapters.store import Store, StoreError
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.growth import (
    CategoryGrowth,
    FolderGrowth,
    InvalidDuration,
    category_growth,
    folder_growth,
    new_large_files,
    parse_duration,
)
from discoclaro.domain.models import LargeFile, Snapshot, Volume
from discoclaro.domain.rules import Rule
from discoclaro.platform import windows
from discoclaro.services.scan_service import (
    DATABASE_NAME,
    ScanError,
    locate,
    normalize_target,
)

DEFAULT_SINCE = "7d"
DEFAULT_TOP_N = 20


class GrowthError(Exception):
    """No se pudo armar la comparación; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str) -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class Growth:
    """Lo que cambió entre dos fotos del mismo destino.

    `categories` es `None` si no se pidió con `--by-category` (puede ser una tupla
    vacía si se pidió y no hubo ninguna). `folders` nunca incluye la raíz del
    destino: siempre "crece" si algo de adentro crece, y no dice nada nuevo.
    """

    before: Snapshot
    after: Snapshot
    folders: tuple[FolderGrowth, ...]
    categories: tuple[CategoryGrowth, ...] | None
    new_large_files: tuple[LargeFile, ...]


def _resolved_name(target: str, volumes: Callable[[], list[Volume]]) -> str:
    """El nombre bajo el que se guardaría la foto de `target`: el volumen o la ruta."""
    try:
        volume, path = locate(normalize_target(target), volumes())
    except ScanError as error:
        raise GrowthError("growth_not_fixed_disk", error.target) from error
    return volume.name if ntpath.splitdrive(path)[1] == "\\" else path


def build_growth(
    target: str | None,
    store: Store,
    since: str = DEFAULT_SINCE,
    *,
    volumes: Callable[[], list[Volume]] = list_volumes,
    top_n: int = DEFAULT_TOP_N,
    by_category: bool = False,
    rules: Callable[[], Sequence[Rule]] = load_builtin_catalog,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
) -> Growth:
    """Compara la foto más reciente de `target` con la más vieja de al menos `since`.

    Lanza `GrowthError` si `since` no tiene un formato válido (`7d`, `24h`, `2w`), si
    no hay ninguna foto de `target` (o de ningún destino, si falta), o si la más
    vieja que hay no llega a esa antigüedad. `rules` y `environment` solo se llaman
    si se pide `by_category`, igual que en `report`.
    """
    try:
        span = parse_duration(since)
    except InvalidDuration as error:
        raise GrowthError("growth_invalid_since", str(error)) from error

    if target is None:
        snapshots = store.snapshots()
        if not snapshots:
            raise GrowthError("growth_no_snapshots", "")
        after = snapshots[0]
    else:
        name = _resolved_name(target, volumes)
        found = store.latest_snapshot(name)
        if found is None:
            raise GrowthError("growth_no_snapshot_for_target", target)
        after = found

    history = store.snapshots(after.volume)
    cutoff = after.taken_at - span
    before = next(
        (s for s in history if s.id != after.id and s.taken_at <= cutoff), None
    )
    if before is None:
        raise GrowthError("growth_not_enough_history", after.volume)

    before_nodes = store.nodes(before.id)
    after_nodes = store.nodes(after.id)

    folders = tuple(
        growth
        for growth in folder_growth(before_nodes, after_nodes)
        if growth.depth > 0
    )[: max(top_n, 0)]

    categories: tuple[CategoryGrowth, ...] | None = None
    if by_category:
        categories = category_growth(before_nodes, after_nodes, rules(), environment())[
            : max(top_n, 0)
        ]

    new_files = new_large_files(
        store.large_files(before.id), store.large_files(after.id)
    )[: max(top_n, 0)]

    return Growth(
        before=before,
        after=after,
        folders=folders,
        categories=categories,
        new_large_files=new_files,
    )


def run_growth(
    target: str | None,
    since: str = DEFAULT_SINCE,
    data_dir: Path | None = None,
    *,
    top_n: int = DEFAULT_TOP_N,
    by_category: bool = False,
) -> Growth:
    """Arma la comparación leyendo la base propia de discoclaro, sin volver a escanear.

    La base vive en `%LOCALAPPDATA%\\discoclaro`, o en `data_dir` si se da (para
    probar).
    """
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise GrowthError("growth_no_snapshots", "")
    try:
        store = Store.open_readonly(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise GrowthError("cannot_open_database", str(error)) from error
    with store:
        return build_growth(target, store, since, top_n=top_n, by_category=by_category)
