"""Caso de uso "chequeo" (T2.7): evalúa los avisos contra las fotos ya guardadas.

Es de solo lectura sobre el disco (no vuelve a escanear) pero sí escribe en la propia
base de discoclaro: cada aviso que dispara queda guardado como avisado recién ahora,
para no repetirlo antes de `ALERT_COOLDOWN`. Como marca el flujo A del plan
(`discoclaro check ─► check_service ─► adapters.notifier`), este servicio es quien
notifica, con el texto ya traducido; `notify` se puede inyectar para probar sin
tocar Windows.
"""

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from discoclaro.adapters import notifier
from discoclaro.adapters.config import load_user_config
from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.alerts import (
    Alert,
    check_big_new_file,
    check_budget,
    check_unexplained_grew,
    check_volume_low_free,
    check_volume_near_limit,
)
from discoclaro.domain.categories import category_totals
from discoclaro.domain.config import Config
from discoclaro.domain.formatting import format_bytes, format_decimal, safe_text
from discoclaro.domain.growth import new_large_files
from discoclaro.domain.models import LARGE_FILE_MIN_BYTES, Snapshot
from discoclaro.domain.rules import Rule
from discoclaro.domain.unexplained import unexplained_of
from discoclaro.i18n import t
from discoclaro.platform import windows
from discoclaro.services.scan_service import DATABASE_NAME

ALERT_COOLDOWN = timedelta(hours=24)
GIB = 1024**3


class CheckError(Exception):
    """No se pudo evaluar los avisos; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


def _bytes(gb: float) -> int:
    return round(gb * GIB)


def _optional_bytes(gb: float | None) -> int | None:
    return None if gb is None else _bytes(gb)


def evaluate(
    store: Store,
    config: Config,
    *,
    rules: Callable[[], Sequence[Rule]] = load_builtin_catalog,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
) -> list[Alert]:
    """Los avisos que disparan ahora mismo, sin filtrar por los que ya se avisaron.

    `rules` y `environment` solo se llaman si hay presupuestos configurados, igual
    que en `report`/`growth`. No incluye los tipos apagados en `config.disabled_alerts`.
    """
    targets = sorted({snapshot.volume for snapshot in store.snapshots()})
    alerts: list[Alert] = []
    for target in targets:
        latest = store.latest_snapshot(target)
        if latest is None:
            continue
        alerts += _volume_alerts(latest, config)
        alerts += _budget_alerts(latest, store, config, rules, environment)
        alerts += _growth_alerts(target, store, config)
    return [alert for alert in alerts if config.is_alert_enabled(alert.kind)]


def _volume_alerts(snapshot: Snapshot, config: Config) -> list[Alert]:
    limit = config.volumes.get(snapshot.volume)
    if limit is None:
        return []
    alerts = []
    low_free = check_volume_low_free(
        snapshot.volume, snapshot.free_bytes, _optional_bytes(limit.min_free_gb)
    )
    if low_free is not None:
        alerts.append(low_free)
    used_percent = (
        100 * snapshot.used_bytes / snapshot.total_bytes
        if snapshot.total_bytes
        else 0.0
    )
    near_limit = check_volume_near_limit(
        snapshot.volume, used_percent, limit.warn_at_percent
    )
    if near_limit is not None:
        alerts.append(near_limit)
    return alerts


def _budget_alerts(
    snapshot: Snapshot,
    store: Store,
    config: Config,
    rules: Callable[[], Sequence[Rule]],
    environment: Callable[[], Mapping[str, str]],
) -> list[Alert]:
    if not config.budgets:
        return []
    nodes = store.nodes(snapshot.id)
    totals = {
        total.category: total.size_bytes
        for total in category_totals(nodes, rules(), environment())
    }
    by_path = {node.path.casefold(): node.size_bytes for node in nodes}
    alerts = []
    for name, budget in config.budgets.items():
        if name in totals:
            current_bytes = totals[name]
        elif name.casefold() in by_path:
            current_bytes = by_path[name.casefold()]
        else:
            continue
        alert = check_budget(name, current_bytes, _bytes(budget.max_gb))
        if alert is not None:
            alerts.append(alert)
    return alerts


def _growth_alerts(target: str, store: Store, config: Config) -> list[Alert]:
    history = store.snapshots(target)
    if len(history) < 2:
        return []
    after, before = history[0], history[1]
    alerts = []
    file_threshold = _optional_bytes(config.growth.new_file_gb) or LARGE_FILE_MIN_BYTES
    for file in new_large_files(
        store.large_files(before.id), store.large_files(after.id)
    ):
        alert = check_big_new_file(file.path, file.size_bytes, file_threshold)
        if alert is not None:
            alerts.append(alert)
    before_unexplained = unexplained_of(before)
    after_unexplained = unexplained_of(after)
    if before_unexplained is not None and after_unexplained is not None:
        grown = after_unexplained.size_bytes - before_unexplained.size_bytes
        alert = check_unexplained_grew(
            target, grown, _optional_bytes(config.growth.max_growth_gb)
        )
        if alert is not None:
            alerts.append(alert)
    return alerts


def format_alert_text(alert: Alert) -> tuple[str, str]:
    """El título y el mensaje ya traducidos de un aviso.

    Lo usa `run_check` para notificar, y la CLI para mostrar lo mismo en su tabla.
    """
    decimal = t("format.decimal_separator")
    if alert.measured_percent is not None:
        measured = format_decimal(alert.measured_percent, decimal)
        limit = format_decimal(alert.limit_percent, decimal)
    else:
        measured = format_bytes(alert.measured_bytes, decimal)
        limit = format_bytes(alert.limit_bytes, decimal)
    title = t(f"alerts.title_{alert.kind}")
    message = t(
        f"alerts.message_{alert.kind}",
        target=safe_text(alert.target),
        measured=measured,
        limit=limit,
    )
    return title, message


def run_check(
    data_dir: Path | None = None,
    config_dir: Path | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    notify: Callable[[str, str], None] = notifier.send_notification,
) -> list[Alert]:
    """Evalúa los avisos, notifica los que correspondan y los devuelve.

    Cada aviso que se devuelve queda guardado como avisado justo ahora, para no
    repetirlo antes de `ALERT_COOLDOWN`. Lanza `CheckError` si todavía no hay
    ninguna foto guardada.
    """
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise CheckError("check_no_snapshots")
    try:
        store = Store.open(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise CheckError("cannot_open_database", str(error)) from error
    with store:
        config = load_user_config(config_dir)
        candidates = evaluate(store, config)
        moment = now()
        due = []
        for alert in candidates:
            last = store.last_alert_at(alert.kind, alert.target)
            if last is not None and moment - last < ALERT_COOLDOWN:
                continue
            store.record_alert(alert.kind, alert.target, moment)
            notify(*format_alert_text(alert))
            due.append(alert)
        return due
