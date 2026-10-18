"""Pruebas de las reglas de disparo de avisos (T2.7): puras, sin fotos ni base."""

from discoclaro.domain.alerts import (
    ALERT_KINDS,
    BUDGET_NEAR_PERCENT,
    Alert,
    check_big_new_file,
    check_budget,
    check_unexplained_grew,
    check_volume_low_free,
    check_volume_near_limit,
)

GIB = 1024**3


def test_the_six_kinds_from_the_plan_are_exact():
    assert ALERT_KINDS == (
        "volume_low_free",
        "volume_near_limit",
        "budget_near",
        "budget_exceeded",
        "big_new_file",
        "unexplained_grew",
    )


def test_volume_low_free_does_not_fire_with_enough_space():
    assert check_volume_low_free("C:", 50 * GIB, 40 * GIB) is None


def test_volume_low_free_fires_below_the_minimum():
    alert = check_volume_low_free("C:", 10 * GIB, 40 * GIB)
    assert alert == Alert(
        kind="volume_low_free",
        target="C:",
        measured_bytes=10 * GIB,
        limit_bytes=40 * GIB,
    )


def test_volume_low_free_fires_exactly_at_the_minimum_minus_one():
    assert check_volume_low_free("C:", 40 * GIB - 1, 40 * GIB) is not None


def test_volume_low_free_never_fires_without_a_configured_minimum():
    assert check_volume_low_free("C:", 0, None) is None


def test_volume_near_limit_does_not_fire_below_the_percent():
    assert check_volume_near_limit("C:", 84.9, 85.0) is None


def test_volume_near_limit_fires_exactly_at_the_percent():
    alert = check_volume_near_limit("C:", 85.0, 85.0)
    assert alert == Alert(
        kind="volume_near_limit", target="C:", measured_percent=85.0, limit_percent=85.0
    )


def test_volume_near_limit_fires_above_the_percent():
    assert check_volume_near_limit("C:", 99.0, 85.0) is not None


def test_budget_does_not_fire_below_the_near_threshold():
    just_under = 30 * GIB * BUDGET_NEAR_PERCENT / 100 - 1
    assert check_budget("ai-models", just_under, 30 * GIB) is None


def test_budget_near_fires_at_the_near_threshold():
    at_threshold = 30 * GIB * BUDGET_NEAR_PERCENT / 100
    alert = check_budget("ai-models", at_threshold, 30 * GIB)
    assert alert == Alert(
        kind="budget_near",
        target="ai-models",
        measured_bytes=at_threshold,
        limit_bytes=30 * GIB,
    )


def test_budget_exceeded_fires_at_the_maximum():
    alert = check_budget("ai-models", 30 * GIB, 30 * GIB)
    assert alert.kind == "budget_exceeded"


def test_budget_exceeded_fires_above_the_maximum():
    alert = check_budget("ai-models", 40 * GIB, 30 * GIB)
    assert alert == Alert(
        kind="budget_exceeded",
        target="ai-models",
        measured_bytes=40 * GIB,
        limit_bytes=30 * GIB,
    )


def test_big_new_file_does_not_fire_below_the_threshold():
    assert check_big_new_file("C:\\model.bin", 10 * GIB - 1, 10 * GIB) is None


def test_big_new_file_fires_at_the_threshold():
    alert = check_big_new_file("C:\\model.bin", 10 * GIB, 10 * GIB)
    assert alert == Alert(
        kind="big_new_file",
        target="C:\\model.bin",
        measured_bytes=10 * GIB,
        limit_bytes=10 * GIB,
    )


def test_unexplained_grew_never_fires_without_a_configured_limit():
    assert check_unexplained_grew("C:", 100 * GIB, None) is None


def test_unexplained_grew_does_not_fire_below_the_limit():
    assert check_unexplained_grew("C:", 4 * GIB, 5 * GIB) is None


def test_unexplained_grew_fires_at_the_limit():
    alert = check_unexplained_grew("C:", 5 * GIB, 5 * GIB)
    assert alert == Alert(
        kind="unexplained_grew",
        target="C:",
        measured_bytes=5 * GIB,
        limit_bytes=5 * GIB,
    )


def test_unexplained_grew_does_not_fire_when_it_shrank():
    assert check_unexplained_grew("C:", -5 * GIB, 1 * GIB) is None
