"""Caso de uso "árbol": un nivel de una foto ya guardada, para el dashboard (T3.1).

Es de solo lectura y no vuelve a recorrer el disco: solo lee la base que dejó un
`scan` anterior, igual que `report_service`.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.models import Node, Snapshot
from discoclaro.domain.tree import children_of, node_at
from discoclaro.platform import windows
from discoclaro.services.scan_service import DATABASE_NAME


class TreeError(Exception):
    """No se pudo armar el árbol; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class TreeView:
    """Un nivel del árbol: la foto de donde sale, el nodo pedido (`None` si se pidió
    la raíz) y sus hijos directos."""

    snapshot: Snapshot
    node: Node | None
    children: tuple[Node, ...]


def build_tree(path: str | None, snapshot_id: int | None, store: Store) -> TreeView:
    """El nivel de `path` dentro de la foto `snapshot_id` (la más reciente de
    cualquier destino, si falta). Lanza `TreeError` si no hay ninguna foto, si el
    id no existe, o si `path` no es un nodo de esa foto."""
    if snapshot_id is None:
        snapshots = store.snapshots()
        if not snapshots:
            raise TreeError("tree_no_snapshots")
        snapshot = snapshots[0]
    else:
        found = store.get_snapshot(snapshot_id)
        if found is None:
            raise TreeError("tree_no_such_snapshot", str(snapshot_id))
        snapshot = found
    nodes = store.nodes(snapshot.id)
    node = None
    if path is not None:
        node = node_at(nodes, path)
        if node is None:
            raise TreeError("tree_no_such_path", path)
    return TreeView(snapshot=snapshot, node=node, children=children_of(nodes, path))


def run_tree(
    path: str | None,
    snapshot_id: int | None = None,
    data_dir: Path | None = None,
) -> TreeView:
    """Arma el árbol leyendo la base propia de discoclaro, sin volver a escanear.

    La base vive en `%LOCALAPPDATA%\\discoclaro`, o en `data_dir` si se da (para
    probar).
    """
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if not database.is_file():
        raise TreeError("tree_no_snapshots")
    try:
        store = Store.open_readonly(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise TreeError("cannot_open_database", str(error)) from error
    with store:
        return build_tree(path, snapshot_id, store)
