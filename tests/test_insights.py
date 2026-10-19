"""Pruebas de la forma común de los inspectores (T2C.1): pura."""

import pytest

from discoclaro.domain.insights import Insight, InsightItem


def test_an_insight_item_needs_a_name():
    with pytest.raises(ValueError):
        InsightItem(name="", size_bytes=10)


def test_an_insight_item_cannot_have_a_negative_size():
    with pytest.raises(ValueError):
        InsightItem(name="images", size_bytes=-1)


def test_an_insight_needs_a_program():
    with pytest.raises(ValueError):
        Insight(program="", items=())


def test_total_bytes_sums_every_item():
    insight = Insight(
        program="docker",
        items=(
            InsightItem(name="images", size_bytes=100),
            InsightItem(name="containers", size_bytes=50),
        ),
    )
    assert insight.total_bytes == 150


def test_total_bytes_of_no_items_is_zero():
    assert Insight(program="docker", items=()).total_bytes == 0
