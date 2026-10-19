"""Inspector de Ollama (T2C.1): qué modelos tiene y cuánto ocupa cada uno.

Corre `ollama list` (de solo lectura: solo lista, nunca borra) y lee el nombre
y el tamaño de cada modelo instalado. A diferencia de Docker, Ollama no separa
"recuperable" de "en uso": todo lo que lista se puede borrar con `ollama rm`,
así que cada modelo es una huella en sí mismo. Si Ollama no está instalado, el
comando falla o la salida no se puede leer, se omite sin error.
"""

import re
from collections.abc import Callable

from discoclaro.adapters.inspectors._process import resolve_executable, run_command
from discoclaro.domain.insights import Insight, InsightItem

_COLUMNS = re.compile(r"\s{2,}")
_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)$", re.IGNORECASE)
_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def _parse_size(text: str) -> int | None:
    match = _SIZE.match(text.strip())
    if match is None:
        return None
    return round(float(match.group(1)) * _UNITS[match.group(2).lower()])


def _parse_list(output: str) -> list[InsightItem]:
    lines = output.splitlines()
    if not lines:
        return []
    header = _COLUMNS.split(lines[0].strip())
    try:
        name_column = header.index("NAME")
        size_column = header.index("SIZE")
    except ValueError:
        return []
    items = []
    for line in lines[1:]:
        if not line.strip():
            continue
        columns = _COLUMNS.split(line.strip())
        if len(columns) <= max(name_column, size_column):
            continue
        size = _parse_size(columns[size_column])
        if size is None:
            continue
        items.append(InsightItem(name=columns[name_column], size_bytes=size))
    return items


def inspect(
    *,
    resolve: Callable[[str], str | None] = resolve_executable,
    run: Callable[[list[str]], str | None] = run_command,
) -> Insight | None:
    """La huella de los modelos de Ollama, o `None` si no hay nada que decir."""
    executable = resolve("ollama")
    if executable is None:
        return None
    output = run([executable, "list"])
    if output is None:
        return None
    items = _parse_list(output)
    if not items:
        return None
    return Insight(program="ollama", items=tuple(items))
