"""Punto de control de un escaneo en curso (T1.10): para retomarlo si se corta.

Un archivo propio de discoclaro, en su carpeta de datos, con las carpetas de primer
nivel que ya se terminaron de medir. Si el programa se corta (apagón, cuelgue,
Ctrl+C) o el usuario pausa a propósito, lo que ya esté aquí no se vuelve a leer la
próxima vez. La foto final se sigue guardando aparte, en una sola transacción, como
siempre: este archivo es solo un borrador de trabajo y se borra en cuanto esa foto
se guarda.

Un punto de control dañado o de otro destino nunca hace fallar un escaneo: se trata
como si no hubiera ninguno y se empieza de cero, tal como se haría sin esta tarea.
"""

import json
import logging
import tempfile
from collections.abc import Mapping
from os import PathLike
from pathlib import Path

from discoclaro.domain.models import Node, ResumedSubtree
from discoclaro.platform import windows

logger = logging.getLogger(__name__)

CHECKPOINT_NAME = "scan-checkpoint.json"
CHECKPOINT_VERSION = 1


def write_checkpoint(
    path: str | PathLike[str], target: str, subtrees: Mapping[str, ResumedSubtree]
) -> None:
    """Reemplaza el punto de control entero por `subtrees`, de forma atómica.

    Revalida antes de escribir que ni la carpeta ni el archivo sean un enlace o
    tengan otro nombre (T1.9), igual que la base y el resultado de la elevación.
    Escribe en un archivo aparte y lo reemplaza de un solo paso (`os.replace`), así
    que nunca queda un punto de control a medio escribir.
    """
    path = Path(path)
    windows.ensure_safe_to_write(path.parent)
    windows.ensure_safe_to_write(path)
    payload = json.dumps(_to_data(target, subtrees), separators=(",", ":"))
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{CHECKPOINT_NAME}.", delete=False
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def read_checkpoint(
    path: str | PathLike[str], target: str
) -> dict[str, ResumedSubtree]:
    """Las carpetas de primer nivel ya medidas para `target`, o vacío si no hay.

    Nunca lanza: un archivo que falta, pesa raro, no es JSON, no tiene el formato
    esperado o es de otro destino se trata igual que si no hubiera ninguno.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
        return _from_data(data, target)
    except (ValueError, TypeError, KeyError) as error:
        logger.warning("checkpoint ignored: %r (%r)", path, type(error).__name__)
        return {}


def clear_checkpoint(path: str | PathLike[str]) -> None:
    """Borra el punto de control; no pasa nada si ya no estaba."""
    Path(path).unlink(missing_ok=True)


def _to_data(target: str, subtrees: Mapping[str, ResumedSubtree]) -> dict[str, object]:
    return {
        "version": CHECKPOINT_VERSION,
        "target": target,
        "subtrees": {
            subtree_path: {
                "nodes": [_node_to_row(node) for node in subtree.nodes],
                "duplicate_bytes": subtree.duplicate_bytes,
                "unreadable_count": subtree.unreadable_count,
                "skipped_links": subtree.skipped_links,
            }
            for subtree_path, subtree in subtrees.items()
        },
    }


def _from_data(data: object, target: str) -> dict[str, ResumedSubtree]:
    if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
        return {}
    if data.get("target") != target:
        return {}
    subtrees = data.get("subtrees")
    if not isinstance(subtrees, dict):
        return {}
    return {
        subtree_path: ResumedSubtree(
            nodes=tuple(_row_to_node(row) for row in entry["nodes"]),
            duplicate_bytes=int(entry["duplicate_bytes"]),
            unreadable_count=int(entry["unreadable_count"]),
            skipped_links=int(entry["skipped_links"]),
        )
        for subtree_path, entry in subtrees.items()
    }


def _node_to_row(node: Node) -> list[object]:
    return [node.path, node.parent_path, node.size_bytes, node.n_files, node.depth]


def _row_to_node(row: object) -> Node:
    path, parent_path, size_bytes, n_files, depth = row
    return Node(
        path=str(path),
        parent_path=None if parent_path is None else str(parent_path),
        size_bytes=int(size_bytes),
        n_files=int(n_files),
        depth=int(depth),
    )
