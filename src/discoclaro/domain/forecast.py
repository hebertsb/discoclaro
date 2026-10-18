"""Estimación de cuántos días faltan para llenar un disco (T2.10): pura.

Es una extrapolación lineal de fotos recientes del MISMO disco, nunca una promesa: si
el ritmo real cambia, el número cambia. No sabe nada de volúmenes ni de la base: quien
llame a esto decide con qué fotos alimentarlo (sección 5.2, lo usará el dashboard).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from discoclaro.domain.models import Snapshot

# Con menos fotos que esto, una recta no dice nada: hace falta al menos una para fijar
# el punto de partida, una para notar un cambio, y una tercera para confirmarlo.
MIN_SNAPSHOTS = 3


@dataclass(frozen=True, slots=True)
class Trend:
    """Cuánto crece el uso de un disco por día, de una regresión lineal simple."""

    bytes_per_day: float
    sample_count: int


def trend_of(snapshots: Sequence[Snapshot]) -> Trend | None:
    """La tendencia de uso de estas fotos (de un mismo disco, en cualquier orden).

    `None` si no llegan a `MIN_SNAPSHOTS`: no hay suficiente historia para estimar
    nada todavía, y eso no es un error, es el estado normal de un disco recién
    escaneado por primera vez.
    """
    if len(snapshots) < MIN_SNAPSHOTS:
        return None
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.taken_at)
    first = ordered[0].taken_at
    days = [(s.taken_at - first).total_seconds() / 86400 for s in ordered]
    used = [float(s.used_bytes) for s in ordered]
    return Trend(bytes_per_day=_slope(days, used), sample_count=len(ordered))


def days_until_full(trend: Trend, free_bytes: int) -> float | None:
    """A este ritmo, en cuántos días se llenaría el disco con `free_bytes` libres.

    `None` si la tendencia no crece (cero o negativa): a este ritmo, nunca se llena.
    """
    if trend.bytes_per_day <= 0:
        return None
    return free_bytes / trend.bytes_per_day


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """La pendiente de la recta de mínimos cuadrados de `ys` sobre `xs`.

    `0.0` si todos los `xs` son iguales (no hay variación en el tiempo de la que
    sacar una tendencia), en vez de dividir por cero.
    """
    count = len(xs)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return numerator / denominator if denominator else 0.0
