"""Lo específico de Windows: discos, archivos de sistema, carpeta de datos y permisos.

Aquí irá también el Programador de tareas. Todo lo que dependa de Windows vive en este
módulo, para poder agregar Linux y macOS sin tocar el núcleo.
"""

import csv
import ctypes
import io
import logging
import msvcrt
import ntpath
import os
import stat
import subprocess
import sys
import time
import winreg
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterable, Mapping, Sequence
from ctypes import wintypes
from pathlib import Path

from discoclaro.domain.classify import ENVIRONMENT_VARIABLES
from discoclaro.domain.models import Program, SystemFile
from discoclaro.domain.system_advisor import PageFileAdvice, RestoreAdvice

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
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class ScheduledTaskError(OSError):
    """`schtasks.exe` devolvió un error al crear, quitar o consultar una tarea."""


def _run_hidden(
    run: Callable[..., subprocess.CompletedProcess],
    args: Sequence[str],
    *,
    timeout: float = SCHEDULED_TASK_TIMEOUT_S,
) -> subprocess.CompletedProcess:
    """Corre `args` sin abrir ninguna ventana de consola fugaz cuando usa el
    `subprocess.run` real; si `run` es un doble de prueba inyectado, le pasa
    exactamente los mismos argumentos de siempre para no acoplar los tests."""
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if run is subprocess.run and CREATE_NO_WINDOW:
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["errors"] = "replace"
    return run(list(args), **kwargs)


def background_python_executable(
    executable: str,
    *,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str:
    """Devuelve `pythonw.exe` en la misma carpeta que `executable` (`python.exe`)
    si existe, para que las tareas programadas en segundo plano no abran una
    ventana de consola por un instante. Si no existe, conserva `executable`."""
    directory, filename = ntpath.split(executable)
    if filename.lower() == "python.exe":
        candidate = ntpath.join(directory, "pythonw.exe")
        if exists(candidate):
            return candidate
    return executable


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
    result = _run_hidden(run, args, timeout=SCHEDULED_TASK_TIMEOUT_S)
    if result.returncode != 0:
        raise ScheduledTaskError(result.returncode, result.stderr.strip())


def remove_scheduled_task(
    name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Quita la tarea `name`. Devuelve si existía: que no exista no es un error."""
    result = _run_hidden(
        run,
        [str(_schtasks_path()), "/delete", "/tn", name, "/f"],
        timeout=SCHEDULED_TASK_TIMEOUT_S,
    )
    return result.returncode == 0


def scheduled_task_exists(
    name: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Si la tarea `name` ya existe en el Programador de tareas."""
    result = _run_hidden(
        run,
        [str(_schtasks_path()), "/query", "/tn", name],
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
    result = _run_hidden(
        run,
        [str(_schtasks_path()), "/query", "/tn", name, "/xml"],
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


_PROGRAMS_CACHE_TTL_S = 15.0
_programs_cache: tuple[float, list[Program]] | None = None


def invalidate_installed_programs_cache() -> None:
    """Invalida la caché en memoria del inventario de programas instalados."""
    global _programs_cache
    _programs_cache = None


def installed_programs(
    *,
    read_entries: Callable[
        [], Iterable[dict[str, object]]
    ] = _read_all_uninstall_entries,
) -> list[Program]:
    """Los programas instalados, de las tres claves "Desinstalar" del registro.

    Es de solo lectura. `read_entries` se puede inyectar con datos simulados para
    probar sin tocar el registro real. Cuando usa el lector real del registro,
    cachea el listado por 15 s para que cambiar de vista en el dashboard sea
    instantáneo.
    """
    global _programs_cache
    use_cache = read_entries is _read_all_uninstall_entries
    now_mono = time.monotonic()
    if use_cache and _programs_cache is not None:
        cached_at, cached_list = _programs_cache
        if now_mono - cached_at < _PROGRAMS_CACHE_TTL_S:
            return list(cached_list)

    programs = []
    for raw in read_entries():
        program = _build_program(raw)
        if program is not None:
            programs.append(program)

    if use_cache:
        _programs_cache = (now_mono, list(programs))
    return programs


def steam_install_path(
    *,
    read_values: Callable[
        [int, str], dict[str, object] | None
    ] = _registry_subkey_values,
) -> str | None:
    """Dónde está instalado Steam (T2C.1), de su propia clave del registro
    (`HKCU\\Software\\Valve\\Steam\\SteamPath`), o `None` si no está instalado.
    De solo lectura. `read_values` se puede inyectar para probar."""
    values = read_values(winreg.HKEY_CURRENT_USER, "Software\\Valve\\Steam")
    if values is None:
        return None
    path = values.get("SteamPath")
    return path if isinstance(path, str) and path else None


def docker_vhdx_path(
    *,
    environ: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Dónde está, de verdad, el disco virtual de Docker (T2C.2): el que usa
    el motor WSL2 (`%LOCALAPPDATA%\\Docker\\wsl\\disk\\docker_data.vhdx`,
    confirmado real en esta máquina), o `None` si no existe (no está
    instalado, o usa el otro motor de Docker Desktop, Hyper-V, que guarda el
    disco en otro lado). `exists` se puede inyectar para probar."""
    environ = os.environ if environ is None else environ
    local_app_data = environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None
    path = str(Path(local_app_data) / "Docker" / "wsl" / "disk" / "docker_data.vhdx")
    return path if exists(path) else None


def _wsl_path(environ: Mapping[str, str] | None = None) -> Path:
    """Ruta completa de `wsl.exe`: nunca se confía en que esté en el PATH."""
    environ = os.environ if environ is None else environ
    windir = environ.get("WINDIR", "")
    if not windir:
        raise OSError("WINDIR is not set")
    return Path(windir) / "System32" / "wsl.exe"


def run_in_wsl(
    distro: str,
    command: Sequence[str],
    *,
    timeout: float = SCHEDULED_TASK_TIMEOUT_S,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    """Corre `command` (lista de argumentos, nunca un texto para una terminal)
    dentro de la distribución WSL `distro`. `run` se puede inyectar para
    probar sin ejecutar nada de verdad."""
    return _run_hidden(
        run,
        [str(_wsl_path()), "-d", distro, *command],
        timeout=timeout,
    )


def system_drive(environ: Mapping[str, str] | None = None) -> str:
    """La unidad donde está instalado Windows (T2C.5), de `%SystemDrive%`
    ("C:" en casi todas las instalaciones reales). Lanza `OSError` si no está
    fijada: sin esto no hay con qué comparar un dato mal ubicado."""
    environ = os.environ if environ is None else environ
    drive = environ.get("SystemDrive", "")
    if not drive:
        raise OSError("SystemDrive is not set")
    return drive


def _powershell_path(environ: Mapping[str, str] | None = None) -> Path:
    """Ruta completa de `powershell.exe`: nunca se confía en que esté en el PATH."""
    environ = os.environ if environ is None else environ
    windir = environ.get("WINDIR", "")
    if not windir:
        raise OSError("WINDIR is not set")
    return Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _query_cim_csv(
    command: str,
    *,
    timeout: float = SCHEDULED_TASK_TIMEOUT_S,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[dict[str, str]] | None:
    """Corre `command` (ya armado, de solo lectura: `Get-CimInstance ... |
    ConvertTo-Csv`) por PowerShell y lo lee como filas de texto. `None` si
    falló o tardó de más; lista vacía si no hay ninguna fila (una clase CIM
    sin ninguna instancia, el caso normal de un archivo de paginación
    administrado por el sistema, o de una unidad sin ningún punto de
    restauración)."""
    try:
        result = _run_hidden(
            run,
            [
                str(_powershell_path()),
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                command,
            ],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 1_000_000:
        return None
    if not result.stdout.strip():
        return []
    return list(csv.DictReader(io.StringIO(result.stdout)))


def pagefile_advice(
    volume: str,
    *,
    query: Callable[[str], list[dict[str, str]] | None] = _query_cim_csv,
) -> PageFileAdvice | None:
    """El archivo de paginación de `volume` ("C:"), de su propia huella en
    Windows (T2C.6): lo que Windows tiene configurado (`None` si lo
    administra solo, el caso más común) y el pico de uso real. `None` si no
    hay ningún archivo de paginación en esa unidad, o si no se pudo
    preguntar."""
    usage_rows = query(
        "Get-CimInstance Win32_PageFileUsage | "
        "Select-Object Name,AllocatedBaseSize,PeakUsage | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    if not usage_rows:
        return None
    drive = volume.rstrip("\\/").upper()
    matching = [
        row for row in usage_rows if row.get("Name", "").upper().startswith(drive)
    ]
    if not matching:
        return None
    row = matching[0]
    path = row.get("Name", "")
    peak = _optional_mb_to_bytes(row.get("PeakUsage"))
    setting_rows = query(
        "Get-CimInstance Win32_PageFileSetting | "
        "Select-Object Name,MaximumSize | ConvertTo-Csv -NoTypeInformation"
    )
    configured = None
    if setting_rows:
        for setting in setting_rows:
            if setting.get("Name", "").upper().startswith(drive):
                configured = _optional_bytes(setting.get("MaximumSize"))
                break
    return PageFileAdvice(path=path, configured_bytes=configured, peak_used_bytes=peak)


def restore_advice(
    volume: str,
    total_bytes: int,
    *,
    query: Callable[[str], list[dict[str, str]] | None] = _query_cim_csv,
) -> RestoreAdvice | None:
    """El almacenamiento de la restauración del sistema de `volume` ("C:")
    (T2C.6): el máximo configurado (un porcentaje de `total_bytes`, lo único
    que da Windows) y lo que de verdad hay usado ahora (`None` si no hay
    ningún punto de restauración todavía). `None` si no se pudo preguntar la
    configuración."""
    config_rows = query(
        "Get-CimInstance -Namespace root\\default -ClassName SystemRestoreConfig | "
        "Select-Object DiskPercent | ConvertTo-Csv -NoTypeInformation"
    )
    if not config_rows:
        return None
    percent = _optional_bytes(config_rows[0].get("DiskPercent"))
    if percent is None:
        return None
    configured_bytes = round(total_bytes * percent / 100)
    storage_rows = query(
        "Get-CimInstance Win32_ShadowStorage | "
        "Select-Object Volume,UsedSpace | ConvertTo-Csv -NoTypeInformation"
    )
    used = None
    if storage_rows:
        drive = volume.rstrip("\\/").upper()
        for row in storage_rows:
            if drive in row.get("Volume", "").upper():
                used = _optional_bytes(row.get("UsedSpace"))
                break
    return RestoreAdvice(
        drive=volume, configured_bytes=configured_bytes, used_bytes=used
    )


# Constantes de MessageBoxW (user32.dll) para confirmación nativa de Windows.
MB_YESNO = 0x00000004
MB_ICONWARNING = 0x00000030
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
IDYES = 6


def confirm_native_dialog(
    title: str,
    message: str,
    *,
    message_box: Callable[[int, str, str, int], int] | None = None,
) -> bool:
    """Muestra un diálogo nativo de Windows (`MessageBoxW`, fuera del control del
    navegador) en primer plano y devuelve `True` solo si el usuario pulsa 'Sí'.

    Cumple la regla de seguridad del proyecto (amenaza 2 de la sección 8.1):
    ninguna acción ni comando disparado desde la API local se ejecuta sin que el
    usuario lo acepte en una ventana real del sistema operativo en ese instante.
    `message_box` se puede inyectar para probar sin abrir ventanas reales.
    """
    box = message_box or _user32_message_box()
    flags = MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND
    return box(0, message, title, flags) == IDYES


def _user32_message_box() -> Callable[[int, str, str, int], int]:
    user32 = ctypes.WinDLL("user32")
    user32.MessageBoxW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    user32.MessageBoxW.restype = ctypes.c_int
    return user32.MessageBoxW


def open_in_explorer(
    path: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Abre `path` en el Explorador de Windows (`explorer.exe`). Si `path` es un
    archivo existente, abre su carpeta contenedora con `/select,<archivo>` para
    dejarlo resaltado; si es una carpeta, la abre directamente."""
    if not path or not os.path.exists(path):
        return False
    windir = os.environ.get("WINDIR", r"C:\Windows")
    explorer = str(Path(windir) / "explorer.exe")
    norm = os.path.normpath(path)
    args = [explorer, norm] if os.path.isdir(norm) else [explorer, f"/select,{norm}"]
    try:
        run(args, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def send_path_to_recycle_bin(
    path: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Envía un archivo o carpeta a la Papelera de reciclaje de Windows usando la
    API nativa `Microsoft.VisualBasic.FileIO.FileSystem` desde PowerShell (sin
    dependencias externas, sin `shell=True`, pasando la ruta por variable de
    entorno para que ningún carácter especial se interprete como código)."""
    if not path or not os.path.exists(path):
        return False
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "$p = $env:DISCOCLARO_TRASH_TARGET; "
        "if (Test-Path -LiteralPath $p -PathType Container) { "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        "$p, 'OnlyErrorDialogs', 'SendToRecycleBin') "
        "} elseif (Test-Path -LiteralPath $p -PathType Leaf) { "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        "$p, 'OnlyErrorDialogs', 'SendToRecycleBin') "
        "} else { exit 1 }"
    )
    env = {**os.environ, "DISCOCLARO_TRASH_TARGET": os.path.normpath(path)}
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 60.0,
        "env": env,
    }
    if run is subprocess.run and CREATE_NO_WINDOW:
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["errors"] = "replace"
    try:
        result = run(
            [
                str(_powershell_path()),
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def create_desktop_shortcut(
    *,
    port: int = 8347,
    python: str = sys.executable,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Crea un acceso directo `DiscoClaro Dashboard.lnk` en el Escritorio real del
    usuario que lanza `pythonw.exe -m discoclaro dashboard` sin abrir ninguna
    ventana de consola y con el icono oficial `discoclaro.ico`."""
    from discoclaro.platform.icon import ensure_discoclaro_ico

    runner = background_python_executable(python)
    workdir = str(Path(__file__).resolve().parents[3])
    icon_target = Path(workdir) / "src" / "discoclaro" / "assets" / "discoclaro.ico"
    try:
        ensure_discoclaro_ico(icon_target)
    except OSError:
        icon_target = default_data_dir() / "discoclaro.ico"
        ensure_discoclaro_ico(icon_target)
    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "if (-not $desktop) { exit 1 }; "
        "$lnkPath = Join-Path $desktop 'DiscoClaro Dashboard.lnk'; "
        "$wsh = New-Object -ComObject WScript.Shell; "
        "$sc = $wsh.CreateShortcut($lnkPath); "
        "$sc.TargetPath = $env:DISCOCLARO_SC_TARGET; "
        "$sc.Arguments = $env:DISCOCLARO_SC_ARGS; "
        "$sc.WorkingDirectory = $env:DISCOCLARO_SC_WORKDIR; "
        "$sc.IconLocation = $env:DISCOCLARO_SC_ICON + ',0'; "
        "$sc.Description = 'DiscoClaro - Analizador y Monitor de Disco sin consola'; "
        "$sc.WindowStyle = 7; "
        "$sc.Save(); "
        "Write-Output $lnkPath"
    )
    env = {
        **os.environ,
        "DISCOCLARO_SC_TARGET": runner,
        "DISCOCLARO_SC_ARGS": f"-m discoclaro dashboard --port {int(port)}",
        "DISCOCLARO_SC_WORKDIR": workdir,
        "DISCOCLARO_SC_ICON": str(icon_target),
    }
    kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 15.0,
        "env": env,
    }
    if run is subprocess.run and CREATE_NO_WINDOW:
        kwargs["creationflags"] = CREATE_NO_WINDOW
        kwargs["errors"] = "replace"
    try:
        result = run(
            [
                str(_powershell_path()),
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = (result.stdout or "").strip()
    return out or str(Path.home() / "Desktop" / "DiscoClaro Dashboard.lnk")


def launch_background_dashboard(
    *,
    port: int = 8347,
    no_open: bool = False,
    python: str = sys.executable,
) -> int:
    """Inicia el servidor del dashboard en segundo plano con `pythonw.exe`
    desacoplado de la consola actual para que el usuario pueda cerrar la
    terminal sin apagar el dashboard."""
    runner = background_python_executable(python)
    workdir = str(Path(__file__).resolve().parents[3])
    cmd = [runner, "-m", "discoclaro", "dashboard", "--port", str(int(port))]
    if no_open:
        cmd.append("--no-open")
    detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    flags = CREATE_NO_WINDOW | detached
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    return int(proc.pid)


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


def recycle_bin_stats() -> tuple[int, int]:
    """Devuelve `(total_bytes, total_items)` acumulados en la Papelera de
    reciclaje de TODOS los discos de Windows usando `SHQueryRecycleBinW`."""
    try:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        hr = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if hr != 0:
            return 0, 0
        return max(0, int(info.i64Size)), max(0, int(info.i64NumItems))
    except (AttributeError, OSError):
        return 0, 0


def empty_recycle_bin() -> bool:
    """Vacía completamente la Papelera de reciclaje de Windows en todos los
    discos usando `SHEmptyRecycleBinW` (sin ventana adicional, tras la
    confirmación nativa de discoclaro)."""
    try:
        flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        # S_OK (0) o E_UNEXPECTED (-2147418113 cuando ya estaba vacía)
        return hr in (0, -2147418113)
    except (AttributeError, OSError):
        return False


def _optional_mb_to_bytes(value: str | None) -> int | None:

    parsed = _optional_bytes(value)
    return None if parsed is None else parsed * 1024 * 1024


def _optional_bytes(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None
