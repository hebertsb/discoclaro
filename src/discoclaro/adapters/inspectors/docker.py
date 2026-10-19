"""Inspector de Docker (T2C.1): cuánto es recuperable, según Docker mismo.

Corre `docker system df --format json` (de solo lectura: solo mide, nunca
borra nada, a diferencia de `docker system prune`) y lee, de cada categoría
(imágenes, contenedores, volúmenes locales, caché de compilación), lo que
Docker ya sabe que se puede recuperar. Es el ejemplo que da la Fase 2C: el
disco virtual de Docker no se encoge solo con liberar espacio adentro (T2C.2
compara esto con el tamaño real del archivo). Si Docker no está instalado, el
daemon no está corriendo o la salida no se puede leer, se omite sin error: no
es una falla del escaneo, es que Docker no tiene nada que decir ahora mismo.
"""

import json
import os
import re
import subprocess
from collections.abc import Callable

from discoclaro.adapters.inspectors._process import (
    MAX_OUTPUT_BYTES,
    resolve_executable,
    run_command,
)
from discoclaro.domain.compaction import CompactionGap
from discoclaro.domain.insights import Insight, InsightItem
from discoclaro.platform import windows

# El motor WSL2 de Docker Desktop (confirmado real en esta máquina, T2C.2):
# la distribución liviana donde corre el daemon, y el punto de montaje fijo de
# su disco de datos, el mismo `docker_data.vhdx` que mide `windows.docker_vhdx_path`.
_WSL_DISTRO = "docker-desktop"
_DATA_MOUNT = "/mnt/docker-desktop-disk"

# Docker arma estos textos con go-units (decimal: kB/MB/GB/TB en potencias de
# 1000, no 1024), sin espacio entre el número y la unidad: "17.02GB", "201.9MB",
# "0B". El paréntesis con el porcentaje ("(75%)") se descarta.
_RECLAIMABLE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)\s*(B|kB|MB|GB|TB)(?:\s*\([0-9.]+%\))?$"
)
_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}


def _parse_reclaimable(text: str) -> int | None:
    match = _RECLAIMABLE.match(text.strip())
    if match is None:
        return None
    return round(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _parse_system_df(output: str) -> list[InsightItem]:
    items = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        name = row.get("Type")
        reclaimable = row.get("Reclaimable")
        if not isinstance(name, str) or not isinstance(reclaimable, str):
            continue
        size = _parse_reclaimable(reclaimable)
        if size is None or size == 0:
            continue
        items.append(InsightItem(name=name, size_bytes=size))
    return items


def inspect(
    *,
    resolve: Callable[[str], str | None] = resolve_executable,
    run: Callable[[list[str]], str | None] = run_command,
) -> Insight | None:
    """Lo recuperable según Docker, o `None` si no hay nada que decir."""
    executable = resolve("docker")
    if executable is None:
        return None
    output = run([executable, "system", "df", "--format", "json"])
    if output is None:
        return None
    items = _parse_system_df(output)
    if not items:
        return None
    return Insight(program="docker", items=tuple(items))


def _parse_df_used_bytes(output: str, mount_point: str) -> int | None:
    """El "Used" en bytes de la línea de `df -k` que termina en
    `mount_point`, o `None` si no aparece ninguna. `df -k` da bloques de 1
    KiB exactos, mucho más fácil de leer que el formato humano ("1.0G")."""
    for line in output.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 6 or columns[-1] != mount_point:
            continue
        try:
            return int(columns[2]) * 1024
        except ValueError:
            return None
    return None


def _wsl_used_bytes(
    *,
    run_in_wsl: Callable[..., subprocess.CompletedProcess] = windows.run_in_wsl,
) -> int | None:
    """Lo que el propio Linux de Docker Desktop dice usar en el disco de
    datos (T2C.2), vía `wsl.exe -d docker-desktop df -k`."""
    try:
        result = run_in_wsl(_WSL_DISTRO, ["df", "-k"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_BYTES:
        return None
    return _parse_df_used_bytes(result.stdout, _DATA_MOUNT)


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def compaction_gap(
    *,
    vhdx_path: Callable[[], str | None] = windows.docker_vhdx_path,
    file_size: Callable[[str], int | None] = _file_size,
    used_bytes: Callable[[], int | None] = _wsl_used_bytes,
) -> CompactionGap | None:
    """Lo liberado por dentro pero no recuperado en el disco de Docker
    (T2C.2): compara el tamaño real de `docker_data.vhdx` con lo que el
    propio Linux de Docker Desktop dice estar usando. `None` si no se puede
    saber alguno de los dos lados (no está instalado, no usa WSL2, el daemon
    o la distribución no están corriendo)."""
    path = vhdx_path()
    if path is None:
        return None
    size = file_size(path)
    used = used_bytes()
    if size is None or used is None:
        return None
    return CompactionGap(path=path, file_bytes=size, used_bytes=used)
