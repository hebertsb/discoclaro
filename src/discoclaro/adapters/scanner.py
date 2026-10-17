"""Recorrido del disco: el tamaño de cada carpeta, sin seguir enlaces.

Solo lee. Usa una pila explícita en vez de recursión, así una carpeta muy anidada no
agota el límite de Python. Los hijos se emiten antes que sus padres.

Quién lista cada carpeta es un *enumerador* intercambiable, que entrega
`DirectoryEntry`:

- En Windows, `windows_listing.enumerate_directory`: da el tamaño **en disco** de cada
  archivo y un identificador de su contenido. Es la opción por defecto.
- En cualquier sistema, `enumerate_with_scandir`: da el tamaño lógico (`st_size`) y no
  conoce los identificadores, así que no reconoce enlaces duros.

El tamaño de una carpeta cuenta **todo lo que tiene dentro**, enlaces duros incluidos,
como el Explorador: así dos fotos son comparables sin depender del orden del recorrido.
Aparte, `duplicate_bytes` suma lo que ocupan de más los enlaces duros (el mismo
contenido visto otra vez con otro nombre), para que el total del volumen sea exacto.

**Retomar un punto de control (T1.10):** si `resume` trae una carpeta de primer nivel
(hija directa de `root`) ya medida de antes, no se vuelve a leer: se usa tal cual. Cada
vez que una carpeta de primer nivel **nueva** termina de medirse, `on_subtree_done` la
recibe completa, para que quien llama la guarde. Solo se checkpointea a esa
profundidad: es poca pérdida si algo se corta (como mucho, una carpeta de primer
nivel) y no hace falta guardar el recorrido entero a cada paso.
"""

import os
import stat
import sys
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from discoclaro.domain.models import (
    DirectoryEntry,
    Node,
    ResumedSubtree,
    ScanProgress,
    ScanResult,
    Unreadable,
)
from discoclaro.platform import windows_listing

MAX_RECORDED_UNREADABLE = 1000

# Atributos de Windows que el módulo `stat` no define.
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000
NOT_ON_DISK = stat.FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT

Enumerator = Callable[[str], Iterable[DirectoryEntry]]
OnProgress = Callable[[ScanProgress], None]
OnSubtreeDone = Callable[[str, ResumedSubtree], None]


def enumerate_with_scandir(
    path: str, scandir: Callable[[str], object] = os.scandir
) -> Iterator[DirectoryEntry]:
    """Lista una carpeta con `os.scandir`: tamaño lógico y sin identificadores.

    Los archivos que están solo en la nube (OneDrive bajo demanda) cuentan como archivo
    pero con 0 bytes, porque no ocupan disco. Si un elemento no se puede leer sale como
    `unreadable` y los demás siguen; un fallo al abrir o recorrer la carpeta se propaga.
    """
    with scandir(path) as entries:
        for entry in entries:
            yield _from_dir_entry(entry)


def default_enumerator() -> Enumerator:
    """El enumerador del sistema: el de Windows en Windows y `scandir` en el resto."""
    if sys.platform == "win32":
        return windows_listing.enumerate_directory
    return enumerate_with_scandir


def _from_dir_entry(entry: os.DirEntry[str]) -> DirectoryEntry:
    try:
        if entry.is_symlink():
            return DirectoryEntry(entry.name, entry.path, "link")
        info = entry.stat(follow_symlinks=False)
        attributes = getattr(info, "st_file_attributes", 0)
        if entry.is_dir(follow_symlinks=False):
            kind = "link" if attributes & REPARSE_POINT else "folder"
            return DirectoryEntry(entry.name, entry.path, kind)
        if entry.is_file(follow_symlinks=False):
            size = 0 if attributes & NOT_ON_DISK else info.st_size
            return DirectoryEntry(entry.name, entry.path, "file", size)
        return DirectoryEntry(entry.name, entry.path, "other")
    except OSError as error:
        return DirectoryEntry(
            entry.name, entry.path, "unreadable", error=type(error).__name__
        )


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

    skip_root_names: frozenset[str] = frozenset()
    nodes: list[Node] = field(default_factory=list)
    unreadable: list[Unreadable] = field(default_factory=list)
    unreadable_count: int = 0
    skipped_links: int = 0
    seen_ids: set[bytes] = field(default_factory=set)
    duplicate_bytes: int = 0
    # Para el progreso (T1.10): archivos y bytes vistos en total, independiente de
    # cómo se van sumando las carpetas unas dentro de otras.
    files_seen: int = 0
    bytes_seen: int = 0

    def record(self, path: str, reason: str) -> None:
        """Cuenta una ruta ilegible y guarda las primeras `MAX_RECORDED_UNREADABLE`."""
        self.unreadable_count += 1
        if len(self.unreadable) < MAX_RECORDED_UNREADABLE:
            self.unreadable.append(Unreadable(path, reason))


def scan_tree(
    root: str,
    enumerate_directory: Enumerator | None = None,
    *,
    skip_root_files: Collection[str] = (),
    on_progress: OnProgress | None = None,
    resume: Mapping[str, ResumedSubtree] | None = None,
    on_subtree_done: OnSubtreeDone | None = None,
) -> ScanResult:
    """Mide `root` y todo lo que cuelga de él.

    Lanza `FileNotFoundError` o `NotADirectoryError` si `root` no es una carpeta
    existente. Cualquier otro error del sistema, en la raíz o más adentro, se cuenta
    como ruta ilegible y el recorrido sigue. `enumerate_directory` se puede inyectar
    para probar; si no se da, se usa el del sistema (`default_enumerator`).

    `skip_root_files` son nombres (sin distinguir mayúsculas) que se omiten solo en la
    raíz del recorrido y no suman ni bytes ni archivos. Sirve para los archivos de
    sistema de un volumen, que se miden aparte; un archivo con ese nombre en una
    subcarpeta sí cuenta.

    `on_progress` avisa, cada vez que una carpeta cualquiera termina de medirse,
    cuánto lleva en total. `resume` y `on_subtree_done` son para retomar un punto de
    control (T1.10): ver el docstring del módulo.
    """
    list_entries = enumerate_directory or default_enumerator()
    tally = _Tally(skip_root_names=frozenset(name.lower() for name in skip_root_files))
    known = resume or {}
    for reused in known.values():
        tally.nodes.extend(reused.nodes)
        tally.duplicate_bytes += reused.duplicate_bytes
        tally.unreadable_count += reused.unreadable_count
        tally.skipped_links += reused.skipped_links
        root_node = reused.nodes[-1]
        tally.files_seen += root_node.n_files
        tally.bytes_seen += root_node.size_bytes
    stack = [_open(_Frame(root, None, 0), list_entries, tally, known)]
    subtree_start = len(tally.nodes)
    marks = (0, 0, 0)  # duplicate_bytes, unreadable_count, skipped_links al empezar
    while stack:
        frame = stack[-1]
        if frame.pending:
            child_path = frame.pending.pop()
            child = _Frame(child_path, frame.path, frame.depth + 1)
            if child.depth == 1:
                subtree_start = len(tally.nodes)
                marks = (
                    tally.duplicate_bytes,
                    tally.unreadable_count,
                    tally.skipped_links,
                )
            stack.append(_open(child, list_entries, tally, known))
            continue
        stack.pop()
        reused = frame.path in known
        if not reused:
            tally.nodes.append(
                Node(
                    path=frame.path,
                    parent_path=frame.parent_path,
                    size_bytes=frame.size_bytes,
                    n_files=frame.n_files,
                    depth=frame.depth,
                )
            )
            if on_progress is not None:
                on_progress(
                    ScanProgress(
                        folders=len(tally.nodes),
                        files=tally.files_seen,
                        bytes_scanned=tally.bytes_seen,
                        current_path=frame.path,
                    )
                )
        if frame.depth == 1 and not reused and on_subtree_done is not None:
            on_subtree_done(
                frame.path,
                ResumedSubtree(
                    nodes=tuple(tally.nodes[subtree_start:]),
                    duplicate_bytes=tally.duplicate_bytes - marks[0],
                    unreadable_count=tally.unreadable_count - marks[1],
                    skipped_links=tally.skipped_links - marks[2],
                ),
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
        duplicate_bytes=tally.duplicate_bytes,
    )


def _open(
    frame: _Frame,
    list_entries: Enumerator,
    tally: _Tally,
    known: Mapping[str, ResumedSubtree],
) -> _Frame:
    """Lee los elementos de una carpeta y suma sus archivos a `frame`.

    Si `frame.path` ya está en `known` (un punto de control), no se lee: se usa el
    total que ya se sabía y se deja sin nada pendiente.
    """
    reused = known.get(frame.path)
    if reused is not None:
        root_node = reused.nodes[-1]
        frame.size_bytes = root_node.size_bytes
        frame.n_files = root_node.n_files
        return frame
    try:
        for entry in list_entries(frame.path):
            _visit(entry, frame, tally)
    except OSError as error:
        if frame.depth == 0 and isinstance(
            error, FileNotFoundError | NotADirectoryError
        ):
            raise
        tally.record(frame.path, type(error).__name__)
    return frame


def _visit(entry: DirectoryEntry, frame: _Frame, tally: _Tally) -> None:
    """Suma un archivo, anota una subcarpeta por visitar u omite un enlace."""
    if (
        tally.skip_root_names
        and frame.depth == 0
        and entry.name.lower() in tally.skip_root_names
    ):
        return
    kind = entry.kind
    if kind == "file":
        frame.n_files += 1
        frame.size_bytes += entry.size_bytes
        tally.files_seen += 1
        tally.bytes_seen += entry.size_bytes
        if entry.file_id is not None:
            if entry.file_id in tally.seen_ids:
                tally.duplicate_bytes += entry.size_bytes
            else:
                tally.seen_ids.add(entry.file_id)
    elif kind == "folder":
        frame.pending.append(entry.path)
    elif kind == "link":
        tally.skipped_links += 1
    elif kind == "unreadable":
        tally.record(entry.path, entry.error or "OSError")
