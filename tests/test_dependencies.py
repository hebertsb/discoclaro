"""Política de dependencias: versión exacta en todo y hashes en los requisitos.

Nada se instala con un rango (`>=`, `~=`, `^`) ni sin comprobar su hash. Los archivos
`requirements*.in` llevan solo las dependencias directas, con `==`; los
`requirements*.txt` se generan a partir de ellos con todas las dependencias y sus
hashes (ver docs/DEPENDENCIAS.md).
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# `nombre==versión`, con extras y marcadores de entorno opcionales.
EXACT_PIN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[0-9][^\s;=<>!~*]*"
    r"(\s*;\s*\S.*)?$"
)
HASH = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")


def _logical_lines(text: str) -> list[str]:
    """Une las líneas que terminan en `\\` y descarta comentarios y líneas vacías."""
    joined = re.sub(r"\\\s*\n", " ", text)
    lines = (line.strip() for line in joined.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def pin_problems(entries: list[str]) -> list[str]:
    """Entradas de `pyproject.toml` o de un `.in` que no están fijadas con `==`."""
    return [
        f"sin versión exacta: {entry}"
        for entry in entries
        if not EXACT_PIN.match(entry)
    ]


def lock_problems(text: str) -> list[str]:
    """Problemas de un archivo de requisitos con hashes (`requirements*.txt`)."""
    problems = []
    for line in _logical_lines(text):
        requirement, _, hashes = line.partition(" --hash=")
        if not EXACT_PIN.match(requirement.strip()):
            problems.append(f"sin versión exacta: {line}")
            continue
        found = [part for part in f"--hash={hashes}".split() if hashes]
        if not found or not all(HASH.match(part) for part in found):
            problems.append(f"sin hashes sha256 válidos: {requirement.strip()}")
    return problems


def _pyproject_pins() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entries = list(data["build-system"]["requires"])
    entries += data["project"].get("dependencies", [])
    for group in data["project"].get("optional-dependencies", {}).values():
        entries += group
    for group in data.get("dependency-groups", {}).values():
        entries += [item for item in group if isinstance(item, str)]
    return entries


def test_pyproject_pins_every_dependency_to_an_exact_version():
    assert pin_problems(_pyproject_pins()) == []


def test_there_are_requirement_files_to_check():
    assert sorted(path.name for path in ROOT.glob("requirements*.txt"))


@pytest.mark.parametrize(
    "path", sorted(ROOT.glob("requirements*.in")), ids=lambda p: p.name
)
def test_direct_requirements_are_exact_pins(path):
    assert pin_problems(_logical_lines(path.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    "path", sorted(ROOT.glob("requirements*.txt")), ids=lambda p: p.name
)
def test_locked_requirements_are_exact_pins_with_hashes(path):
    assert lock_problems(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "path", sorted(ROOT.glob("requirements*.txt")), ids=lambda p: p.name
)
def test_every_lock_file_comes_from_an_input_file(path):
    assert path.with_suffix(".in").is_file()


HASH_A = "a" * 64
HASH_B = "b" * 64

GOOD_LOCK = f"""\
# comentario
colorama==0.4.6 ; sys_platform == 'win32' \\
    --hash=sha256:{HASH_A} \\
    --hash=sha256:{HASH_B}
    # via pytest
pytest==9.1.1 \\
    --hash=sha256:{HASH_A}
"""

BAD_LOCKS = [
    "pytest>=9.0 \\\n    --hash=sha256:" + HASH_A,
    "pytest~=9.1 \\\n    --hash=sha256:" + HASH_A,
    "pytest==9.1.1",
    "pytest==9.1.1 \\\n    --hash=sha256:cortisimo",
    "pytest==9.1.1 \\\n    --hash=md5:" + "a" * 32,
    "pytest \\\n    --hash=sha256:" + HASH_A,
    "-r otro.txt",
    "--index-url https://ejemplo.invalid/simple",
    "git+https://ejemplo.invalid/repo.git#egg=pytest",
    "-e .",
]


def test_a_correct_lock_file_has_no_problems():
    assert lock_problems(GOOD_LOCK) == []


@pytest.mark.parametrize("text", BAD_LOCKS)
def test_incorrect_lock_files_are_reported(text):
    assert lock_problems(text)


@pytest.mark.parametrize(
    "entry",
    [
        "pytest>=9",
        "pytest",
        "pytest~=9.1",
        "pytest==9.*",
        "pytest<=9.1.1",
        "hatchling^1",
    ],
)
def test_ranges_and_missing_pins_are_reported(entry):
    assert pin_problems([entry])


@pytest.mark.parametrize(
    "entry",
    [
        "pytest==9.1.1",
        "uvicorn[standard]==0.52.4",
        "colorama==0.4.6 ; sys_platform == 'win32'",
    ],
)
def test_exact_pins_are_accepted(entry):
    assert pin_problems([entry]) == []
