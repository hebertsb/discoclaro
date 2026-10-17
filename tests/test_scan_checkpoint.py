"""Pruebas del punto de control de un escaneo: siempre en `tmp_path`."""

import os
import sys

import pytest

from discoclaro.adapters import scan_checkpoint
from discoclaro.domain.models import Node, ResumedSubtree
from discoclaro.platform import windows


@pytest.fixture
def path(tmp_path):
    return tmp_path / "scan-checkpoint.json"


def test_round_trips_a_checkpoint_with_several_subtrees(path):
    subtrees = {
        "C:\\Users": ResumedSubtree(
            nodes=(
                Node("C:\\Users\\x\\deep", "C:\\Users\\x", 10, 1, 2),
                Node("C:\\Users\\x", "C:\\Users", 10, 1, 1),
                Node("C:\\Users", None, 10, 1, 0),
            ),
            duplicate_bytes=3,
            unreadable_count=2,
            skipped_links=1,
        ),
        "C:\\Windows": ResumedSubtree(nodes=(Node("C:\\Windows", None, 7, 2, 0),)),
    }
    scan_checkpoint.write_checkpoint(path, "C:", subtrees)
    assert scan_checkpoint.read_checkpoint(path, "C:") == subtrees


def test_a_missing_checkpoint_is_empty_not_an_error(path):
    assert scan_checkpoint.read_checkpoint(path, "C:") == {}


def test_a_checkpoint_of_a_different_target_is_ignored(path):
    scan_checkpoint.write_checkpoint(
        path, "D:", {"D:\\a": ResumedSubtree(nodes=(Node("D:\\a", None, 1, 1, 0),))}
    )
    assert scan_checkpoint.read_checkpoint(path, "C:") == {}


@pytest.mark.parametrize(
    "broken",
    [
        "not json at all",
        "[]",
        '{"version": 99, "target": "C:", "subtrees": {}}',
        '{"version": 1, "target": "C:", "subtrees": "not a dict"}',
        '{"version": 1, "target": "C:", "subtrees": {"C:\\\\a": {"nodes": "x"}}}',
    ],
)
def test_a_malformed_checkpoint_is_ignored_not_an_error(path, broken):
    path.write_text(broken, encoding="utf-8")
    assert scan_checkpoint.read_checkpoint(path, "C:") == {}


def test_clear_removes_the_file(path):
    scan_checkpoint.write_checkpoint(path, "C:", {})
    assert path.exists()
    scan_checkpoint.clear_checkpoint(path)
    assert not path.exists()


def test_clear_does_nothing_if_there_was_no_checkpoint(path):
    scan_checkpoint.clear_checkpoint(path)
    assert not path.exists()


def test_write_never_leaves_a_half_written_file_behind(path):
    scan_checkpoint.write_checkpoint(
        path, "C:", {"C:\\a": ResumedSubtree(nodes=(Node("C:\\a", None, 1, 1, 0),))}
    )
    names = os.listdir(path.parent)
    assert names == [path.name]


def test_write_refuses_an_unsafe_location(tmp_path, monkeypatch):
    calls = []

    def forbidden(checked_path):
        calls.append(checked_path)
        raise windows.UnsafeDataLocation("no")

    monkeypatch.setattr(windows, "ensure_safe_to_write", forbidden)
    with pytest.raises(windows.UnsafeDataLocation):
        scan_checkpoint.write_checkpoint(tmp_path / "x.json", "C:", {})
    assert calls


@pytest.mark.skipif(sys.platform != "win32", reason="usa un enlace real de Windows")
def test_write_refuses_a_real_hard_link(tmp_path):
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    path = tmp_path / "scan-checkpoint.json"
    os.link(other, path)
    with pytest.raises(windows.UnsafeDataLocation):
        scan_checkpoint.write_checkpoint(path, "C:", {})
