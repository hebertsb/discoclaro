"""Decisiones del usuario sobre algo puntual (T2C.4): puro.

Una fila por cosa decidida (`target`: una ruta, una imagen, un modelo…), con un
estado y una nota. Un protegido nunca se sugiere para borrar ni entra en un
plan de acciones (T2C.7 y la Fase 4 lo respetan); un pendiente de decidir se
muestra aparte, sin sugerir nada todavía; un ignorado no vuelve a aparecer en
los avisos, pero tampoco se trata como protegido.
"""

from dataclasses import dataclass
from datetime import datetime

DECISION_STATUSES = ("protected", "pending", "ignored")


@dataclass(frozen=True, slots=True)
class Decision:
    """La decisión guardada sobre `target`, con el motivo en `note` (por
    ejemplo, "déjalo, por si acaso nos piden exponer")."""

    target: str
    status: str
    note: str | None
    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("a decision needs a target")
        if self.status not in DECISION_STATUSES:
            raise ValueError(f"unknown decision status: {self.status!r}")


def is_protected(decision: Decision | None) -> bool:
    """Si `target` está protegido: nunca se sugiere borrarlo ni entra en un
    plan de acciones. `None` (nunca se decidió nada) no está protegido."""
    return decision is not None and decision.status == "protected"
