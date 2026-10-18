"""Caso de uso "programar" (T2.8): instala, quita y consulta la tarea de Windows.

Crea dos tareas en el Programador de tareas, ambas sin elevar y con la ruta absoluta
del intérprete que ya está corriendo: una que escanea el destino elegido y otra que
revisa los avisos, para que el disco se vigile solo, sin dejar un proceso residente
(la periodicidad la da Windows, no discoclaro).
"""

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


class ScheduleError(Exception):
    """No se pudo instalar, quitar o consultar la tarea programada."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class ScheduleStatus:
    """Si las dos tareas (escanear y revisar) ya están instaladas."""

    scan_installed: bool
    check_installed: bool


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


def status(
    *, exists: Callable[[str], bool] = windows.scheduled_task_exists
) -> ScheduleStatus:
    """Si las dos tareas ya están instaladas, cada una por separado."""
    return ScheduleStatus(
        scan_installed=exists(SCAN_TASK_NAME),
        check_installed=exists(CHECK_TASK_NAME),
    )
