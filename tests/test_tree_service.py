"""Pruebas del servicio de árbol (T3.1): un nivel de una foto ya guardada."""

import dataclasses
from datetime import UTC, datetime

import pytest

from discoclaro.adapters.store import Store
from discoclaro.domain.models import Node, Snapshot
from discoclaro.services import tree_service
from discoclaro.services.tree_service import TreeError, build_tree, run_tree

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with Store.open(tmp_path / "test.db") as opened:
        yield opened


def save(store, volume, nodes, **overrides):
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


NODES = [
    Node("C:\\", None, 1000, 10, 0),
    Node("C:\\Games", "C:\\", 600, 5, 1),
    Node("C:\\Games\\Saves", "C:\\Games", 100, 2, 2),
]


def test_build_tree_without_a_path_returns_the_root_as_children(store):
    save(store, "C:", NODES)
    view = build_tree(None, None, store)
    assert view.node is None
    assert [child.path for child in view.children] == ["C:\\"]


def test_build_tree_with_a_path_returns_that_node_and_its_children(store):
    save(store, "C:", NODES)
    view = build_tree("C:\\Games", None, store)
    assert view.node.path == "C:\\Games"
    assert [child.path for child in view.children] == ["C:\\Games\\Saves"]


def test_build_tree_uses_the_given_snapshot_id(store):
    save(store, "C:", NODES, taken_at=T0)
    older = save(store, "C:", NODES, taken_at=T0.replace(hour=10))
    view = build_tree(None, older.id, store)
    assert view.snapshot.id == older.id


def test_build_tree_rejects_an_unknown_snapshot_id(store):
    save(store, "C:", NODES)
    with pytest.raises(TreeError) as error:
        build_tree(None, 999, store)
    assert error.value.kind == "tree_no_such_snapshot"


def test_build_tree_rejects_an_unknown_path(store):
    save(store, "C:", NODES)
    with pytest.raises(TreeError) as error:
        build_tree("C:\\Nope", None, store)
    assert error.value.kind == "tree_no_such_path"


def test_build_tree_with_no_snapshots_at_all_fails(store):
    with pytest.raises(TreeError) as error:
        build_tree(None, None, store)
    assert error.value.kind == "tree_no_snapshots"


def test_run_tree_says_there_are_no_snapshots_when_the_database_file_is_missing(
    tmp_path,
):
    with pytest.raises(TreeError) as error:
        run_tree(None, data_dir=tmp_path / "missing")
    assert error.value.kind == "tree_no_snapshots"


def test_run_tree_reads_the_database_in_the_data_folder(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", NODES)
    view = tree_service.run_tree("C:\\Games", data_dir=data_dir)
    assert view.node.path == "C:\\Games"
