"""Pruebas de la estimación de días hasta llenarse (T2.10): series sintéticas."""

from datetime import UTC, datetime, timedelta

import pytest

from discoclaro.domain.forecast import (
    MIN_SNAPSHOTS,
    Trend,
    days_until_full,
    trend_of,
)
from discoclaro.domain.models import Snapshot

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GIB = 1024**3


def snapshot(days_from_t0, used_bytes):
    return Snapshot(
        taken_at=T0 + timedelta(days=days_from_t0),
        volume="C:",
        total_bytes=1000 * GIB,
        used_bytes=used_bytes,
        free_bytes=1000 * GIB - used_bytes,
        scanned_bytes=used_bytes,
        system_files_bytes=0,
        unreadable_bytes=0,
        unreadable_count=0,
        duration_s=1.0,
        is_admin=False,
    )


def test_fewer_than_the_minimum_snapshots_is_not_enough_data():
    snapshots = [snapshot(0, 100), snapshot(1, 200)]
    assert len(snapshots) < MIN_SNAPSHOTS
    assert trend_of(snapshots) is None


def test_exactly_the_minimum_is_enough():
    snapshots = [snapshot(0, 100), snapshot(1, 200), snapshot(2, 300)]
    assert trend_of(snapshots) is not None


def test_an_empty_history_is_not_enough_data():
    assert trend_of([]) is None


def test_a_clean_growth_trend_has_the_matching_slope():
    snapshots = [snapshot(0, 0), snapshot(1, 100), snapshot(2, 200)]
    trend = trend_of(snapshots)
    assert trend.bytes_per_day == pytest.approx(100.0)
    assert trend.sample_count == 3


def test_a_flat_history_has_a_zero_slope():
    snapshots = [snapshot(0, 500), snapshot(1, 500), snapshot(2, 500)]
    trend = trend_of(snapshots)
    assert trend.bytes_per_day == pytest.approx(0.0)


def test_a_shrinking_history_has_a_negative_slope():
    snapshots = [snapshot(0, 300), snapshot(1, 200), snapshot(2, 100)]
    trend = trend_of(snapshots)
    assert trend.bytes_per_day == pytest.approx(-100.0)


def test_snapshots_spaced_by_hours_are_converted_to_a_daily_rate():
    # 50 bytes cada 12 horas equivalen a 100 por día.
    snapshots = [
        Snapshot(
            taken_at=T0 + timedelta(hours=12 * i),
            volume="C:",
            total_bytes=1000,
            used_bytes=50 * i,
            free_bytes=1000 - 50 * i,
            scanned_bytes=50 * i,
            system_files_bytes=0,
            unreadable_bytes=0,
            unreadable_count=0,
            duration_s=1.0,
            is_admin=False,
        )
        for i in range(3)
    ]
    trend = trend_of(snapshots)
    assert trend.bytes_per_day == pytest.approx(100.0)


def test_snapshots_out_of_order_give_the_same_trend_as_sorted():
    in_order = [snapshot(0, 0), snapshot(1, 100), snapshot(2, 200)]
    shuffled = [in_order[2], in_order[0], in_order[1]]
    assert trend_of(shuffled) == trend_of(in_order)


def test_a_noisy_but_growing_series_still_has_a_positive_slope():
    snapshots = [snapshot(0, 0), snapshot(1, 90), snapshot(2, 130), snapshot(3, 240)]
    trend = trend_of(snapshots)
    assert trend.bytes_per_day > 0


def test_days_until_full_with_a_growing_trend():
    trend = Trend(bytes_per_day=100.0, sample_count=3)
    assert days_until_full(trend, free_bytes=1000) == pytest.approx(10.0)


def test_days_until_full_is_none_for_a_flat_trend():
    trend = Trend(bytes_per_day=0.0, sample_count=3)
    assert days_until_full(trend, free_bytes=1000) is None


def test_days_until_full_is_none_for_a_shrinking_trend():
    trend = Trend(bytes_per_day=-50.0, sample_count=3)
    assert days_until_full(trend, free_bytes=1000) is None


def test_days_until_full_with_no_free_space_left_is_zero():
    trend = Trend(bytes_per_day=100.0, sample_count=3)
    assert days_until_full(trend, free_bytes=0) == 0.0
