"""Volúmenes del equipo: total, usado y libre de cada uno.

Solo lee: le pregunta al sistema operativo por el espacio de cada raíz
(`shutil.disk_usage`). No recorre carpetas ni toca archivos.
"""

import logging
import shutil
import sys
from collections.abc import Callable, Iterable

from discoclaro.domain.models import Volume
from discoclaro.platform import windows

logger = logging.getLogger(__name__)

DiskUsage = Callable[[str], tuple[int, int, int]]


def volume_name(root: str) -> str:
    """Nombre corto de un volumen: `C:\\` pasa a `C:`."""
    return root.rstrip("\\/") or root


def read_volume(root: str, disk_usage: DiskUsage = shutil.disk_usage) -> Volume:
    """Lee el espacio de `root`; lanza `OSError` si el volumen no se puede leer."""
    total, used, free = disk_usage(root)
    return Volume(
        name=volume_name(root),
        root=root,
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
    )


def default_roots() -> list[str]:
    """Raíces de los discos fijos del equipo (por ahora solo en Windows)."""
    if sys.platform != "win32":
        raise NotImplementedError("only Windows is supported for now")
    return windows.fixed_drive_roots()


def list_volumes(
    roots: Iterable[str] | None = None,
    disk_usage: DiskUsage = shutil.disk_usage,
) -> list[Volume]:
    """Volúmenes de `roots` (por defecto, los discos fijos), en el mismo orden.

    Un volumen que no se puede leer (sin permiso, no listo) se omite y se registra;
    los demás siguen. Cualquier otro error se propaga: no viene del disco.
    """
    volumes = []
    for root in default_roots() if roots is None else roots:
        try:
            volumes.append(read_volume(root, disk_usage))
        except OSError as error:
            # `%r` escapa saltos de línea y caracteres de control del texto externo.
            logger.warning(
                "volume skipped: %r (%r)", root, error.strerror or type(error).__name__
            )
    return volumes
