"""Pruebas del servicio de resumen (T3.3): una tarjeta por disco."""

from datetime import UTC, datetime, timedelta

from discoclaro.adapters.store import Store
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.services.summary_service import build_summary, run_summary

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

C = Volume("C:", "C:\\", 1000, 600, 400)
D = Volume("D:", "D:\\", 2000, 500, 1500)


def save(store, volume, nodes=None, **overrides):
    fields = {
        "taken_at": T0,
        "volume": volume,
        "total_bytes": 1000,
        "used_bytes": 600,
        "free_bytes": 400,
        "scanned_bytes": 550,
        "system_files_bytes": 0,
        "unreadable_bytes": 0,
        "unreadable_count": 0,
        "duration_s": 1.0,
        "is_admin": False,
    }
    fields.update(overrides)
    nodes = nodes if nodes is not None else [Node(volume + "\\", None, 550, 5, 0)]
    store.save_snapshot(Snapshot(**fields), nodes)


def test_build_summary_with_no_snapshot_leaves_the_other_fields_none(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        summary = build_summary([C], store)
    assert len(summary.volumes) == 1
    row = summary.volumes[0]
    assert row.volume == C
    assert row.snapshot is None
    assert row.unexplained is None
    assert row.days_until_full is None
    assert row.top_growth is None


def test_build_summary_computes_unexplained_from_the_latest_snapshot(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        save(store, "C:", used_bytes=600, scanned_bytes=550, system_files_bytes=0)
        summary = build_summary([C], store)
    row = summary.volumes[0]
    assert row.unexplained is not None
    assert row.unexplained.size_bytes == 50


def test_build_summary_needs_at_least_three_snapshots_for_a_forecast(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        save(store, "C:", taken_at=T0)
        save(store, "C:", taken_at=T0 - timedelta(days=1))
        summary = build_summary([C], store)
    assert summary.volumes[0].days_until_full is None


def test_build_summary_forecasts_from_a_growing_trend(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        for day, used in ((0, 400), (1, 500), (2, 600)):
            save(
                store,
                "C:",
                taken_at=T0 + timedelta(days=day),
                used_bytes=used,
                scanned_bytes=used - 50,
            )
        summary = build_summary([C], store)
    row = summary.volumes[0]
    assert row.days_until_full is not None
    assert row.days_until_full > 0


def test_build_summary_finds_the_biggest_grower(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        save(
            store,
            "C:",
            nodes=[
                Node("C:\\", None, 550, 5, 0),
                Node("C:\\Games", "C:\\", 100, 2, 1),
            ],
            taken_at=T0 - timedelta(days=10),
        )
        save(
            store,
            "C:",
            nodes=[
                Node("C:\\", None, 900, 8, 0),
                Node("C:\\Games", "C:\\", 450, 5, 1),
            ],
            taken_at=T0,
            used_bytes=900,
            scanned_bytes=900,
        )
        summary = build_summary([C], store)
    row = summary.volumes[0]
    assert row.top_growth is not None
    assert row.top_growth.path == "C:\\Games"
    assert row.top_growth.grown_bytes == 350


def test_build_summary_is_empty_growth_without_enough_history(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        save(store, "C:")
        summary = build_summary([C], store)
    assert summary.volumes[0].top_growth is None


def test_build_summary_orders_volumes_by_total_bytes_descending(tmp_path):
    with Store.open(tmp_path / "test.db") as store:
        summary = build_summary([C, D], store)
    assert [row.volume.name for row in summary.volumes] == ["D:", "C:"]


def test_run_summary_without_a_database_still_lists_every_real_volume(tmp_path):
    summary = run_summary(tmp_path / "missing", list_volumes=lambda: [C, D])
    assert [row.volume.name for row in summary.volumes] == ["D:", "C:"]
    assert all(row.snapshot is None for row in summary.volumes)


def test_run_summary_reads_the_database_in_the_data_folder(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as store:
        save(store, "C:")
    summary = run_summary(data_dir, list_volumes=lambda: [C])
    assert summary.volumes[0].snapshot is not None
