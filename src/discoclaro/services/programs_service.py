"""Caso de uso "programas" (T2B.5): aviso de instalación.

Compara el inventario del registro con el que ya se guardó (T2B.2/T2B.3); de cada
programa recién instalado mide su carpeta de instalación y, entre las carpetas de
primer nivel de `%LOCALAPPDATA%`, `%APPDATA%` y el perfil del usuario, las que ya
parecen suyas por el nombre (T2B.4), guarda esa huella y notifica qué se instaló,
en qué unidad y carpeta y cuánto ocupa. Como marca el flujo E del plan
(`discoclaro programs-check ─► programs_service ─► adapters.notifier`), este
servicio es quien notifica, con el texto ya traducido.

La primera revisión (la base, sin nada que comparar todavía) no notifica nada: si
no se hiciera esta distinción, los ~100 programas ya instalados en una máquina
real aparecerían todos como "recién instalados" la primera vez que se corre.

Alcance a propósito: solo instalaciones (`diff.installed`). Las desinstalaciones
quedan para T2B.7, que necesita además la búsqueda por nombre de T2B.6.
"""

import os
import sqlite3
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from discoclaro.adapters import notifier
from discoclaro.adapters.scanner import scan_tree
from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.footprint import (
    FolderCandidate,
    Footprint,
    footprints_of,
    might_be_related,
)
from discoclaro.domain.formatting import format_bytes, format_int, safe_text
from discoclaro.domain.models import Program
from discoclaro.domain.policy import limit_length
from discoclaro.domain.programs import diff_inventories
from discoclaro.i18n import t
from discoclaro.platform import windows
from discoclaro.services.scan_service import DATABASE_NAME

# Dónde buscar, además de `install_location`: los mismos ejemplos reales de la
# introducción de la Fase 2B (Ollama deja carpetas en estos tres lugares). Se
# excluye `%TEMP%` a propósito: no es donde vive la huella de un programa.
SEARCH_ROOT_VARIABLES = ("%LOCALAPPDATA%", "%APPDATA%", "%USERPROFILE%")


class ProgramsCheckError(Exception):
    """No se pudo revisar el inventario de programas; `kind` es la clave del
    mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


def _display(text: str) -> str:
    return limit_length(safe_text(text))


def _top_level_folders(base: str) -> list[str]:
    try:
        with os.scandir(base) as entries:
            return [
                entry.path for entry in entries if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return []


def _is_drive_root(path: str) -> bool:
    drive, rest = os.path.splitdrive(path)
    return bool(drive) and rest.rstrip("\\/") == ""


def _candidate_paths(program: Program, environment: Mapping[str, str]) -> list[str]:
    """Las rutas a medir de verdad para este programa: su instalación registrada,
    más las carpetas de primer nivel de las ubicaciones típicas cuyo nombre ya
    parece relacionado. `classify_footprint` decide después, con tamaño y fecha
    reales, cuáles de verdad cuentan como huella."""
    paths = []
    if program.install_location and not _is_drive_root(program.install_location):
        paths.append(program.install_location)
    for variable in SEARCH_ROOT_VARIABLES:
        base = environment.get(variable)
        if not base:
            continue
        for folder in _top_level_folders(base):
            if folder != program.install_location and might_be_related(folder, program):
                paths.append(folder)
    return paths


def inspect_folder(path: str) -> FolderCandidate | None:
    """Mide una carpeta candidata de verdad (T2B.5): tamaño total y última
    modificación de la carpeta misma. `None` si no existe, no es una carpeta, es
    la raíz de una unidad (un dato de instalación roto o hostil no mide un disco
    entero) o no se pudo leer."""
    if _is_drive_root(path):
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return None
    try:
        result = scan_tree(path)
    except OSError:
        return None
    return FolderCandidate(
        path=path,
        size_bytes=result.nodes[-1].size_bytes,
        last_modified=datetime.fromtimestamp(info.st_mtime, tz=UTC),
    )


def _format_install_notice(
    program: Program, footprints: list[Footprint]
) -> tuple[str, str]:
    title = t("programs.installed_title")
    name = _display(program.name)
    if not footprints:
        return title, t("programs.installed_unknown_size", name=name)
    primary = next(
        (f for f in footprints if f.reason == "install_location"), footprints[0]
    )
    others = [f for f in footprints if f is not primary]
    decimal = t("format.decimal_separator")
    location = _display(primary.path)
    size = format_bytes(primary.size_bytes, decimal)
    if not others:
        return title, t(
            "programs.installed_message", name=name, location=location, size=size
        )
    extra_size = format_bytes(sum(f.size_bytes for f in others), decimal)
    extra_count = format_int(len(others), t("format.thousands_separator"))
    return title, t(
        "programs.installed_message_with_extra",
        name=name,
        location=location,
        size=size,
        extra_size=extra_size,
        extra_count=extra_count,
    )


def check_programs(
    data_dir: Path | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    read_programs: Callable[[], list[Program]] = windows.installed_programs,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = inspect_folder,
    notify: Callable[[str, str], None] = notifier.send_notification,
) -> tuple[Program, ...]:
    """Revisa el inventario de programas: guarda la revisión y, por cada programa
    recién instalado, mide su carpeta y sus candidatas, guarda la huella y
    notifica. Devuelve los programas detectados como recién instalados (vacío en
    la primera revisión, que solo guarda la base).
    """
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    try:
        store = Store.open(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise ProgramsCheckError("cannot_open_database", str(error)) from error
    with store:
        is_first_check = not store.has_recorded_programs()
        before = store.active_programs()
        after = read_programs()
        moment = now()
        store.save_programs(after, moment)
        if is_first_check:
            return ()
        diff = diff_inventories(before, after)
        env = environment()
        for program in diff.installed:
            candidates = [
                candidate
                for path in _candidate_paths(program, env)
                if (candidate := inspect(path)) is not None
            ]
            footprints = footprints_of(program, candidates)
            if footprints:
                store.save_footprints(program.name, footprints, moment)
            notify(*_format_install_notice(program, footprints))
        return diff.installed
