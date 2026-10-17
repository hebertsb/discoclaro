"""Lo específico de Windows: qué discos tiene el equipo y sus archivos de sistema.

Aquí irán también la elevación y el Programador de tareas. Todo lo que dependa de
Windows vive en este módulo, para poder agregar Linux y macOS sin tocar el núcleo.
"""

import ctypes
import logging
import ntpath
import os
import stat
from collections.abc import Callable, Iterable

from discoclaro.domain.models import SystemFile

logger = logging.getLogger(__name__)

DRIVE_FIXED = 3  # valor de GetDriveTypeW para un disco fijo

# Archivos de sistema grandes que cuelgan de la raíz de un volumen.
SYSTEM_FILE_NAMES = ("pagefile.sys", "hiberfil.sys", "swapfile.sys")


def fixed_drive_roots(
    list_drives: Callable[[], Iterable[str]] | None = None,
    drive_type: Callable[[str], int] | None = None,
) -> list[str]:
    """Raíces (`C:\\`) de los discos fijos del equipo, en el orden del sistema.

    Se omiten red, CD y unidades extraíbles: un disco de red desconectado puede
    bloquear al consultarlo y una unidad extraíble vacía responde con error.
    Las dos funciones se pueden inyectar para probar sin tocar discos reales.
    """
    list_drives = list_drives or os.listdrives
    drive_type = drive_type or _kernel32_drive_type()
    return [root for root in list_drives() if drive_type(root) == DRIVE_FIXED]


def measure_system_files(
    root: str,
    lstat: Callable[[str], os.stat_result] = os.lstat,
) -> list[SystemFile]:
    """Mide los archivos de sistema grandes que haya en la raíz de un volumen.

    El archivo de paginación, el de hibernación y el swapfile ocupan disco pero no son
    datos del usuario: se miden aparte para no contarlos como espacio no explicado.
    Usa `lstat`, así que no sigue enlaces, y solo acepta archivos regulares. Los que no
    existen se omiten sin ruido; los que existen pero no se pueden leer se registran
    y se omiten. `lstat` se puede inyectar para probar sin discos reales.
    """
    base = root.rstrip("\\/") + "\\"
    found = []
    for name in SYSTEM_FILE_NAMES:
        path = ntpath.join(base, name)
        try:
            info = lstat(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            logger.warning(
                "system file not measured: %r (%r)", path, type(error).__name__
            )
            continue
        if stat.S_ISREG(info.st_mode):
            found.append(SystemFile(path=path, size_bytes=info.st_size))
    return found


def _kernel32_drive_type() -> Callable[[str], int]:
    """`GetDriveTypeW` de Windows (solo existe en Windows)."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return kernel32.GetDriveTypeW
