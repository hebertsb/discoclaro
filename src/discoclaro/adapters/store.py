"""Base de datos de las fotos (SQLite): tablas `snapshots` y `nodes`.

Reglas (amenaza 3 del modelo de amenazas):

- Solo consultas parametrizadas con `?`. Los nombres de tabla y de columna están fijos
  en el código y nunca vienen de afuera. Todo el SQL del proyecto vive en este módulo.
- Una transacción por foto: o se guarda completa o no se guarda nada.
- Modo WAL, claves foráneas activadas y versión del esquema en `PRAGMA user_version`.
- `Store.open_readonly` abre la base solo para leer (la usará la API del dashboard).
- Se guardan rutas, tamaños y fechas; nunca el contenido de los archivos.
"""

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Self

from discoclaro.domain.models import Node, Snapshot

BUSY_TIMEOUT_S = 5.0

# Cada versión del esquema es una lista de sentencias literales que termina fijando su
# propio `user_version`. Para cambiar el esquema se agrega una versión; no se edita una.
MIGRATIONS = (
    (
        1,
        (
            """
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY,
                taken_at TEXT NOT NULL,
                volume TEXT NOT NULL,
                total_bytes INTEGER NOT NULL CHECK (total_bytes >= 0),
                used_bytes INTEGER NOT NULL CHECK (used_bytes >= 0),
                free_bytes INTEGER NOT NULL CHECK (free_bytes >= 0),
                scanned_bytes INTEGER NOT NULL CHECK (scanned_bytes >= 0),
                system_files_bytes INTEGER NOT NULL CHECK (system_files_bytes >= 0),
                unreadable_bytes INTEGER NOT NULL CHECK (unreadable_bytes >= 0),
                unreadable_count INTEGER NOT NULL CHECK (unreadable_count >= 0),
                duration_s REAL NOT NULL CHECK (duration_s >= 0),
                is_admin INTEGER NOT NULL CHECK (is_admin IN (0, 1))
            )
            """,
            """
            CREATE TABLE nodes (
                snapshot_id INTEGER NOT NULL
                    REFERENCES snapshots (id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                parent_path TEXT,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                n_files INTEGER NOT NULL CHECK (n_files >= 0),
                depth INTEGER NOT NULL CHECK (depth >= 0),
                rule_id TEXT
            )
            """,
            "CREATE INDEX nodes_by_path ON nodes (snapshot_id, path)",
            "CREATE INDEX nodes_by_rule ON nodes (snapshot_id, rule_id)",
            "CREATE INDEX snapshots_by_volume ON snapshots (volume, taken_at)",
            "PRAGMA user_version = 1",
        ),
    ),
    (
        2,
        (
            # Bytes que ocupan de más los enlaces duros dentro de `scanned_bytes`.
            "ALTER TABLE snapshots ADD COLUMN duplicate_bytes INTEGER NOT NULL "
            "DEFAULT 0 CHECK (duplicate_bytes >= 0)",
            "PRAGMA user_version = 2",
        ),
    ),
)

_INSERT_SNAPSHOT = """
    INSERT INTO snapshots (
        taken_at, volume, total_bytes, used_bytes, free_bytes, scanned_bytes,
        system_files_bytes, unreadable_bytes, unreadable_count, duration_s, is_admin,
        duplicate_bytes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_NODE = """
    INSERT INTO nodes (
        snapshot_id, path, parent_path, size_bytes, n_files, depth, rule_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_ALL_SNAPSHOTS = """
    SELECT id, taken_at, volume, total_bytes, used_bytes, free_bytes, scanned_bytes,
           system_files_bytes, unreadable_bytes, unreadable_count, duration_s, is_admin,
           duplicate_bytes
    FROM snapshots
    ORDER BY taken_at DESC, id DESC
"""

_SELECT_SNAPSHOTS_OF_VOLUME = """
    SELECT id, taken_at, volume, total_bytes, used_bytes, free_bytes, scanned_bytes,
           system_files_bytes, unreadable_bytes, unreadable_count, duration_s, is_admin,
           duplicate_bytes
    FROM snapshots
    WHERE volume = ?
    ORDER BY taken_at DESC, id DESC
"""

_SELECT_ONE_SNAPSHOT = """
    SELECT id, taken_at, volume, total_bytes, used_bytes, free_bytes, scanned_bytes,
           system_files_bytes, unreadable_bytes, unreadable_count, duration_s, is_admin,
           duplicate_bytes
    FROM snapshots
    WHERE id = ?
"""

_SELECT_NODES = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ?
    ORDER BY path, rowid
"""

# En Python, un sustituto suelto (UTF-16 inválido) es siempre un carácter solo.
_LONE_SURROGATES = re.compile("[\ud800-\udfff]")


class StoreError(Exception):
    """Un problema propio de la base: esquema más nuevo, desactualizado o sin WAL."""


class Store:
    """Las fotos guardadas. Se abre con `Store.open` o `Store.open_readonly`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: str | PathLike[str]) -> Self:
        """Abre la base para leer y escribir; si no existe la crea y la deja al día.

        La carpeta de `path` tiene que existir. Lanza `StoreError` si la base es de una
        versión más nueva del programa, y `sqlite3.DatabaseError` si no es una base.
        """
        connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            _refuse_if_newer(connection)
            _configure(connection)
            _migrate(connection)
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    @classmethod
    def open_readonly(cls, path: str | PathLike[str]) -> Self:
        """Abre una base que ya existe y está al día, sin poder escribirle.

        No crea el archivo si falta y rechaza una base con otra versión del esquema.
        """
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(
            uri, uri=True, timeout=BUSY_TIMEOUT_S, isolation_level=None
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            current, latest = _user_version(connection), _latest_version()
            if current != latest:
                raise StoreError(
                    f"database schema version is {current} but {latest} is needed; "
                    "open it once with the program to update it"
                )
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    def close(self) -> None:
        """Cierra la conexión; se puede llamar más de una vez."""
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def save_snapshot(self, snapshot: Snapshot, nodes: Iterable[Node]) -> int:
        """Guarda una foto y sus carpetas en UNA transacción y devuelve su id.

        Si algo falla a mitad (incluida una interrupción con Ctrl+C), no queda nada. El
        `id` de `snapshot` se ignora: lo asigna la base. Los nombres con UTF-16 inválido
        se guardan con U+FFFD, porque SQLite no puede representarlos.
        """
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                _INSERT_SNAPSHOT, _snapshot_params(snapshot)
            )
            snapshot_id = cursor.lastrowid
            self._connection.executemany(
                _INSERT_NODE, (_node_params(snapshot_id, node) for node in nodes)
            )
            self._connection.execute("COMMIT")
        except BaseException:
            _rollback(self._connection)
            raise
        return snapshot_id

    def snapshots(self, volume: str | None = None) -> list[Snapshot]:
        """Las fotos, de la más nueva a la más vieja; solo las de `volume` si se da."""
        if volume is None:
            rows = self._connection.execute(_SELECT_ALL_SNAPSHOTS).fetchall()
        else:
            rows = self._connection.execute(
                _SELECT_SNAPSHOTS_OF_VOLUME, (_clean(volume),)
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def latest_snapshot(self, volume: str) -> Snapshot | None:
        """La foto más reciente de `volume`, o `None` si no hay ninguna."""
        found = self.snapshots(volume)
        return found[0] if found else None

    def get_snapshot(self, snapshot_id: int) -> Snapshot | None:
        """La foto con ese id, o `None` si no existe."""
        row = self._connection.execute(_SELECT_ONE_SNAPSHOT, (snapshot_id,)).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def nodes(self, snapshot_id: int) -> list[Node]:
        """Las carpetas de una foto, ordenadas por ruta; vacío si el id no existe."""
        rows = self._connection.execute(_SELECT_NODES, (snapshot_id,)).fetchall()
        return [
            Node(
                path=path,
                parent_path=parent_path,
                size_bytes=size_bytes,
                n_files=n_files,
                depth=depth,
                rule_id=rule_id,
            )
            for path, parent_path, size_bytes, n_files, depth, rule_id in rows
        ]


def _clean(text: str) -> str:
    """Cambia los sustitutos sueltos por U+FFFD: SQLite guarda UTF-8 y no los acepta."""
    return _LONE_SURROGATES.sub("�", text)


def _clean_optional(text: str | None) -> str | None:
    return None if text is None else _clean(text)


def _snapshot_params(snapshot: Snapshot) -> tuple[object, ...]:
    taken_at = snapshot.taken_at.astimezone(UTC).isoformat(timespec="microseconds")
    return (
        taken_at,
        _clean(snapshot.volume),
        snapshot.total_bytes,
        snapshot.used_bytes,
        snapshot.free_bytes,
        snapshot.scanned_bytes,
        snapshot.system_files_bytes,
        snapshot.unreadable_bytes,
        snapshot.unreadable_count,
        snapshot.duration_s,
        int(snapshot.is_admin),
        snapshot.duplicate_bytes,
    )


def _node_params(snapshot_id: int | None, node: Node) -> tuple[object, ...]:
    return (
        snapshot_id,
        _clean(node.path),
        _clean_optional(node.parent_path),
        node.size_bytes,
        node.n_files,
        node.depth,
        _clean_optional(node.rule_id),
    )


def _snapshot_from_row(row: tuple[object, ...]) -> Snapshot:
    (
        snapshot_id,
        taken_at,
        volume,
        total_bytes,
        used_bytes,
        free_bytes,
        scanned_bytes,
        system_files_bytes,
        unreadable_bytes,
        unreadable_count,
        duration_s,
        is_admin,
        duplicate_bytes,
    ) = row
    return Snapshot(
        id=snapshot_id,
        taken_at=datetime.fromisoformat(taken_at),
        volume=volume,
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        free_bytes=free_bytes,
        scanned_bytes=scanned_bytes,
        system_files_bytes=system_files_bytes,
        unreadable_bytes=unreadable_bytes,
        unreadable_count=unreadable_count,
        duration_s=duration_s,
        is_admin=bool(is_admin),
        duplicate_bytes=duplicate_bytes,
    )


def _user_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def _latest_version() -> int:
    return MIGRATIONS[-1][0]


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _configure(connection: sqlite3.Connection) -> None:
    """Activa el modo WAL y las claves foráneas, y comprueba que quedaron activos."""
    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        raise StoreError(f"could not turn on WAL mode (the database reports {mode!r})")
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise StoreError("could not turn on foreign key checks")


def _refuse_if_newer(connection: sqlite3.Connection) -> None:
    """Rechaza una base de una versión más nueva, antes de tocarle nada."""
    current, latest = _user_version(connection), _latest_version()
    if current > latest:
        raise StoreError(
            f"database schema version {current} is newer than the {latest} "
            "this program knows"
        )


def _migrate(connection: sqlite3.Connection) -> None:
    """Lleva el esquema a la última versión, cada versión en su propia transacción."""
    if _user_version(connection) == _latest_version():
        return
    for version, statements in MIGRATIONS:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if _user_version(connection) < version:
                for statement in statements:
                    connection.execute(statement)
            connection.execute("COMMIT")
        except BaseException:
            _rollback(connection)
            raise
