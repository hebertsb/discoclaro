"""Caso de uso "programas": avisos de instalación y de desinstalación.

`check_programs()` compara el inventario del registro con el que ya se guardó
(T2B.2/T2B.3). De cada programa **recién instalado** (T2B.5) mide su carpeta de
instalación y, entre las carpetas de primer nivel de `%LOCALAPPDATA%`, `%APPDATA%`
y el perfil del usuario, las que ya parecen suyas por el nombre (T2B.4), guarda esa
huella y notifica qué se instaló, en qué unidad y carpeta y cuánto ocupa. De cada
programa **recién desinstalado** (T2B.7) mide qué sigue existiendo de su huella ya
guardada y de la misma búsqueda por nombre, actualiza la huella guardada (puede
haber cambiado o desaparecido) y notifica cuánto quedó. Como marca el flujo E del
plan (`discoclaro programs-check ─► programs_service ─► adapters.notifier`), este
servicio es quien notifica, con el texto ya traducido.

La primera revisión (la base, sin nada que comparar todavía) no notifica nada: si
no se hiciera esta distinción, los ~100 programas ya instalados en una máquina
real aparecerían todos como "recién instalados" la primera vez que se corre.

También `find_leftovers(name)` (T2B.6), para un programa que ya se desinstaló antes
de usar discoclaro y por eso no tiene ningún `Program` ni huella guardada: busca
solo por nombre, en las mismas ubicaciones típicas, sin inventar un resto que no
exista.
"""

import os
import sqlite3
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
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
# introducción de la Fase 2B (Ollama deja carpetas en estos tres lugares) más
# `%TEMP%`, para encontrar también carpetas temporales del propio programa.
SEARCH_ROOT_VARIABLES = ("%LOCALAPPDATA%", "%APPDATA%", "%USERPROFILE%", "%TEMP%")

# Subcarpetas contenedoras dentro de `%LOCALAPPDATA%` o `%APPDATA%` donde viven
# instalaciones por usuario (VS Code, Ollama, 7-Zip, Python) o datos agrupados.
NESTED_PROGRAM_TAINERS = ("Programs",)


class ProgramsCheckError(Exception):
    """No se pudo revisar el inventario de programas; `kind` es la clave del
    mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


@dataclass(frozen=True, slots=True)
class InstalledProgram:
    """Un programa recién instalado (T2B.5) y la huella que se le midió (ruta,
    tamaño, fecha, confianza y motivo de cada carpeta), vacía si no se encontró
    ninguna carpeta suya."""

    program: Program
    footprints: tuple[Footprint, ...]


@dataclass(frozen=True, slots=True)
class UninstallLeftovers:
    """Lo que sigue existiendo de un programa recién desinstalado (T2B.7): el
    programa y la lista completa de restos (ruta, tamaño, fecha, confianza y
    motivo de cada uno), vacía si no quedó nada apreciable."""

    program: Program
    footprints: tuple[Footprint, ...]


@dataclass(frozen=True, slots=True)
class ProgramsCheckResult:
    """Lo que cambió en una revisión del inventario: de cada programa recién
    instalado, su huella medida; de cada uno recién desinstalado, lo que le
    quedó."""

    installed: tuple[InstalledProgram, ...]
    uninstalled: tuple[UninstallLeftovers, ...]


@dataclass(frozen=True, slots=True)
class ProgramEntry:
    """Un programa instalado, sus huellas conocidas y su tamaño total (T3.9)."""

    program: Program
    footprints: tuple[Footprint, ...]
    total_bytes: int


@dataclass(frozen=True, slots=True)
class ProgramsOverview:
    """Vista completa para el dashboard (T3.9): instalados ordenados por tamaño
    total (estimado o suma de huellas) y desinstalados que aún tienen restos."""

    installed: tuple[ProgramEntry, ...]
    uninstalled_with_leftovers: tuple[UninstallLeftovers, ...]


def list_programs_overview(
    data_dir: Path | None = None,
    *,
    read_programs: Callable[[], list[Program]] = windows.installed_programs,
) -> ProgramsOverview:
    """Lee los programas instalados actualmente en el sistema (con su
    `uninstall_string` real del registro) y los cruza con las huellas y los
    programas desinstalados guardados en la base (si existe)."""
    current = read_programs()
    footprints_by_name: dict[str, tuple[Footprint, ...]] = {}
    removed_list: list[UninstallLeftovers] = []

    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if database.is_file():
        check_disk_exists = read_programs is windows.installed_programs
        try:
            with Store.open_readonly(database) as store:
                footprints_by_name = store.all_program_footprints()
                for rem in store.removed_programs():
                    rem_footprints = footprints_by_name.get(rem.name, ())
                    if check_disk_exists and rem_footprints:
                        rem_footprints = tuple(
                            f for f in rem_footprints if os.path.exists(f.path)
                        )
                    if rem_footprints:
                        removed_list.append(
                            UninstallLeftovers(program=rem, footprints=rem_footprints)
                        )
        except (OSError, sqlite3.Error, StoreError):
            pass

    entries: list[ProgramEntry] = []
    for prog in current:
        fps = footprints_by_name.get(prog.name, ())
        footprint_sum = sum(f.size_bytes for f in fps)
        estimated = prog.estimated_size_bytes or 0
        total = max(estimated, footprint_sum)
        entries.append(
            ProgramEntry(program=prog, footprints=fps, total_bytes=total)
        )
    entries.sort(key=lambda e: (e.total_bytes, e.program.name.casefold()), reverse=True)
    return ProgramsOverview(
        installed=tuple(entries),
        uninstalled_with_leftovers=tuple(removed_list),
    )


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


def _search_root_candidates(
    program: Program, environment: Mapping[str, str]
) -> list[str]:
    """Las carpetas de primer nivel de las ubicaciones típicas (`%LOCALAPPDATA%`,
    `%APPDATA%`, `%USERPROFILE%`, `%TEMP%`) más un segundo nivel dentro de
    contenedores de programas (`%LOCALAPPDATA%\\Programs`) o de la carpeta del
    propio editor (`%APPDATA%\\<Publisher>\\<Program>`), cuyo nombre ya parece
    relacionado con `program`, sin mirar todavía tamaño ni fecha."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        key = candidate.rstrip("\\/").casefold()
        if key not in seen:
            seen.add(key)
            paths.append(candidate)

    publisher_key = (program.publisher or "").strip().casefold()
    for variable in SEARCH_ROOT_VARIABLES:
        base = environment.get(variable)
        if not base:
            continue
        for folder in _top_level_folders(base):
            folder_name = os.path.basename(folder.rstrip("\\/"))
            if folder_name in NESTED_PROGRAM_TAINERS:
                for child in _top_level_folders(folder):
                    if might_be_related(child, program):
                        _add(child)
                continue
            if might_be_related(folder, program):
                _add(folder)
            if publisher_key and folder_name.casefold() == publisher_key:
                for child in _top_level_folders(folder):
                    if might_be_related(child, program):
                        _add(child)
    return paths


def _candidate_paths(program: Program, environment: Mapping[str, str]) -> list[str]:
    """Las rutas a medir de verdad para un programa recién instalado (T2B.5): su
    instalación registrada, más las carpetas candidatas de las ubicaciones
    típicas. `classify_footprint` decide después, con tamaño y fecha reales,
    cuáles de verdad cuentan como huella."""
    paths = []
    if program.install_location and not _is_drive_root(program.install_location):
        paths.append(program.install_location)
    for folder in _search_root_candidates(program, environment):
        if folder != program.install_location:
            paths.append(folder)
    return paths


def _uninstall_candidate_paths(
    program: Program, known_paths: Iterable[str], environment: Mapping[str, str]
) -> list[str]:
    """Las rutas a medir de verdad para un programa recién desinstalado (T2B.7):
    su huella ya guardada (si discoclaro llegó a verlo instalado), más las
    carpetas candidatas de las ubicaciones típicas que no estuvieran ya ahí."""
    paths = list(known_paths)
    for folder in _search_root_candidates(program, environment):
        if folder not in paths:
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


def format_install_notice(
    program: Program, footprints: Sequence[Footprint]
) -> tuple[str, str]:
    """El título y el mensaje ya traducidos del aviso de instalación (T2B.5).

    Lo usa `check_programs` para notificar, y la CLI para mostrar lo mismo en
    su tabla (T2B.8), sin armar el texto dos veces."""
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


def format_uninstall_notice(
    program: Program, footprints: Sequence[Footprint]
) -> tuple[str, str]:
    """El título y el mensaje ya traducidos del aviso de desinstalación (T2B.7).

    Lo usa `check_programs` para notificar, y la CLI para mostrar lo mismo en
    su tabla (T2B.8), sin armar el texto dos veces."""
    title = t("programs.uninstalled_title")
    name = _display(program.name)
    if not footprints:
        return title, t("programs.uninstalled_clean", name=name)
    decimal = t("format.decimal_separator")
    size = format_bytes(sum(f.size_bytes for f in footprints), decimal)
    count = format_int(len(footprints), t("format.thousands_separator"))
    return title, t("programs.uninstalled_message", name=name, size=size, count=count)


def _uninstall_leftovers(
    store: Store,
    program: Program,
    *,
    environment: Mapping[str, str],
    inspect: Callable[[str], FolderCandidate | None],
    moment: datetime,
) -> UninstallLeftovers:
    """Mide lo que sigue existiendo de un programa recién desinstalado (T2B.7):
    su huella ya guardada (si discoclaro llegó a verlo instalado) más la misma
    búsqueda por nombre de T2B.6, y actualiza la huella guardada con lo que de
    verdad sigue estando."""
    known_paths = [f.path for f in store.footprints_of_program(program.name)]
    candidates = [
        candidate
        for path in _uninstall_candidate_paths(program, known_paths, environment)
        if (candidate := inspect(path)) is not None
    ]
    footprints = tuple(
        footprints_of(program, candidates, known_footprint_paths=frozenset(known_paths))
    )
    store.save_footprints(program.name, footprints, moment)
    return UninstallLeftovers(program=program, footprints=footprints)


def check_programs(
    data_dir: Path | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    read_programs: Callable[[], list[Program]] = windows.installed_programs,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = inspect_folder,
    notify: Callable[[str, str], None] = notifier.send_notification,
) -> ProgramsCheckResult:
    """Revisa el inventario de programas: guarda la revisión y, por cada programa
    recién instalado o recién desinstalado, mide su carpeta (o lo que le quedó),
    guarda la huella y notifica. Vacío en la primera revisión, que solo guarda
    la base para poder comparar la próxima.
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
            return ProgramsCheckResult(installed=(), uninstalled=())
        diff = diff_inventories(before, after)
        env = environment()
        installed = []
        for program in diff.installed:
            candidates = [
                candidate
                for path in _candidate_paths(program, env)
                if (candidate := inspect(path)) is not None
            ]
            footprints = tuple(footprints_of(program, candidates))
            if footprints:
                store.save_footprints(program.name, footprints, moment)
            notify(*format_install_notice(program, footprints))
            installed.append(InstalledProgram(program=program, footprints=footprints))
        uninstalled = []
        for program in diff.uninstalled:
            leftovers = _uninstall_leftovers(
                store, program, environment=env, inspect=inspect, moment=moment
            )
            notify(*format_uninstall_notice(program, leftovers.footprints))
            uninstalled.append(leftovers)
        return ProgramsCheckResult(
            installed=tuple(installed), uninstalled=tuple(uninstalled)
        )


def find_leftovers(
    name: str,
    *,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = inspect_folder,
) -> list[Footprint]:
    """Busca restos de un programa ya desinstalado, solo por nombre (T2B.6).

    Sin ningún `Program` del registro ni huella guardada de referencia (se
    desinstaló antes de usar discoclaro), así que solo pueden salir restos por
    coincidencia de nombre (`name_match`/`partial_name_match`): nunca por
    `install_location`, `known_footprint` ni `near_install_date`, que necesitan
    datos que este caso no tiene. Devuelve una lista vacía, nunca un resto
    inventado, si no se encuentra nada; ordenados del más grande al más chico.
    """
    if not name:
        raise ProgramsCheckError("leftovers_empty_name")
    program = Program(
        name=name,
        version=None,
        publisher=None,
        install_location=None,
        install_date=None,
        estimated_size_bytes=None,
        uninstall_string=None,
    )
    candidates = [
        candidate
        for path in _search_root_candidates(program, environment())
        if (candidate := inspect(path)) is not None
    ]
    found = footprints_of(program, candidates)
    return sorted(found, key=lambda footprint: footprint.size_bytes, reverse=True)


def inspect_program_footprints(
    program_name: str,
    *,
    read_programs: Callable[[], list[Program]] = windows.installed_programs,
    environment: Callable[[], Mapping[str, str]] = windows.environment_variables,
    inspect: Callable[[str], FolderCandidate | None] = inspect_folder,
) -> list[Footprint]:
    """Mide en vivo todas las carpetas (instalación, AppData, LocalAppData,
    Programs, perfil y Temp) relacionadas con un programa instalado o por nombre."""
    if not program_name:
        raise ProgramsCheckError("leftovers_empty_name")
    matched = next(
        (p for p in read_programs() if p.name.casefold() == program_name.casefold()),
        None,
    )
    if matched is None:
        return find_leftovers(program_name, environment=environment, inspect=inspect)
    env = environment()
    candidates = [
        candidate
        for path in _candidate_paths(matched, env)
        if (candidate := inspect(path)) is not None
    ]
    found = footprints_of(matched, candidates)
    return sorted(found, key=lambda f: f.size_bytes, reverse=True)

