"""Pruebas de la comparación entre dos fotos: sin base, sin disco, sin reloj real."""

from datetime import timedelta

import pytest

from discoclaro.domain.growth import (
    CategoryGrowth,
    FolderGrowth,
    InvalidDuration,
    category_growth,
    folder_growth,
    new_large_files,
    parse_duration,
)
from discoclaro.domain.models import LargeFile, Node
from discoclaro.domain.rules import Rule


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", timedelta(days=7)),
        ("24h", timedelta(hours=24)),
        ("2w", timedelta(weeks=2)),
        ("7D", timedelta(days=7)),
        ("0d", timedelta(days=0)),
        (" 7d ", timedelta(days=7)),
    ],
)
def test_parse_duration_accepts_a_number_and_a_unit(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "7", "d7", "-1d", "7 d", "7x", "seven days"])
def test_parse_duration_rejects_anything_else(text):
    with pytest.raises(InvalidDuration):
        parse_duration(text)


def test_a_new_folder_has_zero_before_bytes_and_is_marked_new():
    after = [Node("C:\\a", None, 100, 1, 0)]
    [growth] = folder_growth([], after)
    assert growth == FolderGrowth(
        path="C:\\a", depth=0, before_bytes=0, after_bytes=100, is_new=True
    )
    assert growth.grown_bytes == 100


def test_a_deleted_folder_has_zero_after_bytes():
    before = [Node("C:\\a", None, 100, 1, 0)]
    [growth] = folder_growth(before, [])
    assert growth == FolderGrowth(
        path="C:\\a", depth=0, before_bytes=100, after_bytes=0, is_new=False
    )
    assert growth.grown_bytes == -100


def test_a_folder_in_both_photos_is_not_marked_new():
    before = [Node("C:\\a", None, 50, 1, 0)]
    after = [Node("C:\\a", None, 80, 1, 0)]
    [growth] = folder_growth(before, after)
    assert growth.is_new is False
    assert (growth.before_bytes, growth.after_bytes) == (50, 80)


def test_folders_are_sorted_from_biggest_grower_to_biggest_shrinker():
    before = [Node("C:\\shrunk", None, 100, 1, 0)]
    after = [
        Node("C:\\shrunk", None, 10, 1, 0),
        Node("C:\\grew", None, 50, 1, 0),
    ]
    results = folder_growth(before, after)
    assert [g.path for g in results] == ["C:\\grew", "C:\\shrunk"]


def test_depth_comes_from_whichever_snapshot_has_the_node():
    after = [Node("C:\\a\\b", "C:\\a", 10, 1, 2)]
    [growth] = folder_growth([], after)
    assert growth.depth == 2
    before = [Node("C:\\c\\d", "C:\\c", 10, 1, 3)]
    [growth] = folder_growth(before, [])
    assert growth.depth == 3


def rule(id, *paths, category):
    return Rule(
        id=id, name=id, paths=paths, category=category, risk="safe", explanation="x"
    )


def test_a_category_that_only_exists_after_is_shown_as_fully_new():
    after = [Node("C:\\cache", None, 100, 1, 0)]
    rules = [rule("cache", "C:\\cache", category="cache")]
    [growth] = category_growth([], after, rules, {})
    assert growth == CategoryGrowth(category="cache", before_bytes=0, after_bytes=100)


def test_category_growth_adds_up_matching_folders_on_each_side():
    before = [Node("C:\\cache", None, 50, 1, 0)]
    after = [Node("C:\\cache", None, 120, 1, 0)]
    rules = [rule("cache", "C:\\cache", category="cache")]
    [growth] = category_growth(before, after, rules, {})
    assert (growth.before_bytes, growth.after_bytes) == (50, 120)


def test_categories_are_sorted_from_biggest_grower_to_biggest_shrinker():
    before = [Node("C:\\a", None, 100, 1, 0), Node("C:\\b", None, 10, 1, 0)]
    after = [Node("C:\\a", None, 10, 1, 0), Node("C:\\b", None, 100, 1, 0)]
    rules = [
        rule("a", "C:\\a", category="shrinks"),
        rule("b", "C:\\b", category="grows"),
    ]
    results = category_growth(before, after, rules, {})
    assert [g.category for g in results] == ["grows", "shrinks"]


def test_a_file_only_in_after_is_a_new_large_file():
    after = [LargeFile("C:\\model.gguf", 100)]
    assert new_large_files([], after) == (LargeFile("C:\\model.gguf", 100),)


def test_a_file_present_in_both_is_not_new_even_if_it_grew():
    before = [LargeFile("C:\\model.gguf", 100)]
    after = [LargeFile("C:\\model.gguf", 500)]
    assert new_large_files(before, after) == ()


def test_new_large_files_are_sorted_from_biggest_to_smallest():
    after = [LargeFile("C:\\small.bin", 60), LargeFile("C:\\big.bin", 90)]
    assert new_large_files([], after) == (
        LargeFile("C:\\big.bin", 90),
        LargeFile("C:\\small.bin", 60),
    )


def test_no_large_files_at_all_gives_an_empty_result():
    assert new_large_files([], []) == ()
