"""Asesor de archivos del sistema (T2C.6): puro.

`pagefile.sys` y la copia de seguridad de Windows (restauración del sistema)
tienen un lado "configurado" (lo que Windows reservó o permite) y un lado
"real" (lo que de verdad se usó). Esta fase solo los muestra y los explica;
nunca los toca (son del catálogo `no_tocar`, sección 8 del plan).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageFileAdvice:
    """El archivo de paginación de una unidad. `configured_bytes` es `None`
    cuando Windows lo administra solo (no hay un tamaño fijo que mostrar, el
    caso más común); `peak_used_bytes` es `None` si no se pudo leer."""

    path: str
    configured_bytes: int | None
    peak_used_bytes: int | None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a page file advice needs a path")
        if self.configured_bytes is not None and self.configured_bytes < 0:
            raise ValueError("configured_bytes cannot be negative")
        if self.peak_used_bytes is not None and self.peak_used_bytes < 0:
            raise ValueError("peak_used_bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class RestoreAdvice:
    """El almacenamiento de la restauración del sistema (copias de seguridad
    de Windows) de una unidad. `configured_bytes` es el máximo permitido;
    `used_bytes`, `None` si no hay ningún punto de restauración todavía (no
    es una falla, es el estado normal si nunca se usó)."""

    drive: str
    configured_bytes: int
    used_bytes: int | None

    def __post_init__(self) -> None:
        if not self.drive:
            raise ValueError("a restore advice needs a drive")
        if self.configured_bytes < 0:
            raise ValueError("configured_bytes cannot be negative")
        if self.used_bytes is not None and self.used_bytes < 0:
            raise ValueError("used_bytes cannot be negative")
