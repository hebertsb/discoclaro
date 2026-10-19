"""Pruebas de las decisiones del usuario (T2C.4): puras."""

from datetime import UTC, datetime

import pytest

from discoclaro.domain.decisions import Decision, is_protected

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def test_a_decision_needs_a_target():
    with pytest.raises(ValueError):
        Decision(target="", status="protected", note=None, decided_at=T0)


def test_a_decision_needs_a_known_status():
    with pytest.raises(ValueError):
        Decision(target="x", status="unknown", note=None, decided_at=T0)


def test_is_protected_is_true_for_a_protected_decision():
    decision = Decision(
        target="x", status="protected", note="por si acaso", decided_at=T0
    )
    assert is_protected(decision) is True


@pytest.mark.parametrize("status", ["pending", "ignored"])
def test_is_protected_is_false_for_other_statuses(status):
    decision = Decision(target="x", status=status, note=None, decided_at=T0)
    assert is_protected(decision) is False


def test_is_protected_is_false_when_nothing_was_decided():
    assert is_protected(None) is False
