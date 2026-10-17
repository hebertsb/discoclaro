"""Pruebas de los modelos del núcleo."""

import dataclasses

import pytest

from discoclaro.domain.models import Volume


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
