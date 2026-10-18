"""Agrupa las carpetas de una foto por categoría del catálogo de reglas (T2.4).

Pura: recibe las carpetas, el catálogo y las variables de entorno ya resueltas; no
clasifica cada una sin más, sino que recorre de la más superficial a la más profunda
y, apenas una entra en una categoría, deja de mirar lo que cuelga de ella (sus bytes
ya están adentro del total de esa carpeta de afuera, caiga en la misma categoría o en
otra distinta: contarla aparte sería sumar esos bytes dos veces).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from discoclaro.domain.classify import classify
from discoclaro.domain.models import Node
from discoclaro.domain.rules import Rule


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    """Cuánto ocupan, en total, las carpetas de una categoría dentro de una foto."""

    category: str
    size_bytes: int
    folders: int


def category_totals(
    nodes: Sequence[Node], rules: Sequence[Rule], environment: Mapping[str, str]
) -> tuple[CategoryTotal, ...]:
    """Los totales por categoría de `nodes`, de mayor a menor tamaño."""
    totals: dict[str, list[int]] = {}
    counted: list[str] = []
    for node in sorted(nodes, key=lambda item: item.depth):
        if any(_is_inside(node.path, parent) for parent in counted):
            continue
        rule = classify(node.path, rules, environment)
        if rule is None:
            continue
        counted.append(node.path)
        bucket = totals.setdefault(rule.category, [0, 0])
        bucket[0] += node.size_bytes
        bucket[1] += 1
    results = (
        CategoryTotal(category=category, size_bytes=size, folders=count)
        for category, (size, count) in totals.items()
    )
    return tuple(sorted(results, key=lambda total: total.size_bytes, reverse=True))


def _is_inside(path: str, parent: str) -> bool:
    """Si `path` está dentro de `parent` (o es el mismo), sin distinguir mayúsculas."""
    path, parent = path.lower(), parent.lower()
    return path == parent or path.startswith((parent + "\\", parent + "/"))
