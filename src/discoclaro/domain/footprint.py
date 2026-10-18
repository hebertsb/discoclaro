"""Candidatos a "huella" o "resto" de un programa, con nivel de confianza (T2B.4).

Pura: recibe carpetas ya encontradas (ruta, tamaño, última modificación) y el
programa con el que se las compara; no recorre el disco ni lee el registro, eso ya
lo dan `adapters`/`platform`. `confidence` y `reason` son claves cortas en inglés,
para el catálogo de textos (i18n), nunca una frase ya armada.
"""

import ntpath
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from discoclaro.domain.models import Program

CONFIDENCE_LEVELS = ("high", "medium", "low")

# Nunca se mira dentro de estas carpetas (sección 6 de la Fase 2B): son del sistema,
# no de un programa en particular.
PROTECTED_SEGMENTS = frozenset({"windows", "system32", "winsxs"})

# Carpetas compartidas por varios programas, o genéricas: coincidir con el nombre no
# alcanza para creer que son de uno en particular.
GENERIC_FOLDER_NAMES = frozenset(
    {"common", "programs", ".cache", "temp", "google", "microsoft"}
)

# Qué tan cerca de la fecha de instalación tiene que estar la última modificación
# de una carpeta para contar como indicio (confianza baja). Elegido simple: ni el
# mismo día a fuerza, ni una ventana tan ancha que coincida con cualquier cosa.
DEFAULT_NEAR_INSTALL_WINDOW = timedelta(days=3)


@dataclass(frozen=True, slots=True)
class FolderCandidate:
    """Una carpeta ya encontrada, a evaluar como posible huella de un programa."""

    path: str
    size_bytes: int
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class Footprint:
    """Una carpeta que se cree relacionada con un programa, y por qué."""

    path: str
    size_bytes: int
    last_modified: datetime | None
    confidence: str
    reason: str


def _is_protected(path: str) -> bool:
    segments = path.split("\\")
    return any(segment.casefold() in PROTECTED_SEGMENTS for segment in segments)


def _folder_name(path: str) -> str:
    return ntpath.basename(path.rstrip("\\/"))


def _is_generic_name(name: str) -> bool:
    return name.casefold() in GENERIC_FOLDER_NAMES


def _same_location(a: str, b: str) -> bool:
    return a.rstrip("\\/").casefold() == b.rstrip("\\/").casefold()


def _parse_install_date(install_date: str | None) -> date | None:
    if install_date is None or len(install_date) != 8 or not install_date.isdigit():
        return None
    try:
        return date(
            int(install_date[0:4]), int(install_date[4:6]), int(install_date[6:8])
        )
    except ValueError:
        return None


def classify_footprint(
    program: Program,
    candidate: FolderCandidate,
    *,
    known_footprint_paths: frozenset[str] = frozenset(),
    near_install_window: timedelta = DEFAULT_NEAR_INSTALL_WINDOW,
) -> Footprint | None:
    """Clasifica una carpeta candidata para un programa.

    `None` si la carpeta está en una zona protegida, si su nombre es genérico, o si
    no coincide con nada de `program`: no toda carpeta encontrada es una huella.
    `known_footprint_paths` son rutas ya guardadas como huella de este programa
    cuando se instaló (T2B.5); si `candidate.path` es una de ellas, la confianza es
    siempre alta, sin volver a mirar el nombre.
    """
    if _is_protected(candidate.path):
        return None
    name = _folder_name(candidate.path)
    if _is_generic_name(name):
        return None

    if any(_same_location(candidate.path, known) for known in known_footprint_paths):
        return _footprint(candidate, "high", "known_footprint")
    if program.install_location and _same_location(
        candidate.path, program.install_location
    ):
        return _footprint(candidate, "high", "install_location")

    if name.casefold() in {
        value.casefold() for value in (program.name, program.publisher) if value
    }:
        return _footprint(candidate, "medium", "name_match")

    if _partially_matches(name, program.name) or (
        program.publisher and _partially_matches(name, program.publisher)
    ):
        return _footprint(candidate, "low", "partial_name_match")

    installed_on = _parse_install_date(program.install_date)
    if installed_on is not None and candidate.last_modified is not None:
        modified_on = candidate.last_modified.date()
        if abs(modified_on - installed_on) <= near_install_window:
            return _footprint(candidate, "low", "near_install_date")

    return None


def footprints_of(
    program: Program,
    candidates: Sequence[FolderCandidate],
    *,
    known_footprint_paths: frozenset[str] = frozenset(),
    near_install_window: timedelta = DEFAULT_NEAR_INSTALL_WINDOW,
) -> list[Footprint]:
    """`classify_footprint` sobre varias carpetas a la vez, sin las que no aplican."""
    footprints = (
        classify_footprint(
            program,
            candidate,
            known_footprint_paths=known_footprint_paths,
            near_install_window=near_install_window,
        )
        for candidate in candidates
    )
    return [footprint for footprint in footprints if footprint is not None]


def _partially_matches(folder_name: str, program_text: str) -> bool:
    folder = folder_name.casefold()
    text = program_text.casefold()
    return bool(folder) and (folder in text or text in folder)


def _footprint(candidate: FolderCandidate, confidence: str, reason: str) -> Footprint:
    return Footprint(
        path=candidate.path,
        size_bytes=candidate.size_bytes,
        last_modified=candidate.last_modified,
        confidence=confidence,
        reason=reason,
    )
