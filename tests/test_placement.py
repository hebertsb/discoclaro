"""Pruebas de datos mal ubicados (T2C.5): puras."""

import pytest

from discoclaro.domain.placement import StorageLocation, find_misplaced


def test_a_storage_location_needs_a_name():
    with pytest.raises(ValueError):
        StorageLocation(name="", drive="C:", size_bytes=1)


def test_a_storage_location_needs_a_drive():
    with pytest.raises(ValueError):
        StorageLocation(name="x", drive="", size_bytes=1)


def test_a_storage_location_cannot_have_a_negative_size():
    with pytest.raises(ValueError):
        StorageLocation(name="x", drive="C:", size_bytes=-1)


def test_the_real_case_a_game_on_the_system_drive_with_another_library_elsewhere():
    locations = [
        StorageLocation(name="BigGame", drive="C:", size_bytes=74 * 1024**3),
        StorageLocation(name="SmallGame", drive="D:", size_bytes=1 * 1024**3),
    ]
    found = find_misplaced(locations, "C:")
    assert [m.name for m in found] == ["BigGame"]
    assert found[0].other_drives == ("D:",)
    assert found[0].size_bytes == 74 * 1024**3


def test_nothing_is_misplaced_when_everything_is_on_one_drive():
    locations = [StorageLocation(name="x", drive="C:", size_bytes=1)]
    assert find_misplaced(locations, "C:") == []


def test_nothing_on_the_system_drive_means_nothing_misplaced():
    locations = [StorageLocation(name="x", drive="D:", size_bytes=1)]
    assert find_misplaced(locations, "C:") == []


def test_comparison_is_case_insensitive_and_ignores_a_trailing_slash():
    locations = [
        StorageLocation(name="x", drive="c:\\", size_bytes=1),
        StorageLocation(name="y", drive="d:", size_bytes=1),
    ]
    found = find_misplaced(locations, "C:")
    assert [m.name for m in found] == ["x"]


def test_several_other_drives_are_all_listed_sorted():
    locations = [
        StorageLocation(name="x", drive="C:", size_bytes=1),
        StorageLocation(name="y", drive="E:", size_bytes=1),
        StorageLocation(name="z", drive="D:", size_bytes=1),
    ]
    found = find_misplaced(locations, "C:")
    assert found[0].other_drives == ("D:", "E:")


def test_several_items_on_the_system_drive_are_all_reported():
    locations = [
        StorageLocation(name="x", drive="C:", size_bytes=1),
        StorageLocation(name="y", drive="C:", size_bytes=2),
        StorageLocation(name="z", drive="D:", size_bytes=3),
    ]
    found = find_misplaced(locations, "C:")
    assert [m.name for m in found] == ["x", "y"]


def test_find_misplaced_of_no_locations_is_empty():
    assert find_misplaced([], "C:") == []
