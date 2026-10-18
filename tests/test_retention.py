"""Pruebas de la política de retención (T2.9): puras, con fotos sintéticas."""

from datetime import UTC, datetime, timedelta

import pytest

from discoclaro.domain.models import Snapshot
from discoclaro.domain.retention import snapshots_to_delete

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def snapshot(id, days_ago, **changes):
    fields = {
        "id": id,
        "taken_at": NOW - timedelta(days=days_ago),
        "volume": "C:",
        "total_bytes": 100,
        "used_bytes": 50,
        "free_bytes": 50,
        "scanned_bytes": 10,
        "system_files_bytes": 0,
        "unreadable_bytes": 0,
        "unreadable_count": 0,
        "duration_s": 1.0,
        "is_admin": False,
    }
    return Snapshot(**{**fields, **changes})


def test_everything_within_retention_days_is_kept():
    snapshots = [snapshot(1, 29), snapshot(2, 15), snapshot(3, 0)]
    assert snapshots_to_delete(snapshots, 30, NOW) == []


def test_a_snapshot_right_at_the_cutoff_is_kept():
    assert snapshots_to_delete([snapshot(1, 30)], 30, NOW) == []


def test_a_single_old_snapshot_is_kept_as_its_own_week():
    assert snapshots_to_delete([snapshot(1, 40)], 30, NOW) == []


def test_only_the_most_recent_of_each_old_week_is_kept():
    # Dos fotos viejas de la misma semana ISO: solo se guarda la más nueva.
    same_week = [snapshot(1, 60), snapshot(2, 58)]
    assert snapshots_to_delete(same_week, 30, NOW) == [1]


def test_old_snapshots_of_different_weeks_are_each_kept():
    different_weeks = [snapshot(1, 60), snapshot(2, 53)]
    assert snapshots_to_delete(different_weeks, 30, NOW) == []


def test_recent_snapshots_are_never_thinned_even_several_in_the_same_week():
    recent_same_week = [snapshot(1, 2), snapshot(2, 1), snapshot(3, 0)]
    assert snapshots_to_delete(recent_same_week, 30, NOW) == []


def test_a_mix_of_recent_and_old_only_thins_the_old_ones():
    snapshots = [
        snapshot(1, 90),  # vieja, sola en su semana: se guarda
        snapshot(2, 61),  # vieja, misma semana que la 3
        snapshot(3, 60),  # vieja, la más nueva de esa semana: se guarda
        snapshot(4, 5),  # reciente: se guarda
    ]
    assert snapshots_to_delete(snapshots, 30, NOW) == [2]


def test_an_empty_list_has_nothing_to_delete():
    assert snapshots_to_delete([], 30, NOW) == []


def test_negative_retention_days_is_rejected():
    with pytest.raises(ValueError):
        snapshots_to_delete([snapshot(1, 0)], -1, NOW)


def test_zero_retention_days_still_keeps_one_per_week():
    # Con retention_days=0, "reciente" es solo el instante exacto de `now`: estas dos
    # quedan viejas y, al ser de la misma semana, se adelgazan igual que cualquier otra.
    snapshots = [snapshot(1, 2), snapshot(2, 1)]
    assert snapshots_to_delete(snapshots, 0, NOW) == [1]
