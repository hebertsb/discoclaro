"""Lo específico de Windows: discos, archivos de sistema, carpeta de datos y permisos.

Aquí irá también el Programador de tareas. Todo lo que dependa de Windows vive en este
módulo, para poder agregar Linux y macOS sin tocar el núcleo.
"""

import ctypes
import logging
import ntpath
import os
import stat
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from ctypes import wintypes
from pathlib import Path

from discoclaro.domain.models import SystemFile

logger = logging.getLogger(__name__)

DRIVE_FIXED = 3  # valor de GetDriveTypeW para un disco fijo

# Archivos de sistema grandes que cuelgan de la raíz de un volumen.
SYSTEM_FILE_NAMES = ("pagefile.sys", "hiberfil.sys", "swapfile.sys")

APP_FOLDER = "discoclaro"

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
ERROR_CANCELLED = 1223  # el usuario rechazó la ventana de permisos (UAC)
WAIT_TIMEOUT = 0x00000102
MAX_WAIT_MS = 0xFFFFFFFE


class ElevationDeclined(OSError):
    """El usuario rechazó la ventana de permisos de Windows (UAC)."""


class UnsafeDataLocation(OSError):
    """La carpeta o el archivo no es seguro para escribir: enlace o varios nombres."""


class _ShellExecuteInfo(ctypes.Structure):
    """`SHELLEXECUTEINFOW`, la estructura de `ShellExecuteExW`."""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    )


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


def default_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Carpeta de datos propia de discoclaro: `%LOCALAPPDATA%\\discoclaro`.

    Es el único sitio donde discoclaro guarda lo suyo. Lanza `OSError` si
    `LOCALAPPDATA` falta o no es una ruta absoluta con unidad. `environ` se puede
    inyectar para probar sin tocar las variables reales.
    """
    environ = os.environ if environ is None else environ
    base = environ.get("LOCALAPPDATA", "")
    if not base or not ntpath.isabs(base) or not ntpath.splitdrive(base)[0]:
        raise OSError("LOCALAPPDATA is not set to an absolute path")
    return Path(base) / APP_FOLDER


def is_admin(is_user_an_admin: Callable[[], int] | None = None) -> bool:
    """Si este proceso tiene permisos de administrador (está elevado).

    Si no se puede saber, se asume que no. La función de Windows se puede inyectar.
    """
    try:
        return bool((is_user_an_admin or _shell32_is_user_an_admin)())
    except (OSError, AttributeError):
        return False


def command_line(parts: Sequence[str]) -> str:
    """La línea de comandos de Windows para esas partes, con sus comillas.

    Es la misma regla con la que se arma la orden que recibe Windows, así que sirve
    para mostrársela al usuario tal cual.
    """
    return subprocess.list2cmdline(list(parts))


def run_elevated(
    executable: str,
    arguments: Sequence[str],
    directory: str,
    timeout_s: float,
    *,
    apis: tuple[object, object] | None = None,
    last_error: Callable[[], int] | None = None,
) -> int:
    """Ejecuta un programa con permisos de administrador y espera su código de salida.

    Windows muestra su ventana de permisos (UAC); discoclaro nunca ve ni guarda una
    contraseña. El proceso corre sin ventana. `executable` y `directory` deben ser rutas
    absolutas: el proceso elevado no arranca en la carpeta actual. Lanza
    `ElevationDeclined` si el usuario rechaza el permiso, `TimeoutError` si pasa
    `timeout_s` sin que termine (no se puede cerrar desde un proceso sin permisos) y
    `OSError` si no se pudo lanzar. `apis` y `last_error` se inyectan para probar.
    """
    shell32, kernel32 = apis or _load_elevation_apis()
    get_last_error = last_error or ctypes.get_last_error
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(list(arguments))
    info.lpDirectory = directory
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.pointer(info)):
        code = get_last_error()
        if code == ERROR_CANCELLED:
            raise ElevationDeclined(code, "the permission prompt was declined")
        raise OSError(code, "the elevated process could not be started")
    handle = info.hProcess
    if not handle:
        raise OSError("the elevated process did not return a handle")
    try:
        wait_ms = min(int(timeout_s * 1000), MAX_WAIT_MS)
        if kernel32.WaitForSingleObject(handle, wait_ms) == WAIT_TIMEOUT:
            raise TimeoutError("the elevated process took too long")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.pointer(exit_code)):
            raise OSError(get_last_error(), "the exit code could not be read")
        return exit_code.value
    finally:
        kernel32.CloseHandle(handle)


def _load_elevation_apis() -> tuple[object, object]:
    """`shell32` y `kernel32` con los tipos de las funciones que se usan."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return shell32, kernel32


def _kernel32_drive_type() -> Callable[[str], int]:
    """`GetDriveTypeW` de Windows (solo existe en Windows)."""
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return kernel32.GetDriveTypeW


def _shell32_is_user_an_admin() -> int:
    """`IsUserAnAdmin` de Windows (solo existe en Windows)."""
    return ctypes.WinDLL("shell32").IsUserAnAdmin()


def ensure_safe_to_write(
    path: str | os.PathLike[str],
    lstat: Callable[[str | os.PathLike[str]], os.stat_result] = os.lstat,
) -> None:
    """Revalida que `path` no sea un enlace ni tenga otro nombre, justo antes de usarlo.

    Se llama justo antes de cada escritura propia de discoclaro (la carpeta de datos,
    la base y el resultado de la elevación): el tiempo entre comprobar y escribir es la
    ventana de un ataque de enlaces, así que revalidar antes de cada una es el punto.
    Si `path` todavía no existe no hay nada que proteger y no se lanza nada: se creará
    de cero. Lanza `UnsafeDataLocation` si es un enlace o una *junction* (atributo
    `FILE_ATTRIBUTE_REPARSE_POINT`), si tiene más de un nombre (`st_nlink > 1`, un
    enlace duro) o si ni se pudo mirar.
    """
    try:
        info = lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise UnsafeDataLocation(f"could not check before writing: {path!r}") from error
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeDataLocation(f"this is a link or a junction: {path!r}")
    if info.st_nlink > 1:
        raise UnsafeDataLocation(f"this has more than one name: {path!r}")
