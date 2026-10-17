"""Verifica que los archivos instalados de un paquete coincidan con su `RECORD`.

Se usa antes de pedir permisos de administrador (T1.9): si algo no coincide, no se
eleva. Solo stdlib (`importlib.metadata`, `hashlib`, `csv`): nada que instalar.

Una instalación **editable** (la de desarrollo, `pip install -e .`) no registra los
archivos fuente en su `RECORD` —solo el lanzador y los metadatos—, así que aquí no hay
nada que comparar. No se bloquea, pero tampoco se finge que se comprobó: se marca
`editable` aparte para que quien llama lo diga.

El `RECORD` se lee con `csv`, no con `Distribution.files`: esa propiedad **oculta**
los archivos que ya no existen en disco (pensada para listar "qué hay instalado"), así
que con ella un archivo borrado después de instalar pasaría sin avisar. Aquí interesa
justo lo contrario.
"""

import base64
import csv
import hashlib
import io
import json
import tempfile
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path

# Los algoritmos que puede nombrar un RECORD real (los que usa pip hoy). Cualquier otro
# nombre se trata como si no coincidiera: no se llama a hashlib con un texto ajeno.
_HASHES = {"sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512}
_READ_CHUNK = 1024 * 1024
_NO_RECORD = "no file list (RECORD) available"


class DistributionNotFound(Exception):
    """El paquete no aparece instalado (no se encontró su `dist-info`)."""


@dataclass(frozen=True, slots=True)
class IntegrityCheck:
    """El resultado de comparar los archivos instalados de un paquete con su `RECORD`.

    `editable` es una instalación de desarrollo: `ok` vale `True` sin haber comprobado
    nada, porque no hay con qué. `mismatches` son las rutas (relativas al sitio de
    instalación) que no coinciden o faltan; vacío si todo coincide. `base_dir` es la
    carpeta donde vive el paquete, para avisar aparte si el usuario puede escribir ahí.
    """

    ok: bool
    editable: bool
    mismatches: tuple[str, ...]
    base_dir: Path


@dataclass(frozen=True, slots=True)
class _RecordEntry:
    """Una fila del `RECORD`, tal cual: ruta, algoritmo de hash y valor (o `None`)."""

    path: str
    algorithm: str | None
    digest: str | None


def inspect_distribution(dist: importlib_metadata.Distribution) -> IntegrityCheck:
    """Compara los archivos de una distribución ya cargada con su `RECORD`.

    No sigue enlaces de más: lee cada archivo tal cual está en disco. Recibe la
    distribución en vez de un nombre para poder probarse con un `dist-info` sintético
    (`Distribution.at(ruta)`), sin tocar la instalación real.
    """
    base_dir = Path(dist.locate_file("."))
    if _is_editable(dist):
        return IntegrityCheck(ok=True, editable=True, mismatches=(), base_dir=base_dir)
    entries = _record_entries(dist)
    if not entries:
        return IntegrityCheck(
            ok=False, editable=False, mismatches=(_NO_RECORD,), base_dir=base_dir
        )
    mismatches = tuple(entry.path for entry in entries if not _matches(dist, entry))
    return IntegrityCheck(
        ok=not mismatches, editable=False, mismatches=mismatches, base_dir=base_dir
    )


def check_installed_files(package: str) -> IntegrityCheck:
    """La comprobación de la distribución instalada de verdad con ese nombre.

    Lanza `DistributionNotFound` si no está instalada.
    """
    try:
        dist = importlib_metadata.distribution(package)
    except importlib_metadata.PackageNotFoundError as error:
        raise DistributionNotFound(package) from error
    return inspect_distribution(dist)


def is_writable_by_the_user(directory: Path) -> bool:
    """Si este usuario puede escribir en `directory`: lo intenta de verdad.

    No hay una forma simple y portátil de leer los permisos de Windows sin una
    librería nueva; escribir y borrar un archivo propio es la comprobación real. El
    archivo se borra solo (`tempfile`), incluso si algo falla a mitad.
    """
    try:
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError:
        return False
    return True


def _is_editable(dist: importlib_metadata.Distribution) -> bool:
    """Si `dist` es una instalación editable, según `direct_url.json` (PEP 610)."""
    try:
        raw = dist.read_text("direct_url.json")
    except OSError:
        return False
    if raw is None:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    return bool(isinstance(data, dict) and data.get("dir_info", {}).get("editable"))


def _record_entries(
    dist: importlib_metadata.Distribution,
) -> list[_RecordEntry] | None:
    """Las filas del `RECORD` de `dist`, completas: `None` si no tiene uno."""
    try:
        text = dist.read_text("RECORD")
    except OSError:
        return None
    if not text:
        return None
    entries = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        path = row[0]
        hash_field = row[1] if len(row) > 1 else ""
        algorithm, _, digest = hash_field.partition("=")
        entries.append(_RecordEntry(path, algorithm or None, digest or None))
    return entries


def _matches(dist: importlib_metadata.Distribution, entry: _RecordEntry) -> bool:
    if entry.algorithm is None:
        return True  # el propio RECORD no guarda su hash; es normal, no una alarma.
    algorithm = _HASHES.get(entry.algorithm)
    if algorithm is None:
        return False
    try:
        actual = _file_hash(Path(dist.locate_file(entry.path)), algorithm)
    except OSError:
        return False
    return actual == entry.digest


def _file_hash(path: Path, algorithm) -> str:
    """El hash de `path` en el mismo formato que usa `RECORD`: base64 sin relleno."""
    hasher = algorithm()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            hasher.update(chunk)
    return base64.urlsafe_b64encode(hasher.digest()).rstrip(b"=").decode("ascii")
