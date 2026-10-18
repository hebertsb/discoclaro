"""Política de retención de fotos (T2.9): pura, solo decide cuáles sobran.

No borra nada ni toca la base: `services/scan_service.py` llama a esto después de
guardar una foto nueva del mismo destino, y es quien borra lo que esta función diga.
Así la base no crece sin control (sección 5.3 del plan).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from discoclaro.domain.models import Snapshot

DEFAULT_RETENTION_DAYS = 30


def snapshots_to_delete(
    snapshots: Sequence[Snapshot], retention_days: int, now: datetime
) -> list[int]:
    """Los ids de las fotos de UN MISMO destino que ya no hace falta guardar.

    Se guardan todas las de los últimos `retention_days` días; de las más viejas,
    solo la más reciente de cada semana del calendario (año + número de semana ISO):
    el resto se puede borrar. Lanza `ValueError` si `retention_days` es negativo.
    """
    if retention_days < 0:
        raise ValueError("retention_days cannot be negative")
    cutoff = now - timedelta(days=retention_days)
    keep_ids = {snapshot.id for snapshot in snapshots if snapshot.taken_at >= cutoff}
    best_per_week: dict[tuple[int, int], Snapshot] = {}
    for snapshot in snapshots:
        if snapshot.taken_at >= cutoff:
            continue
        week = snapshot.taken_at.isocalendar()[:2]
        best = best_per_week.get(week)
        if best is None or snapshot.taken_at > best.taken_at:
            best_per_week[week] = snapshot
    keep_ids.update(snapshot.id for snapshot in best_per_week.values())
    return [snapshot.id for snapshot in snapshots if snapshot.id not in keep_ids]
