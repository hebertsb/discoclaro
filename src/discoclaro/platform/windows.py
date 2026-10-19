"""Lo específico de Windows: discos, archivos de sistema, carpeta de datos y permisos.

Aquí irá también el Programador de tareas. Todo lo que dependa de Windows vive en este
módulo, para poder agregar Linux y macOS sin tocar el núcleo.
"""

import ctypes
import logging
import msvcrt
import ntpath
import os
import stat
import subprocess
import winreg
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable, Mapping, Sequence
from ctypes import wintypes
from pathlib import Path

from discoclaro.domain.classify import ENVIRONMENT_VARIABLES
from discoclaro.domain.models import Program, SystemFile

logger = logging.getLogger(__name__)

DRIVE_FIXED = 3  # valor de GetDriveTypeW para un disco fijo

# Archivos de sistema grandes que cuelgan de la raíz de un volumen.
SYSTEM_FILE_NAMES = ("pagefile.sys", "hiberfil.sys", "swapfile.sys")

# Tecla para pausar un escaneo a propósito (T1.10), sin distinguir mayúsculas.
PAUSE_KEY = b"p"

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


def _env_app_dir(environ: Mapping[str, str] | None, variable: str) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get(variable, "")
    if not base or not ntpath.isabs(base) or not ntpath.splitdrive(base)[0]:
        raise OSError(f"{variable} is not set to an absolute path")
    return Path(base) / APP_FOLDER


def default_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Carpeta de datos propia de discoclaro: `%LOCALAPPDATA%\\discoclaro`.

    Ahí se guardan las fotos; no se sincroniza entre equipos. Lanza `OSError` si
    `LOCALAPPDATA` falta o no es una ruta absoluta con unidad. `environ` se puede
    inyectar para probar sin tocar las variables reales.
    """
    return _env_app_dir(environ, "LOCALAPPDATA")


def default_config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Carpeta de configuración propia de discoclaro: `%APPDATA%\\discoclaro`.

    Ahí va `config.toml`, el que edita el usuario a mano (T2.6); a diferencia de la
    carpeta de datos, esta sí es la "roaming" que Windows puede llevar entre equipos.
    Lanza `OSError` si `APPDATA` falta o no es una ruta absoluta con unidad.
    """
    return _env_app_dir(environ, "APPDATA")


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


def pause_requested(
    kbhit: Callable[[], bool] = msvcrt.kbhit,
    getch: Callable[[], bytes] = msvcrt.getch,
) -> bool:
    """Si se pulsó la tecla de pausar (T1.10) desde la última vez que se miró.

    Lee y descarta todo lo que haya esperando en el teclado: nunca bloquea, y una
    tecla que no es la de pausar se pierde, como cualquier tecla que se aprieta sin
    que el programa la lea en ese momento. `kbhit` y `getch` se pueden inyectar para
    probar sin un teclado real.
    """
    pressed = False
    while kbhit():
        if getch().lower() == PAUSE_KEY:
            pressed = True
    return pressed


SCHEDULED_TASK_TIMEOUT_S = 15.0


class ScheduledTaskError(OSError):
    """`schtasks.exe` devolvió un error al crear, quitar o consultar una tarea."""


def _schtasks_path(environ: Mapping[str, str] | None = None) -> Path:
    """Ruta completa de `schtasks.exe`: nunca se confía en que esté en el PATH."""
    environ = os.environ if environ is None else environ
    windir = environ.get("WINDIR", "")
    if not windir:
        raise OSError("WINDIR is not set")
    return Path(windir) / "System32" / "schtasks.exe"


def create_scheduled_task(
    name: str,
    command: Sequence[str],
    *,
    schedule: str,
    start_time: str | None = None,
    event_channel: str | None = None,
    event_query: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Crea (o reemplaza) la tarea `name`, que corre `command` sin elevar.

    `command` es el ejecutable y sus argumentos, nunca un texto para una terminal:
    se le pasa a `schtasks.exe` ya armado con las mismas reglas de citado que vería
    Windows (`command_line`), sin pasar por ninguna terminal. `start_time` no hace
    falta para `ONLOGON` ni `HOURLY` (T2B.8): `schtasks` usa la hora actual, que no
    importa para esos dos. `event_channel`/`event_query` son para `ONEVENT`
    (T2B.8): el canal (`/EC`, por ejemplo `"Application"`) y la condición en XPath
    (`/MO`) que dispara la tarea. Lanza `ScheduledTaskError` si `schtasks.exe` no
    pudo crearla. `run` se puede inyectar para probar sin tocar el Programador de
    tareas real.
    """
    args = [
        str(_schtasks_path()),
        "/create",
        "/tn",
        name,
        "/tr",
        command_line(command),
        "/sc",
        schedule,
    ]
    if start_time is not None:
        args += ["/st", start_time]
    if event_channel is not None:
        args += ["/ec", event_channel]
    if event_query is not None:
        args += ["/mo", event_query]
    args.append("/f")
    result = run(args, capture_output=True, text=True, timeout=SCHEDULED_TASK_TIMEOUT_S)
    if result.returncode != 0:
        raise ScheduledTaskError(result.returncode, result.stderr.strip())


def remove_scheduled_task(
    name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Quita la tarea `name`. Devuelve si existía: que no exista no es un error."""
    result = run(
        [str(_schtasks_path()), "/delete", "/tn", name, "/f"],
        capture_output=True,
        text=True,
        timeout=SCHEDULED_TASK_TIMEOUT_S,
    )
    return result.returncode == 0


def scheduled_task_exists(
    name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Si la tarea `name` ya existe en el Programador de tareas."""
    result = run(
        [str(_schtasks_path()), "/query", "/tn", name],
        capture_output=True,
        text=True,
        timeout=SCHEDULED_TASK_TIMEOUT_S,
    )
    return result.returncode == 0


def scheduled_task_command(
    name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[str, str] | None:
    """El ejecutable y los argumentos que de verdad tiene registrados `name` ahora
    mismo (T2.13), o `None` si no existe.

    Lee el XML nativo del Programador de tareas (`/xml`), no el texto de `/query`
    sin más: esos nombres de campo del XML son siempre los mismos, sin importar el
    idioma de Windows (el texto de `/query` normal sí se traduce). Si el XML no se
    pudo leer o no tiene la acción esperada, también devuelve `None`: no hay nada
    confiable para comparar.
    """
    result = run(
        [str(_schtasks_path()), "/query", "/tn", name, "/xml"],
        capture_output=True,
        text=True,
        timeout=SCHEDULED_TASK_TIMEOUT_S,
    )
    if result.returncode != 0:
        return None
    if "<!DOCTYPE" in result.stdout:
        # Un XML de tarea de verdad nunca trae DOCTYPE; si aparece uno, podría ser
        # un intento de bomba de entidades. Se rechaza antes de parsear, sin gastar
        # tiempo en seguir leyéndolo.
        return None
    try:
        root = ElementTree.fromstring(result.stdout)  # noqa: S314
    except ElementTree.ParseError:
        return None
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    command = root.find(".//t:Actions/t:Exec/t:Command", namespace)
    if command is None or not command.text:
        return None
    arguments = root.find(".//t:Actions/t:Exec/t:Arguments", namespace)
    return command.text, (arguments.text or "" if arguments is not None else "")


def environment_variables(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Los valores reales de las variables que aceptan los patrones de las reglas.

    Da `{"%LOCALAPPDATA%": "C:\\Users\\x\\AppData\\Local", ...}`. Las que falten en
    el entorno no se incluyen: esas partes del patrón nunca coincidirán con nada
    (ver `domain/classify.py`), en vez de fallar. `environ` se puede inyectar para
    probar sin tocar las variables de entorno reales.
    """
    environ = os.environ if environ is None else environ
    return {
        token: environ[token.strip("%")]
        for token in ENVIRONMENT_VARIABLES
        if token.strip("%") in environ
    }


# Las tres claves "Desinstalar" del registro (T2B.1): la de la máquina, la de los
# programas de 32 bits en un Windows de 64, y la del usuario actual.
_UNINSTALL_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
_UNINSTALL_ROOTS = (
    (winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_PATH),
    (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + _UNINSTALL_PATH),
    (winreg.HKEY_CURRENT_USER, _UNINSTALL_PATH),
)


def _registry_subkey_values(hive: int, path: str) -> dict[str, object] | None:
    """Los nombres y valores de una subclave, o `None` si no se pudo abrir."""
    try:
        key = winreg.OpenKey(hive, path)
    except OSError:
        return None
    try:
        values: dict[str, object] = {}
        index = 0
        while True:
            try:
                name, data, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            values[name] = data
            index += 1
        return values
    finally:
        winreg.CloseKey(key)


def _registry_entries(hive: int, path: str) -> Iterable[dict[str, object]]:
    """Las subclaves directas de una clave "Desinstalar", cada una con sus valores."""
    try:
        key = winreg.OpenKey(hive, path)
    except OSError:
        return
    try:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(key, index)
            except OSError:
                return
            index += 1
            values = _registry_subkey_values(hive, f"{path}\\{subkey_name}")
            if values is not None:
                yield values
    finally:
        winreg.CloseKey(key)


def _read_all_uninstall_entries() -> Iterable[dict[str, object]]:
    """Las subclaves de las tres raíces "Desinstalar" reales, una tras otra."""
    for hive, path in _UNINSTALL_ROOTS:
        yield from _registry_entries(hive, path)


def _optional_registry_str(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _build_program(raw: dict[str, object]) -> Program | None:
    """Arma un `Program` de una subclave ya leída, o `None` si no tiene nombre.

    Sin `DisplayName` no es un programa que alguien instaló a propósito: son
    actualizaciones, componentes del sistema y otras subclaves que no le sirven al
    usuario como inventario.
    """
    name = _optional_registry_str(raw, "DisplayName")
    if name is None:
        return None
    size_kb = raw.get("EstimatedSize")
    size_bytes = size_kb * 1024 if isinstance(size_kb, int) else None
    return Program(
        name=name,
        version=_optional_registry_str(raw, "DisplayVersion"),
        publisher=_optional_registry_str(raw, "Publisher"),
        install_location=_optional_registry_str(raw, "InstallLocation"),
        install_date=_optional_registry_str(raw, "InstallDate"),
        estimated_size_bytes=size_bytes,
        uninstall_string=_optional_registry_str(raw, "UninstallString"),
    )


def installed_programs(
    *,
    read_entries: Callable[
        [], Iterable[dict[str, object]]
    ] = _read_all_uninstall_entries,
) -> list[Program]:
    """Los programas instalados, de las tres claves "Desinstalar" del registro.

    Es de solo lectura. `read_entries` se puede inyectar con datos simulados para
    probar sin tocar el registro real.
    """
    programs = []
    for raw in read_entries():
        program = _build_program(raw)
        if program is not None:
            programs.append(program)
    return programs
