"""Cosas mal ubicadas (T2C.5): puro.

Cuando un programa tiene más de una ubicación configurada (como las
bibliotecas de Steam) y algo vive en el disco del sistema mientras hay otra
ubicación disponible en otro disco, eso puede ser un dato mal ubicado: no es
un error, es una sugerencia para revisar. Caso real que dio pie a esta tarea:
un juego de 74 GB en C: con una biblioteca de Steam ya registrada en D:.
"""

from collections.abc import Sequence
from dataclasses import dataclass


def _normalize_drive(drive: str) -> str:
    return drive.rstrip("\\/").upper()


@dataclass(frozen=True, slots=True)
class StorageLocation:
    """Dónde vive algo: `name`, en qué unidad (`drive`, como "C:") y cuánto
    ocupa."""

    name: str
    drive: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a storage location needs a name")
        if not self.drive:
            raise ValueError("a storage location needs a drive")
        if self.size_bytes < 0:
            raise ValueError("size cannot be negative")


@dataclass(frozen=True, slots=True)
class Misplaced:
    """Algo que vive en el disco del sistema mientras el mismo programa ya
    tiene otras ubicaciones configuradas, en `other_drives`."""

    name: str
    drive: str
    size_bytes: int
    other_drives: tuple[str, ...]


def find_misplaced(
    locations: Sequence[StorageLocation], system_drive: str
) -> list[Misplaced]:
    """Lo que vive en `system_drive` cuando el mismo programa también tiene
    ubicaciones configuradas en otras unidades (si no hay ninguna otra
    unidad en juego, no hay nada que comparar, y no se sugiere nada)."""
    normalized_system = _normalize_drive(system_drive)
    other_drives = sorted(
        {
            _normalize_drive(location.drive)
            for location in locations
            if _normalize_drive(location.drive) != normalized_system
        }
    )
    if not other_drives:
        return []
    return [
        Misplaced(
            name=location.name,
            drive=location.drive,
            size_bytes=location.size_bytes,
            other_drives=tuple(other_drives),
        )
        for location in locations
        if _normalize_drive(location.drive) == normalized_system
    ]
