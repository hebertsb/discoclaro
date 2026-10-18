"""Reglas de disparo de los avisos (T2.7): puras, sin fotos ni configuración completas.

Cada función recibe solo los números ya resueltos (lo medido y el límite) y decide si
hay que avisar; quien junta esos números desde una foto real y el `Config` del usuario
es `services/check_service.py`. Que no se repita el mismo aviso en 24 h también es
cosa del servicio: aquí no se sabe cuándo se avisó por última vez.
"""

from dataclasses import dataclass

ALERT_KINDS = (
    "volume_low_free",
    "volume_near_limit",
    "budget_near",
    "budget_exceeded",
    "big_new_file",
    "unexplained_grew",
)

# A qué porcentaje de un presupuesto se avisa "ya casi", antes de llegar al tope.
# Elegido simple (T2.6 no lo deja configurar todavía): no hay un criterio mejor que
# documentar que "90 % ya es para prestarle atención".
BUDGET_NEAR_PERCENT = 90.0


@dataclass(frozen=True, kw_only=True)
class Alert:
    """Un aviso disparado: qué regla lo generó, sobre qué y con qué números.

    `measured_bytes`/`limit_bytes` se usan en los avisos de tamaño; `measured_percent`/
    `limit_percent`, en los de porcentaje (`volume_near_limit`). Lo que no aplica a un
    `kind` se queda en su valor por defecto.
    """

    kind: str
    target: str
    measured_bytes: int = 0
    limit_bytes: int = 0
    measured_percent: float | None = None
    limit_percent: float | None = None


def check_volume_low_free(
    volume: str, free_bytes: int, min_free_bytes: int | None
) -> Alert | None:
    """Avisa si a `volume` le quedan menos de `min_free_bytes` libres.

    Sin límite configurado (`None`), nunca avisa.
    """
    if min_free_bytes is None or free_bytes >= min_free_bytes:
        return None
    return Alert(
        kind="volume_low_free",
        target=volume,
        measured_bytes=free_bytes,
        limit_bytes=min_free_bytes,
    )


def check_volume_near_limit(
    volume: str, used_percent: float, warn_at_percent: float
) -> Alert | None:
    """Avisa si `volume` ya usó al menos `warn_at_percent` de su espacio."""
    if used_percent < warn_at_percent:
        return None
    return Alert(
        kind="volume_near_limit",
        target=volume,
        measured_percent=used_percent,
        limit_percent=warn_at_percent,
    )


def check_budget(name: str, current_bytes: int, max_bytes: int) -> Alert | None:
    """Avisa si `name` ya llegó (`budget_exceeded`) o está cerca de `max_bytes`."""
    if current_bytes >= max_bytes:
        return Alert(
            kind="budget_exceeded",
            target=name,
            measured_bytes=current_bytes,
            limit_bytes=max_bytes,
        )
    if current_bytes >= max_bytes * BUDGET_NEAR_PERCENT / 100:
        return Alert(
            kind="budget_near",
            target=name,
            measured_bytes=current_bytes,
            limit_bytes=max_bytes,
        )
    return None


def check_big_new_file(
    path: str, size_bytes: int, threshold_bytes: int
) -> Alert | None:
    """Avisa si un archivo nuevo llega a `threshold_bytes`."""
    if size_bytes < threshold_bytes:
        return None
    return Alert(
        kind="big_new_file",
        target=path,
        measured_bytes=size_bytes,
        limit_bytes=threshold_bytes,
    )


def check_unexplained_grew(
    target: str, grown_bytes: int, max_growth_bytes: int | None
) -> Alert | None:
    """Avisa si lo no explicado creció al menos `max_growth_bytes` entre dos fotos.

    Sin límite configurado (`None`), nunca avisa: no hay un valor por defecto
    razonable para "cuánto crecimiento es demasiado".
    """
    if max_growth_bytes is None or grown_bytes < max_growth_bytes:
        return None
    return Alert(
        kind="unexplained_grew",
        target=target,
        measured_bytes=grown_bytes,
        limit_bytes=max_growth_bytes,
    )
