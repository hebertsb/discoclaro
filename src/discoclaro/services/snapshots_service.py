"""Caso de uso "fotos guardadas": la lista de las que ya se tomaron (T3.1).

Es de solo lectura y no vuelve a escanear: solo lee la base, igual que `report_service`.
Nunca haber escaneado todavía no es un error aquí (a diferencia de `report`/`growth`,
que sí necesitan al menos una foto): una lista vacía es una respuesta válida.
"""

import sqlite3
from pathlib import Path

from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.models import Snapshot
from discoclaro.platform import windows
from discoclaro.services.scan_service import DATABASE_NAME


class SnapshotsError(Exception):
    """No se pudo leer la lista de fotos; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


def run_snapshots(
    volume: str | None = None, data_dir: Path | None = None
) -> list[Snapshot]:
    """Las fotos guardadas, de la más nueva a la más vieja; solo las de `volume` si
    se da. Vacía si todavía no se escaneó nada."""
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / DATABASE_NAME
    if not database.is_file():
        return []
    try:
        store = Store.open_readonly(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise SnapshotsError("cannot_open_database", str(error)) from error
    with store:
        return store.snapshots(volume)
