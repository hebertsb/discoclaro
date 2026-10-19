"""Pruebas de la base de fotos: siempre sobre archivos en `tmp_path`."""

import dataclasses
import inspect
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest

from discoclaro.adapters import store as store_module
from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.footprint import Footprint
from discoclaro.domain.models import LargeFile, Node, Program, Snapshot
from discoclaro.platform import windows

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

INSERT_NODE_SQL = (
    "INSERT INTO nodes (snapshot_id, path, parent_path, size_bytes, n_files, depth, "
    "rule_id) VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def make_snapshot(**changes):
    fields = {
        "taken_at": T0,
        "volume": "C:",
        "total_bytes": 1000,
        "used_bytes": 400,
        "free_bytes": 600,
        "scanned_bytes": 350,
        "system_files_bytes": 40,
        "unreadable_bytes": 0,
        "unreadable_count": 2,
        "duration_s": 12.5,
        "is_admin": False,
    }
    return Snapshot(**{**fields, **changes})


def make_nodes():
    return [
        Node("C:\\", None, 135, 4, 0),
        Node("C:\\docs", "C:\\", 35, 3, 1, "documents"),
        Node("C:\\docs\\deep", "C:\\docs", 5, 1, 2),
    ]


def raw_query(path, sql, params=()):
    """Mira la base sin pasar por `Store`."""
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "discoclaro.db"


@pytest.fixture
def store(db_path):
    with Store.open(db_path) as opened:
        yield opened


def count(store, table):
    sql = {
        "nodes": "SELECT COUNT(*) FROM nodes",
        "snapshots": "SELECT COUNT(*) FROM snapshots",
    }
    return store._connection.execute(sql[table]).fetchone()[0]


def test_open_creates_the_schema_with_wal_and_foreign_keys(store):
    connection = store._connection
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert {row[0] for row in tables} == {
        "snapshots",
        "nodes",
        "large_files",
        "alerts",
        "programs",
        "program_footprints",
    }
    indexes = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
    )
    assert {row[0] for row in indexes} == {
        "nodes_by_path",
        "nodes_by_rule",
        "snapshots_by_volume",
        "large_files_by_path",
        "programs_by_name",
        "program_footprints_by_program",
    }


def test_reopening_keeps_the_data(db_path):
    with Store.open(db_path) as first:
        snapshot_id = first.save_snapshot(make_snapshot(), make_nodes())
    with Store.open(db_path) as second:
        assert [s.id for s in second.snapshots()] == [snapshot_id]
        assert second.nodes(snapshot_id) == make_nodes()


def test_open_accepts_text_and_path_and_does_not_create_folders(tmp_path):
    with Store.open(str(tmp_path / "as_text.db")):
        pass
    assert (tmp_path / "as_text.db").exists()
    with pytest.raises(sqlite3.OperationalError):
        Store.open(tmp_path / "missing_folder" / "x.db")
    assert not (tmp_path / "missing_folder").exists()


def test_a_snapshot_and_its_folders_roundtrip(store):
    snapshot, nodes = make_snapshot(duplicate_bytes=45), make_nodes()
    snapshot_id = store.save_snapshot(snapshot, nodes)
    assert snapshot_id == 1
    assert store.get_snapshot(snapshot_id) == dataclasses.replace(snapshot, id=1)
    assert store.nodes(snapshot_id) == nodes


def test_large_files_roundtrip_and_are_sorted_by_path(store):
    files = [
        LargeFile("C:\\z\\big.gguf", 100),
        LargeFile("C:\\a\\model.gguf", 200),
    ]
    snapshot_id = store.save_snapshot(make_snapshot(), [], files)
    assert store.large_files(snapshot_id) == sorted(files, key=lambda f: f.path)


def test_a_snapshot_without_large_files_has_none(store):
    snapshot_id = store.save_snapshot(make_snapshot(), [])
    assert store.large_files(snapshot_id) == []


def test_large_files_of_a_missing_snapshot_is_empty(store):
    assert store.large_files(9999) == []


def test_large_files_are_deleted_with_their_snapshot(store):
    snapshot_id = store.save_snapshot(
        make_snapshot(), [], [LargeFile("C:\\a.bin", 100)]
    )
    store._connection.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
    assert store.large_files(snapshot_id) == []


def test_delete_snapshots_removes_the_given_ids(store):
    kept_id = store.save_snapshot(make_snapshot(), [])
    doomed_id = store.save_snapshot(make_snapshot(), [])
    store.delete_snapshots([doomed_id])
    assert {s.id for s in store.snapshots()} == {kept_id}


def test_delete_snapshots_takes_several_ids_at_once(store):
    first = store.save_snapshot(make_snapshot(), [])
    second = store.save_snapshot(make_snapshot(), [])
    store.delete_snapshots([first, second])
    assert store.snapshots() == []


def test_delete_snapshots_with_an_empty_list_does_nothing(store):
    kept_id = store.save_snapshot(make_snapshot(), [])
    store.delete_snapshots([])
    assert {s.id for s in store.snapshots()} == {kept_id}


def test_delete_snapshots_of_a_missing_id_is_not_an_error(store):
    store.delete_snapshots([9999])


def test_delete_snapshots_cascades_to_nodes_and_large_files(store):
    snapshot_id = store.save_snapshot(
        make_snapshot(), make_nodes(), [LargeFile("C:\\a.bin", 100)]
    )
    store.delete_snapshots([snapshot_id])
    assert store.nodes(snapshot_id) == []
    assert store.large_files(snapshot_id) == []


def make_program(**changes):
    fields = {
        "name": "Acme App",
        "version": "1.0",
        "publisher": "Acme",
        "install_location": "C:\\Program Files\\Acme",
        "install_date": "20261017",
        "estimated_size_bytes": 1024,
        "uninstall_string": "C:\\Program Files\\Acme\\uninst.exe",
    }
    return Program(**{**fields, **changes})


def program_row(store, name):
    return store._connection.execute(
        "SELECT first_seen, last_seen, removed_at FROM programs WHERE name = ?",
        (name,),
    ).fetchone()


def test_a_saved_program_can_be_read_back_as_active(store):
    store.save_programs([make_program()], T0)
    [program] = store.active_programs()
    assert program.name == "Acme App"
    assert program.version == "1.0"
    assert program.publisher == "Acme"
    assert program.install_location == "C:\\Program Files\\Acme"
    assert program.install_date == "20261017"
    assert program.estimated_size_bytes == 1024


def test_active_programs_never_carries_the_uninstall_string(store):
    store.save_programs([make_program()], T0)
    [program] = store.active_programs()
    assert program.uninstall_string is None


def test_saving_the_same_program_again_keeps_its_first_seen(store):
    store.save_programs([make_program()], T0)
    store.save_programs([make_program()], T0 + timedelta(days=1))
    first_seen, last_seen, removed_at = program_row(store, "Acme App")
    assert first_seen == T0.isoformat(timespec="microseconds")
    assert last_seen == (T0 + timedelta(days=1)).isoformat(timespec="microseconds")
    assert removed_at is None


def test_a_program_missing_from_a_later_save_is_marked_removed(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_programs([make_program(name="B")], T0 + timedelta(days=1))
    assert {p.name for p in store.active_programs()} == {"B"}
    _, _, removed_at = program_row(store, "A")
    assert removed_at == (T0 + timedelta(days=1)).isoformat(timespec="microseconds")


def test_an_already_removed_program_keeps_its_original_removed_at(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_programs([], T0 + timedelta(days=1))
    store.save_programs([], T0 + timedelta(days=2))
    _, _, removed_at = program_row(store, "A")
    assert removed_at == (T0 + timedelta(days=1)).isoformat(timespec="microseconds")


def test_a_removed_program_that_reappears_becomes_active_again(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_programs([], T0 + timedelta(days=1))
    store.save_programs([make_program(name="A")], T0 + timedelta(days=2))
    assert {p.name for p in store.active_programs()} == {"A"}
    _, _, removed_at = program_row(store, "A")
    assert removed_at is None


def test_saving_a_program_again_updates_its_changed_fields(store):
    store.save_programs([make_program(version="1.0")], T0)
    store.save_programs([make_program(version="2.0")], T0 + timedelta(days=1))
    [program] = store.active_programs()
    assert program.version == "2.0"


def test_saving_an_empty_inventory_marks_everything_removed(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_programs([], T0 + timedelta(days=1))
    assert store.active_programs() == []


def test_a_program_with_lone_surrogates_in_its_name_round_trips(store):
    hostile = "ghost\ud800name"
    store.save_programs([make_program(name=hostile)], T0)
    [program] = store.active_programs()
    assert program.name == "ghost�name"


def test_has_recorded_programs_is_false_before_any_save(store):
    assert store.has_recorded_programs() is False


def test_has_recorded_programs_is_true_after_a_save(store):
    store.save_programs([make_program()], T0)
    assert store.has_recorded_programs() is True


def test_has_recorded_programs_stays_true_once_everything_is_removed(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_programs([], T0 + timedelta(days=1))
    assert store.active_programs() == []
    assert store.has_recorded_programs() is True


def make_footprint(**changes):
    fields = {
        "path": "C:\\Program Files\\Acme",
        "size_bytes": 2048,
        "last_modified": None,
        "confidence": "high",
        "reason": "install_location",
    }
    return Footprint(**{**fields, **changes})


def footprint_rows(store, program_name):
    return store._connection.execute(
        """
        SELECT path, size_bytes, confidence, reason, recorded_at
        FROM program_footprints
        JOIN programs ON programs.id = program_footprints.program_id
        WHERE programs.name = ?
        ORDER BY path
        """,
        (program_name,),
    ).fetchall()


def test_save_footprints_of_an_unknown_program_does_nothing(store):
    store.save_footprints("Ghost", [make_footprint()], T0)
    assert footprint_rows(store, "Ghost") == []


def test_saved_footprints_can_be_read_back(store):
    store.save_programs([make_program()], T0)
    store.save_footprints(
        "Acme App",
        [
            make_footprint(path="C:\\Program Files\\Acme"),
            make_footprint(
                path="C:\\Users\\x\\AppData\\Roaming\\Acme",
                size_bytes=512,
                confidence="low",
                reason="partial_name_match",
            ),
        ],
        T0,
    )
    rows = footprint_rows(store, "Acme App")
    assert [row[0] for row in rows] == [
        "C:\\Program Files\\Acme",
        "C:\\Users\\x\\AppData\\Roaming\\Acme",
    ]
    assert rows[0][1:4] == (2048, "high", "install_location")
    assert rows[0][4] == T0.isoformat(timespec="microseconds")


def test_saving_footprints_again_replaces_the_previous_ones(store):
    store.save_programs([make_program()], T0)
    store.save_footprints("Acme App", [make_footprint(path="C:\\Old")], T0)
    store.save_footprints(
        "Acme App", [make_footprint(path="C:\\New")], T0 + timedelta(days=1)
    )
    assert [row[0] for row in footprint_rows(store, "Acme App")] == ["C:\\New"]


def test_footprints_are_deleted_with_their_program(store):
    store.save_programs([make_program(name="A")], T0)
    store.save_footprints("A", [make_footprint()], T0)
    store._connection.execute("DELETE FROM programs WHERE name = 'A'")
    assert footprint_rows(store, "A") == []


def test_footprints_of_program_reads_back_what_was_saved(store):
    store.save_programs([make_program()], T0)
    store.save_footprints(
        "Acme App",
        [
            make_footprint(path="C:\\Program Files\\Acme", size_bytes=2048),
            make_footprint(
                path="C:\\Users\\x\\AppData\\Roaming\\Acme",
                size_bytes=512,
                confidence="low",
                reason="partial_name_match",
            ),
        ],
        T0,
    )
    found = store.footprints_of_program("Acme App")
    assert [f.path for f in found] == [
        "C:\\Program Files\\Acme",
        "C:\\Users\\x\\AppData\\Roaming\\Acme",
    ]
    assert found[0].size_bytes == 2048
    assert found[0].confidence == "high"
    assert found[0].reason == "install_location"
    assert found[0].last_modified is None


def test_footprints_of_program_is_empty_for_an_unknown_program(store):
    assert store.footprints_of_program("Ghost") == []


def test_footprints_of_program_is_empty_when_none_were_ever_saved(store):
    store.save_programs([make_program(name="A")], T0)
    assert store.footprints_of_program("A") == []


def test_an_alert_never_fired_has_no_last_time(store):
    assert store.last_alert_at("volume_low_free", "C:") is None


def test_recording_an_alert_can_be_read_back(store):
    fired_at = T0
    store.record_alert("volume_low_free", "C:", fired_at)
    assert store.last_alert_at("volume_low_free", "C:") == fired_at


def test_recording_the_same_alert_again_replaces_its_time(store):
    store.record_alert("volume_low_free", "C:", T0)
    later = T0 + timedelta(hours=1)
    store.record_alert("volume_low_free", "C:", later)
    assert store.last_alert_at("volume_low_free", "C:") == later


def test_different_kinds_or_targets_are_tracked_separately(store):
    store.record_alert("volume_low_free", "C:", T0)
    assert store.last_alert_at("volume_near_limit", "C:") is None
    assert store.last_alert_at("volume_low_free", "D:") is None


def test_an_alert_time_is_stored_in_utc_and_comes_back_equal(store):
    local = T0.astimezone(timezone(timedelta(hours=-3)))
    store.record_alert("volume_low_free", "C:", local)
    assert store.last_alert_at("volume_low_free", "C:") == local


def test_time_is_stored_in_utc_and_comes_back_equal(store):
    local = timezone(timedelta(hours=5, minutes=30))
    taken = datetime(2026, 10, 17, 17, 30, 15, 123456, tzinfo=local)
    snapshot_id = store.save_snapshot(make_snapshot(taken_at=taken), [])
    saved = store.get_snapshot(snapshot_id)
    assert saved.taken_at == taken
    assert saved.taken_at.utcoffset() == timedelta(0)
    stored = store._connection.execute("SELECT taken_at FROM snapshots").fetchone()[0]
    assert stored == "2026-10-17T12:00:15.123456+00:00"


def test_snapshots_come_newest_first_and_can_be_filtered_by_volume(store):
    hour = timedelta(hours=1)
    first = store.save_snapshot(make_snapshot(taken_at=T0, volume="C:"), [])
    second = store.save_snapshot(make_snapshot(taken_at=T0 + 2 * hour, volume="D:"), [])
    third = store.save_snapshot(make_snapshot(taken_at=T0 + hour, volume="C:"), [])
    assert [s.id for s in store.snapshots()] == [second, third, first]
    assert [s.id for s in store.snapshots("C:")] == [third, first]
    assert store.latest_snapshot("C:").id == third
    assert store.latest_snapshot("Z:") is None
    assert store.get_snapshot(9999) is None
    assert store.nodes(9999) == []


def test_snapshots_of_the_same_moment_are_ordered_by_id(store):
    first = store.save_snapshot(make_snapshot(), [])
    second = store.save_snapshot(make_snapshot(), [])
    assert [s.id for s in store.snapshots()] == [second, first]


def test_each_snapshot_keeps_its_own_folders(store):
    first = store.save_snapshot(make_snapshot(), make_nodes())
    second = store.save_snapshot(make_snapshot(), make_nodes()[:1])
    assert len(store.nodes(first)) == 3
    assert len(store.nodes(second)) == 1


def failing_nodes(error):
    yield Node("C:\\a", "C:\\", 1, 1, 1)
    yield Node("C:\\b", "C:\\", 1, 1, 1)
    raise error


@pytest.mark.parametrize("error", [ValueError("boom"), KeyboardInterrupt()])
def test_a_failure_in_the_middle_leaves_nothing_and_the_store_keeps_working(
    store, error
):
    with pytest.raises(type(error)):
        store.save_snapshot(make_snapshot(), failing_nodes(error))
    assert count(store, "snapshots") == 0
    assert count(store, "nodes") == 0
    assert not store._connection.in_transaction
    snapshot_id = store.save_snapshot(make_snapshot(), make_nodes())
    assert store.nodes(snapshot_id) == make_nodes()


def test_the_database_itself_rejects_impossible_rows(store):
    snapshot_id = store.save_snapshot(make_snapshot(), [])
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(
            INSERT_NODE_SQL, (snapshot_id, "C:\\x", None, -1, 0, 0, None)
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute(INSERT_NODE_SQL, (999, "C:\\x", None, 1, 0, 0, None))


def test_deleting_a_snapshot_removes_its_folders(store):
    snapshot_id = store.save_snapshot(make_snapshot(), make_nodes())
    store._connection.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
    assert store.nodes(snapshot_id) == []
    assert count(store, "nodes") == 0


HOSTILE = [
    "'; DROP TABLE nodes;--",
    '"); DELETE FROM snapshots;--',
    "100%_done",
    "50% \\_ off",
    'O\'Brien "quoted" `tick`',
    "<img src=x onerror=alert(1)>",
    "<script>alert(1)</script>",
    "line\nbreak",
    "tab\there",
    "carriage\rreturn",
    "\x1b[31mred\x1b[0m",
    "ñandú 日本語 📁 \u202e rtl \u200b zero-width",
    "e\u0301 combining",
    "..\\..\\..\\Windows",
    "\\\\?\\C:\\very\\odd",
    "CON",
    "trailing dot. ",
    "x" * 5000,
    "\U0001f4a9" * 50,
    "nul\x00inside",
]


def test_hostile_names_are_stored_and_recovered_verbatim_and_the_tables_survive(store):
    nodes = [
        Node(
            path=f"C:\\{name}",
            parent_path=f"C:\\{name}\\up",
            size_bytes=index + 1,
            n_files=index,
            depth=1,
            rule_id=name,
        )
        for index, name in enumerate(HOSTILE)
    ]
    snapshot = make_snapshot(volume="D:'; DROP TABLE snapshots;--")
    snapshot_id = store.save_snapshot(snapshot, nodes)

    assert store.get_snapshot(snapshot_id) == dataclasses.replace(
        snapshot, id=snapshot_id
    )
    assert store.nodes(snapshot_id) == sorted(nodes, key=lambda node: node.path)
    assert [s.id for s in store.snapshots(snapshot.volume)] == [snapshot_id]

    tables = store._connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    assert {row[0] for row in tables} == {
        "snapshots",
        "nodes",
        "large_files",
        "alerts",
        "programs",
        "program_footprints",
    }
    assert count(store, "snapshots") == 1
    assert count(store, "nodes") == len(HOSTILE)
    assert store.save_snapshot(make_snapshot(), make_nodes()) == snapshot_id + 1


def test_names_with_invalid_utf16_are_stored_with_a_replacement_character(store):
    nodes = [
        Node("C:\\a\ud800b", "C:\\", 1, 1, 1),
        Node("C:\\a\udc00b", "C:\\", 2, 1, 1),
        Node("C:\\ok", "C:\\", 3, 1, 1),
    ]
    snapshot_id = store.save_snapshot(make_snapshot(), nodes)
    saved = store.nodes(snapshot_id)
    assert sorted(node.path for node in saved) == [
        "C:\\a\ufffdb",
        "C:\\a\ufffdb",
        "C:\\ok",
    ]
    assert sorted(node.size_bytes for node in saved) == [1, 2, 3]


def test_the_reader_sees_what_the_writer_saves_even_while_it_is_open(store, db_path):
    first = store.save_snapshot(make_snapshot(), make_nodes())
    with Store.open_readonly(db_path) as reader:
        assert [s.id for s in reader.snapshots()] == [first]
        second = store.save_snapshot(
            make_snapshot(taken_at=T0 + timedelta(hours=1)), []
        )
        assert [s.id for s in reader.snapshots()] == [second, first]
        assert reader.nodes(first) == make_nodes()


def test_the_reader_cannot_write(store, db_path):
    store.save_snapshot(make_snapshot(), make_nodes())
    with Store.open_readonly(db_path) as reader:
        with pytest.raises(sqlite3.OperationalError):
            reader.save_snapshot(make_snapshot(), [])
        with pytest.raises(sqlite3.OperationalError):
            reader._connection.execute("DELETE FROM nodes")
        assert len(reader.snapshots()) == 1
        assert len(reader.nodes(1)) == 3


def test_the_reader_does_not_create_a_missing_database(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        Store.open_readonly(missing)
    assert not missing.exists()


def test_the_reader_refuses_a_database_that_is_not_up_to_date(db_path):
    db_path.write_bytes(b"")
    with pytest.raises(StoreError):
        Store.open_readonly(db_path)


def test_odd_characters_in_the_path_work_for_both_kinds_of_connection(tmp_path):
    folder = tmp_path / "a b#c%d&e"
    folder.mkdir()
    path = folder / "x y#1.db"
    with Store.open(path) as writer:
        snapshot_id = writer.save_snapshot(make_snapshot(), [])
    with Store.open_readonly(path) as reader:
        assert [s.id for s in reader.snapshots()] == [snapshot_id]


def test_a_database_from_a_newer_program_is_refused_and_left_untouched(db_path):
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    before = db_path.read_bytes()
    with pytest.raises(StoreError):
        Store.open(db_path)
    assert db_path.read_bytes() == before
    assert raw_query(db_path, "PRAGMA user_version") == [(999,)]


def test_a_file_that_is_not_a_database_is_rejected_and_released(db_path):
    db_path.write_bytes(b"this is not a sqlite database" * 50)
    with pytest.raises(sqlite3.DatabaseError):
        Store.open(db_path)
    db_path.unlink()
    assert not db_path.exists()


def test_a_failed_migration_leaves_no_half_built_schema(db_path, monkeypatch):
    broken = ((1, ("CREATE TABLE half_built (a INTEGER)", "THIS IS NOT SQL")),)
    monkeypatch.setattr(store_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        Store.open(db_path)
    assert raw_query(db_path, "SELECT name FROM sqlite_master") == []
    assert raw_query(db_path, "PRAGMA user_version") == [(0,)]


def test_open_checks_the_folder_and_the_file_before_connecting(db_path):
    checked = []
    with Store.open(db_path, ensure_safe=checked.append):
        pass
    assert checked == [db_path.parent, db_path]


def test_open_refuses_what_ensure_safe_refuses_and_touches_nothing(tmp_path):
    db_path = tmp_path / "x.db"

    def ensure_safe(path):
        raise windows.UnsafeDataLocation(f"no: {path}")

    with pytest.raises(windows.UnsafeDataLocation):
        Store.open(db_path, ensure_safe=ensure_safe)
    assert not db_path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="usa un enlace real de Windows")
def test_open_refuses_a_real_database_that_is_a_hard_link(tmp_path):
    other = tmp_path / "other.db"
    other.write_bytes(b"")
    db_path = tmp_path / "x.db"
    os.link(other, db_path)
    with pytest.raises(windows.UnsafeDataLocation):
        Store.open(db_path)


def test_the_real_check_is_the_default_without_calling_it():
    parameters = inspect.signature(Store.open).parameters
    assert parameters["ensure_safe"].default is windows.ensure_safe_to_write


def test_a_version_1_database_is_migrated_keeping_its_photos(db_path):
    first_version = store_module.MIGRATIONS[0]
    connection = sqlite3.connect(db_path)
    for statement in first_version[1]:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO snapshots (taken_at, volume, total_bytes, used_bytes, free_bytes, "
        "scanned_bytes, system_files_bytes, unreadable_bytes, unreadable_count, "
        "duration_s, is_admin) VALUES ('2026-10-17T12:00:00.000000+00:00', 'C:', "
        "1000, 400, 600, 300, 40, 0, 2, 5.0, 0)"
    )
    connection.commit()
    connection.close()
    assert raw_query(db_path, "PRAGMA user_version") == [(1,)]
    with Store.open(db_path) as store:
        (old,) = store.snapshots()
        assert (old.id, old.scanned_bytes, old.duplicate_bytes) == (1, 300, 0)
        store.save_snapshot(make_snapshot(duplicate_bytes=50), [])
        assert store.snapshots("C:")[0].duplicate_bytes == 50
    assert raw_query(db_path, "PRAGMA user_version") == [(5,)]


def test_migrations_are_applied_one_version_at_a_time(db_path, monkeypatch):
    version_1 = (
        1,
        ("CREATE TABLE first_table (a INTEGER)", "PRAGMA user_version = 1"),
    )
    version_2 = (
        2,
        ("CREATE TABLE second_table (b INTEGER)", "PRAGMA user_version = 2"),
    )
    monkeypatch.setattr(store_module, "MIGRATIONS", (version_1,))
    Store.open(db_path).close()
    assert raw_query(db_path, "PRAGMA user_version") == [(1,)]
    monkeypatch.setattr(store_module, "MIGRATIONS", (version_1, version_2))
    Store.open(db_path).close()
    tables = raw_query(db_path, "SELECT name FROM sqlite_master WHERE type = 'table'")
    assert {row[0] for row in tables} == {"first_table", "second_table"}
    assert raw_query(db_path, "PRAGMA user_version") == [(2,)]


def test_close_can_be_repeated_and_the_context_manager_closes(db_path):
    with Store.open(db_path) as opened:
        opened.save_snapshot(make_snapshot(), [])
    with pytest.raises(sqlite3.ProgrammingError):
        opened.snapshots()
    opened.close()


def test_the_store_only_writes_its_own_files(tmp_path, db_path):
    with Store.open(db_path) as opened:
        opened.save_snapshot(make_snapshot(), make_nodes())
    names = {path.name for path in tmp_path.iterdir()}
    allowed = {"discoclaro.db", "discoclaro.db-wal", "discoclaro.db-shm"}
    assert names <= allowed
