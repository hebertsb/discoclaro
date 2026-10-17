"""Lo específico de Windows. Hoy: qué discos tiene el equipo.

Aquí irán también la elevación, el Programador de tareas y los archivos de sistema.
Todo lo que dependa de Windows vive en este módulo, para poder agregar Linux y macOS
sin tocar el núcleo.
"""

import ctypes
import os
from collections.abc import Callable, Iterable

DRIVE_FIXED = 3  # valor de GetDriveTypeW para un disco fijo


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


def _kernel32_drive_type() -> Callable[[str], int]:
    """`GetDriveTypeW` de Windows (solo existe en Windows)."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return kernel32.GetDriveTypeW
