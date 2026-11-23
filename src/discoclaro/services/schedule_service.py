"""Caso de uso "programar" (T2.8, T2.13, T2B.8): instala, quita y consulta las
tareas de Windows.

Crea cuatro tareas en el Programador de tareas, todas sin elevar y con la ruta
absoluta del intérprete que ya está corriendo, para que el disco (y el inventario
de programas) se vigile solo, sin dejar un proceso residente (la periodicidad la
da Windows, no discoclaro): escanea el destino elegido, revisa los avisos, y
revisa el inventario de programas cada hora y al terminar de instalarse un
programa por MSI (T2B.8). `status()` también compara lo que de verdad tiene
registrado cada tarea contra lo esperado, para avisar si alguien la alteró a mano
(T2.13): otro ejecutable, u otros argumentos que no sean exactamente los que arma
`install()`.

**Sin disparador "al iniciar sesión", a propósito:** el plan lo pedía además de
"cada hora", pero crear una tarea con disparador `ONLOGON` (probado real,
2026-10-18) da "Acceso denegado" en esta cuenta sin estar elevada —aunque sea
administradora, UAC le da un token "solo para denegar" al correr sin elevar—,
mientras que `HOURLY` y `ONEVENT` sí se crean sin pedir nada. Pedir elevación
solo para esto rompería la regla de seguridad del proyecto (estas tareas corren
sin elevar); `HOURLY` ya cubre el caso con, como mucho, 1 hora de atraso —
incluida la primera revisión después de iniciar sesión—, así que se decidió no
agregarlo. Ver la nota completa de T2B.8 en el plan.
"""

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Volume
from discoclaro.platform import windows
from discoclaro.services.scan_service import ScanError, locate, normalize_target

SCAN_TASK_NAME = "\\discoclaro\\scan"
CHECK_TASK_NAME = "\\discoclaro\\check"
PROGRAMS_HOURLY_TASK_NAME = "\\discoclaro\\programs-hourly"
PROGRAMS_ONEVENT_TASK_NAME = "\\discoclaro\\programs-onevent"
DEFAULT_SCHEDULE = "DAILY"
DEFAULT_START_TIME = "09:00"

# El canal y la condición (XPath) del disparador por evento de instalador (T2B.8):
# el ID 1033 de "MsiInstaller" es el que deja Windows Installer al terminar de
# instalar un producto (confirmado con el registro de eventos real de esta
# máquina: Java, PostgreSQL, Blender). Solo cubre instaladores MSI; la mayoría de
# los programas reales de esta máquina (Docker Desktop, Ollama, VS Code, DBeaver…)
# no son MSI y no generan ningún evento parecido, así que el disparador de cada
# hora sigue siendo el que de verdad los detecta a todos.
MSI_INSTALL_EVENT_CHANNEL = "Application"
MSI_INSTALL_EVENT_QUERY = "*[System[Provider[@Name='MsiInstaller'] and (EventID=1033)]]"

_MODULE_PREFIX = ("-m", "discoclaro")
_FIXED_ARGUMENT_ACTIONS = ("check", "programs-check")


class ScheduleError(Exception):
    """No se pudo instalar, quitar o consultar la tarea programada."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class TaskStatus:
    """Si una tarea está instalada y, de estarlo, si coincide con lo esperado.

    `altered` solo tiene sentido cuando `installed` es verdadero: si no está
    instalada, no hay nada que comparar y queda en `False`.
    """

    installed: bool
    altered: bool = False


@dataclass(frozen=True, slots=True)
class ScheduleStatus:
    """El estado de las cuatro tareas, cada una por separado."""

    scan: TaskStatus
    check: TaskStatus
    programs_hourly: TaskStatus
    programs_onevent: TaskStatus


def _resolved_name(target: str, volumes: Callable[[], list[Volume]]) -> str:
    """El nombre bajo el que se guardaría la foto de `target`: el volumen o la ruta."""
    try:
        volume, path = locate(normalize_target(target), volumes())
    except ScanError as error:
        raise ScheduleError("not_fixed_disk", error.target) from error
    return volume.name if path == volume.name + "\\" else path


def install(
    target: str,
    *,
    volumes: Callable[[], list[Volume]] = list_volumes,
    python: str = sys.executable,
    create: Callable[..., None] = windows.create_scheduled_task,
) -> None:
    """Crea (o reemplaza) las cuatro tareas: escanear `target`, revisar los
    avisos, y revisar el inventario de programas cada hora y al terminar de
    instalarse un programa por MSI (T2B.8).

    `target` tiene que ser un disco fijo o una carpeta de uno (igual que en `scan`).
    Las cuatro corren como el usuario normal, sin pedir administrador.
    """
    name = _resolved_name(target, volumes)
    runner = windows.background_python_executable(python)
    programs_check = [runner, "-m", "discoclaro", "programs-check"]
    try:
        create(
            SCAN_TASK_NAME,
            [runner, "-m", "discoclaro", "scan", name],
            schedule=DEFAULT_SCHEDULE,
            start_time=DEFAULT_START_TIME,
        )
        create(
            CHECK_TASK_NAME,
            [runner, "-m", "discoclaro", "check"],
            schedule=DEFAULT_SCHEDULE,
            start_time=DEFAULT_START_TIME,
        )
        create(PROGRAMS_HOURLY_TASK_NAME, programs_check, schedule="HOURLY")
        create(
            PROGRAMS_ONEVENT_TASK_NAME,
            programs_check,
            schedule="ONEVENT",
            event_channel=MSI_INSTALL_EVENT_CHANNEL,
            event_query=MSI_INSTALL_EVENT_QUERY,
        )
    except windows.ScheduledTaskError as error:
        raise ScheduleError("schedule_failed", str(error)) from error


def remove(*, delete: Callable[[str], bool] = windows.remove_scheduled_task) -> bool:
    """Quita las cuatro tareas. Devuelve si alguna de las cuatro existía."""
    removed = [
        delete(SCAN_TASK_NAME),
        delete(CHECK_TASK_NAME),
        delete(PROGRAMS_HOURLY_TASK_NAME),
        delete(PROGRAMS_ONEVENT_TASK_NAME),
    ]
    return any(removed)


def _scan_arguments_pattern() -> re.Pattern[str]:
    prefix = re.escape(windows.command_line([*_MODULE_PREFIX, "scan"]))
    return re.compile(rf'^{prefix} (?:"[^"]*"|\S+)$')


def _task_status(
    name: str,
    action: str,
    python: str,
    get_command: Callable[[str], tuple[str, str] | None],
) -> TaskStatus:
    found = get_command(name)
    if found is None:
        return TaskStatus(installed=False)
    command, arguments = found
    allowed_executables = {
        python,
        windows.background_python_executable(python),
        windows.background_python_executable(python, exists=lambda _: True),
    }
    if command not in allowed_executables:
        return TaskStatus(installed=True, altered=True)
    if action in _FIXED_ARGUMENT_ACTIONS:
        matches = arguments == windows.command_line([*_MODULE_PREFIX, action])
    else:
        matches = bool(_scan_arguments_pattern().fullmatch(arguments))
    return TaskStatus(installed=True, altered=not matches)


def status(
    *,
    python: str = sys.executable,
    get_command: Callable[
        [str], tuple[str, str] | None
    ] = windows.scheduled_task_command,
) -> ScheduleStatus:
    """El estado de las cuatro tareas: instaladas o no, y si coinciden con lo
    esperado.

    Lo esperado es el mismo ejecutable que se usaría para instalar ahora
    (`python`) y los mismos argumentos que armaría `install()`; no compara el
    destino exacto del escaneo (no hay otra fuente de verdad para saberlo), pero sí
    rechaza cualquier otra cosa: otro ejecutable, argumentos de otro módulo, o algo
    de más después del destino. No compara el disparador (cada hora, por evento):
    solo lee la acción registrada, igual que antes de esta tarea (T2.13 no lo
    necesitaba).
    """
    return ScheduleStatus(
        scan=_task_status(SCAN_TASK_NAME, "scan", python, get_command),
        check=_task_status(CHECK_TASK_NAME, "check", python, get_command),
        programs_hourly=_task_status(
            PROGRAMS_HOURLY_TASK_NAME, "programs-check", python, get_command
        ),
        programs_onevent=_task_status(
            PROGRAMS_ONEVENT_TASK_NAME, "programs-check", python, get_command
        ),
    )


def migrate_tasks_to_windowless(
    *,
    python: str = sys.executable,
    get_command: Callable[
        [str], tuple[str, str] | None
    ] = windows.scheduled_task_command,
    create: Callable[..., None] = windows.create_scheduled_task,
) -> bool:
    """Si las tareas programadas ya estaban instaladas con `python.exe` (que abre
    una ventana de consola fugaz cada hora), las actualiza automáticamente a
    `pythonw.exe` conservando el volumen o carpeta configurada en `scan`."""
    runner = windows.background_python_executable(python)
    if runner.lower().endswith("python.exe"):
        return False
    scan_cmd = get_command(SCAN_TASK_NAME)
    hourly_cmd = get_command(PROGRAMS_HOURLY_TASK_NAME)
    if scan_cmd is None and hourly_cmd is None:
        return False
    needs_migration = False
    for cmd_tuple in (scan_cmd, hourly_cmd):
        if cmd_tuple is not None and cmd_tuple[0].lower().endswith("python.exe"):
            needs_migration = True
            break
    if not needs_migration:
        return False

    target = "C:"
    if scan_cmd is not None:
        _, args = scan_cmd
        parts = args.split("scan ", 1)
        if len(parts) == 2 and parts[1].strip():
            target = parts[1].strip().strip('"')
    try:
        install(target, python=python, create=create)
    except (ScheduleError, OSError):
        return False
    return True
