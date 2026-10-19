"""El veredicto por niveles (T2C.7): puro.

Junta lo que ya saben los inspectores (T2C.1), qué está en uso por un proyecto
(T2C.3) y qué decidió el usuario (T2C.4) en tres niveles: **1 seguro** (cachés
y espacio que el propio programa ya dice que se puede recuperar), **2
revisar** (algo más grande o específico, como un modelo o un juego, que
conviene mirar antes de borrar) y **3 mover a otro disco** (no es basura,
está mal ubicado, T2C.5). Lo protegido o en uso nunca entra en ningún nivel.
"""

from collections.abc import Sequence
from dataclasses import dataclass

LEVELS = ("safe", "review", "move")


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Una fila del plan: `target` es el identificador estable para buscar
    una decisión guardada (T2C.4), con la forma `"{program}:{name}"`."""

    level: str
    program: str
    name: str
    size_bytes: int
    reason: str
    target: str

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"unknown plan level: {self.level!r}")
        if not self.program:
            raise ValueError("a plan item needs a program")
        if not self.name:
            raise ValueError("a plan item needs a name")
        if self.size_bytes < 0:
            raise ValueError("size cannot be negative")
        if not self.target:
            raise ValueError("a plan item needs a target")


@dataclass(frozen=True, slots=True)
class Plan:
    """Los tres niveles ya armados, en el orden en que se encontró cada cosa."""

    items: tuple[PlanItem, ...]

    def of_level(self, level: str) -> tuple[PlanItem, ...]:
        return tuple(item for item in self.items if item.level == level)

    def total_bytes(self, level: str) -> int:
        return sum(item.size_bytes for item in self.of_level(level))


def target_of(program: str, name: str) -> str:
    """El identificador estable de algo, para guardar o buscar una decisión."""
    return f"{program}:{name}"


def build_plan(
    candidates: Sequence[PlanItem],
    *,
    is_excluded: Sequence[str] | None = None,
) -> Plan:
    """Arma el plan a partir de `candidates` ya clasificados, sacando los que
    están en `is_excluded` (protegidos o en uso: T2C.4/T2C.3). El orden
    original de `candidates` se conserva dentro de cada nivel."""
    excluded = frozenset(is_excluded or ())
    return Plan(items=tuple(item for item in candidates if item.target not in excluded))
