"""Caso de uso "resumen": una tarjeta por disco para el dashboard (T3.3).

Junta, por cada disco real del equipo, su espacio usado/libre de ahora mismo, el
espacio no explicado y los días hasta llenarse de la foto más reciente que tenga, y
lo que más creció desde la foto anterior. No corre los inspectores de programas
(T2C.1) ni el plan por niveles (T2C.7): esos son más lentos (llaman a Docker,
Steam…) y el dashboard los pide aparte, sin bloquear el resumen del disco.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.forecast import days_until_full, trend_of
from discoclaro.domain.growth import FolderGrowth
from discoclaro.domain.models import Snapshot, Volume
from discoclaro.domain.unexplained import (
    DEFAULT_THRESHOLD_PERCENT,
    Unexplained,
    unexplained_of,
)
from discoclaro.platform import windows
from discoclaro.services import volumes_service
from discoclaro.services.growth_service import DEFAULT_SINCE, GrowthError, build_growth
from discoclaro.services.scan_service import DATABASE_NAME


class SummaryError(Exception):
    """No se pudo armar el resumen; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class VolumeSummary:
    """El resumen de un disco real: `snapshot` es `None` si nunca se escaneó."""

    volume: Volume
    snapshot: Snapshot | None
    unexplained: Unexplained | None
    days_until_full: float | None
    top_growth: FolderGrowth | None


@dataclass(frozen=True, slots=True)
class Summary:
    """El resumen de todos los discos, de mayor a menor tamaño total."""

    volumes: tuple[VolumeSummary, ...]


# Caché corta para que el Resumen del dashboard responda al instante (<2 ms).
SUMMARY_CACHE_TTL_S = 15.0
_summary_cache: dict[str, tuple[float, Summary]] = {}


def _volume_summary(
    volume: Volume,
    store: Store,
    *,
    since: str,
    threshold_percent: float,
) -> VolumeSummary:
    """El resumen de `volume`, leyendo su historia real de fotos de `store`."""
    history = [
        snapshot for snapshot in store.snapshots(volume.name) if snapshot.is_volume_scan
    ]
    if not history:
        return VolumeSummary(volume, None, None, None, None)
    snapshot = history[0]

    unexplained = unexplained_of(snapshot, threshold_percent)
    trend = trend_of(history)
    forecast_days = days_until_full(trend, volume.free_bytes) if trend else None

    top_growth = None
    try:
        growth = build_growth(volume.name, store, since)
    except GrowthError:
        growth = None
    if growth is not None and growth.folders:
        top_growth = growth.folders[0]

    return VolumeSummary(volume, snapshot, unexplained, forecast_days, top_growth)


def build_summary(
    volumes: list[Volume],
    store: Store,
    *,
    since: str = DEFAULT_SINCE,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
) -> Summary:
    """El resumen de `volumes`, leyendo la historia real de fotos de `store`."""
    ordered = sorted(volumes, key=lambda volume: volume.total_bytes, reverse=True)
    return Summary(
        volumes=tuple(
            _volume_summary(
                volume, store, since=since, threshold_percent=threshold_percent
            )
            for volume in ordered
        )
    )


def run_summary(
    data_dir: Path | None = None,
    *,
    list_volumes: Callable[[], list[Volume]] = volumes_service.run_volumes,
    since: str = DEFAULT_SINCE,
) -> Summary:
    """Arma el resumen leyendo la base propia de discoclaro, sin volver a escanear.

    Si todavía no hay ninguna base (nunca se escaneó nada), cada disco sale con su
    espacio real de ahora mismo y el resto de los campos en `None`: eso no es un
    error, es el estado normal de una instalación nueva.
    """
    use_cache = (
        data_dir is None
        and list_volumes is volumes_service.run_volumes
        and since == DEFAULT_SINCE
    )
    now_mono = time.monotonic()
    if use_cache and "default" in _summary_cache:
        cached_at, cached_summary = _summary_cache["default"]
        if now_mono - cached_at < SUMMARY_CACHE_TTL_S:
            return cached_summary

    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    volumes = sorted(
        list_volumes(), key=lambda volume: volume.total_bytes, reverse=True
    )
    if not database.is_file():
        return Summary(
            volumes=tuple(
                VolumeSummary(volume, None, None, None, None) for volume in volumes
            )
        )
    try:
        store = Store.open_readonly(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise SummaryError("cannot_open_database", str(error)) from error
    with store:
        result = build_summary(volumes, store, since=since)
    if use_cache:
        _summary_cache["default"] = (now_mono, result)
    return result
