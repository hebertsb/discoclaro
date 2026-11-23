"""Base de datos de las fotos (SQLite): `snapshots`, `nodes`, `large_files` y `alerts`.

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
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Self

from discoclaro.domain.decisions import Decision
from discoclaro.domain.footprint import Footprint
from discoclaro.domain.models import LargeFile, Node, Program, Snapshot
from discoclaro.platform import windows

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
    (
        3,
        (
            # Archivos individuales grandes (T2.5), para que growth pueda nombrar uno
            # nuevo; el resto de los archivos nunca se nombra, solo se suma.
            """
            CREATE TABLE large_files (
                snapshot_id INTEGER NOT NULL
                    REFERENCES snapshots (id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)
            )
            """,
            "CREATE INDEX large_files_by_path ON large_files (snapshot_id, path)",
            "PRAGMA user_version = 3",
        ),
    ),
    (
        4,
        (
            # Para no repetir el mismo aviso en menos de 24 h (T2.7): una fila por
            # par (kind, target) con la última vez que se avisó.
            """
            CREATE TABLE alerts (
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                fired_at TEXT NOT NULL,
                PRIMARY KEY (kind, target)
            )
            """,
            "PRAGMA user_version = 4",
        ),
    ),
    (
        5,
        (
            # Inventario de programas instalados (T2B.2), leído del registro. No es
            # una foto por revisión como `snapshots`: cada programa es UNA fila que
            # se actualiza (`last_seen`) mientras sigue instalado, y se marca
            # `removed_at` el día que una revisión ya no lo encuentra. Sin
            # restricción UNIQUE en `name`: dos instalaciones reales (máquina y
            # usuario) pueden compartir el mismo nombre, y una colisión ahí no debe
            # tumbar el guardado.
            """
            CREATE TABLE programs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT,
                publisher TEXT,
                install_location TEXT,
                install_date TEXT,
                estimated_size_bytes INTEGER
                    CHECK (estimated_size_bytes IS NULL OR estimated_size_bytes >= 0),
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                removed_at TEXT
            )
            """,
            "CREATE INDEX programs_by_name ON programs (name)",
            # Carpetas candidatas de un programa (T2B.4/T2B.5); por ahora solo el
            # esquema, todavía no la llena nada: `confidence` no lleva una lista
            # cerrada de valores aquí porque esa lista la define domain/footprint.py,
            # que no existe todavía.
            """
            CREATE TABLE program_footprints (
                program_id INTEGER NOT NULL
                    REFERENCES programs (id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                confidence TEXT NOT NULL,
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX program_footprints_by_program "
            "ON program_footprints (program_id, path)",
            "PRAGMA user_version = 5",
        ),
    ),
    (
        6,
        (
            # Decisiones del usuario sobre algo puntual (T2C.4): una fila por
            # `target`, la última que se guardó. `status` no lleva una lista
            # cerrada aquí a propósito, la misma razón que `program_footprints`
            # en T2B.2: la define `domain/decisions.py`.
            """
            CREATE TABLE decisions (
                target TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                note TEXT,
                decided_at TEXT NOT NULL
            )
            """,
            "PRAGMA user_version = 6",
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

_INSERT_LARGE_FILE = """
    INSERT INTO large_files (snapshot_id, path, size_bytes) VALUES (?, ?, ?)
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

_SELECT_NODE_AT_PATH = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ? AND path = ?
    ORDER BY rowid
    LIMIT 1
"""

_SELECT_ROOT_NODES = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ? AND depth = 0
    ORDER BY size_bytes DESC, path
"""

_SELECT_CHILD_NODES = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ? AND parent_path = ?
    ORDER BY size_bytes DESC, path
"""

_SELECT_LARGE_FILES = """
    SELECT path, size_bytes
    FROM large_files
    WHERE snapshot_id = ?
    ORDER BY path, rowid
"""

_SELECT_NODES_MATCHING = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ? AND path LIKE ?
    ORDER BY size_bytes DESC, path
    LIMIT ?
"""

_SELECT_TOP_NODES = """
    SELECT path, parent_path, size_bytes, n_files, depth, rule_id
    FROM nodes
    WHERE snapshot_id = ? AND depth > 0
    ORDER BY size_bytes DESC, path
    LIMIT ?
"""

_SELECT_TOP_LARGE_FILES = """
    SELECT path, size_bytes
    FROM large_files
    WHERE snapshot_id = ?
    ORDER BY size_bytes DESC, path
    LIMIT ?
"""

_SELECT_LARGE_FILES_MATCHING = """
    SELECT path, size_bytes
    FROM large_files
    WHERE snapshot_id = ? AND path LIKE ?
    ORDER BY size_bytes DESC, path
    LIMIT ?
"""

_SELECT_ALERT = "SELECT fired_at FROM alerts WHERE kind = ? AND target = ?"

_UPSERT_ALERT = """
    INSERT INTO alerts (kind, target, fired_at) VALUES (?, ?, ?)
    ON CONFLICT (kind, target) DO UPDATE SET fired_at = excluded.fired_at
"""

_UPSERT_DECISION = """
    INSERT INTO decisions (target, status, note, decided_at) VALUES (?, ?, ?, ?)
    ON CONFLICT (target) DO UPDATE SET
        status = excluded.status, note = excluded.note, decided_at = excluded.decided_at
"""

_SELECT_DECISION = "SELECT status, note, decided_at FROM decisions WHERE target = ?"

_SELECT_ALL_DECISIONS = (
    "SELECT target, status, note, decided_at FROM decisions ORDER BY target"
)

_SELECT_PROGRAM_ID_BY_NAME = "SELECT id FROM programs WHERE name = ?"

_INSERT_PROGRAM = """
    INSERT INTO programs (
        name, version, publisher, install_location, install_date,
        estimated_size_bytes, first_seen, last_seen, removed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
"""

_UPDATE_PROGRAM = """
    UPDATE programs SET
        version = ?, publisher = ?, install_location = ?, install_date = ?,
        estimated_size_bytes = ?, last_seen = ?, removed_at = NULL
    WHERE name = ?
"""

_SELECT_ACTIVE_PROGRAMS = """
    SELECT name, version, publisher, install_location, install_date,
           estimated_size_bytes
    FROM programs
    WHERE removed_at IS NULL
    ORDER BY name
"""

_SELECT_REMOVED_PROGRAMS = """
    SELECT name, version, publisher, install_location, install_date,
           estimated_size_bytes
    FROM programs
    WHERE removed_at IS NOT NULL
    ORDER BY removed_at DESC, name
"""

_MARK_ALL_REMOVED = "UPDATE programs SET removed_at = ? WHERE removed_at IS NULL"

_INSERT_FOOTPRINT = """
    INSERT INTO program_footprints (
        program_id, path, size_bytes, confidence, reason, recorded_at
    ) VALUES (?, ?, ?, ?, ?, ?)
"""

_DELETE_FOOTPRINTS = "DELETE FROM program_footprints WHERE program_id = ?"

_SELECT_FOOTPRINTS_OF_PROGRAM = """
    SELECT f.path, f.size_bytes, f.confidence, f.reason
    FROM program_footprints f
    JOIN programs p ON p.id = f.program_id
    WHERE p.name = ?
    ORDER BY f.path
"""

_SELECT_ALL_FOOTPRINTS = """
    SELECT p.name, f.path, f.size_bytes, f.confidence, f.reason
    FROM program_footprints f
    JOIN programs p ON p.id = f.program_id
    ORDER BY p.name, f.path
"""

_DELETE_SNAPSHOT = "DELETE FROM snapshots WHERE id = ?"

# En Python, un sustituto suelto (UTF-16 inválido) es siempre un carácter solo.
_LONE_SURROGATES = re.compile("[\ud800-\udfff]")


class StoreError(Exception):
    """Un problema propio de la base: esquema más nuevo, desactualizado o sin WAL."""


class Store:
    """Las fotos guardadas. Se abre con `Store.open` o `Store.open_readonly`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(
        cls,
        path: str | PathLike[str],
        *,
        ensure_safe: Callable[
            [str | PathLike[str]], None
        ] = windows.ensure_safe_to_write,
    ) -> Self:
        """Abre la base para leer y escribir; si no existe la crea y la deja al día.

        La carpeta de `path` tiene que existir. Antes de tocar nada revalida que ni la
        carpeta ni el archivo sean un enlace o tengan otro nombre (T1.9); si `path`
        todavía no existe, no hay nada que revalidar. Lanza `StoreError` si la base es
        de una versión más nueva del programa, y `sqlite3.DatabaseError` si no es una
        base.
        """
        path = Path(path)
        ensure_safe(path.parent)
        ensure_safe(path)
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

    def save_snapshot(
        self,
        snapshot: Snapshot,
        nodes: Iterable[Node],
        large_files: Iterable[LargeFile] = (),
    ) -> int:
        """Guarda una foto, sus carpetas y sus archivos grandes en UNA transacción.

        Devuelve el id de la foto. Si algo falla a mitad (incluida una interrupción
        con Ctrl+C), no queda nada. El `id` de `snapshot` se ignora: lo asigna la
        base. Los nombres con UTF-16 inválido se guardan con U+FFFD, porque SQLite no
        puede representarlos.
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
            self._connection.executemany(
                _INSERT_LARGE_FILE,
                (_large_file_params(snapshot_id, file) for file in large_files),
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

    def node_at_path(self, snapshot_id: int, path: str) -> Node | None:
        """Una sola carpeta por su ruta exacta dentro de `snapshot_id`, usando el
        índice `nodes_by_path` sin cargar los decenas de miles de nodos de la foto."""
        row = self._connection.execute(
            _SELECT_NODE_AT_PATH, (snapshot_id, _clean(path))
        ).fetchone()
        if row is None:
            return None
        p, parent_path, size_bytes, n_files, depth, rule_id = row
        return Node(
            path=p,
            parent_path=parent_path,
            size_bytes=size_bytes,
            n_files=n_files,
            depth=depth,
            rule_id=rule_id,
        )

    def children_nodes(self, snapshot_id: int, parent_path: str | None) -> list[Node]:
        """Los hijos directos de `parent_path` (o las raíces `depth = 0` si es `None`),
        ordenados de mayor a menor tamaño directamente en SQLite."""
        if parent_path is None:
            rows = self._connection.execute(
                _SELECT_ROOT_NODES, (snapshot_id,)
            ).fetchall()
        else:
            rows = self._connection.execute(
                _SELECT_CHILD_NODES, (snapshot_id, _clean(parent_path))
            ).fetchall()
        return [
            Node(
                path=p,
                parent_path=parent,
                size_bytes=size_bytes,
                n_files=n_files,
                depth=depth,
                rule_id=rule_id,
            )
            for p, parent, size_bytes, n_files, depth, rule_id in rows
        ]

    def large_files(self, snapshot_id: int) -> list[LargeFile]:
        """Los archivos grandes de una foto, ordenados por ruta; vacío si no hay."""
        rows = self._connection.execute(_SELECT_LARGE_FILES, (snapshot_id,)).fetchall()
        return [
            LargeFile(path=path, size_bytes=size_bytes) for path, size_bytes in rows
        ]

    def search_nodes(
        self, snapshot_id: int, query: str = "", limit: int = 100
    ) -> list[Node]:
        """Busca carpetas en `snapshot_id` por texto (`query`) o devuelve las más
        grandes si `query` está vacío, directo desde SQLite con `LIMIT`."""
        cleaned = _clean(query.strip())
        if not cleaned:
            rows = self._connection.execute(
                _SELECT_TOP_NODES, (snapshot_id, limit)
            ).fetchall()
        else:
            pattern = f"%{cleaned}%"
            rows = self._connection.execute(
                _SELECT_NODES_MATCHING, (snapshot_id, pattern, limit)
            ).fetchall()
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

    def search_large_files(
        self, snapshot_id: int, query: str = "", limit: int = 100
    ) -> list[LargeFile]:
        """Busca archivos grandes (>= 50 MiB) en `snapshot_id` por texto o devuelve
        los más grandes de mayor a menor tamaño."""
        cleaned = _clean(query.strip())
        if not cleaned:
            rows = self._connection.execute(
                _SELECT_TOP_LARGE_FILES, (snapshot_id, limit)
            ).fetchall()
        else:
            pattern = f"%{cleaned}%"
            rows = self._connection.execute(
                _SELECT_LARGE_FILES_MATCHING, (snapshot_id, pattern, limit)
            ).fetchall()
        return [
            LargeFile(path=path, size_bytes=size_bytes) for path, size_bytes in rows
        ]

    def last_alert_at(self, kind: str, target: str) -> datetime | None:
        """Cuándo se avisó por última vez de `kind` en `target`, o `None` si nunca."""
        row = self._connection.execute(
            _SELECT_ALERT, (_clean(kind), _clean(target))
        ).fetchone()
        return None if row is None else datetime.fromisoformat(row[0])

    def record_alert(self, kind: str, target: str, fired_at: datetime) -> None:
        """Guarda que `kind` en `target` se avisó en `fired_at` (una fila por par)."""
        self._connection.execute(
            _UPSERT_ALERT,
            (
                _clean(kind),
                _clean(target),
                fired_at.astimezone(UTC).isoformat(timespec="microseconds"),
            ),
        )

    def delete_snapshots(self, ids: Iterable[int]) -> None:
        """Borra esas fotos; sus carpetas y archivos grandes se van con ellas
        (`ON DELETE CASCADE`). Lo usa la retención (T2.9) para no guardar todo
        para siempre."""
        self._connection.executemany(_DELETE_SNAPSHOT, [(i,) for i in ids])

    def save_programs(self, programs: Iterable[Program], seen_at: datetime) -> None:
        """Guarda una revisión del inventario (T2B.2): actualiza (o inserta) cada
        programa que se encontró y marca como quitados, justo en `seen_at`, los que
        antes estaban activos y ya no aparecieron. Dos programas reales con el
        mismo nombre (instalación de máquina y de usuario) se guardan como uno
        solo: no hay otra forma de distinguirlos con lo que da el registro.
        """
        moment = seen_at.astimezone(UTC).isoformat(timespec="microseconds")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            # Primero se marcan como quitados todos los que estaban activos; el
            # propio guardado de abajo los "des-marca" (vuelve a poner `removed_at`
            # en NULL) si siguen apareciendo. Así no hace falta un "NOT IN (...)"
            # con una cantidad de valores que cambia cada vez.
            self._connection.execute(_MARK_ALL_REMOVED, (moment,))
            for program in programs:
                name = _clean(program.name)
                values = (
                    _clean_optional(program.version),
                    _clean_optional(program.publisher),
                    _clean_optional(program.install_location),
                    _clean_optional(program.install_date),
                    program.estimated_size_bytes,
                )
                row = self._connection.execute(
                    _SELECT_PROGRAM_ID_BY_NAME, (name,)
                ).fetchone()
                if row is None:
                    self._connection.execute(
                        _INSERT_PROGRAM, (name, *values, moment, moment)
                    )
                else:
                    self._connection.execute(_UPDATE_PROGRAM, (*values, moment, name))
            self._connection.execute("COMMIT")
        except BaseException:
            _rollback(self._connection)
            raise

    def has_recorded_programs(self) -> bool:
        """Si ya se guardó alguna revisión del inventario de programas, aunque
        ahora mismo no haya ninguno activo. Distingue la primera revisión de
        verdad (nada que comparar todavía, T2B.5) de una base vacía porque todo
        se desinstaló."""
        row = self._connection.execute("SELECT 1 FROM programs LIMIT 1").fetchone()
        return row is not None

    def active_programs(self) -> list[Program]:
        """Los programas de la última revisión que no están marcados como
        quitados (`removed_at` vacío), ordenados por nombre."""
        rows = self._connection.execute(_SELECT_ACTIVE_PROGRAMS).fetchall()
        return [
            Program(
                name=name,
                version=version,
                publisher=publisher,
                install_location=install_location,
                install_date=install_date,
                estimated_size_bytes=estimated_size_bytes,
                uninstall_string=None,
            )
            for (
                name,
                version,
                publisher,
                install_location,
                install_date,
                estimated_size_bytes,
            ) in rows
        ]

    def removed_programs(self) -> list[Program]:
        """Los programas que alguna vez se registraron y ya están marcados como
        desinstalados (`removed_at` no vacío), del más reciente al más viejo."""
        rows = self._connection.execute(_SELECT_REMOVED_PROGRAMS).fetchall()
        return [
            Program(
                name=name,
                version=version,
                publisher=publisher,
                install_location=install_location,
                install_date=install_date,
                estimated_size_bytes=estimated_size_bytes,
                uninstall_string=None,
            )
            for (
                name,
                version,
                publisher,
                install_location,
                install_date,
                estimated_size_bytes,
            ) in rows
        ]

    def save_footprints(
        self, program_name: str, footprints: Iterable[Footprint], recorded_at: datetime
    ) -> None:
        """Guarda la huella de un programa (T2B.5): una fila por carpeta candidata.

        Reemplaza cualquier huella guardada antes para este programa (un
        programa desinstalado y vuelto a instalar conserva el mismo `id`, T2B.2;
        sin este reemplazo quedarían mezcladas huellas de dos instalaciones
        distintas). No hace nada si `program_name` no está en `programs` (hay
        que llamar `save_programs` primero con ese programa).
        """
        row = self._connection.execute(
            _SELECT_PROGRAM_ID_BY_NAME, (_clean(program_name),)
        ).fetchone()
        if row is None:
            return
        program_id = row[0]
        moment = recorded_at.astimezone(UTC).isoformat(timespec="microseconds")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(_DELETE_FOOTPRINTS, (program_id,))
            self._connection.executemany(
                _INSERT_FOOTPRINT,
                [
                    (
                        program_id,
                        _clean(footprint.path),
                        footprint.size_bytes,
                        footprint.confidence,
                        footprint.reason,
                        moment,
                    )
                    for footprint in footprints
                ],
            )
            self._connection.execute("COMMIT")
        except BaseException:
            _rollback(self._connection)
            raise

    def footprints_of_program(self, name: str) -> list[Footprint]:
        """La huella ya guardada de un programa (T2B.7), ordenada por ruta;
        vacía si nunca se guardó ninguna. `last_modified` siempre es `None`:
        esa columna no se persiste, solo cuándo se guardó (`recorded_at`)."""
        rows = self._connection.execute(
            _SELECT_FOOTPRINTS_OF_PROGRAM, (_clean(name),)
        ).fetchall()
        return [
            Footprint(
                path=path,
                size_bytes=size_bytes,
                last_modified=None,
                confidence=confidence,
                reason=reason,
            )
            for path, size_bytes, confidence, reason in rows
        ]

    def all_program_footprints(self) -> dict[str, tuple[Footprint, ...]]:
        """Todas las huellas guardadas de todos los programas en una sola consulta."""
        rows = self._connection.execute(_SELECT_ALL_FOOTPRINTS).fetchall()
        grouped: dict[str, list[Footprint]] = {}
        for name, path, size_bytes, confidence, reason in rows:
            grouped.setdefault(name, []).append(
                Footprint(
                    path=path,
                    size_bytes=size_bytes,
                    last_modified=None,
                    confidence=confidence,
                    reason=reason,
                )
            )
        return {name: tuple(items) for name, items in grouped.items()}

    def save_decision(
        self, target: str, status: str, note: str | None, decided_at: datetime
    ) -> None:
        """Guarda la decisión sobre `target` (T2C.4): reemplaza la anterior,
        si había una. No valida `status` (eso lo hace `domain/decisions.py`
        antes de llamar acá, igual que con `program_footprints.confidence`)."""
        self._connection.execute(
            _UPSERT_DECISION,
            (
                _clean(target),
                _clean(status),
                _clean_optional(note),
                decided_at.astimezone(UTC).isoformat(timespec="microseconds"),
            ),
        )

    def decision_of(self, target: str) -> Decision | None:
        """La decisión guardada sobre `target`, o `None` si nunca se decidió
        nada."""
        row = self._connection.execute(_SELECT_DECISION, (_clean(target),)).fetchone()
        if row is None:
            return None
        status, note, decided_at = row
        return Decision(
            target=target,
            status=status,
            note=note,
            decided_at=datetime.fromisoformat(decided_at),
        )

    def all_decisions(self) -> list[Decision]:
        """Todas las decisiones guardadas, ordenadas por `target`."""
        rows = self._connection.execute(_SELECT_ALL_DECISIONS).fetchall()
        return [
            Decision(
                target=target,
                status=status,
                note=note,
                decided_at=datetime.fromisoformat(decided_at),
            )
            for target, status, note, decided_at in rows
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


def _large_file_params(snapshot_id: int | None, file: LargeFile) -> tuple[object, ...]:
    return (snapshot_id, _clean(file.path), file.size_bytes)


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
