"""Pruebas de los totales por categoría: sin disco, sin variables de entorno reales."""

from discoclaro.domain.categories import CategoryTotal, category_totals
from discoclaro.domain.models import Node
from discoclaro.domain.rules import Rule


def rule(id, *paths, category):
    return Rule(
        id=id, name=id, paths=paths, category=category, risk="safe", explanation="x"
    )


def test_one_matching_folder_makes_one_total():
    nodes = [Node("C:\\a\\pip\\Cache", "C:\\a", 100, 1, 1)]
    rules = [rule("pip-cache", "C:\\a\\pip\\Cache", category="cache")]
    assert category_totals(nodes, rules, {}) == (
        CategoryTotal(category="cache", size_bytes=100, folders=1),
    )


def test_a_folder_that_matches_nothing_is_left_out():
    nodes = [Node("C:\\a\\docs", "C:\\a", 100, 1, 1)]
    rules = [rule("pip-cache", "C:\\a\\pip\\Cache", category="cache")]
    assert category_totals(nodes, rules, {}) == ()


def test_two_folders_in_the_same_category_add_up():
    nodes = [
        Node("C:\\a\\pip\\Cache", "C:\\a", 100, 1, 1),
        Node("C:\\b\\pip\\Cache", "C:\\b", 50, 1, 1),
    ]
    rules = [rule("pip-cache", "C:\\*\\pip\\Cache", category="cache")]
    [total] = category_totals(nodes, rules, {})
    assert total == CategoryTotal(category="cache", size_bytes=150, folders=2)


def test_results_are_sorted_from_biggest_to_smallest_category():
    nodes = [
        Node("C:\\temp", None, 10, 1, 0),
        Node("C:\\cache", None, 90, 1, 0),
    ]
    rules = [
        rule("temp", "C:\\temp", category="temp"),
        rule("cache", "C:\\cache", category="cache"),
    ]
    totals = category_totals(nodes, rules, {})
    assert [total.category for total in totals] == ["cache", "temp"]


def test_a_folder_inside_an_already_counted_one_is_not_counted_again():
    """El padre ya cuenta todo lo de adentro: contar la hija sería sumar dos veces."""
    nodes = [
        Node("C:\\a", None, 1000, 1, 0),
        Node("C:\\a\\pip\\Cache", "C:\\a", 100, 1, 2),
    ]
    rules = [
        rule("whole-a", "C:\\a", category="misc"),
        rule("pip-cache", "C:\\a\\pip\\Cache", category="cache"),
    ]
    assert category_totals(nodes, rules, {}) == (
        CategoryTotal(category="misc", size_bytes=1000, folders=1),
    )


def test_shallower_folders_are_considered_before_deeper_ones_regardless_of_order():
    """Da igual el orden en que lleguen los nodos: el más superficial manda."""
    nodes = [
        Node("C:\\a\\pip\\Cache", "C:\\a", 100, 1, 2),
        Node("C:\\a", None, 1000, 1, 0),
    ]
    rules = [
        rule("whole-a", "C:\\a", category="misc"),
        rule("pip-cache", "C:\\a\\pip\\Cache", category="cache"),
    ]
    assert category_totals(nodes, rules, {}) == (
        CategoryTotal(category="misc", size_bytes=1000, folders=1),
    )


def test_a_folder_that_looks_like_a_prefix_but_is_not_inside_is_counted_on_its_own():
    """ "C:\\abc" no está dentro de "C:\\ab": no es el mismo prefijo de carpeta."""
    nodes = [
        Node("C:\\ab", None, 10, 1, 0),
        Node("C:\\abc", None, 20, 1, 0),
    ]
    rules = [
        rule("ab", "C:\\ab", category="ab"),
        rule("abc", "C:\\abc", category="abc"),
    ]
    totals = {
        total.category: total.size_bytes for total in category_totals(nodes, rules, {})
    }
    assert totals == {"ab": 10, "abc": 20}


def test_sibling_folders_in_different_categories_both_count():
    nodes = [
        Node("C:\\a\\pip\\Cache", "C:\\a", 100, 1, 1),
        Node("C:\\a\\temp", "C:\\a", 20, 1, 1),
    ]
    rules = [
        rule("pip-cache", "C:\\a\\pip\\Cache", category="cache"),
        rule("temp", "C:\\a\\temp", category="temp"),
    ]
    totals = {
        total.category: total.size_bytes for total in category_totals(nodes, rules, {})
    }
    assert totals == {"cache": 100, "temp": 20}


def test_environment_variables_are_used_to_match():
    nodes = [Node("C:\\Users\\x\\AppData\\Local\\pip\\Cache", None, 5, 1, 0)]
    rules = [rule("pip-cache", "%LOCALAPPDATA%\\pip\\Cache", category="cache")]
    env = {"%LOCALAPPDATA%": "C:\\Users\\x\\AppData\\Local"}
    assert category_totals(nodes, rules, env) == (
        CategoryTotal(category="cache", size_bytes=5, folders=1),
    )


def test_no_nodes_gives_an_empty_result():
    rules = [rule("pip-cache", "C:\\a", category="cache")]
    assert category_totals([], rules, {}) == ()


def test_no_rules_gives_an_empty_result():
    nodes = [Node("C:\\a", None, 10, 1, 0)]
    assert category_totals(nodes, [], {}) == ()
