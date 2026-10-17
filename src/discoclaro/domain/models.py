"""Datos puros del núcleo: sin disco, sin SQLite, sin red y sin Windows."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Volume:
    """Un volumen del equipo con su espacio total, usado y libre, en bytes.

    `name` es la clave corta (`C:`) que usan la base y la configuración; `root` es la
    ruta desde la que se recorre (`C:\\`).
    """

    name: str
    root: str
    total_bytes: int
    used_bytes: int
    free_bytes: int

    def __post_init__(self) -> None:
        if not self.name or not self.root:
            raise ValueError("a volume needs a name and a root")
        if min(self.total_bytes, self.used_bytes, self.free_bytes) < 0:
            raise ValueError("volume sizes cannot be negative")


@dataclass(frozen=True, slots=True)
class Node:
    """Una carpeta con el tamaño de TODO su contenido, subcarpetas incluidas.

    `depth` es 0 para la raíz del recorrido y `parent_path` es `None` en la raíz.
    `n_files` cuenta los archivos de todo el subárbol. `rule_id` lo asigna después
    el clasificador.
    """

    path: str
    parent_path: str | None
    size_bytes: int
    n_files: int
    depth: int
    rule_id: str | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a node needs a path")
        if min(self.size_bytes, self.n_files, self.depth) < 0:
            raise ValueError("node sizes, counts and depth cannot be negative")


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Una ruta que no se pudo leer y el nombre estable del error del sistema."""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Resultado de recorrer un árbol de carpetas.

    `nodes` va en orden posterior: cada carpeta aparece después de sus subcarpetas y
    la raíz es la última. `unreadable` guarda solo las primeras rutas ilegibles, pero
    `unreadable_count` las cuenta todas. `skipped_links` cuenta los enlaces y puntos de
    reanálisis que no se siguieron.
    """

    root: str
    nodes: tuple[Node, ...]
    unreadable_count: int
    unreadable: tuple[Unreadable, ...]
    skipped_links: int

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("a scan result needs at least the root node")
        if self.unreadable_count < len(self.unreadable) or self.skipped_links < 0:
            raise ValueError("inconsistent scan counters")

    @property
    def scanned_bytes(self) -> int:
        """Bytes de todo lo que se pudo leer (el total de la raíz)."""
        return self.nodes[-1].size_bytes

    @property
    def n_files(self) -> int:
        """Archivos de todo el árbol (el total de la raíz)."""
        return self.nodes[-1].n_files
