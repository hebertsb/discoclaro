"""Pruebas del escáner con árboles reales en `tmp_path`, con cada enumerador.

Nunca se recorre una carpeta real del usuario.
"""

import os
import shutil
import sys

import pytest

from discoclaro.adapters.scanner import enumerate_with_scandir, scan_tree
from discoclaro.domain.models import Unreadable
from discoclaro.platform import windows_listing

on_windows = sys.platform == "win32"
windows_only = pytest.mark.skipif(not on_windows, reason="usa la API de Windows")


@pytest.fixture(
    params=[
        pytest.param(enumerate_with_scandir, id="scandir"),
        pytest.param(
            windows_listing.enumerate_directory,
            id="windows",
            marks=windows_only,
        ),
    ]
)
def enumerator(request):
    return request.param


def make_tree(base, spec):
    """Crea el árbol de `spec`: número = archivo de ese tamaño, dict = carpeta."""
    for name, value in spec.items():
        path = base / name
        if isinstance(value, dict):
            path.mkdir()
            make_tree(path, value)
        else:
            path.write_bytes(b"x" * value)


def by_path(result):
    return {node.path: node for node in result.nodes}


def deny(denied_path, base, error=None):
    """Un enumerador que falla solo con `denied_path`, como una carpeta sin permiso."""
    error = error or PermissionError(13, "denied")

    def enumerate_directory(path):
        if path == denied_path:
            raise error
        return base(path)

    return enumerate_directory


def test_sizes_counts_depth_and_parents(tmp_path, enumerator):
    make_tree(
        tmp_path,
        {
            "a.bin": 100,
            "docs": {"b.txt": 10, "c.txt": 20, "deep": {"d.txt": 5}},
            "empty": {},
        },
    )
    result = scan_tree(str(tmp_path), enumerator)
    root = str(tmp_path)
    docs = str(tmp_path / "docs")
    deep = str(tmp_path / "docs" / "deep")
    empty = str(tmp_path / "empty")
    expected = {
        root: (135, 4, 0, None),
        docs: (35, 3, 1, root),
        deep: (5, 1, 2, docs),
        empty: (0, 0, 1, root),
    }
    nodes = by_path(result)
    assert set(nodes) == set(expected)
    for path, (logical, files, depth, parent) in expected.items():
        node = nodes[path]
        assert (node.n_files, node.depth, node.parent_path) == (files, depth, parent)
        # Con `scandir` es el tamaño lógico; con Windows, lo que ocupa en disco.
        assert logical <= node.size_bytes <= logical + 64 * 1024 * max(files, 1)
    assert result.n_files == 4
    assert result.root == root


def test_with_scandir_the_sizes_are_exactly_the_logical_ones(tmp_path):
    make_tree(tmp_path, {"a.bin": 100, "docs": {"b.txt": 10}})
    result = scan_tree(str(tmp_path), enumerate_with_scandir)
    assert result.scanned_bytes == 110
    assert by_path(result)[str(tmp_path / "docs")].size_bytes == 10


def test_children_are_emitted_before_their_parents_and_the_root_is_last(
    tmp_path, enumerator
):
    make_tree(tmp_path, {"a": {"b": {"c": {"f.bin": 1}}}, "z": {}})
    result = scan_tree(str(tmp_path), enumerator)
    paths = [node.path for node in result.nodes]
    assert paths[-1] == str(tmp_path)
    for node in result.nodes:
        if node.parent_path is not None:
            assert paths.index(node.path) < paths.index(node.parent_path)


def test_an_empty_root_gives_a_single_zero_node(tmp_path, enumerator):
    result = scan_tree(str(tmp_path), enumerator)
    assert len(result.nodes) == 1
    assert (result.scanned_bytes, result.n_files) == (0, 0)
    assert (result.unreadable_count, result.skipped_links) == (0, 0)


def test_a_missing_or_non_folder_root_raises(tmp_path, enumerator):
    with pytest.raises(FileNotFoundError):
        scan_tree(str(tmp_path / "no-existe"), enumerator)
    a_file = tmp_path / "file.txt"
    a_file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_tree(str(a_file), enumerator)


def test_odd_folder_names_are_kept_verbatim(tmp_path, enumerator):
    names = [
        "'; DROP TABLE nodes;--",
        "100%_done",
        "ñandú 日本語 📁",
        "dos  espacios",
        "a'b",
        "\u202e-rtl",
        "x[1]{2}(3)",
        "..oculta",
        "-rf",
    ]
    make_tree(tmp_path, {name: {"f": index + 1} for index, name in enumerate(names)})
    nodes = by_path(scan_tree(str(tmp_path), enumerator))
    for index, name in enumerate(names):
        node = nodes[str(tmp_path / name)]
        assert (node.n_files, node.depth) == (1, 1)
        assert node.size_bytes >= index + 1


def test_an_unreadable_folder_is_counted_recorded_and_the_rest_is_scanned(
    tmp_path, enumerator
):
    make_tree(
        tmp_path,
        {"ok.bin": 10, "locked": {"secret.bin": 999}, "open": {"a.bin": 7}},
    )
    locked = str(tmp_path / "locked")
    result = scan_tree(str(tmp_path), deny(locked, enumerator))
    nodes = by_path(result)
    assert nodes[locked].size_bytes == 0
    assert nodes[str(tmp_path / "open")].size_bytes >= 7
    assert result.n_files == 2
    assert result.unreadable == (Unreadable(locked, "PermissionError"),)


def test_an_unreadable_root_is_recorded_not_raised(tmp_path, enumerator):
    result = scan_tree(str(tmp_path), deny(str(tmp_path), enumerator))
    assert len(result.nodes) == 1
    assert result.scanned_bytes == 0
    assert result.unreadable_count == 1


def test_skip_root_files_ignores_those_names_only_at_the_root(tmp_path, enumerator):
    make_tree(
        tmp_path,
        {
            "pagefile.sys": 100,
            "Hiberfil.SYS": 50,
            "keep.bin": 10,
            "sub": {"pagefile.sys": 7},
        },
    )
    result = scan_tree(
        str(tmp_path), enumerator, skip_root_files=("pagefile.sys", "HIBERFIL.sys")
    )
    assert result.n_files == 2
    assert by_path(result)[str(tmp_path / "sub")].n_files == 1
    # Sin lo omitido, solo quedan `keep.bin` y el `pagefile.sys` de la subcarpeta.
    everything = scan_tree(str(tmp_path), enumerator)
    assert everything.n_files == 4
    assert everything.scanned_bytes > result.scanned_bytes


@windows_only
def test_a_real_junction_is_not_followed_and_a_loop_of_junctions_ends(
    tmp_path, enumerator
):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "real.bin").write_bytes(b"x" * 10)
    _winapi.CreateJunction(str(outside), str(root / "to_outside"))
    _winapi.CreateJunction(str(root), str(root / "sub" / "loop"))
    result = scan_tree(str(root), enumerator)
    assert result.n_files == 1
    assert result.skipped_links == 2
    assert set(by_path(result)) == {str(root), str(root / "sub")}


def test_real_symlinks_are_not_followed(tmp_path, enumerator):
    target = tmp_path / "target"
    target.mkdir()
    (target / "big.bin").write_bytes(b"x" * 1000)
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.bin").write_bytes(b"x" * 10)
    try:
        os.symlink(target, root / "dirlink", target_is_directory=True)
        os.symlink(target / "big.bin", root / "filelink")
    except OSError:
        pytest.skip("este equipo no permite crear enlaces simbólicos")
    result = scan_tree(str(root), enumerator)
    assert result.n_files == 1
    assert result.skipped_links == 2


@windows_only
def test_a_real_hard_link_is_counted_in_every_folder_but_once_in_the_duplicates(
    tmp_path,
):
    (tmp_path / "otra").mkdir()
    (tmp_path / "datos.bin").write_bytes(b"x" * 5000)
    os.link(tmp_path / "datos.bin", tmp_path / "otra" / "mismo_contenido.bin")
    result = scan_tree(str(tmp_path), windows_listing.enumerate_directory)
    nodes = by_path(result)
    one_copy = nodes[str(tmp_path / "otra")].size_bytes
    assert one_copy >= 5000
    assert result.n_files == 2
    assert result.scanned_bytes == 2 * one_copy
    assert result.duplicate_bytes == one_copy


def test_with_scandir_hard_links_are_unknown_and_not_deducted(tmp_path):
    (tmp_path / "datos.bin").write_bytes(b"x" * 5000)
    os.link(tmp_path / "datos.bin", tmp_path / "otro_nombre.bin")
    result = scan_tree(str(tmp_path), enumerate_with_scandir)
    assert (result.scanned_bytes, result.duplicate_bytes) == (10000, 0)


@windows_only
def test_on_windows_the_default_enumerator_gives_the_same_as_the_windows_one(tmp_path):
    make_tree(tmp_path, {"a.bin": 5000, "docs": {"b.bin": 1}})
    default = scan_tree(str(tmp_path))
    explicit = scan_tree(str(tmp_path), windows_listing.enumerate_directory)
    assert default == explicit


@windows_only
def test_a_file_of_5000_bytes_occupies_whole_blocks_on_disk(tmp_path):
    (tmp_path / "cinco_mil").write_bytes(b"x" * 5000)
    result = scan_tree(str(tmp_path), windows_listing.enumerate_directory)
    assert result.scanned_bytes >= 5000
    assert result.scanned_bytes % 512 == 0


@windows_only
def test_a_tree_deeper_than_260_characters_is_scanned_completely(tmp_path):
    name = "carpeta_larga_" + "x" * 40
    deep = tmp_path
    for _ in range(6):
        deep = deep / name
    try:
        os.makedirs(windows_listing.extended_path(str(deep)))
        with open(windows_listing.extended_path(str(deep / "dentro.txt")), "wb") as f:
            f.write(b"hola")
        assert len(str(deep)) > 260
        result = scan_tree(str(tmp_path), windows_listing.enumerate_directory)
        assert result.n_files == 1
        assert len(result.nodes) == 7
        assert result.unreadable_count == 0
    finally:
        shutil.rmtree(windows_listing.extended_path(str(tmp_path / name)))
