"""Medir una carpeta de verdad (T2C.1), para los inspectores que no tienen un
comando propio y confiable para decir su propio tamaño (uv, npm: ver sus
módulos para el motivo de cada uno). Reusa el mismo medidor del escaneo
(`adapters/scanner.py`), no uno nuevo.
"""

from discoclaro.adapters.scanner import scan_tree


def measure_folder(path: str) -> int | None:
    """El tamaño total de `path`, o `None` si no existe, no es una carpeta o
    no se pudo leer."""
    try:
        result = scan_tree(path)
    except OSError:
        return None
    return result.nodes[-1].size_bytes
