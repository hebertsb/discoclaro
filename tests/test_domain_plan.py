"""Pruebas del veredicto por niveles (T2C.7): puras."""

import pytest

from discoclaro.domain.plan import Plan, PlanItem, build_plan, target_of


def _item(**overrides) -> PlanItem:
    base = dict(
        level="safe",
        program="docker",
        name="build-cache",
        size_bytes=100,
        reason="reclaimable",
        target="docker:build-cache",
    )
    base.update(overrides)
    return PlanItem(**base)


def test_target_of_joins_program_and_name():
    assert target_of("ollama", "llama3") == "ollama:llama3"


def test_a_plan_item_rejects_an_unknown_level():
    with pytest.raises(ValueError):
        _item(level="urgent")


def test_a_plan_item_needs_a_program():
    with pytest.raises(ValueError):
        _item(program="")


def test_a_plan_item_needs_a_name():
    with pytest.raises(ValueError):
        _item(name="")


def test_a_plan_item_rejects_a_negative_size():
    with pytest.raises(ValueError):
        _item(size_bytes=-1)


def test_a_plan_item_needs_a_target():
    with pytest.raises(ValueError):
        _item(target="")


def test_of_level_filters_by_level():
    safe = _item(level="safe", target="docker:a")
    review = _item(level="review", target="ollama:b")
    plan = Plan(items=(safe, review))

    assert plan.of_level("safe") == (safe,)
    assert plan.of_level("review") == (review,)
    assert plan.of_level("move") == ()


def test_total_bytes_sums_only_that_level():
    plan = Plan(
        items=(
            _item(level="safe", size_bytes=10, target="docker:a"),
            _item(level="safe", size_bytes=20, target="docker:b"),
            _item(level="review", size_bytes=1000, target="ollama:c"),
        )
    )

    assert plan.total_bytes("safe") == 30
    assert plan.total_bytes("review") == 1000
    assert plan.total_bytes("move") == 0


def test_build_plan_keeps_candidates_not_excluded():
    a = _item(target="docker:a")
    b = _item(target="docker:b")

    plan = build_plan([a, b], is_excluded=["docker:b"])

    assert plan.items == (a,)


def test_build_plan_with_no_exclusions_keeps_everything():
    a = _item(target="docker:a")
    b = _item(target="docker:b")

    plan = build_plan([a, b])

    assert plan.items == (a, b)


def test_build_plan_preserves_the_original_order_within_a_level():
    a = _item(target="docker:a", name="a")
    b = _item(target="docker:b", name="b")
    c = _item(target="docker:c", name="c")

    plan = build_plan([a, b, c], is_excluded=["docker:b"])

    assert [item.name for item in plan.items] == ["a", "c"]
