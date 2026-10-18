"""Pruebas del servicio de crecimiento: siempre sobre fotos ya guardadas en `tmp_path`.

Es de solo lectura: nunca vuelve a escanear ni crea una foto nueva.
"""

import dataclasses
import inspect
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.adapters.store import Store
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.growth import CategoryGrowth
from discoclaro.domain.models import LargeFile, Node, Snapshot, Volume
from discoclaro.domain.rules import Rule
from discoclaro.platform import windows
from discoclaro.services import growth_service
from discoclaro.services.growth_service import GrowthError, build_growth

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
VOLUMES = [Volume("C:", "C:\\", 1000, 400, 600), Volume("D:", "D:\\", 2000, 500, 1500)]


def fake_volumes():
    return list(VOLUMES)


@pytest.fixture
def store(tmp_path):
    with Store.open(tmp_path / "test.db") as opened:
        yield opened


def save(store, volume, nodes, large_files=(), **overrides):
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
    snapshot_id = store.save_snapshot(snapshot, nodes, large_files)
    return dataclasses.replace(snapshot, id=snapshot_id)


def rule(id, *paths, category):
    return Rule(
        id=id, name=id, paths=paths, category=category, risk="safe", explanation="x"
    )


def test_with_no_target_compares_the_two_latest_snapshots_of_that_volume(store):
    save(store, "C:", [Node("C:\\", None, 100, 1, 0)], taken_at=T0)
    save(store, "D:\\data", [Node("D:\\data", None, 1, 1, 0)], taken_at=T0)
    after = save(
        store,
        "C:",
        [Node("C:\\", None, 200, 1, 0)],
        taken_at=T0 + timedelta(days=8),
    )
    growth = build_growth(None, store, "7d", volumes=fake_volumes)
    assert growth.after.id == after.id
    assert growth.before.volume == "C:"


def test_with_a_target_compares_that_targets_own_history(store):
    save(store, "C:", [Node("C:\\", None, 999, 1, 0)], taken_at=T0 + timedelta(days=8))
    save(store, "D:", [Node("D:\\", None, 10, 1, 0)], taken_at=T0)
    after = save(
        store, "D:", [Node("D:\\", None, 20, 1, 0)], taken_at=T0 + timedelta(days=8)
    )
    growth = build_growth("D:", store, "7d", volumes=fake_volumes)
    assert growth.after.id == after.id
    assert growth.before.volume == "D:"


def test_picks_the_most_recent_snapshot_that_is_old_enough(store):
    too_recent = save(
        store, "C:", [Node("C:\\", None, 50, 1, 0)], taken_at=T0 + timedelta(days=3)
    )
    old_enough = save(store, "C:", [Node("C:\\", None, 10, 1, 0)], taken_at=T0)
    after = save(
        store, "C:", [Node("C:\\", None, 90, 1, 0)], taken_at=T0 + timedelta(days=8)
    )
    growth = build_growth("C:", store, "7d", volumes=fake_volumes)
    assert growth.after.id == after.id
    assert growth.before.id == old_enough.id
    assert growth.before.id != too_recent.id


def test_raises_when_nothing_is_old_enough(store):
    save(store, "C:", [Node("C:\\", None, 10, 1, 0)], taken_at=T0 + timedelta(days=6))
    save(store, "C:", [Node("C:\\", None, 90, 1, 0)], taken_at=T0 + timedelta(days=8))
    with pytest.raises(GrowthError) as error:
        build_growth("C:", store, "7d", volumes=fake_volumes)
    assert error.value.kind == "growth_not_enough_history"


def test_raises_when_there_is_only_one_snapshot(store):
    save(store, "C:", [Node("C:\\", None, 10, 1, 0)])
    with pytest.raises(GrowthError) as error:
        build_growth("C:", store, "7d", volumes=fake_volumes)
    assert error.value.kind == "growth_not_enough_history"


@pytest.mark.parametrize("since", ["", "7", "d7", "-1d", "seven days"])
def test_an_invalid_since_is_reported_before_touching_the_store(store, since):
    with pytest.raises(GrowthError) as error:
        build_growth("C:", store, since, volumes=fake_volumes)
    assert error.value.kind == "growth_invalid_since"


def test_raises_when_there_are_no_snapshots_at_all(store):
    with pytest.raises(GrowthError) as error:
        build_growth(None, store, "7d", volumes=fake_volumes)
    assert error.value.kind == "growth_no_snapshots"


def test_raises_when_the_target_was_never_scanned(store):
    save(store, "C:", [Node("C:\\", None, 10, 1, 0)])
    with pytest.raises(GrowthError) as error:
        build_growth("D:", store, "7d", volumes=fake_volumes)
    assert (error.value.kind, error.value.target) == (
        "growth_no_snapshot_for_target",
        "D:",
    )


def test_raises_when_the_target_is_not_on_a_fixed_disk(store):
    with pytest.raises(GrowthError) as error:
        build_growth("Z:\\x", store, "7d", volumes=fake_volumes)
    assert error.value.kind == "growth_not_fixed_disk"


def test_folder_growth_excludes_the_root(store):
    save(
        store,
        "C:",
        [Node("C:\\a", "C:\\", 10, 1, 1), Node("C:\\", None, 10, 1, 0)],
        taken_at=T0,
    )
    save(
        store,
        "C:",
        [Node("C:\\a", "C:\\", 50, 1, 1), Node("C:\\", None, 50, 1, 0)],
        taken_at=T0 + timedelta(days=8),
    )
    growth = build_growth("C:", store, "7d", volumes=fake_volumes)
    assert [g.path for g in growth.folders] == ["C:\\a"]


def test_folder_growth_respects_top_n(store):
    save(
        store,
        "C:",
        [Node(f"C:\\{letter}", "C:\\", 0, 1, 1) for letter in "abc"],
        taken_at=T0,
    )
    save(
        store,
        "C:",
        [
            Node("C:\\a", "C:\\", 30, 1, 1),
            Node("C:\\b", "C:\\", 20, 1, 1),
            Node("C:\\c", "C:\\", 10, 1, 1),
        ],
        taken_at=T0 + timedelta(days=8),
    )
    growth = build_growth("C:", store, "7d", volumes=fake_volumes, top_n=2)
    assert [g.path for g in growth.folders] == ["C:\\a", "C:\\b"]


def test_categories_are_none_without_by_category(store):
    save(store, "C:", [Node("C:\\a", "C:\\", 10, 1, 1)], taken_at=T0)
    save(
        store, "C:", [Node("C:\\a", "C:\\", 50, 1, 1)], taken_at=T0 + timedelta(days=8)
    )
    growth = build_growth("C:", store, "7d", volumes=fake_volumes)
    assert growth.categories is None


def test_categories_are_filled_in_when_asked_for(store):
    save(store, "C:", [Node("C:\\a", "C:\\", 10, 1, 1)], taken_at=T0)
    save(
        store, "C:", [Node("C:\\a", "C:\\", 50, 1, 1)], taken_at=T0 + timedelta(days=8)
    )
    rules = [rule("a", "C:\\a", category="cache")]
    growth = build_growth(
        "C:",
        store,
        "7d",
        volumes=fake_volumes,
        by_category=True,
        rules=lambda: rules,
        environment=dict,
    )
    assert growth.categories == (
        CategoryGrowth(category="cache", before_bytes=10, after_bytes=50),
    )


def test_rules_and_environment_are_not_called_without_by_category(store):
    save(store, "C:", [Node("C:\\a", "C:\\", 10, 1, 1)], taken_at=T0)
    save(
        store, "C:", [Node("C:\\a", "C:\\", 50, 1, 1)], taken_at=T0 + timedelta(days=8)
    )

    def forbidden():
        raise AssertionError("no debería armar el catálogo ni el entorno")

    build_growth(
        "C:", store, "7d", volumes=fake_volumes, rules=forbidden, environment=forbidden
    )


def test_new_large_files_compares_both_snapshots(store):
    save(
        store,
        "C:",
        [Node("C:\\", None, 10, 1, 0)],
        [LargeFile("C:\\old.bin", 60 * 1024 * 1024)],
        taken_at=T0,
    )
    save(
        store,
        "C:",
        [Node("C:\\", None, 10, 1, 0)],
        [
            LargeFile("C:\\old.bin", 60 * 1024 * 1024),
            LargeFile("C:\\new.bin", 90 * 1024 * 1024),
        ],
        taken_at=T0 + timedelta(days=8),
    )
    growth = build_growth("C:", store, "7d", volumes=fake_volumes)
    assert growth.new_large_files == (LargeFile("C:\\new.bin", 90 * 1024 * 1024),)


def test_growth_error_carries_the_message_key_and_the_target():
    error = GrowthError("growth_no_snapshot_for_target", "C:")
    assert (error.kind, error.target) == ("growth_no_snapshot_for_target", "C:")


def test_the_real_adapters_are_the_defaults_without_calling_them():
    parameters = inspect.signature(build_growth).parameters
    assert parameters["volumes"].default is list_volumes
    assert parameters["rules"].default is load_builtin_catalog
    assert parameters["environment"].default is windows.environment_variables


def test_run_growth_says_there_are_no_snapshots_when_the_database_file_is_missing(
    tmp_path,
):
    with pytest.raises(GrowthError) as error:
        growth_service.run_growth(None, data_dir=tmp_path / "missing")
    assert error.value.kind == "growth_no_snapshots"


def test_run_growth_reads_the_database_in_the_data_folder(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)], taken_at=T0)
        save(
            opened,
            "C:",
            [Node("C:\\", None, 20, 1, 0)],
            taken_at=T0 + timedelta(days=8),
        )
    growth = growth_service.run_growth("C:", "7d", data_dir)
    assert (growth.before.scanned_bytes, growth.after.scanned_bytes) == (350, 350)
    assert growth.after.volume == "C:"


def test_run_growth_uses_the_default_data_folder_when_none_is_given(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(windows, "default_data_dir", lambda: tmp_path / "default")
    (tmp_path / "default").mkdir()
    with Store.open(tmp_path / "default" / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)], taken_at=T0)
        save(
            opened,
            "C:",
            [Node("C:\\", None, 20, 1, 0)],
            taken_at=T0 + timedelta(days=8),
        )
    growth = growth_service.run_growth(None)
    assert growth.after.volume == "C:"


def test_run_growth_closes_the_database_after_use(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", [Node("C:\\", None, 10, 1, 0)], taken_at=T0)
        save(
            opened,
            "C:",
            [Node("C:\\", None, 20, 1, 0)],
            taken_at=T0 + timedelta(days=8),
        )
    seen = {}

    def fake_build_growth(target, store, since, **kwargs):
        seen["store"] = store
        store.snapshots()
        return "ok"

    monkeypatch.setattr(growth_service, "build_growth", fake_build_growth)
    assert growth_service.run_growth("C:", "7d", data_dir) == "ok"
    with pytest.raises(sqlite3.ProgrammingError):
        seen["store"].snapshots()
