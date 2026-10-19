"""Navegar un árbol de carpetas ya guardado (T3.1): puro.

No vuelve a recorrer el disco: solo filtra los nodos de una foto que ya se guardó,
para pedir un nivel del árbol a la vez (lo que necesita un treemap o un sunburst con
*drill-down*, en vez de traer todo de una sola vez).
"""

from collections.abc import Sequence

from discoclaro.domain.models import Node


def node_at(nodes: Sequence[Node], path: str) -> Node | None:
    """El nodo de esa ruta exacta, o `None` si no está en la foto."""
    return next((node for node in nodes if node.path == path), None)


def children_of(nodes: Sequence[Node], path: str | None) -> tuple[Node, ...]:
    """Los hijos directos de `path` (o la raíz del recorrido, si `path` es `None`),
    de mayor a menor tamaño."""
    matches = (
        [node for node in nodes if node.depth == 0]
        if path is None
        else [node for node in nodes if node.parent_path == path]
    )
    return tuple(sorted(matches, key=lambda node: node.size_bytes, reverse=True))
