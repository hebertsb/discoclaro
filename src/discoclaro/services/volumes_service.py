"""Caso de uso "volúmenes": el espacio real de cada disco ahora mismo (T3.1).

No necesita ninguna foto guardada: mide los discos en el momento, igual que hace
`scan` antes de recorrer nada.
"""

from collections.abc import Callable

from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Volume


def run_volumes(*, volumes: Callable[[], list[Volume]] = list_volumes) -> list[Volume]:
    """Los volúmenes del equipo, tal como están ahora mismo."""
    return volumes()
