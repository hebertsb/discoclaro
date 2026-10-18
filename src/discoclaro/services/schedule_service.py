"""Caso de uso "programar" (T2.8, T2.13): instala, quita y consulta la tarea de
Windows.

Crea dos tareas en el Programador de tareas, ambas sin elevar y con la ruta absoluta
del intérprete que ya está corriendo: una que escanea el destino elegido y otra que
revisa los avisos, para que el disco se vigile solo, sin dejar un proceso residente
(la periodicidad la da Windows, no discoclaro). `status()` también compara lo que de
verdad tiene registrado cada tarea contra lo esperado, para avisar si alguien la
alteró a mano (T2.13): otro ejecutable, u otros argumentos que no sean exactamente
"-m discoclaro scan <algo>" o "-m discoclaro check".
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
DEFAULT_SCHEDULE = "DAILY"
DEFAULT_START_TIME = "09:00"
_MODULE_PREFIX = ("-m", "discoclaro")


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
    """El estado de las dos tareas (escanear y revisar), cada una por separado."""

    scan: TaskStatus
    check: TaskStatus


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
    """Crea (o reemplaza) las dos tareas: escanear `target` y, después, revisar.

    `target` tiene que ser un disco fijo o una carpeta de uno (igual que en `scan`).
    Las dos corren como el usuario normal, sin pedir administrador.
    """
    name = _resolved_name(target, volumes)
    try:
        create(
            SCAN_TASK_NAME,
            [python, "-m", "discoclaro", "scan", name],
            schedule=DEFAULT_SCHEDULE,
            start_time=DEFAULT_START_TIME,
        )
        create(
            CHECK_TASK_NAME,
            [python, "-m", "discoclaro", "check"],
            schedule=DEFAULT_SCHEDULE,
            start_time=DEFAULT_START_TIME,
        )
    except windows.ScheduledTaskError as error:
        raise ScheduleError("schedule_failed", str(error)) from error


def remove(*, delete: Callable[[str], bool] = windows.remove_scheduled_task) -> bool:
    """Quita las dos tareas. Devuelve si alguna de las dos existía."""
    removed_scan = delete(SCAN_TASK_NAME)
    removed_check = delete(CHECK_TASK_NAME)
    return removed_scan or removed_check


def _expected_check_arguments() -> str:
    return windows.command_line([*_MODULE_PREFIX, "check"])


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
    if command != python:
        return TaskStatus(installed=True, altered=True)
    if action == "check":
        matches = arguments == _expected_check_arguments()
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
    """El estado de las dos tareas: instaladas o no, y si coinciden con lo esperado.

    Lo esperado es el mismo ejecutable que se usaría para instalar ahora
    (`python`) y los mismos argumentos que armaría `install()`; no compara el
    destino exacto del escaneo (no hay otra fuente de verdad para saberlo), pero sí
    rechaza cualquier otra cosa: otro ejecutable, argumentos de otro módulo, o algo
    de más después del destino.
    """
    return ScheduleStatus(
        scan=_task_status(SCAN_TASK_NAME, "scan", python, get_command),
        check=_task_status(CHECK_TASK_NAME, "check", python, get_command),
    )
