"""Pruebas de los modelos del núcleo."""

import dataclasses
from datetime import UTC, datetime

import pytest

from discoclaro.domain.models import (
    Node,
    ScanResult,
    Snapshot,
    SystemFile,
    Unreadable,
    Volume,
)


def make_volume(**changes):
    fields = {
        "name": "C:",
        "root": "C:\\",
        "total_bytes": 1000,
        "used_bytes": 400,
        "free_bytes": 600,
    }
    return Volume(**{**fields, **changes})


def test_volume_keeps_its_data():
    volume = make_volume()
    assert (volume.name, volume.root) == ("C:", "C:\\")
    assert (volume.total_bytes, volume.used_bytes, volume.free_bytes) == (
        1000,
        400,
        600,
    )


def test_volume_is_immutable():
    volume = make_volume()
    with pytest.raises(dataclasses.FrozenInstanceError):
        volume.free_bytes = 0


def test_volumes_with_the_same_data_are_equal():
    assert make_volume() == make_volume()
    assert make_volume() != make_volume(free_bytes=599)


@pytest.mark.parametrize("field", ["total_bytes", "used_bytes", "free_bytes"])
def test_negative_sizes_are_rejected(field):
    with pytest.raises(ValueError):
        make_volume(**{field: -1})


@pytest.mark.parametrize("field", ["name", "root"])
def test_name_and_root_cannot_be_empty(field):
    with pytest.raises(ValueError):
        make_volume(**{field: ""})


def test_zero_sizes_are_valid():
    volume = make_volume(total_bytes=0, used_bytes=0, free_bytes=0)
    assert volume.total_bytes == 0


def make_node(**changes):
    fields = {
        "path": "C:\\data",
        "parent_path": "C:\\",
        "size_bytes": 100,
        "n_files": 3,
        "depth": 1,
    }
    return Node(**{**fields, **changes})


def test_node_keeps_its_data_and_has_no_rule_yet():
    node = make_node()
    assert (node.path, node.parent_path) == ("C:\\data", "C:\\")
    assert (node.size_bytes, node.n_files, node.depth) == (100, 3, 1)
    assert node.rule_id is None


def test_node_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_node().size_bytes = 0


def test_the_root_node_has_no_parent():
    assert make_node(parent_path=None, depth=0).parent_path is None


@pytest.mark.parametrize("field", ["size_bytes", "n_files", "depth"])
def test_node_rejects_negative_numbers(field):
    with pytest.raises(ValueError):
        make_node(**{field: -1})


def test_node_needs_a_path():
    with pytest.raises(ValueError):
        make_node(path="")


def make_result(**changes):
    child = make_node()
    root = make_node(path="C:\\", parent_path=None, size_bytes=250, n_files=7, depth=0)
    fields = {
        "root": "C:\\",
        "nodes": (child, root),
        "unreadable_count": 2,
        "unreadable": (Unreadable("C:\\locked", "PermissionError"),),
        "skipped_links": 1,
    }
    return ScanResult(**{**fields, **changes})


def test_scan_result_totals_come_from_the_root_which_is_last():
    result = make_result()
    assert result.scanned_bytes == 250
    assert result.n_files == 7


def test_scan_result_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_result().skipped_links = 5


def test_scan_result_needs_at_least_the_root():
    with pytest.raises(ValueError):
        make_result(nodes=())


def test_scan_result_counts_must_be_consistent():
    with pytest.raises(ValueError):
        make_result(unreadable_count=0)
    with pytest.raises(ValueError):
        make_result(skipped_links=-1)


def test_unreadable_keeps_the_path_and_the_error_name():
    item = Unreadable("C:\\locked", "PermissionError")
    assert (item.path, item.reason) == ("C:\\locked", "PermissionError")


def test_system_file_keeps_its_data_and_is_immutable():
    item = SystemFile("D:\\pagefile.sys", 48)
    assert (item.path, item.size_bytes) == ("D:\\pagefile.sys", 48)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.size_bytes = 0


def test_system_file_rejects_an_empty_path_and_negative_sizes():
    with pytest.raises(ValueError):
        SystemFile("", 1)
    with pytest.raises(ValueError):
        SystemFile("D:\\pagefile.sys", -1)


def test_a_system_file_of_zero_bytes_is_valid():
    assert SystemFile("C:\\swapfile.sys", 0).size_bytes == 0


def make_snapshot(**changes):
    fields = {
        "taken_at": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
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


def test_snapshot_keeps_its_data_and_has_no_id_until_it_is_saved():
    snapshot = make_snapshot()
    assert snapshot.volume == "C:"
    assert (snapshot.total_bytes, snapshot.used_bytes, snapshot.free_bytes) == (
        1000,
        400,
        600,
    )
    assert snapshot.is_admin is False
    assert snapshot.id is None
    assert make_snapshot(id=7).id == 7


def test_snapshot_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_snapshot().volume = "D:"


def test_snapshot_needs_a_time_zone_and_a_volume():
    with pytest.raises(ValueError):
        make_snapshot(taken_at=datetime(2026, 10, 17, 12, 0))
    with pytest.raises(ValueError):
        make_snapshot(volume="")


@pytest.mark.parametrize(
    "field",
    [
        "total_bytes",
        "used_bytes",
        "free_bytes",
        "scanned_bytes",
        "system_files_bytes",
        "unreadable_bytes",
        "unreadable_count",
        "duration_s",
    ],
)
def test_snapshot_rejects_negative_numbers(field):
    with pytest.raises(ValueError):
        make_snapshot(**{field: -1})


def test_snapshot_arguments_must_be_named():
    with pytest.raises(TypeError):
        Snapshot(datetime(2026, 10, 17, tzinfo=UTC), "C:")
