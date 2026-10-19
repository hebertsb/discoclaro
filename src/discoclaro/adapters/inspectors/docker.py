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
import re
from collections.abc import Callable

from discoclaro.adapters.inspectors._process import resolve_executable, run_command
from discoclaro.domain.insights import Insight, InsightItem

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
