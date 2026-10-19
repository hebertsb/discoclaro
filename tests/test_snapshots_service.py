"""Pruebas del servicio de fotos guardadas (T3.1)."""

from datetime import UTC, datetime

from discoclaro.adapters.store import Store
from discoclaro.domain.models import Node, Snapshot
from discoclaro.services.snapshots_service import run_snapshots

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def save(store, volume, **overrides):
    fields = {
        "taken_at": T0,
        "volume": volume,
        "total_bytes": 1000,
        "used_bytes": 400,
        "free_bytes": 600,
        "scanned_bytes": 350,
        "system_files_bytes": 0,
        "unreadable_bytes": 0,
        "unreadable_count": 0,
        "duration_s": 1.0,
        "is_admin": False,
    }
    fields.update(overrides)
    store.save_snapshot(Snapshot(**fields), [Node("C:\\", None, 10, 1, 0)])


def test_run_snapshots_is_empty_when_the_database_file_is_missing(tmp_path):
    assert run_snapshots(data_dir=tmp_path / "missing") == []


def test_run_snapshots_reads_the_database_in_the_data_folder(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:")
        save(opened, "D:")
    snapshots = run_snapshots(data_dir=data_dir)
    assert {snapshot.volume for snapshot in snapshots} == {"C:", "D:"}


def test_run_snapshots_filters_by_volume(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:")
        save(opened, "D:")
    snapshots = run_snapshots("D:", data_dir=data_dir)
    assert [snapshot.volume for snapshot in snapshots] == ["D:"]
