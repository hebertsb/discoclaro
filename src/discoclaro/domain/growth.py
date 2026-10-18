"""Compara dos fotos del mismo destino (T2.5): qué carpetas y categorías crecieron
más, y qué archivos grandes son nuevos.

Pura: recibe las carpetas, los archivos grandes, el catálogo y las variables de
entorno ya resueltos; no lee la base ni el disco ni el reloj.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from discoclaro.domain.categories import category_totals
from discoclaro.domain.models import LargeFile, Node
from discoclaro.domain.rules import Rule

_DURATION = re.compile(r"(\d+)([dhw])", re.IGNORECASE)
_DURATION_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


class InvalidDuration(ValueError):
    """`--since` no tiene el formato esperado: un número seguido de `d`, `h` o `w`."""


def parse_duration(text: str) -> timedelta:
    """Convierte `"7d"`, `"24h"` o `"2w"` en un `timedelta`.

    Lanza `InvalidDuration` si no tiene exactamente esa forma (sin mayúsculas
    distintas, sin espacios de más, sin signo).
    """
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        raise InvalidDuration(text)
    amount, unit = match.groups()
    return timedelta(**{_DURATION_UNITS[unit.lower()]: int(amount)})


@dataclass(frozen=True, slots=True)
class FolderGrowth:
    """Cuánto creció (o encogió) una carpeta entre dos fotos.

    `is_new` es cierto si no existía en la foto vieja (`before_bytes` vale 0 en ese
    caso). Si una carpeta desapareció, `after_bytes` vale 0.
    """

    path: str
    depth: int
    before_bytes: int
    after_bytes: int
    is_new: bool

    @property
    def grown_bytes(self) -> int:
        """Lo que creció; negativo si encogió."""
        return self.after_bytes - self.before_bytes


@dataclass(frozen=True, slots=True)
class CategoryGrowth:
    """Cuánto creció (o encogió) una categoría entre dos fotos."""

    category: str
    before_bytes: int
    after_bytes: int

    @property
    def grown_bytes(self) -> int:
        """Lo que creció; negativo si encogió."""
        return self.after_bytes - self.before_bytes


def folder_growth(
    before: Sequence[Node], after: Sequence[Node]
) -> tuple[FolderGrowth, ...]:
    """Cada carpeta de cualquiera de las dos fotos, de la que más creció a la que
    más encogió."""
    before_by_path = {node.path: node for node in before}
    after_by_path = {node.path: node for node in after}
    results = []
    for path in before_by_path.keys() | after_by_path.keys():
        old_node = before_by_path.get(path)
        new_node = after_by_path.get(path)
        results.append(
            FolderGrowth(
                path=path,
                depth=(new_node or old_node).depth,
                before_bytes=old_node.size_bytes if old_node else 0,
                after_bytes=new_node.size_bytes if new_node else 0,
                is_new=old_node is None,
            )
        )
    return tuple(sorted(results, key=lambda growth: growth.grown_bytes, reverse=True))


def category_growth(
    before: Sequence[Node],
    after: Sequence[Node],
    rules: Sequence[Rule],
    environment: Mapping[str, str],
) -> tuple[CategoryGrowth, ...]:
    """Cada categoría que aparece en cualquiera de las dos fotos, de la que más
    creció a la que más encogió."""
    before_totals = {
        total.category: total.size_bytes
        for total in category_totals(before, rules, environment)
    }
    after_totals = {
        total.category: total.size_bytes
        for total in category_totals(after, rules, environment)
    }
    results = [
        CategoryGrowth(
            category=category,
            before_bytes=before_totals.get(category, 0),
            after_bytes=after_totals.get(category, 0),
        )
        for category in before_totals.keys() | after_totals.keys()
    ]
    return tuple(sorted(results, key=lambda growth: growth.grown_bytes, reverse=True))


def new_large_files(
    before: Sequence[LargeFile], after: Sequence[LargeFile]
) -> tuple[LargeFile, ...]:
    """Los archivos de `after` que no estaban en `before` (por ruta), del más grande
    al más chico."""
    before_paths = {file.path for file in before}
    found = [file for file in after if file.path not in before_paths]
    return tuple(sorted(found, key=lambda file: file.size_bytes, reverse=True))
