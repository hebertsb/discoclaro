"""Servicio de límites y presupuestos para la vista Límites (T3.9).

Cruza la configuración del usuario (`config.toml`: límites por disco y
presupuestos por carpeta o categoría) con el estado real de los discos y
las fotos guardadas, incluyendo la estimación de días hasta llenarse
(`domain/forecast.py`).
"""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.config import load_user_config
from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.categories import category_totals
from discoclaro.domain.config import Config
from discoclaro.domain.forecast import days_until_full, trend_of
from discoclaro.domain.models import Volume
from discoclaro.domain.rules import Rule
from discoclaro.platform import windows
from discoclaro.services import volumes_service
from discoclaro.services.scan_service import DATABASE_NAME

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class VolumeLimitStatus:
    """Un disco frente a sus límites configurados y su pronóstico."""

    volume: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    warn_at_percent: float
    min_free_bytes: int | None
    days_until_full: float | None
    status: str  # "safe" | "review" | "exceeded"


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """Una categoría o carpeta frente a su presupuesto máximo en `config.toml`."""

    target: str
    kind: str  # "category" | "folder" | "unmeasured"
    used_bytes: int
    max_bytes: int
    used_percent: float
    status: str  # "safe" | "review" | "exceeded"


@dataclass(frozen=True, slots=True)
class LimitsOverview:
    """Resumen completo para la vista Límites (T3.9)."""

    volumes: tuple[VolumeLimitStatus, ...]
    budgets: tuple[BudgetStatus, ...]


def _volume_status(
    used_percent: float,
    warn_at_percent: float,
    free_bytes: int,
    min_free_bytes: int | None,
) -> str:
    if min_free_bytes is not None and free_bytes < min_free_bytes:
        return "exceeded"
    if used_percent >= warn_at_percent:
        return "exceeded" if used_percent >= 95.0 else "review"
    if used_percent >= max(warn_at_percent - 10.0, 70.0):
        return "review"
    return "safe"


def _budget_level(used_percent: float) -> str:
    if used_percent >= 100.0:
        return "exceeded"
    if used_percent >= 90.0:
        return "review"
    return "safe"


def run_limits_overview(
    data_dir: Path | None = None,
    config_dir: Path | None = None,
    *,
    load_config: Callable[[Path | None], Config] = load_user_config,
    list_volumes: Callable[[], list[Volume]] = volumes_service.run_volumes,
    rules: Callable[[], Sequence[Rule]] = load_builtin_catalog,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
) -> LimitsOverview:
    """Arma el estado de todos los discos y presupuestos configurados."""
    try:
        config = load_config(config_dir)
    except Exception:
        config = Config()

    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME

    forecasts: dict[str, float | None] = {}
    cat_totals: dict[str, int] = {}
    folder_totals: dict[str, int] = {}

    if database.is_file():
        try:
            with Store.open_readonly(database) as store:
                all_rules = rules()
                env = environment()
                for vol in list_volumes():
                    history = [
                        s for s in store.snapshots(vol.name) if s.is_volume_scan
                    ]
                    if history:
                        trend = trend_of(history)
                        forecasts[vol.name] = (
                            days_until_full(trend, vol.free_bytes) if trend else None
                        )
                        snap_id = history[0].id
                        disk_cat = directory / f"categories_detail_{snap_id}.json"
                        loaded_from_disk = False
                        if (
                            snap_id is not None
                            and disk_cat.is_file()
                            and rules is load_builtin_catalog
                            and environment is windows.environment_variables
                        ):
                            import json

                            try:
                                raw_cat = json.loads(
                                    disk_cat.read_text(encoding="utf-8")
                                )
                                for row in raw_cat.get("totals", []):
                                    c_name = row["category"]
                                    cat_totals[c_name] = cat_totals.get(
                                        c_name, 0
                                    ) + int(row["size_bytes"])
                                for item in raw_cat.get("items", []):
                                    folder_totals[item["path"].casefold()] = int(
                                        item["size_bytes"]
                                    )
                                loaded_from_disk = True
                            except Exception:
                                loaded_from_disk = False
                        if not loaded_from_disk:
                            nodes = store.nodes(snap_id)
                            for ct in category_totals(nodes, all_rules, env):
                                cat_totals[ct.category] = (
                                    cat_totals.get(ct.category, 0) + ct.size_bytes
                                )
                            for node in nodes:
                                folder_totals[node.path.casefold()] = node.size_bytes
        except (OSError, sqlite3.Error, StoreError):
            pass

    vol_statuses: list[VolumeLimitStatus] = []
    for vol in sorted(list_volumes(), key=lambda v: v.total_bytes, reverse=True):
        limit = config.volumes.get(vol.name)
        warn_at = limit.warn_at_percent if limit is not None else 85.0
        min_free = (
            round(limit.min_free_gb * GIB)
            if (limit is not None and limit.min_free_gb is not None)
            else None
        )
        used_pct = (
            (100.0 * vol.used_bytes / vol.total_bytes) if vol.total_bytes else 0.0
        )
        vol_statuses.append(
            VolumeLimitStatus(
                volume=vol.name,
                total_bytes=vol.total_bytes,
                used_bytes=vol.used_bytes,
                free_bytes=vol.free_bytes,
                used_percent=used_pct,
                warn_at_percent=warn_at,
                min_free_bytes=min_free,
                days_until_full=forecasts.get(vol.name),
                status=_volume_status(used_pct, warn_at, vol.free_bytes, min_free),
            )
        )

    budget_statuses: list[BudgetStatus] = []
    for name, budget in config.budgets.items():
        max_b = round(budget.max_gb * GIB)
        if name in cat_totals:
            used_b = cat_totals[name]
            kind = "category"
        elif name.casefold() in folder_totals:
            used_b = folder_totals[name.casefold()]
            kind = "folder"
        else:
            used_b = 0
            kind = "unmeasured"
        pct = (100.0 * used_b / max_b) if max_b > 0 else 0.0
        budget_statuses.append(
            BudgetStatus(
                target=name,
                kind=kind,
                used_bytes=used_b,
                max_bytes=max_b,
                used_percent=pct,
                status=_budget_level(pct),
            )
        )

    # Si hay categorías medidas en disco que aún no tienen un budget explícito en
    # config.toml, también las incluimos con un presupuesto sugerido para que el
    # usuario vea sus categorías reales y pueda fijar su propio límite con 1 clic.
    for cat_name, used_b in sorted(
        cat_totals.items(), key=lambda item: item[1], reverse=True
    ):
        if cat_name in config.budgets:
            continue
        suggested_gb = max(round((used_b / GIB) * 1.5, 1), 5.0)
        max_b = round(suggested_gb * GIB)
        pct = (100.0 * used_b / max_b) if max_b > 0 else 0.0
        budget_statuses.append(
            BudgetStatus(
                target=cat_name,
                kind="suggested",
                used_bytes=used_b,
                max_bytes=max_b,
                used_percent=pct,
                status=_budget_level(pct),
            )
        )

    return LimitsOverview(
        volumes=tuple(vol_statuses),
        budgets=tuple(budget_statuses),
    )
