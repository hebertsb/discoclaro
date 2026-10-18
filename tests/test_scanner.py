"""Pruebas de la lógica del escáner con enumeradores falsos: sin tocar disco."""

import contextlib
import inspect
import sys
import types

import pytest

from discoclaro.adapters import scanner
from discoclaro.adapters.scanner import enumerate_with_scandir, scan_tree
from discoclaro.domain.models import (
    DirectoryEntry,
    LargeFile,
    Node,
    ResumedSubtree,
    Unreadable,
)
from discoclaro.platform import windows_listing

REPARSE_POINT = 0x400
OFFLINE = 0x1000
RECALL_ON_OPEN = 0x40000
RECALL_ON_DATA_ACCESS = 0x400000


def name_of(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def file(path, size=0, file_id=None):
    return DirectoryEntry(name_of(path), path, "file", size, file_id)


def folder(path):
    return DirectoryEntry(name_of(path), path, "folder")


def link(path):
    return DirectoryEntry(name_of(path), path, "link")


def unreadable(path, error="PermissionError"):
    return DirectoryEntry(name_of(path), path, "unreadable", error=error)


def ident(number):
    return bytes([number]) * 16


def fake_enumerator(tree):
    """Un enumerador falso: `tree` da a cada ruta su lista de elementos o un error."""

    def enumerate_directory(path):
        listing = tree[path]
        if isinstance(listing, Exception):
            raise listing
        return iter(listing)

    return enumerate_directory


def by_path(result):
    return {node.path: node for node in result.nodes}


SIMPLE_TREE = {
    "/r": [file("/r/a.bin", 100), folder("/r/docs"), folder("/r/empty")],
    "/r/docs": [file("/r/docs/b", 10), file("/r/docs/c", 20), folder("/r/docs/deep")],
    "/r/docs/deep": [file("/r/docs/deep/d", 5)],
    "/r/empty": [],
}


def test_sizes_counts_depth_and_parents():
    result = scan_tree("/r", fake_enumerator(SIMPLE_TREE))
    expected = {
        "/r": (135, 4, 0, None),
        "/r/docs": (35, 3, 1, "/r"),
        "/r/docs/deep": (5, 1, 2, "/r/docs"),
        "/r/empty": (0, 0, 1, "/r"),
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
    assert (result.scanned_bytes, result.n_files, result.root) == (135, 4, "/r")
    assert result.duplicate_bytes == 0


def test_children_are_emitted_before_their_parents_and_the_root_is_last():
    result = scan_tree("/r", fake_enumerator(SIMPLE_TREE))
    paths = [node.path for node in result.nodes]
    assert paths[-1] == "/r"
    for node in result.nodes:
        if node.parent_path is not None:
            assert paths.index(node.path) < paths.index(node.parent_path)


def test_an_empty_root_gives_a_single_zero_node():
    result = scan_tree("/r", fake_enumerator({"/r": []}))
    assert len(result.nodes) == 1
    assert (result.scanned_bytes, result.n_files) == (0, 0)
    assert (result.unreadable_count, result.skipped_links) == (0, 0)


@pytest.mark.parametrize("error", [FileNotFoundError(), NotADirectoryError()])
def test_a_missing_root_or_a_file_as_root_raises(error):
    with pytest.raises(type(error)):
        scan_tree("/r", fake_enumerator({"/r": error}))


def test_a_very_deep_tree_does_not_hit_the_recursion_limit():
    depth = sys.getrecursionlimit() * 3
    tree = {}
    path = "/r"
    for _ in range(depth):
        child = f"{path}/d"
        tree[path] = [folder(child), file(f"{path}/f.bin", 1)]
        path = child
    tree[path] = []
    result = scan_tree("/r", fake_enumerator(tree))
    assert len(result.nodes) == depth + 1
    assert result.scanned_bytes == depth
    assert result.nodes[0].depth == depth
    assert result.nodes[-1].path == "/r"


def test_an_unreadable_folder_is_counted_recorded_and_the_rest_is_scanned():
    tree = {
        "/r": [file("/r/ok.bin", 10), folder("/r/locked"), folder("/r/open")],
        "/r/locked": PermissionError(13, "denied"),
        "/r/open": [file("/r/open/a.bin", 7)],
    }
    result = scan_tree("/r", fake_enumerator(tree))
    nodes = by_path(result)
    assert nodes["/r/locked"].size_bytes == 0
    assert nodes["/r/open"].size_bytes == 7
    assert result.scanned_bytes == 17
    assert result.unreadable_count == 1
    assert result.unreadable == (Unreadable("/r/locked", "PermissionError"),)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "denied"),
        FileNotFoundError(2, "vanished"),
        OSError(5, "io error"),
    ],
)
def test_the_reason_is_the_stable_name_of_the_error(error):
    tree = {"/r": [folder("/r/sub")], "/r/sub": error}
    result = scan_tree("/r", fake_enumerator(tree))
    assert result.unreadable == (Unreadable("/r/sub", type(error).__name__),)


def test_an_unreadable_root_is_recorded_not_raised():
    result = scan_tree("/r", fake_enumerator({"/r": PermissionError(13, "denied")}))
    assert len(result.nodes) == 1
    assert result.scanned_bytes == 0
    assert result.unreadable == (Unreadable("/r", "PermissionError"),)


def test_an_error_in_the_middle_of_a_listing_keeps_what_was_read_and_is_recorded():
    def enumerate_directory(path):
        yield file("/r/ok.bin", 5)
        raise OSError(5, "io error")

    result = scan_tree("/r", enumerate_directory)
    assert result.scanned_bytes == 5
    assert result.unreadable == (Unreadable("/r", "OSError"),)


def test_an_entry_that_cannot_be_read_is_recorded_and_the_others_count():
    tree = {
        "/r": [
            file("/r/ok.bin", 5),
            unreadable("/r/bad.bin", "PermissionError"),
            file("/r/ok2.bin", 6),
        ]
    }
    result = scan_tree("/r", fake_enumerator(tree))
    assert (result.scanned_bytes, result.n_files) == (11, 2)
    assert result.unreadable == (Unreadable("/r/bad.bin", "PermissionError"),)


def test_an_entry_without_an_error_name_is_recorded_as_a_generic_os_error():
    entry = DirectoryEntry("x", "/r/x", "unreadable")
    result = scan_tree("/r", fake_enumerator({"/r": [entry]}))
    assert result.unreadable == (Unreadable("/r/x", "OSError"),)


def test_all_unreadable_paths_are_counted_but_only_the_first_ones_are_kept():
    total = scanner.MAX_RECORDED_UNREADABLE + 500
    entries = [unreadable(f"/r/f{index}", "OSError") for index in range(total)]
    result = scan_tree("/r", fake_enumerator({"/r": entries}))
    assert result.unreadable_count == total
    assert len(result.unreadable) == scanner.MAX_RECORDED_UNREADABLE
    assert result.unreadable[0].path == "/r/f0"


def test_links_are_counted_and_never_entered():
    tree = {
        "/r": [file("/r/real.bin", 10), link("/r/dirlink"), link("/r/filelink")],
    }
    result = scan_tree("/r", fake_enumerator(tree))
    assert result.scanned_bytes == 10
    assert result.n_files == 1
    assert result.skipped_links == 2
    assert [node.path for node in result.nodes] == ["/r"]


def test_entries_of_unknown_kind_are_ignored():
    other = DirectoryEntry("raro", "/r/raro", "other")
    result = scan_tree("/r", fake_enumerator({"/r": [other, file("/r/a", 3)]}))
    assert (result.scanned_bytes, result.n_files) == (3, 1)
    assert (result.unreadable_count, result.skipped_links) == (0, 0)


def test_a_hard_link_adds_to_every_folder_but_counts_once_in_the_duplicates():
    tree = {
        "/r": [file("/r/a", 100, ident(1)), folder("/r/b")],
        "/r/b": [
            file("/r/b/a2", 100, ident(1)),
            file("/r/b/c", 50, ident(2)),
            file("/r/b/c2", 50, ident(2)),
        ],
    }
    result = scan_tree("/r", fake_enumerator(tree))
    nodes = by_path(result)
    assert nodes["/r/b"].size_bytes == 200
    assert nodes["/r"].size_bytes == 300
    assert result.n_files == 4
    assert result.duplicate_bytes == 150


def test_three_names_for_the_same_content_make_two_repeats():
    tree = {
        "/r": [
            file("/r/one", 40, ident(7)),
            file("/r/two", 40, ident(7)),
            file("/r/three", 40, ident(7)),
        ]
    }
    result = scan_tree("/r", fake_enumerator(tree))
    assert (result.scanned_bytes, result.duplicate_bytes) == (120, 80)


def test_files_without_an_id_are_never_taken_for_duplicates():
    tree = {"/r": [file("/r/a", 10), file("/r/b", 10), file("/r/c", 10)]}
    result = scan_tree("/r", fake_enumerator(tree))
    assert (result.scanned_bytes, result.duplicate_bytes) == (30, 0)


def test_on_progress_fires_once_per_folder_with_running_totals():
    # "/r" se abre antes de que empiece el bucle principal, así que su archivo
    # suelto (loose.bin) ya cuenta en el primer aviso, aunque "/r" cierre después.
    tree = {
        "/r": [folder("/r/a"), file("/r/loose.bin", 5)],
        "/r/a": [file("/r/a/x", 10)],
    }
    seen = []
    scan_tree("/r", fake_enumerator(tree), on_progress=seen.append)
    assert [(p.folders, p.files, p.bytes_scanned, p.current_path) for p in seen] == [
        (1, 2, 15, "/r/a"),
        (2, 2, 15, "/r"),
    ]


def test_a_known_top_level_folder_is_not_read_again():
    tree = {
        "/r": [folder("/r/a"), folder("/r/b")],
        "/r/b": [file("/r/b/y", 20)],
        # "/r/a" no está: si algo intentara leerla, esto lanzaría KeyError.
    }
    known_a = ResumedSubtree(nodes=(Node("/r/a", "/r", 10, 1, 1),))
    result = scan_tree("/r", fake_enumerator(tree), resume={"/r/a": known_a})
    nodes = by_path(result)
    assert nodes["/r/a"].size_bytes == 10
    assert nodes["/r/b"].size_bytes == 20
    assert nodes["/r"].size_bytes == 30
    assert result.n_files == 2


def test_a_resumed_folder_is_never_enumerated():
    calls = []
    tree = {"/r": [folder("/r/a")]}

    def enumerate_directory(path):
        calls.append(path)
        return iter(tree[path])

    known_a = ResumedSubtree(nodes=(Node("/r/a", "/r", 10, 1, 1),))
    scan_tree("/r", enumerate_directory, resume={"/r/a": known_a})
    assert calls == ["/r"]


def test_a_resumed_subtree_keeps_its_descendant_nodes_as_they_were():
    tree = {"/r": [folder("/r/a"), folder("/r/b")], "/r/b": [file("/r/b/y", 1)]}
    known_descendant = Node("/r/a/inner", "/r/a", 7, 1, 2)
    known_root = Node("/r/a", "/r", 7, 1, 1)
    known_a = ResumedSubtree(nodes=(known_descendant, known_root))
    result = scan_tree("/r", fake_enumerator(tree), resume={"/r/a": known_a})
    nodes = by_path(result)
    assert nodes["/r/a/inner"] == known_descendant
    assert nodes["/r/a"].size_bytes == 7
    assert nodes["/r"].size_bytes == 8


def test_on_subtree_done_fires_only_for_freshly_scanned_top_level_folders():
    tree = {
        "/r": [folder("/r/a"), folder("/r/b")],
        "/r/b": [file("/r/b/y", 20, ident(1)), file("/r/b/z", 20, ident(1))],
    }
    known_a = ResumedSubtree(nodes=(Node("/r/a", "/r", 10, 1, 1),), duplicate_bytes=5)
    done = []
    result = scan_tree(
        "/r",
        fake_enumerator(tree),
        resume={"/r/a": known_a},
        on_subtree_done=lambda path, subtree: done.append((path, subtree)),
    )
    assert [path for path, _ in done] == ["/r/b"]
    [(_, subtree)] = done
    assert subtree.nodes[-1].path == "/r/b"
    assert subtree.duplicate_bytes == 20
    assert result.duplicate_bytes == 25


def test_resumed_subtree_counts_carry_into_the_final_totals():
    tree = {"/r": [folder("/r/a")]}
    known_a = ResumedSubtree(
        nodes=(Node("/r/a", "/r", 0, 0, 1),), unreadable_count=3, skipped_links=2
    )
    result = scan_tree("/r", fake_enumerator(tree), resume={"/r/a": known_a})
    assert (result.unreadable_count, result.skipped_links) == (3, 2)


def test_a_file_at_or_above_the_threshold_is_recorded_as_a_large_file():
    tree = {"/r": [file("/r/big.bin", 100), file("/r/small.bin", 99)]}
    result = scan_tree("/r", fake_enumerator(tree), large_file_bytes=100)
    assert result.large_files == (LargeFile("/r/big.bin", 100),)


def test_the_threshold_defaults_to_the_domain_constant():
    from discoclaro.domain.models import LARGE_FILE_MIN_BYTES

    tree = {"/r": [file("/r/big.bin", LARGE_FILE_MIN_BYTES)]}
    result = scan_tree("/r", fake_enumerator(tree))
    assert result.large_files == (LargeFile("/r/big.bin", LARGE_FILE_MIN_BYTES),)


def test_large_files_from_several_folders_are_all_collected():
    tree = {
        "/r": [folder("/r/a"), file("/r/root.bin", 100)],
        "/r/a": [file("/r/a/x.bin", 200)],
    }
    result = scan_tree("/r", fake_enumerator(tree), large_file_bytes=100)
    assert set(result.large_files) == {
        LargeFile("/r/root.bin", 100),
        LargeFile("/r/a/x.bin", 200),
    }


def test_large_files_are_not_deduplicated_by_hard_link():
    tree = {"/r": [file("/r/a.bin", 100, ident(1)), file("/r/b.bin", 100, ident(1))]}
    result = scan_tree("/r", fake_enumerator(tree), large_file_bytes=100)
    assert set(result.large_files) == {
        LargeFile("/r/a.bin", 100),
        LargeFile("/r/b.bin", 100),
    }
    assert result.duplicate_bytes == 100


def test_a_resumed_subtrees_large_files_carry_into_the_final_result():
    tree = {"/r": [folder("/r/a")]}
    known_a = ResumedSubtree(
        nodes=(Node("/r/a", "/r", 100, 1, 1),),
        large_files=(LargeFile("/r/a/big.bin", 100),),
    )
    result = scan_tree(
        "/r", fake_enumerator(tree), large_file_bytes=100, resume={"/r/a": known_a}
    )
    assert result.large_files == (LargeFile("/r/a/big.bin", 100),)


def test_on_subtree_done_only_carries_the_large_files_found_in_that_subtree():
    tree = {
        "/r": [folder("/r/a"), folder("/r/b")],
        "/r/b": [file("/r/b/big.bin", 100)],
    }
    known_a = ResumedSubtree(
        nodes=(Node("/r/a", "/r", 100, 1, 1),),
        large_files=(LargeFile("/r/a/big.bin", 100),),
    )
    done = []
    scan_tree(
        "/r",
        fake_enumerator(tree),
        large_file_bytes=100,
        resume={"/r/a": known_a},
        on_subtree_done=lambda path, subtree: done.append((path, subtree)),
    )
    [(path, subtree)] = done
    assert path == "/r/b"
    assert subtree.large_files == (LargeFile("/r/b/big.bin", 100),)


def test_different_ids_with_the_same_size_are_not_duplicates():
    tree = {"/r": [file("/r/a", 10, ident(1)), file("/r/b", 10, ident(2))]}
    assert scan_tree("/r", fake_enumerator(tree)).duplicate_bytes == 0


def test_skip_root_files_ignores_those_names_only_at_the_root_and_in_any_case():
    tree = {
        "/r": [
            file("/r/pagefile.sys", 100),
            file("/r/Hiberfil.SYS", 50),
            file("/r/keep.bin", 10),
            folder("/r/sub"),
        ],
        "/r/sub": [file("/r/sub/pagefile.sys", 7)],
    }
    result = scan_tree(
        "/r", fake_enumerator(tree), skip_root_files=("pagefile.sys", "HIBERFIL.sys")
    )
    assert (result.scanned_bytes, result.n_files) == (17, 2)
    assert by_path(result)["/r/sub"].size_bytes == 7


def test_without_skip_root_files_everything_counts():
    tree = {"/r": [file("/r/pagefile.sys", 100), file("/r/keep.bin", 10)]}
    result = scan_tree("/r", fake_enumerator(tree))
    assert (result.scanned_bytes, result.n_files) == (110, 2)


def test_a_skipped_name_counts_for_nothing_not_even_as_unreadable_or_duplicate():
    tree = {
        "/r": [
            unreadable("/r/pagefile.sys"),
            file("/r/hiberfil.sys", 999, ident(9)),
            file("/r/a", 1, ident(9)),
        ]
    }
    result = scan_tree(
        "/r", fake_enumerator(tree), skip_root_files=["pagefile.sys", "hiberfil.sys"]
    )
    assert (result.scanned_bytes, result.n_files) == (1, 1)
    assert (result.unreadable_count, result.duplicate_bytes) == (0, 0)


def test_skip_root_files_is_keyword_only():
    with pytest.raises(TypeError):
        scan_tree("/r", fake_enumerator({"/r": []}), ("pagefile.sys",))


def test_the_system_enumerator_is_used_when_none_is_given(monkeypatch):
    monkeypatch.setattr(
        scanner,
        "default_enumerator",
        lambda: fake_enumerator({"/r": [file("/r/a", 4)]}),
    )
    assert scan_tree("/r").scanned_bytes == 4
    assert (
        inspect.signature(scan_tree).parameters["enumerate_directory"].default is None
    )


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", windows_listing.enumerate_directory),
        ("linux", enumerate_with_scandir),
        ("darwin", enumerate_with_scandir),
    ],
)
def test_the_default_enumerator_depends_on_the_system(monkeypatch, platform, expected):
    monkeypatch.setattr(scanner, "sys", types.SimpleNamespace(platform=platform))
    assert scanner.default_enumerator() is expected


class FakeDirEntry:
    """Un `os.DirEntry` falso, con lo mínimo que usa el convertidor."""

    def __init__(
        self,
        path,
        *,
        is_dir=False,
        is_file=None,
        size=0,
        attributes=0,
        symlink=False,
        stat_error=None,
    ):
        self.path = path
        self.name = name_of(path)
        self._is_dir = is_dir
        self._is_file = (not is_dir and not symlink) if is_file is None else is_file
        self._size = size
        self._attributes = attributes
        self._symlink = symlink
        self._stat_error = stat_error

    def is_symlink(self):
        return self._symlink

    def is_dir(self, *, follow_symlinks=True):
        return self._is_dir

    def is_file(self, *, follow_symlinks=True):
        return self._is_file

    def stat(self, *, follow_symlinks=True):
        if self._stat_error:
            raise self._stat_error
        return types.SimpleNamespace(
            st_size=self._size, st_file_attributes=self._attributes
        )


def convert(entry):
    return scanner._from_dir_entry(entry)


def test_a_normal_file_keeps_its_logical_size_and_has_no_id():
    result = convert(FakeDirEntry("/r/a.bin", size=123))
    assert result == DirectoryEntry("a.bin", "/r/a.bin", "file", 123)


def test_a_folder_is_a_folder():
    assert convert(FakeDirEntry("/r/d", is_dir=True)).kind == "folder"


def test_symlinks_and_reparse_point_folders_are_links():
    assert convert(FakeDirEntry("/r/l", symlink=True)).kind == "link"
    assert convert(FakeDirEntry("/r/l", is_dir=True, symlink=True)).kind == "link"
    reparse = FakeDirEntry("/r/m", is_dir=True, attributes=REPARSE_POINT)
    assert convert(reparse).kind == "link"


def test_something_that_is_neither_a_file_nor_a_folder_is_other():
    assert convert(FakeDirEntry("/r/pipe", is_file=False)).kind == "other"


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
def test_files_only_in_the_cloud_have_no_size_with_scandir(attributes, counted):
    entry = FakeDirEntry("/r/f.bin", size=4000, attributes=attributes)
    assert convert(entry).size_bytes == (4000 if counted else 0)


def test_an_entry_that_cannot_be_measured_is_unreadable_with_its_error_name():
    entry = FakeDirEntry("/r/x", stat_error=PermissionError(13, "denied"))
    result = convert(entry)
    assert (result.kind, result.error, result.path) == (
        "unreadable",
        "PermissionError",
        "/r/x",
    )


def test_scandir_listing_converts_every_entry_and_closes_the_listing():
    closed = []

    @contextlib.contextmanager
    def fake_scandir(path):
        assert path == "/r"
        try:
            yield iter(
                [FakeDirEntry("/r/a", size=1), FakeDirEntry("/r/d", is_dir=True)]
            )
        finally:
            closed.append(path)

    result = list(enumerate_with_scandir("/r", fake_scandir))
    assert [(e.name, e.kind) for e in result] == [("a", "file"), ("d", "folder")]
    assert closed == ["/r"]


def test_scandir_listing_lets_a_failure_to_open_the_folder_through():
    def broken(path):
        raise PermissionError(13, "denied")

    with pytest.raises(PermissionError):
        list(enumerate_with_scandir("/r", broken))
