"""Pruebas de los modelos del núcleo."""

import dataclasses

import pytest

from discoclaro.domain.models import Node, ScanResult, Unreadable, Volume


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
