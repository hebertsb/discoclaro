"""Datos puros del núcleo: sin disco, sin SQLite, sin red y sin Windows."""

from dataclasses import dataclass
from datetime import datetime


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


ENTRY_KINDS = frozenset({"file", "folder", "link", "other", "unreadable"})


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """Un elemento de una carpeta, ya clasificado para el recorrido.

    `kind` es `file`, `folder`, `link` (un enlace o punto de reanálisis que no se
    sigue), `other` (algo que no se mide) o `unreadable` (no se pudo leer; `error`
    guarda el nombre del error del sistema). `size_bytes` es lo que ocupa un archivo
    en disco. `file_id` identifica su contenido en el volumen, para reconocer enlaces
    duros, y vale `None` si no se conoce.
    """

    name: str
    path: str
    kind: str
    size_bytes: int = 0
    file_id: bytes | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ENTRY_KINDS:
            raise ValueError(f"unknown entry kind: {self.kind!r}")
        if self.size_bytes < 0:
            raise ValueError("entry size cannot be negative")


@dataclass(frozen=True, slots=True)
class Unreadable:
    """Una ruta que no se pudo leer y el nombre estable del error del sistema."""

    path: str
    reason: str


# Un archivo individual se guarda aparte (T2.5) solo si llega a este tamaño; el resto
# no se nombra, igual que las carpetas chicas (ver `keep_node` en services/scan_service
# .py, que usa el mismo número). Así `growth` puede nombrar un archivo grande nuevo sin
# que la base guarde cada archivo del disco.
LARGE_FILE_MIN_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LargeFile:
    """Un archivo grande, guardado con su ruta para poder nombrarlo en `growth`."""

    path: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a large file needs a path")
        if self.size_bytes < 0:
            raise ValueError("large file size cannot be negative")


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Resultado de recorrer un árbol de carpetas.

    `nodes` va en orden posterior: cada carpeta aparece después de sus subcarpetas y
    la raíz es la última. `unreadable` guarda solo las primeras rutas ilegibles, pero
    `unreadable_count` las cuenta todas. `skipped_links` cuenta los enlaces y puntos de
    reanálisis que no se siguieron. `duplicate_bytes` son los bytes que ocupan de más
    los enlaces duros (el mismo contenido con otro nombre): cada carpeta cuenta su
    contenido entero, así que la raíz los incluye y el total real es la raíz menos esto.
    `large_files` son los archivos sueltos de al menos `LARGE_FILE_MIN_BYTES`.
    """

    root: str
    nodes: tuple[Node, ...]
    unreadable_count: int
    unreadable: tuple[Unreadable, ...]
    skipped_links: int
    duplicate_bytes: int = 0
    large_files: tuple[LargeFile, ...] = ()

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("a scan result needs at least the root node")
        if self.unreadable_count < len(self.unreadable) or self.skipped_links < 0:
            raise ValueError("inconsistent scan counters")
        if not 0 <= self.duplicate_bytes <= self.nodes[-1].size_bytes:
            raise ValueError("inconsistent duplicate bytes")

    @property
    def scanned_bytes(self) -> int:
        """Bytes de todo lo que se pudo leer (el total de la raíz)."""
        return self.nodes[-1].size_bytes

    @property
    def n_files(self) -> int:
        """Archivos de todo el árbol (el total de la raíz)."""
        return self.nodes[-1].n_files


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """Un aviso de cuánto lleva recorrido el escaneo, mientras sigue en curso.

    Los conteos son del total acumulado en esta misma corrida (no cuentan lo que ya
    venía de un punto de control retomado). `current_path` es la última carpeta que
    terminó de medirse.
    """

    folders: int
    files: int
    bytes_scanned: int
    current_path: str

    def __post_init__(self) -> None:
        if min(self.folders, self.files, self.bytes_scanned) < 0:
            raise ValueError("scan progress counts cannot be negative")


@dataclass(frozen=True, slots=True)
class ResumedSubtree:
    """Lo que ya se sabía de una carpeta de primer nivel, de un punto de control.

    `nodes` son esa carpeta y todas sus subcarpetas, en orden posterior (T1.10): no
    hace falta volver a leerla. `duplicate_bytes`, `unreadable_count` y
    `skipped_links` son lo que esa carpeta aportó al total del escaneo completo; se
    suman aparte al retomar porque no están dentro de `nodes`. `large_files` son los
    archivos grandes que esa carpeta había encontrado (T2.5).
    """

    nodes: tuple[Node, ...]
    duplicate_bytes: int = 0
    unreadable_count: int = 0
    skipped_links: int = 0
    large_files: tuple[LargeFile, ...] = ()

    def __post_init__(self) -> None:
        if not self.nodes:
            raise ValueError("a resumed subtree needs at least its own node")
        numbers = (self.duplicate_bytes, self.unreadable_count, self.skipped_links)
        if min(numbers) < 0:
            raise ValueError("resumed subtree counts cannot be negative")


@dataclass(frozen=True, slots=True)
class SystemFile:
    """Un archivo de sistema grande (como `pagefile.sys`) medido aparte."""

    path: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a system file needs a path")
        if self.size_bytes < 0:
            raise ValueError("system file size cannot be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    """Una foto de un volumen: lo que se midió en un momento dado.

    `taken_at` lleva siempre zona horaria. `id` lo asigna la base al guardar la foto,
    así que vale `None` en una foto que aún no se guardó.
    """

    taken_at: datetime
    volume: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    scanned_bytes: int
    system_files_bytes: int
    unreadable_bytes: int
    unreadable_count: int
    duration_s: float
    is_admin: bool
    duplicate_bytes: int = 0
    id: int | None = None

    def __post_init__(self) -> None:
        if self.taken_at.utcoffset() is None:
            raise ValueError("taken_at needs a time zone")
        if not self.volume:
            raise ValueError("a snapshot needs a volume")
        numbers = (
            self.total_bytes,
            self.used_bytes,
            self.free_bytes,
            self.scanned_bytes,
            self.system_files_bytes,
            self.unreadable_bytes,
            self.unreadable_count,
            self.duration_s,
            self.duplicate_bytes,
        )
        if min(numbers) < 0:
            raise ValueError("snapshot sizes, counts and duration cannot be negative")
        if self.duplicate_bytes > self.scanned_bytes:
            raise ValueError("duplicate bytes cannot exceed the scanned bytes")

    @property
    def is_volume_scan(self) -> bool:
        """Si es de un volumen completo (se guarda por su nombre, `C:`) y no de una
        carpeta (se guarda por su ruta)."""
        return "\\" not in self.volume and "/" not in self.volume


@dataclass(frozen=True, slots=True)
class Program:
    """Un programa instalado, leído del registro de Windows (T2B.1).

    Todo viene de afuera (el registro), así que se guarda tal cual, sin
    sanearlo: quien lo muestre tiene que escaparlo (`domain/policy.py`), igual que
    una ruta del escaneo. `uninstall_string` es el texto que Windows usaría para
    desinstalarlo; se guarda solo para mostrarlo, **nunca se ejecuta** desde
    discoclaro (catálogo cerrado de tareas, no comandos libres).
    """

    name: str
    version: str | None
    publisher: str | None
    install_location: str | None
    install_date: str | None
    estimated_size_bytes: int | None
    uninstall_string: str | None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a program needs a name")
        if self.estimated_size_bytes is not None and self.estimated_size_bytes < 0:
            raise ValueError("estimated size cannot be negative")
