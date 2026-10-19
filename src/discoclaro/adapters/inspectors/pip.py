"""Inspector de pip (T2C.1): cuánto ocupa su caché, según pip mismo.

Corre `pip cache info` (de solo lectura: no borra ni modifica nada, a
diferencia de `pip cache purge`, que nunca se usa aquí) y lee el tamaño de la
caché de páginas de índice y el de las ruedas (wheels) ya compiladas. Si pip no
está instalado, el comando falla o la salida no se puede leer, se omite sin
error: no hay nada que decir de este programa en esta máquina, no es una
falla del escaneo.
"""

import re
from collections.abc import Callable

from discoclaro.adapters.inspectors._process import resolve_executable, run_command
from discoclaro.domain.insights import Insight, InsightItem

_SIZE_LINE = re.compile(
    r"^(Package index page cache size|Locally built wheels size):\s*(.+)$"
)
_SIZE_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(bytes|kb|mb|gb)$", re.IGNORECASE)
_UNITS = {"bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}


def _parse_size(text: str) -> int | None:
    match = _SIZE_VALUE.match(text.strip())
    if match is None:
        return None
    return round(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _parse_cache_info(output: str) -> list[InsightItem]:
    items = []
    for line in output.splitlines():
        match = _SIZE_LINE.match(line.strip())
        if match is None:
            continue
        label, raw_size = match.groups()
        size = _parse_size(raw_size)
        if size is None:
            continue
        name = "index" if "index" in label else "wheels"
        items.append(InsightItem(name=name, size_bytes=size))
    return items


def inspect(
    *,
    resolve: Callable[[str], str | None] = resolve_executable,
    run: Callable[[list[str]], str | None] = run_command,
) -> Insight | None:
    """La huella de la caché de pip, o `None` si no hay nada que decir."""
    executable = resolve("pip")
    if executable is None:
        return None
    output = run([executable, "cache", "info"])
    if output is None:
        return None
    items = _parse_cache_info(output)
    if not items:
        return None
    return Insight(program="pip", items=tuple(items))
