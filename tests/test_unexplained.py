"""Pruebas del espacio no explicado, con números sintéticos."""

from datetime import UTC, datetime

import pytest

from discoclaro.domain.models import Snapshot
from discoclaro.domain.unexplained import (
    DEFAULT_THRESHOLD_PERCENT,
    Unexplained,
    compute_unexplained,
    unexplained_of,
)

GIB = 1024**3


def make_snapshot(**changes):
    fields = {
        "taken_at": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        "volume": "C:",
        "total_bytes": 2000,
        "used_bytes": 1000,
        "free_bytes": 1000,
        "scanned_bytes": 700,
        "system_files_bytes": 100,
        "unreadable_bytes": 0,
        "unreadable_count": 3,
        "duration_s": 5.0,
        "is_admin": False,
    }
    return Snapshot(**{**fields, **changes})


def test_what_is_scanned_and_the_system_files_are_subtracted_from_what_is_used():
    assert compute_unexplained(1000, 700, 100) == Unexplained(
        size_bytes=200, percent_of_used=20.0, exceeds_threshold=True
    )


def test_the_default_threshold_is_five_percent_of_what_is_used():
    assert DEFAULT_THRESHOLD_PERCENT == 5.0


@pytest.mark.parametrize(
    ("unexplained", "exceeds"), [(49, False), (50, False), (51, True)]
)
def test_exactly_at_the_threshold_does_not_warn(unexplained, exceeds):
    result = compute_unexplained(1000, 1000 - unexplained, 0)
    assert result.exceeds_threshold is exceeds


def test_nothing_unexplained_does_not_warn():
    assert compute_unexplained(1000, 900, 100) == Unexplained(0, 0.0, False)


def test_more_scanned_than_used_is_negative_and_does_not_warn():
    result = compute_unexplained(1000, 990, 50)
    assert result.size_bytes == -40
    assert result.percent_of_used == -4.0
    assert result.exceeds_threshold is False


def test_a_volume_with_nothing_used_has_nothing_unexplained():
    assert compute_unexplained(0, 0, 0) == Unexplained(0, 0.0, False)


def test_the_threshold_can_be_changed():
    assert compute_unexplained(1000, 940, 0).exceeds_threshold is True
    assert compute_unexplained(1000, 940, 0, 10).exceeds_threshold is False
    assert compute_unexplained(1000, 999, 0, 0).exceeds_threshold is True
    assert compute_unexplained(1000, 1000, 0, 0).exceeds_threshold is False


def test_the_figures_measured_on_a_real_disk_would_warn():
    # Ejemplo con las cifras redondeadas de una medición real, sin administrador.
    result = compute_unexplained(int(291.62 * GIB), int(267.14 * GIB), int(6.54 * GIB))
    assert round(result.size_bytes / GIB, 1) == 17.9
    assert round(result.percent_of_used, 1) == 6.2
    assert result.exceeds_threshold is True


@pytest.mark.parametrize("arguments", [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_negative_sizes_are_rejected(arguments):
    with pytest.raises(ValueError):
        compute_unexplained(*arguments)


def test_a_negative_threshold_is_rejected():
    with pytest.raises(ValueError):
        compute_unexplained(1000, 900, 0, -1)


def test_a_volume_snapshot_gives_its_unexplained_space():
    result = unexplained_of(make_snapshot())
    assert result == Unexplained(200, 20.0, True)


def test_the_threshold_reaches_the_snapshot_calculation():
    assert unexplained_of(make_snapshot(), 25.0).exceeds_threshold is False


def test_a_folder_snapshot_has_no_unexplained_space():
    assert unexplained_of(make_snapshot(volume="D:\\data\\sub")) is None
