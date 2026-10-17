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
