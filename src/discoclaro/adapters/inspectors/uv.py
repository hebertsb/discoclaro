"""Inspector de uv (T2C.1): cuánto ocupa su caché.

`uv cache size` existe, pero su propia ayuda la marca "experimental" ("may
change without warning"): no se usa, para no depender de un formato que puede
cambiar de un día para el otro. En cambio, `uv cache dir` (de solo lectura,
estable) da la ruta, y esa carpeta se mide de verdad con el mismo medidor del
escaneo (T2B.5 hace lo mismo para la huella de un programa). Si uv no está
instalado, el comando falla o la carpeta no existe, se omite sin error.
"""

from collections.abc import Callable

from discoclaro.adapters.inspectors._folder import measure_folder
from discoclaro.adapters.inspectors._process import resolve_executable, run_command
from discoclaro.domain.insights import Insight, InsightItem


def inspect(
    *,
    resolve: Callable[[str], str | None] = resolve_executable,
    run: Callable[[list[str]], str | None] = run_command,
    measure: Callable[[str], int | None] = measure_folder,
) -> Insight | None:
    """La huella de la caché de uv, o `None` si no hay nada que decir."""
    executable = resolve("uv")
    if executable is None:
        return None
    output = run([executable, "cache", "dir"])
    if output is None:
        return None
    path = output.strip()
    if not path:
        return None
    size = measure(path)
    if not size:
        return None
    return Insight(program="uv", items=(InsightItem(name="cache", size_bytes=size),))
