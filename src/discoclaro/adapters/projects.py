"""Qué usa un proyecto configurado (T2C.3): lee sus archivos solo como texto.

Nunca ejecuta nada (ni `docker-compose`, ni `npm`, ni un interprete de
Dockerfile): solo abre `docker-compose.yml`/`compose.yml`, `Dockerfile`,
`requirements.txt`, `pubspec.yaml` y `package.json`, con un tope de tamaño, y
saca de ahí nombres de imágenes y de paquetes con expresiones regulares
simples (no un analizador de YAML completo: alcanza con las claves puntuales
que se buscan, y evita la dependencia nueva que pediría un YAML de verdad,
sin inventar código que interprete lo que lee). Los modelos se buscan por
coincidencia de texto contra los nombres ya instalados que da `known_models`
(por ejemplo, de `adapters/inspectors/ollama.py`): sin un campo estándar para
"qué modelo usa este proyecto", es la única pista real disponible.
"""

import json
import os
import re
from collections.abc import Callable, Iterable

from discoclaro.domain.inuse import ProjectReference, ProjectUsage

MAX_FILE_BYTES = 1_000_000

_COMPOSE_FILE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

_FROM_LINE = re.compile(r"^\s*FROM\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_IMAGE_LINE = re.compile(r"^\s*image:\s*[\"']?([^\"'#\s]+)", re.MULTILINE)
_REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
_PUBSPEC_SECTION = re.compile(r"^(dependencies|dev_dependencies):\s*$")
_PUBSPEC_ENTRY = re.compile(r"^ {2}([A-Za-z0-9_]+):")


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    return None if len(text) > MAX_FILE_BYTES else text


def _images_from_dockerfile(text: str) -> list[str]:
    return [match.group(1) for match in _FROM_LINE.finditer(text) if match.group(1)]


def _images_from_compose(text: str) -> list[str]:
    return [match.group(1) for match in _IMAGE_LINE.finditer(text) if match.group(1)]


def _packages_from_requirements(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = _REQUIREMENT_LINE.match(stripped)
        if match:
            names.append(match.group(1))
    return names


def _packages_from_package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    names = []
    for key in ("dependencies", "devDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names.extend(name for name in section if isinstance(name, str))
    return names


def _packages_from_pubspec(text: str) -> list[str]:
    names = []
    in_section = False
    for line in text.splitlines():
        if _PUBSPEC_SECTION.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if not line.strip():
            continue
        entry = _PUBSPEC_ENTRY.match(line)
        if entry:
            names.append(entry.group(1))
            continue
        if not line.startswith(" "):
            in_section = False
    return names


def _models_mentioned(texts: Iterable[str], known_models: frozenset[str]) -> list[str]:
    if not known_models:
        return []
    combined = "\n".join(texts).casefold()
    return [model for model in known_models if model.casefold() in combined]


def scan_project(
    folder: str,
    *,
    known_models: frozenset[str] = frozenset(),
    read_text: Callable[[str], str | None] = _read_text,
) -> ProjectUsage:
    """Lo que el proyecto en `folder` referencia, de sus propios archivos.

    El nombre del proyecto es el de la carpeta, no la ruta completa. Un
    archivo que no existe o no se puede leer simplemente no aporta nada: no
    es un error, la mayoría de los proyectos no tienen los cinco.
    """
    project = os.path.basename(folder.rstrip("\\/")) or folder
    images: list[str] = []
    packages: list[str] = []
    texts: list[str] = []

    for name in _COMPOSE_FILE_NAMES:
        text = read_text(os.path.join(folder, name))
        if text is not None:
            images.extend(_images_from_compose(text))
            texts.append(text)

    dockerfile = read_text(os.path.join(folder, "Dockerfile"))
    if dockerfile is not None:
        images.extend(_images_from_dockerfile(dockerfile))
        texts.append(dockerfile)

    requirements = read_text(os.path.join(folder, "requirements.txt"))
    if requirements is not None:
        packages.extend(_packages_from_requirements(requirements))
        texts.append(requirements)

    pubspec = read_text(os.path.join(folder, "pubspec.yaml"))
    if pubspec is not None:
        packages.extend(_packages_from_pubspec(pubspec))
        texts.append(pubspec)

    package_json = read_text(os.path.join(folder, "package.json"))
    if package_json is not None:
        packages.extend(_packages_from_package_json(package_json))
        texts.append(package_json)

    references = [
        ProjectReference(kind="image", name=name) for name in dict.fromkeys(images)
    ]
    references += [
        ProjectReference(kind="package", name=name) for name in dict.fromkeys(packages)
    ]
    references += [
        ProjectReference(kind="model", name=name)
        for name in _models_mentioned(texts, known_models)
    ]
    return ProjectUsage(project=project, references=tuple(references))


def scan_projects(
    folders: Iterable[str],
    *,
    known_models: frozenset[str] = frozenset(),
    read_text: Callable[[str], str | None] = _read_text,
) -> list[ProjectUsage]:
    """`scan_project` sobre cada carpeta configurada, en el mismo orden."""
    return [
        scan_project(folder, known_models=known_models, read_text=read_text)
        for folder in folders
    ]
