"""Agrupa las carpetas de una foto por categoría del catálogo de reglas (T2.4).

Pura: recibe las carpetas, el catálogo y las variables de entorno ya resueltas; no
clasifica cada una sin más, sino que recorre de la más superficial a la más profunda
y, apenas una entra en una categoría, deja de mirar lo que cuelga de ella (sus bytes
ya están adentro del total de esa carpeta de afuera, caiga en la misma categoría o en
otra distinta: contarla aparte sería sumar esos bytes dos veces).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from discoclaro.domain.classify import CompiledCatalog
from discoclaro.domain.models import Node
from discoclaro.domain.rules import Rule


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    """Cuánto ocupan, en total, las carpetas de una categoría dentro de una foto."""

    category: str
    size_bytes: int
    folders: int


def _normalize_key(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def _has_counted_ancestor(norm_path: str, counted_set: set[str]) -> bool:
    if not counted_set:
        return False
    if norm_path in counted_set:
        return True
    idx = norm_path.find("\\")
    while idx != -1:
        if norm_path[:idx] in counted_set:
            return True
        idx = norm_path.find("\\", idx + 1)
    return False


def category_totals(
    nodes: Sequence[Node], rules: Sequence[Rule], environment: Mapping[str, str]
) -> tuple[CategoryTotal, ...]:
    """Los totales por categoría de `nodes`, de mayor a menor tamaño."""
    catalog = CompiledCatalog(rules, environment)
    totals: dict[str, list[int]] = {}
    counted_set: set[str] = set()
    for node in sorted(nodes, key=lambda item: item.depth):
        norm = _normalize_key(node.path)
        if _has_counted_ancestor(norm, counted_set):
            continue
        rule = catalog.classify_path(node.path)
        if rule is None:
            continue
        counted_set.add(norm)
        bucket = totals.setdefault(rule.category, [0, 0])
        bucket[0] += node.size_bytes
        bucket[1] += 1
    results = (
        CategoryTotal(category=category, size_bytes=size, folders=count)
        for category, (size, count) in totals.items()
    )
    return tuple(sorted(results, key=lambda total: total.size_bytes, reverse=True))


@dataclass(frozen=True, slots=True)
class CategorizedFolder:
    """Una carpeta concreta que coincidió con una regla del catálogo."""

    path: str
    size_bytes: int
    n_files: int
    category: str
    rule_id: str
    rule_name: str
    risk: str
    explanation: str
    suggested_command: str | None


def categorized_folders(
    nodes: Sequence[Node], rules: Sequence[Rule], environment: Mapping[str, str]
) -> tuple[CategorizedFolder, ...]:
    """Las carpetas de primer nivel clasificadas por el catálogo de reglas (sin
    duplicar subcarpetas que cuelgan de una ya contada), de mayor a menor tamaño."""
    catalog = CompiledCatalog(rules, environment)
    found: list[CategorizedFolder] = []
    counted_set: set[str] = set()
    for node in sorted(nodes, key=lambda item: item.depth):
        norm = _normalize_key(node.path)
        if _has_counted_ancestor(norm, counted_set):
            continue
        rule = catalog.classify_path(node.path)
        if rule is None:
            continue
        counted_set.add(norm)
        cmd = (
            rule.suggested_action.text
            if (rule.suggested_action and rule.suggested_action.type == "command")
            else None
        )
        found.append(
            CategorizedFolder(
                path=node.path,
                size_bytes=node.size_bytes,
                n_files=node.n_files,
                category=rule.category,
                rule_id=rule.id,
                rule_name=rule.name,
                risk=rule.risk,
                explanation=rule.explanation,
                suggested_command=cmd,
            )
        )
    return tuple(sorted(found, key=lambda item: item.size_bytes, reverse=True))


def _is_inside(path: str, parent: str) -> bool:
    """Si `path` está dentro de `parent` (o es el mismo), sin distinguir mayúsculas."""
    path, parent = path.lower(), parent.lower()
    return path == parent or path.startswith((parent + "\\", parent + "/"))
