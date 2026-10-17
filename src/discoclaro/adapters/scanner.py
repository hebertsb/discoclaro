"""Recorrido del disco: el tamaño de cada carpeta, sin seguir enlaces.

Solo lee. Recorre con `os.scandir` (en Windows `DirEntry.stat()` entrega el tamaño sin
otra llamada al sistema) y con una pila explícita en vez de recursión, así una carpeta
muy anidada no agota el límite de Python. Los hijos se emiten antes que sus padres.

El tamaño es el lógico (`st_size`). Los archivos que están solo en la nube (OneDrive
bajo demanda) cuentan como archivo pero con 0 bytes, porque no ocupan disco. Los
enlaces duros se cuentan una vez por enlace.
"""

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from discoclaro.domain.models import Node, ScanResult, Unreadable

MAX_RECORDED_UNREADABLE = 1000

# Atributos de Windows que el módulo `stat` no define.
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000
NOT_ON_DISK = stat.FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT

Scandir = Callable[[str], AbstractContextManager[Iterator[os.DirEntry[str]]]]


@dataclass(slots=True)
class _Frame:
    """Una carpeta en curso: lo que lleva sumado y las subcarpetas por visitar."""

    path: str
    parent_path: str | None
    depth: int
    size_bytes: int = 0
    n_files: int = 0
    pending: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Tally:
    """Lo que se va acumulando durante el recorrido."""

    nodes: list[Node] = field(default_factory=list)
    unreadable: list[Unreadable] = field(default_factory=list)
    unreadable_count: int = 0
    skipped_links: int = 0

    def record(self, path: str, error: OSError) -> None:
        """Cuenta una ruta ilegible y guarda las primeras `MAX_RECORDED_UNREADABLE`."""
        self.unreadable_count += 1
        if len(self.unreadable) < MAX_RECORDED_UNREADABLE:
            self.unreadable.append(Unreadable(path, type(error).__name__))


def scan_tree(root: str, scandir: Scandir = os.scandir) -> ScanResult:
    """Mide `root` y todo lo que cuelga de él.

    Lanza `FileNotFoundError` o `NotADirectoryError` si `root` no es una carpeta
    existente. Cualquier otro error del sistema, en la raíz o más adentro, se cuenta
    como ruta ilegible y el recorrido sigue. `scandir` se puede inyectar para probar.
    """
    tally = _Tally()
    stack = [_open(_Frame(root, None, 0), scandir, tally)]
    while stack:
        frame = stack[-1]
        if frame.pending:
            child = _Frame(frame.pending.pop(), frame.path, frame.depth + 1)
            stack.append(_open(child, scandir, tally))
            continue
        stack.pop()
        tally.nodes.append(
            Node(
                path=frame.path,
                parent_path=frame.parent_path,
                size_bytes=frame.size_bytes,
                n_files=frame.n_files,
                depth=frame.depth,
            )
        )
        if stack:
            stack[-1].size_bytes += frame.size_bytes
            stack[-1].n_files += frame.n_files
    return ScanResult(
        root=root,
        nodes=tuple(tally.nodes),
        unreadable_count=tally.unreadable_count,
        unreadable=tuple(tally.unreadable),
        skipped_links=tally.skipped_links,
    )


def _open(frame: _Frame, scandir: Scandir, tally: _Tally) -> _Frame:
    """Lee los elementos de una carpeta y suma sus archivos a `frame`."""
    try:
        with scandir(frame.path) as entries:
            for entry in entries:
                _visit(entry, frame, tally)
    except OSError as error:
        if frame.depth == 0 and isinstance(
            error, FileNotFoundError | NotADirectoryError
        ):
            raise
        tally.record(frame.path, error)
    return frame


def _visit(entry: os.DirEntry[str], frame: _Frame, tally: _Tally) -> None:
    """Suma un archivo, anota una subcarpeta por visitar u omite un enlace."""
    try:
        if entry.is_symlink():
            tally.skipped_links += 1
            return
        info = entry.stat(follow_symlinks=False)
        attributes = getattr(info, "st_file_attributes", 0)
        if entry.is_dir(follow_symlinks=False):
            if attributes & REPARSE_POINT:
                tally.skipped_links += 1
            else:
                frame.pending.append(entry.path)
        elif entry.is_file(follow_symlinks=False):
            frame.n_files += 1
            if not attributes & NOT_ON_DISK:
                frame.size_bytes += info.st_size
    except OSError as error:
        tally.record(entry.path, error)
