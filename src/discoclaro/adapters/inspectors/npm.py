"""Inspector de npm (T2C.1): cuánto ocupa su caché.

`npm cache verify` da el tamaño, pero **no es de solo lectura**: de verdad
recolecta basura en la caché (`Content garbage-collected: ...` en su propia
salida, comprobado real), algo que esta fase nunca puede hacer. En cambio,
`npm config get cache` (de solo lectura) da la ruta, y esa carpeta se mide de
verdad con el mismo medidor del escaneo. Si npm no está instalado, el comando
falla o la carpeta no existe, se omite sin error.
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
    """La huella de la caché de npm, o `None` si no hay nada que decir."""
    executable = resolve("npm")
    if executable is None:
        return None
    output = run([executable, "config", "get", "cache"])
    if output is None:
        return None
    path = output.strip()
    if not path:
        return None
    size = measure(path)
    if not size:
        return None
    return Insight(program="npm", items=(InsightItem(name="cache", size_bytes=size),))
