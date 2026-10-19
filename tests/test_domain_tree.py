"""Pruebas de la navegación del árbol (T3.1): puras."""

from discoclaro.domain.models import Node
from discoclaro.domain.tree import children_of, node_at

ROOT = Node("C:\\", None, 1000, 10, 0)
DOCS = Node("C:\\Docs", "C:\\", 400, 5, 1)
GAMES = Node("C:\\Games", "C:\\", 600, 5, 1)
SAVES = Node("C:\\Games\\Saves", "C:\\Games", 100, 2, 2)
NODES = [ROOT, DOCS, GAMES, SAVES]


def test_node_at_finds_the_exact_path():
    assert node_at(NODES, "C:\\Games") == GAMES


def test_node_at_is_none_for_an_unknown_path():
    assert node_at(NODES, "C:\\Nope") is None


def test_children_of_none_returns_the_root_nodes():
    assert children_of(NODES, None) == (ROOT,)


def test_children_of_a_path_returns_only_its_direct_children():
    assert children_of(NODES, "C:\\") == (GAMES, DOCS)


def test_children_of_a_leaf_is_empty():
    assert children_of(NODES, "C:\\Games\\Saves") == ()


def test_children_are_sorted_by_size_descending():
    children = children_of(NODES, "C:\\")
    assert [child.size_bytes for child in children] == [600, 400]
