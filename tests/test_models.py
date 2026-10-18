"""Pruebas de los modelos del núcleo."""

import dataclasses
from datetime import UTC, datetime

import pytest

from discoclaro.domain.models import (
    LARGE_FILE_MIN_BYTES,
    DirectoryEntry,
    LargeFile,
    Node,
    Program,
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


def test_large_file_keeps_its_data_and_is_immutable():
    item = LargeFile("D:\\models\\model.gguf", LARGE_FILE_MIN_BYTES)
    assert (item.path, item.size_bytes) == (
        "D:\\models\\model.gguf",
        LARGE_FILE_MIN_BYTES,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.size_bytes = 0


def test_large_file_rejects_an_empty_path_and_negative_sizes():
    with pytest.raises(ValueError):
        LargeFile("", 1)
    with pytest.raises(ValueError):
        LargeFile("D:\\x.bin", -1)


def test_a_large_file_of_zero_bytes_is_valid():
    assert LargeFile("D:\\x.bin", 0).size_bytes == 0


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


@pytest.mark.parametrize(
    ("volume", "is_volume_scan"),
    [
        ("C:", True),
        ("D:", True),
        ("C:\\Users", False),
        ("D:\\data\\sub", False),
        ("D:/data", False),
    ],
)
def test_a_volume_name_marks_a_full_scan_and_a_path_a_partial_one(
    volume, is_volume_scan
):
    assert make_snapshot(volume=volume).is_volume_scan is is_volume_scan


def test_snapshot_duplicate_bytes_default_to_zero_and_keep_their_value():
    assert make_snapshot().duplicate_bytes == 0
    assert make_snapshot(duplicate_bytes=40).duplicate_bytes == 40


def test_snapshot_duplicates_cannot_be_negative_or_exceed_what_was_scanned():
    with pytest.raises(ValueError):
        make_snapshot(duplicate_bytes=-1)
    with pytest.raises(ValueError):
        make_snapshot(scanned_bytes=350, duplicate_bytes=351)
    assert make_snapshot(scanned_bytes=350, duplicate_bytes=350).duplicate_bytes == 350


def test_scan_result_duplicates_default_to_zero_and_keep_their_value():
    assert make_result().duplicate_bytes == 0
    assert make_result(duplicate_bytes=250).duplicate_bytes == 250


@pytest.mark.parametrize("duplicate_bytes", [-1, 251])
def test_scan_result_duplicates_must_fit_inside_the_root_size(duplicate_bytes):
    with pytest.raises(ValueError):
        make_result(duplicate_bytes=duplicate_bytes)


def test_directory_entry_keeps_its_data_and_is_immutable():
    entry = DirectoryEntry("a.txt", "C:\\a.txt", "file", 4096, b"\x01" * 16)
    assert (entry.name, entry.path, entry.kind) == ("a.txt", "C:\\a.txt", "file")
    assert entry.size_bytes == 4096
    assert entry.file_id == b"\x01" * 16
    assert entry.error is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.size_bytes = 0


def test_a_directory_entry_needs_only_a_name_a_path_and_a_kind():
    entry = DirectoryEntry("Windows", "C:\\Windows", "folder")
    assert (entry.size_bytes, entry.file_id, entry.error) == (0, None, None)


@pytest.mark.parametrize("kind", ["file", "folder", "link", "other", "unreadable"])
def test_every_known_kind_is_accepted(kind):
    assert DirectoryEntry("x", "C:\\x", kind).kind == kind


def test_an_unknown_kind_or_a_negative_size_is_rejected():
    with pytest.raises(ValueError):
        DirectoryEntry("x", "C:\\x", "symlink")
    with pytest.raises(ValueError):
        DirectoryEntry("x", "C:\\x", "file", -1)


def test_snapshot_arguments_must_be_named():
    with pytest.raises(TypeError):
        Snapshot(datetime(2026, 10, 17, tzinfo=UTC), "C:")


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


def test_a_program_needs_a_name():
    with pytest.raises(ValueError):
        make_program(name="")


def test_a_program_can_have_no_estimated_size():
    assert make_program(estimated_size_bytes=None).estimated_size_bytes is None


def test_a_program_rejects_a_negative_estimated_size():
    with pytest.raises(ValueError):
        make_program(estimated_size_bytes=-1)


def test_a_program_can_have_every_optional_field_missing():
    program = Program(
        name="Solo nombre",
        version=None,
        publisher=None,
        install_location=None,
        install_date=None,
        estimated_size_bytes=None,
        uninstall_string=None,
    )
    assert program.name == "Solo nombre"
