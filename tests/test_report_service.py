"""Pruebas del servicio de informe: carpetas más grandes de una foto guardada.

Es de solo lectura: lee la base con una foto ya guardada (por `scan`), nunca la
vuelve a crear ni recorre el disco de verdad.
"""

import dataclasses
import inspect
import sqlite3
from datetime import UTC, datetime

import pytest

from discoclaro.adapters.store import Store
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.domain.unexplained import Unexplained
from discoclaro.platform import windows
from discoclaro.services import report_service
from discoclaro.services.report_service import ReportError, build_report

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
VOLUMES = [Volume("C:", "C:\\", 1000, 400, 600), Volume("D:", "D:\\", 2000, 500, 1500)]


def fake_volumes():
    return list(VOLUMES)


@pytest.fixture
def store(tmp_path):
    with Store.open(tmp_path / "test.db") as opened:
        yield opened


def save(store, volume, nodes, **overrides):
    """Guarda una foto sencilla de `volume` con `nodes` y la devuelve con su id."""
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
    snapshot = Snapshot(**fields)
    snapshot_id = store.save_snapshot(snapshot, nodes)
    return dataclasses.replace(snapshot, id=snapshot_id)


def test_with_no_target_uses_the_latest_snapshot_of_any_volume(store):
    save(store, "C:", [Node("C:\\", None, 350, 7, 0)])
    newer = save(
        store,
        "D:\\data",
        [Node("D:\\data", None, 10, 1, 0)],
        taken_at=T0.replace(hour=13),
    )
    report = build_report(None, store, volumes=fake_volumes)
    assert report.snapshot.id == newer.id


def test_with_a_target_uses_its_own_latest_snapshot_not_the_global_latest(store):
    older = save(store, "C:", [Node("C:\\", None, 100, 1, 0)])
    save(
        store,
        "D:\\data",
        [Node("D:\\data", None, 999, 1, 0)],
        taken_at=T0.replace(hour=13),
    )
    report = build_report("C:", store, volumes=fake_volumes)
    assert report.snapshot.id == older.id


def test_a_bare_drive_letter_resolves_case_insensitively(store):
    saved = save(store, "C:", [Node("C:\\", None, 100, 1, 0)])
    report = build_report("c:", store, volumes=fake_volumes)
    assert report.snapshot.id == saved.id


def test_a_folder_target_resolves_by_its_normalized_path(store):
    saved = save(store, "D:\\data\\sub", [Node("D:\\data\\sub", None, 10, 1, 0)])
    report = build_report("d:/data/sub/", store, volumes=fake_volumes)
    assert report.snapshot.id == saved.id


def test_folders_are_sorted_by_size_descending_and_limited_to_top_n(store):
    nodes = [
        Node("C:\\a", "C:\\", 10, 1, 1),
        Node("C:\\b", "C:\\", 30, 1, 1),
        Node("C:\\c", "C:\\", 20, 1, 1),
        Node("C:\\", None, 60, 3, 0),
    ]
    save(store, "C:", nodes)
    report = build_report("C:", store, volumes=fake_volumes, top_n=2)
    assert [node.path for node in report.top_folders] == ["C:\\", "C:\\b"]
    assert report.total_folders == 4


def test_top_n_zero_or_negative_shows_no_folders_but_keeps_the_total(store):
    nodes = [Node("C:\\a", "C:\\", 10, 1, 1), Node("C:\\", None, 10, 1, 0)]
    save(store, "C:", nodes)
    report = build_report("C:", store, volumes=fake_volumes, top_n=-5)
    assert report.top_folders == ()
    assert report.total_folders == 2


def test_a_volume_scan_reports_its_unexplained_space(store):
    save(
        store,
        "C:",
        [Node("C:\\", None, 300, 1, 0)],
        used_bytes=400,
        scanned_bytes=300,
    )
    report = build_report("C:", store, volumes=fake_volumes)
    assert report.unexplained == Unexplained(
        size_bytes=100, percent_of_used=25.0, exceeds_threshold=True
    )


def test_a_folder_scan_has_no_unexplained_space(store):
    save(store, "D:\\data", [Node("D:\\data", None, 10, 1, 0)])
    report = build_report("D:\\data", store, volumes=fake_volumes)
    assert report.unexplained is None


def test_no_target_never_asks_for_the_volumes(store):
    save(store, "C:", [Node("C:\\", None, 10, 1, 0)])

    def forbidden():
        raise AssertionError("no debería consultar los volúmenes")

    build_report(None, store, volumes=forbidden)


def test_raises_when_there_are_no_snapshots_at_all(store):
    with pytest.raises(ReportError) as error:
        build_report(None, store, volumes=fake_volumes)
    assert error.value.kind == "report_no_snapshots"


def test_raises_when_the_target_was_never_scanned(store):
    save(store, "C:", [Node("C:\\", None, 10, 1, 0)])
    with pytest.raises(ReportError) as error:
        build_report("D:", store, volumes=fake_volumes)
    assert (error.value.kind, error.value.target) == (
        "report_no_snapshot_for_target",
        "D:",
    )


def test_raises_when_the_target_is_not_on_a_fixed_disk(store):
    with pytest.raises(ReportError) as error:
        build_report("Z:\\x", store, volumes=fake_volumes)
    assert error.value.kind == "report_not_fixed_disk"


def test_report_error_carries_the_message_key_and_the_target():
    error = ReportError("report_no_snapshot_for_target", "C:")
    assert (error.kind, error.target) == ("report_no_snapshot_for_target", "C:")


def test_the_real_volumes_adapter_is_the_default_without_calling_it():
    parameters = inspect.signature(build_report).parameters
    assert parameters["volumes"].default is list_volumes


def test_run_report_says_there_are_no_snapshots_when_the_database_file_is_missing(
    tmp_path,
):
    with pytest.raises(ReportError) as error:
        report_service.run_report(None, tmp_path / "missing")
    assert error.value.kind == "report_no_snapshots"


def test_run_report_reads_the_database_in_the_data_folder(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)])
    report = report_service.run_report("C:", data_dir)
    assert report.snapshot.volume == "C:"


def test_run_report_uses_the_default_data_folder_when_none_is_given(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(windows, "default_data_dir", lambda: tmp_path / "default")
    (tmp_path / "default").mkdir()
    with Store.open(tmp_path / "default" / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)])
    report = report_service.run_report(None)
    assert report.snapshot.volume == "C:"


def test_run_report_closes_the_database_after_use(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)])
    seen = {}

    def fake_build_report(target, store, **kwargs):
        seen["store"] = store
        store.snapshots()
        return "ok"

    monkeypatch.setattr(report_service, "build_report", fake_build_report)
    assert report_service.run_report("C:", data_dir) == "ok"
    with pytest.raises(sqlite3.ProgrammingError):
        seen["store"].snapshots()
