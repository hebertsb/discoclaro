"""Pruebas del escáner: árboles sintéticos en `tmp_path` y listados falsos.

Nunca se recorre una carpeta real del usuario.
"""

import contextlib
import inspect
import os
import sys
import types

import pytest

from discoclaro.adapters import scanner
from discoclaro.adapters.scanner import scan_tree
from discoclaro.domain.models import Unreadable

REPARSE_POINT = 0x400
OFFLINE = 0x1000
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000


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


def deny(denied_path, error=None):
    """`scandir` real que falla solo con `denied_path`, como una carpeta sin permiso."""
    error = error or PermissionError(13, "denied")

    def scandir(path):
        if path == denied_path:
            raise error
        return os.scandir(path)

    return scandir


class FakeEntry:
    """Un elemento de carpeta falso, con lo mínimo que usa el escáner."""

    def __init__(
        self,
        path,
        *,
        is_dir=False,
        size=0,
        attributes=0,
        symlink=False,
        stat_error=None,
    ):
        self.path = path
        self.name = path.replace("\\", "/").rsplit("/", 1)[-1]
        self._is_dir = is_dir
        self._size = size
        self._attributes = attributes
        self._symlink = symlink
        self._stat_error = stat_error

    def is_symlink(self):
        return self._symlink

    def is_dir(self, *, follow_symlinks=True):
        return self._is_dir

    def is_file(self, *, follow_symlinks=True):
        return not self._is_dir and not self._symlink

    def stat(self, *, follow_symlinks=True):
        if self._stat_error:
            raise self._stat_error
        return types.SimpleNamespace(
            st_size=self._size, st_file_attributes=self._attributes
        )


def fake_scandir(tree):
    """`scandir` falso: `tree` asigna a cada ruta su lista de elementos o un error."""

    @contextlib.contextmanager
    def scandir(path):
        listing = tree[path]
        if isinstance(listing, Exception):
            raise listing
        yield iter(listing)

    return scandir


def test_sizes_counts_depth_and_parents(tmp_path):
    make_tree(
        tmp_path,
        {
            "a.bin": 100,
            "docs": {"b.txt": 10, "c.txt": 20, "deep": {"d.txt": 5}},
            "empty": {},
        },
    )
    result = scan_tree(str(tmp_path))
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
    for path, (size, files, depth, parent) in expected.items():
        node = nodes[path]
        assert (node.size_bytes, node.n_files, node.depth, node.parent_path) == (
            size,
            files,
            depth,
            parent,
        )
    assert result.scanned_bytes == 135
    assert result.n_files == 4
    assert result.root == root


def test_children_are_emitted_before_their_parents_and_the_root_is_last(tmp_path):
    make_tree(tmp_path, {"a": {"b": {"c": {"f.bin": 1}}}, "z": {}})
    result = scan_tree(str(tmp_path))
    paths = [node.path for node in result.nodes]
    assert paths[-1] == str(tmp_path)
    for node in result.nodes:
        if node.parent_path is not None:
            assert paths.index(node.path) < paths.index(node.parent_path)


def test_an_empty_root_gives_a_single_zero_node(tmp_path):
    result = scan_tree(str(tmp_path))
    assert len(result.nodes) == 1
    assert (result.scanned_bytes, result.n_files) == (0, 0)
    assert result.unreadable_count == 0
    assert result.skipped_links == 0


def test_a_missing_or_non_folder_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_tree(str(tmp_path / "no-existe"))
    a_file = tmp_path / "file.txt"
    a_file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        scan_tree(str(a_file))


def test_odd_folder_names_are_kept_verbatim(tmp_path):
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
    nodes = by_path(scan_tree(str(tmp_path)))
    for index, name in enumerate(names):
        node = nodes[str(tmp_path / name)]
        assert (node.size_bytes, node.n_files, node.depth) == (index + 1, 1, 1)


def test_a_very_deep_tree_does_not_hit_the_recursion_limit():
    depth = sys.getrecursionlimit() * 3
    tree = {}
    path = "/r"
    for _ in range(depth):
        child = f"{path}/d"
        tree[path] = [FakeEntry(child, is_dir=True), FakeEntry(f"{path}/f.bin", size=1)]
        path = child
    tree[path] = []
    result = scan_tree("/r", fake_scandir(tree))
    assert len(result.nodes) == depth + 1
    assert result.scanned_bytes == depth
    assert result.nodes[0].depth == depth
    assert result.nodes[-1].path == "/r"


def test_an_unreadable_folder_is_counted_recorded_and_the_rest_is_scanned(tmp_path):
    make_tree(
        tmp_path,
        {"ok.bin": 10, "locked": {"secret.bin": 999}, "open": {"a.bin": 7}},
    )
    locked = str(tmp_path / "locked")
    result = scan_tree(str(tmp_path), deny(locked))
    nodes = by_path(result)
    assert nodes[locked].size_bytes == 0
    assert nodes[str(tmp_path / "open")].size_bytes == 7
    assert result.scanned_bytes == 17
    assert result.unreadable_count == 1
    assert result.unreadable == (Unreadable(locked, "PermissionError"),)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "denied"),
        FileNotFoundError(2, "vanished"),
        OSError(5, "io error"),
    ],
)
def test_the_reason_is_the_stable_name_of_the_error(tmp_path, error):
    make_tree(tmp_path, {"sub": {"f.bin": 1}})
    sub = str(tmp_path / "sub")
    result = scan_tree(str(tmp_path), deny(sub, error))
    assert result.unreadable == (Unreadable(sub, type(error).__name__),)


def test_an_unreadable_root_is_recorded_not_raised(tmp_path):
    result = scan_tree(str(tmp_path), deny(str(tmp_path)))
    assert len(result.nodes) == 1
    assert result.scanned_bytes == 0
    assert result.unreadable_count == 1


def test_a_file_that_cannot_be_measured_is_recorded_and_the_others_count():
    tree = {
        "/r": [
            FakeEntry("/r/ok.bin", size=5),
            FakeEntry("/r/bad.bin", size=9, stat_error=PermissionError(13, "x")),
            FakeEntry("/r/ok2.bin", size=6),
        ]
    }
    result = scan_tree("/r", fake_scandir(tree))
    assert result.scanned_bytes == 11
    assert result.n_files == 2
    assert result.unreadable == (Unreadable("/r/bad.bin", "PermissionError"),)


def test_all_unreadable_paths_are_counted_but_only_the_first_ones_are_kept():
    total = scanner.MAX_RECORDED_UNREADABLE + 500
    entries = [
        FakeEntry(f"/r/f{index}", stat_error=OSError(5, "io")) for index in range(total)
    ]
    result = scan_tree("/r", fake_scandir({"/r": entries}))
    assert result.unreadable_count == total
    assert len(result.unreadable) == scanner.MAX_RECORDED_UNREADABLE
    assert result.unreadable[0].path == "/r/f0"


def test_symlinks_are_skipped_and_counted():
    tree = {
        "/r": [
            FakeEntry("/r/real.bin", size=10),
            FakeEntry("/r/dirlink", is_dir=True, symlink=True),
            FakeEntry("/r/filelink", symlink=True, size=500),
        ],
        "/r/dirlink": [FakeEntry("/r/dirlink/huge.bin", size=10_000)],
    }
    result = scan_tree("/r", fake_scandir(tree))
    assert result.scanned_bytes == 10
    assert result.n_files == 1
    assert result.skipped_links == 2
    assert [node.path for node in result.nodes] == ["/r"]


def test_a_reparse_point_folder_that_is_not_a_symlink_is_skipped():
    tree = {
        "/r": [FakeEntry("/r/mount", is_dir=True, attributes=REPARSE_POINT)],
        "/r/mount": [FakeEntry("/r/mount/huge.bin", size=10_000)],
    }
    result = scan_tree("/r", fake_scandir(tree))
    assert result.scanned_bytes == 0
    assert result.skipped_links == 1
    assert [node.path for node in result.nodes] == ["/r"]


@pytest.mark.parametrize(
    ("attributes", "counted"),
    [
        (OFFLINE, False),
        (RECALL_ON_OPEN, False),
        (RECALL_ON_DATA_ACCESS, False),
        (RECALL_ON_DATA_ACCESS | REPARSE_POINT, False),
        (0x20, True),
        (0x80, True),
        (REPARSE_POINT, True),
        (0, True),
    ],
)
def test_files_only_in_the_cloud_count_as_files_but_not_as_bytes(attributes, counted):
    tree = {"/r": [FakeEntry("/r/f.bin", size=4000, attributes=attributes)]}
    result = scan_tree("/r", fake_scandir(tree))
    assert result.n_files == 1
    assert result.scanned_bytes == (4000 if counted else 0)


@pytest.mark.skipif(sys.platform != "win32", reason="las junctions son de Windows")
def test_a_junction_is_not_followed_and_a_loop_of_junctions_ends(tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "real.bin").write_bytes(b"x" * 10)
    _winapi.CreateJunction(str(outside), str(root / "to_outside"))
    _winapi.CreateJunction(str(root), str(root / "sub" / "loop"))
    result = scan_tree(str(root))
    assert result.scanned_bytes == 10
    assert result.skipped_links == 2
    assert set(by_path(result)) == {str(root), str(root / "sub")}


def test_real_symlinks_are_not_followed(tmp_path):
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
    result = scan_tree(str(root))
    assert result.scanned_bytes == 10
    assert result.n_files == 1
    assert result.skipped_links == 2


def test_the_real_scandir_is_the_default_without_calling_it():
    assert inspect.signature(scan_tree).parameters["scandir"].default is os.scandir


def test_skip_root_files_ignores_those_names_only_at_the_root(tmp_path):
    make_tree(
        tmp_path,
        {
            "pagefile.sys": 100,
            "Hiberfil.SYS": 50,
            "keep.bin": 10,
            "sub": {"pagefile.sys": 7},
        },
    )
    result = scan_tree(str(tmp_path), skip_root_files=("pagefile.sys", "HIBERFIL.sys"))
    assert result.scanned_bytes == 17
    assert result.n_files == 2
    assert by_path(result)[str(tmp_path / "sub")].size_bytes == 7


def test_without_skip_root_files_everything_counts(tmp_path):
    make_tree(tmp_path, {"pagefile.sys": 100, "keep.bin": 10})
    result = scan_tree(str(tmp_path))
    assert (result.scanned_bytes, result.n_files) == (110, 2)


def test_a_skipped_name_is_not_measured_nor_counted_as_unreadable_or_link():
    broken = PermissionError(13, "locked by the system")
    tree = {
        "/r": [
            FakeEntry("/r/pagefile.sys", size=999, stat_error=broken),
            FakeEntry("/r/a.bin", size=1),
        ]
    }
    result = scan_tree("/r", fake_scandir(tree), skip_root_files=["pagefile.sys"])
    assert result.scanned_bytes == 1
    assert result.n_files == 1
    assert result.unreadable_count == 0
    assert result.skipped_links == 0


def test_skip_root_files_is_keyword_only(tmp_path):
    with pytest.raises(TypeError):
        scan_tree(str(tmp_path), os.scandir, ("pagefile.sys",))
