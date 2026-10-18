"""Pruebas del servicio de escaneo: adaptadores falsos y la base en `tmp_path`."""

import inspect
import os
import sqlite3
from datetime import UTC, datetime

import pytest

from discoclaro.adapters import scan_checkpoint
from discoclaro.adapters.scanner import enumerate_with_scandir, scan_tree
from discoclaro.adapters.store import Store
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import (
    LargeFile,
    Node,
    ResumedSubtree,
    ScanResult,
    SystemFile,
    Volume,
)
from discoclaro.domain.unexplained import Unexplained
from discoclaro.platform import windows
from discoclaro.services import scan_service
from discoclaro.services.scan_service import (
    ScanError,
    keep_node,
    normalize_target,
    scan_target,
)

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
MIB = 1024 * 1024
VOLUMES = [Volume("C:", "C:\\", 1000, 400, 600), Volume("D:", "D:\\", 2000, 500, 1500)]


def fake_volumes():
    return list(VOLUMES)


class FakeScanner:
    """Un escáner falso que anota con qué se lo llamó."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.progress_calls = []
        self.resumes = []
        self.subtree_done_callbacks = []

    def __call__(
        self,
        path,
        skip_root_files=(),
        large_file_bytes=None,
        on_progress=None,
        resume=None,
        on_subtree_done=None,
    ):
        self.calls.append((path, tuple(skip_root_files)))
        self.resumes.append(resume)
        self.subtree_done_callbacks.append(on_subtree_done)
        if on_progress is not None:
            self.progress_calls.append(on_progress)
        if self.error:
            raise self.error
        return self.result


def make_result(
    nodes=None, unreadable_count=0, skipped_links=0, duplicate_bytes=0, large_files=()
):
    nodes = nodes or [Node("C:\\", None, 350, 7, 0)]
    return ScanResult(
        root=nodes[-1].path,
        nodes=tuple(nodes),
        unreadable_count=unreadable_count,
        unreadable=(),
        skipped_links=skipped_links,
        duplicate_bytes=duplicate_bytes,
        large_files=tuple(large_files),
    )


def ticks(*values):
    pending = list(values)
    return lambda: pending.pop(0)


def run(target, store, scanner=None, **overrides):
    options = {
        "volumes": fake_volumes,
        "scanner": scanner or FakeScanner(make_result()),
        "system_files": lambda root: [],
        "is_admin": lambda: False,
        "is_folder": lambda path: True,
        "canonical_path": lambda path: path,
        "now": lambda: T0,
        "monotonic": ticks(0.0, 1.0),
    }
    options.update(overrides)
    return scan_target(target, store, **options)


@pytest.fixture
def store(tmp_path):
    with Store.open(tmp_path / "test.db") as opened:
        yield opened


def test_a_volume_is_scanned_with_its_system_files_measured_apart(store):
    scanner = FakeScanner(make_result())
    asked = []

    def system_files(root):
        asked.append(root)
        return [SystemFile("C:\\hiberfil.sys", 300), SystemFile("C:\\swapfile.sys", 40)]

    summary = run("c:", store, scanner, system_files=system_files)
    assert scanner.calls == [("C:\\", windows.SYSTEM_FILE_NAMES)]
    assert asked == ["C:\\"]
    assert summary.is_volume
    assert summary.target == "C:\\"
    snapshot = summary.snapshot
    assert snapshot.volume == "C:"
    assert (snapshot.total_bytes, snapshot.used_bytes, snapshot.free_bytes) == (
        1000,
        400,
        600,
    )
    assert snapshot.scanned_bytes == 350
    assert snapshot.system_files_bytes == 340
    assert snapshot.id is not None
    assert store.get_snapshot(snapshot.id) == snapshot
    assert [file.size_bytes for file in summary.system_files] == [300, 40]


def test_a_folder_is_a_partial_scan_named_after_its_path(store):
    scanner = FakeScanner(make_result([Node("D:\\data\\sub", None, 10, 1, 0)]))
    asked = []

    def system_files(root):
        asked.append(root)
        return []

    summary = run("D:/data/sub/", store, scanner, system_files=system_files)
    assert scanner.calls == [("D:\\data\\sub", ())]
    assert asked == []
    assert not summary.is_volume
    snapshot = summary.snapshot
    assert snapshot.volume == "D:\\data\\sub"
    assert (snapshot.total_bytes, snapshot.used_bytes, snapshot.free_bytes) == (
        2000,
        500,
        1500,
    )
    assert snapshot.system_files_bytes == 0
    assert store.latest_snapshot("D:") is None


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("C:", "C:\\"),
        ("c:", "c:\\"),
        ("C:\\", "C:\\"),
        ("C:/", "C:\\"),
        ("D:/data/sub/", "D:\\data\\sub"),
        ("D:\\a\\..\\b", "D:\\b"),
        ("D:\\a\\.\\b", "D:\\a\\b"),
    ],
)
def test_the_target_is_normalized(target, expected):
    assert normalize_target(target) == expected


@pytest.mark.parametrize("target", ["Z:\\x", "Z:", "\\\\server\\share\\x"])
def test_only_fixed_disks_are_accepted(store, target):
    scanner = FakeScanner(make_result())
    with pytest.raises(ScanError) as error:
        run(target, store, scanner)
    assert error.value.kind == "not_fixed_disk"
    assert scanner.calls == []
    assert store.snapshots() == []


@pytest.mark.parametrize("failure", [FileNotFoundError(), NotADirectoryError()])
def test_a_missing_folder_or_a_file_is_reported_and_nothing_is_saved(store, failure):
    with pytest.raises(ScanError) as error:
        run("D:\\nope", store, FakeScanner(error=failure))
    assert error.value.kind == "not_a_folder"
    assert error.value.target == "D:\\nope"
    assert store.snapshots() == []


def test_the_target_must_be_an_existing_folder_or_nothing_is_scanned(store):
    asked = []

    def is_folder(path):
        asked.append(path)
        return False

    scanner = FakeScanner(make_result())
    with pytest.raises(ScanError) as error:
        run("d:/missing", store, scanner, is_folder=is_folder)
    assert error.value.kind == "not_a_folder"
    assert error.value.target == "D:\\missing"
    assert asked == ["D:\\missing"]
    assert scanner.calls == []
    assert store.snapshots() == []


def test_a_null_character_is_reported_without_reaching_the_disk(store):
    with pytest.raises(ScanError) as error:
        run("D:\\a\x00b", store, is_folder=os.path.isdir)
    assert error.value.kind == "not_a_folder"


@pytest.mark.parametrize(
    ("target", "path"),
    [("c:", "C:\\"), ("c:\\", "C:\\"), ("d:/data/sub", "D:\\data\\sub")],
)
def test_the_drive_letter_always_comes_from_the_system_name(store, target, path):
    scanner = FakeScanner(make_result())
    summary = run(target, store, scanner)
    assert scanner.calls[0][0] == path
    assert summary.target == path


def test_the_canonical_path_names_the_folder_however_it_was_typed(store):
    scanner = FakeScanner(make_result())
    summary = run(
        "d:\\universidad\\data",
        store,
        scanner,
        canonical_path=lambda path: "D:\\UNIVERSIDAD\\Data",
    )
    assert scanner.calls == [("D:\\UNIVERSIDAD\\Data", ())]
    assert summary.target == "D:\\UNIVERSIDAD\\Data"
    assert summary.snapshot.volume == "D:\\UNIVERSIDAD\\Data"


def test_a_link_to_another_fixed_disk_is_scanned_where_it_really_is(store):
    scanner = FakeScanner(make_result())
    summary = run(
        "D:\\link", store, scanner, canonical_path=lambda path: "C:\\real\\place"
    )
    assert scanner.calls == [("C:\\real\\place", ())]
    assert summary.snapshot.volume == "C:\\real\\place"
    assert summary.snapshot.total_bytes == 1000


def test_a_link_that_leads_outside_the_fixed_disks_is_refused(store):
    scanner = FakeScanner(make_result())
    with pytest.raises(ScanError) as error:
        run(
            "D:\\link",
            store,
            scanner,
            canonical_path=lambda path: "\\\\server\\share\\x",
        )
    assert error.value.kind == "not_fixed_disk"
    assert scanner.calls == []
    assert store.snapshots() == []


def test_an_interrupted_scan_saves_nothing(store):
    with pytest.raises(KeyboardInterrupt):
        run("C:", store, FakeScanner(error=KeyboardInterrupt()))
    assert store.snapshots() == []


def test_the_start_time_and_the_duration_come_from_the_injected_clocks(store):
    summary = run("C:", store, now=lambda: T0, monotonic=ticks(100.0, 102.5))
    assert summary.snapshot.taken_at == T0
    assert summary.snapshot.duration_s == 2.5


@pytest.mark.parametrize("admin", [True, False])
def test_the_admin_flag_is_what_the_system_says(store, admin):
    summary = run("C:", store, is_admin=lambda: admin)
    assert summary.snapshot.is_admin is admin


def test_a_volume_scan_reports_its_unexplained_space(store):
    summary = run("C:", store)
    assert summary.unexplained == Unexplained(
        size_bytes=50, percent_of_used=12.5, exceeds_threshold=True
    )


def test_the_system_files_are_not_counted_as_unexplained(store):
    def system_files(root):
        return [SystemFile("C:\\hiberfil.sys", 40)]

    summary = run("C:", store, system_files=system_files)
    assert summary.unexplained == Unexplained(
        size_bytes=10, percent_of_used=2.5, exceeds_threshold=False
    )


def test_hard_link_duplicates_reach_the_snapshot_and_count_against_what_was_scanned(
    store,
):
    summary = run("C:", store, FakeScanner(make_result(duplicate_bytes=100)))
    assert summary.snapshot.duplicate_bytes == 100
    assert store.get_snapshot(summary.snapshot.id).duplicate_bytes == 100
    assert summary.unexplained == Unexplained(
        size_bytes=150, percent_of_used=37.5, exceeds_threshold=True
    )


def test_the_unexplained_threshold_can_be_changed(store):
    summary = run("C:", store, threshold_percent=20.0)
    assert summary.unexplained.size_bytes == 50
    assert summary.unexplained.exceeds_threshold is False


def test_a_partial_scan_has_no_unexplained_space(store):
    scanner = FakeScanner(make_result([Node("D:\\data", None, 10, 1, 0)]))
    assert run("D:\\data", store, scanner).unexplained is None


def test_counts_and_unreadable_paths_reach_the_summary_and_the_snapshot(store):
    result = make_result(unreadable_count=5, skipped_links=3)
    summary = run("C:", store, FakeScanner(result))
    assert summary.folders == 1
    assert summary.files == 7
    assert summary.skipped_links == 3
    assert summary.snapshot.unreadable_count == 5


@pytest.mark.parametrize(
    ("depth", "size", "kept"),
    [
        (0, 0, True),
        (3, 0, True),
        (4, 0, False),
        (4, 50 * MIB - 1, False),
        (4, 50 * MIB, True),
        (9, 10 * 1024 * MIB, True),
    ],
)
def test_keep_node_keeps_shallow_or_big_folders(depth, size, kept):
    assert keep_node(Node("C:\\x", "C:\\", size, 1, depth)) is kept


def test_large_files_are_saved_alongside_the_nodes(store):
    large_files = [LargeFile("C:\\model.gguf", 100)]
    scanner = FakeScanner(make_result(large_files=large_files))
    summary = run("C:", store, scanner)
    assert summary.large_files_saved == 1
    assert store.large_files(summary.snapshot.id) == large_files


def test_no_large_files_means_nothing_extra_is_saved(store):
    summary = run("C:", store, FakeScanner(make_result()))
    assert summary.large_files_saved == 0
    assert store.large_files(summary.snapshot.id) == []


def test_the_scanner_is_called_with_the_large_file_threshold(store):
    seen = []

    def scanner(path, **kwargs):
        seen.append(kwargs.get("large_file_bytes"))
        return make_result()

    run("C:", store, scanner)
    assert seen == [scan_service.LARGE_FILE_MIN_BYTES]
    # El mismo número decide qué carpeta se guarda y qué archivo se guarda aparte
    # (ver el comentario junto a KEEP_MIN_BYTES en services/scan_service.py).
    assert scan_service.KEEP_MIN_BYTES == scan_service.LARGE_FILE_MIN_BYTES


def test_only_the_folders_that_pass_the_rule_are_saved(store):
    nodes = [
        Node("C:\\a\\b\\c\\d\\small", "C:\\a\\b\\c\\d", 10, 1, 5),
        Node("C:\\a\\b\\c\\d\\big", "C:\\a\\b\\c\\d", 60 * MIB, 1, 5),
        Node("C:\\a\\b\\c\\d", "C:\\a\\b\\c", 60 * MIB + 10, 2, 4),
        Node("C:\\a", "C:\\", 70 * MIB, 3, 1),
        Node("C:\\", None, 80 * MIB, 4, 0),
    ]
    summary = run("C:", store, FakeScanner(make_result(nodes)))
    assert summary.folders == 5
    assert summary.nodes_saved == 4
    saved = {node.path for node in store.nodes(summary.snapshot.id)}
    assert saved == {
        "C:\\a\\b\\c\\d\\big",
        "C:\\a\\b\\c\\d",
        "C:\\a",
        "C:\\",
    }


def test_real_resume_does_not_rescan_a_finished_top_level_folder(tmp_path):
    """De punta a punta con un árbol real y el escáner de verdad, sin fakes."""
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "x.bin").write_bytes(b"1" * 100)
    (root / "b").mkdir()
    (root / "b" / "y.bin").write_bytes(b"2" * 50)

    checkpoint_path = tmp_path / "checkpoint.json"
    touched = []
    should_fail = {"b": True}

    def flaky_enumerator(path):
        touched.append(path)
        if path == str(root / "b") and should_fail["b"]:
            should_fail["b"] = False
            raise KeyboardInterrupt
        # El recorrido usa una pila (LIFO): lo último que se lista es lo primero que
        # se visita. Se ordena al revés para que "a" se mida antes que "b".
        return sorted(
            enumerate_with_scandir(path), key=lambda entry: entry.name, reverse=True
        )

    def flaky_scanner(path, **kwargs):
        return scan_tree(path, flaky_enumerator, **kwargs)

    def do_scan():
        with Store.open(tmp_path / "test.db") as store:
            return scan_target(
                str(root),
                store,
                volumes=fake_volumes,
                scanner=flaky_scanner,
                system_files=lambda r: [],
                is_admin=lambda: False,
                is_folder=lambda p: True,
                canonical_path=lambda p: p,
                now=lambda: T0,
                monotonic=ticks(0.0, 1.0),
                checkpoint_path=checkpoint_path,
            )

    with pytest.raises(KeyboardInterrupt):
        do_scan()
    assert str(root / "a") in touched
    assert str(root / "b") in touched

    touched.clear()
    summary = do_scan()
    assert str(root / "a") not in touched
    assert str(root / "b") in touched
    assert summary.resumed_folders == 1
    with Store.open(tmp_path / "test.db") as store:
        nodes = {node.path: node for node in store.nodes(summary.snapshot.id)}
    assert nodes[str(root)].size_bytes == 150
    assert nodes[str(root / "a")].size_bytes == 100
    assert nodes[str(root / "b")].size_bytes == 50
    assert not checkpoint_path.exists()


def test_run_scan_opens_the_database_in_the_data_folder_and_closes_it(
    tmp_path, monkeypatch
):
    seen = {}

    def fake_scan_target(target, store, **kwargs):
        seen["target"] = target
        seen["store"] = store
        store.snapshots()
        return "summary"

    monkeypatch.setattr(scan_service, "scan_target", fake_scan_target)
    data_dir = tmp_path / "new" / "folder"
    assert scan_service.run_scan("C:", data_dir) == "summary"
    assert seen["target"] == "C:"
    assert (data_dir / "discoclaro.db").exists()
    with pytest.raises(sqlite3.ProgrammingError):
        seen["store"].snapshots()


def test_run_scan_uses_the_default_data_folder_when_none_is_given(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(windows, "default_data_dir", lambda: tmp_path / "default")
    monkeypatch.setattr(scan_service, "scan_target", lambda target, store, **kw: "ok")
    assert scan_service.run_scan("C:") == "ok"
    assert (tmp_path / "default" / "discoclaro.db").exists()


class InterruptingScanner:
    """Avisa una carpeta de primer nivel terminada y después se corta, sin terminar."""

    def __init__(self, subtree_path, subtree):
        self.subtree_path = subtree_path
        self.subtree = subtree
        self.resumes = []

    def __call__(
        self,
        path,
        skip_root_files=(),
        large_file_bytes=None,
        on_progress=None,
        resume=None,
        on_subtree_done=None,
    ):
        self.resumes.append(resume)
        if on_subtree_done is not None:
            on_subtree_done(self.subtree_path, self.subtree)
        raise KeyboardInterrupt()


def test_an_interruption_leaves_a_checkpoint_that_is_not_lost(tmp_path, store):
    checkpoint_path = tmp_path / "checkpoint.json"
    subtree = ResumedSubtree(nodes=(Node("C:\\a", "C:\\", 10, 1, 1),))
    with pytest.raises(KeyboardInterrupt):
        run(
            "C:",
            store,
            InterruptingScanner("C:\\a", subtree),
            checkpoint_path=checkpoint_path,
        )
    assert store.snapshots() == []
    assert scan_checkpoint.read_checkpoint(checkpoint_path, "C:\\") == {
        "C:\\a": subtree
    }


def test_a_checkpoint_is_read_and_passed_to_the_scanner(tmp_path, store):
    checkpoint_path = tmp_path / "checkpoint.json"
    subtree = ResumedSubtree(nodes=(Node("C:\\a", "C:\\", 10, 1, 1),))
    scan_checkpoint.write_checkpoint(checkpoint_path, "C:\\", {"C:\\a": subtree})
    scanner = FakeScanner(make_result())
    summary = run("C:", store, scanner, checkpoint_path=checkpoint_path)
    assert scanner.resumes == [{"C:\\a": subtree}]
    assert summary.resumed_folders == 1


def test_restart_ignores_an_existing_checkpoint(tmp_path, store):
    checkpoint_path = tmp_path / "checkpoint.json"
    subtree = ResumedSubtree(nodes=(Node("C:\\a", "C:\\", 10, 1, 1),))
    scan_checkpoint.write_checkpoint(checkpoint_path, "C:\\", {"C:\\a": subtree})
    scanner = FakeScanner(make_result())
    summary = run("C:", store, scanner, checkpoint_path=checkpoint_path, restart=True)
    assert scanner.resumes == [{}]
    assert summary.resumed_folders == 0


def test_the_checkpoint_is_cleared_after_a_successful_save(tmp_path, store):
    checkpoint_path = tmp_path / "checkpoint.json"
    scan_checkpoint.write_checkpoint(checkpoint_path, "C:\\", {})
    run("C:", store, FakeScanner(make_result()), checkpoint_path=checkpoint_path)
    assert not checkpoint_path.exists()


def test_without_a_checkpoint_path_nothing_is_read_or_written(store):
    scanner = FakeScanner(make_result())
    run("C:", store, scanner)
    assert scanner.resumes == [{}]
    assert scanner.subtree_done_callbacks == [None]


def test_on_checkpoint_fires_once_per_subtree_done(tmp_path, store):
    checkpoint_path = tmp_path / "checkpoint.json"
    subtree = ResumedSubtree(nodes=(Node("C:\\a", "C:\\", 10, 1, 1),))

    class TwoSubtreeScanner:
        def __call__(
            self,
            path,
            skip_root_files=(),
            large_file_bytes=None,
            on_progress=None,
            resume=None,
            on_subtree_done=None,
        ):
            on_subtree_done("C:\\a", subtree)
            on_subtree_done("C:\\b", subtree)
            return make_result()

    seen = []
    run(
        "C:",
        store,
        TwoSubtreeScanner(),
        checkpoint_path=checkpoint_path,
        on_checkpoint=lambda: seen.append(1),
    )
    assert seen == [1, 1]


def test_the_real_adapters_are_the_defaults_without_calling_them():
    parameters = inspect.signature(scan_target).parameters
    assert parameters["volumes"].default is list_volumes
    assert parameters["scanner"].default is scan_tree
    assert parameters["system_files"].default is windows.measure_system_files
    assert parameters["is_admin"].default is windows.is_admin
    assert parameters["is_folder"].default is os.path.isdir
    assert parameters["canonical_path"].default is os.path.realpath
    assert parameters["keep"].default is keep_node
    assert parameters["read_checkpoint"].default is scan_checkpoint.read_checkpoint
    assert parameters["write_checkpoint"].default is scan_checkpoint.write_checkpoint
    assert parameters["clear_checkpoint"].default is scan_checkpoint.clear_checkpoint


def test_scan_error_carries_the_message_key_and_the_target():
    error = ScanError("not_a_folder", "D:\\x")
    assert (error.kind, error.target) == ("not_a_folder", "D:\\x")
