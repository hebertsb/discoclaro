"""Pruebas del punto de entrada fijo con permisos de administrador. Sin UAC real."""

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from discoclaro.interfaces.elevated import main, parse_arguments
from discoclaro.platform import windows
from discoclaro.services import elevation_service, scan_service
from discoclaro.services.elevation_service import (
    EXIT_BAD_ARGUMENTS,
    EXIT_FAILED,
    EXIT_NOT_ELEVATED,
    EXIT_OK,
    MAX_DETAIL_CHARS,
)
from discoclaro.services.scan_service import ScanError

DIRECTORY = Path("R:\\Datos\\discoclaro")


class Recorder:
    """Anota lo que el punto de entrada escanea y escribe."""

    def __init__(self, error=None, write_error=None):
        self.error = error
        self.write_error = write_error
        self.scanned = []
        self.written = []

    def scan(self, volume, directory):
        self.scanned.append((volume, directory))
        if self.error:
            raise self.error
        return SimpleNamespace(
            snapshot=SimpleNamespace(id=7),
            folders=100,
            files=2000,
            skipped_links=5,
            nodes_saved=40,
        )

    def write(self, directory, result):
        if self.write_error:
            raise self.write_error
        self.written.append((directory, result))


def run_main(argv, recorder=None, **overrides):
    recorder = recorder or Recorder()
    options = {
        "is_admin": lambda: True,
        "data_dir": lambda: DIRECTORY,
        "scan": recorder.scan,
        "write": recorder.write,
    }
    options.update(overrides)
    return main(argv, **options), recorder


@pytest.mark.parametrize("argv", [["scan", "C:"], ["scan", "c:"], ["scan", "Z:"]])
def test_exactly_scan_and_a_drive_letter_is_accepted(argv):
    assert parse_arguments(argv) == argv[1]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [""],
        ["scan"],
        ["scan", ""],
        ["scan", "C:\\"],
        ["scan", "C:\\Users"],
        ["scan", "C:", "--x"],
        ["scan", "C:", "D:"],
        ["delete", "C:"],
        ["SCAN", "C:"],
        ["scan", "CC:"],
        ["scan", "1:"],
        ["scan", "C:;calc"],
        ["scan", "C: "],
        ["scan", "\\\\server\\share"],
        ["scan", "C:\n"],
        ["-c", "import os"],
        ["C:", "scan"],
    ],
)
def test_anything_else_is_refused(argv):
    assert parse_arguments(argv) is None


def test_bad_arguments_do_nothing_at_all():
    code, recorder = run_main(["scan", "C:\\Users"])
    assert code == EXIT_BAD_ARGUMENTS
    assert recorder.scanned == []
    assert recorder.written == []


def test_arguments_are_checked_before_anything_else():
    code, _ = run_main(["rm"], is_admin=lambda: False)
    assert code == EXIT_BAD_ARGUMENTS


def test_without_real_permissions_it_does_nothing():
    code, recorder = run_main(["scan", "C:"], is_admin=lambda: False)
    assert code == EXIT_NOT_ELEVATED
    assert recorder.scanned == []
    assert recorder.written == []


def test_an_unusable_data_folder_does_nothing():
    def broken():
        raise OSError("LOCALAPPDATA is not set")

    code, recorder = run_main(["scan", "C:"], data_dir=broken)
    assert code == EXIT_FAILED
    assert recorder.scanned == []
    assert recorder.written == []


def test_a_successful_scan_leaves_exactly_the_fixed_result():
    code, recorder = run_main(["scan", "C:"])
    assert code == EXIT_OK
    assert recorder.scanned == [("C:", DIRECTORY)]
    assert recorder.written == [
        (
            DIRECTORY,
            {
                "version": 1,
                "ok": True,
                "snapshot_id": 7,
                "folders": 100,
                "files": 2000,
                "skipped_links": 5,
                "nodes_saved": 40,
            },
        )
    ]


@pytest.mark.parametrize(
    "kind", ["not_fixed_disk", "not_a_folder", "cannot_open_database"]
)
def test_a_scan_error_is_reported_with_its_kind_and_detail(kind):
    code, recorder = run_main(["scan", "C:"], Recorder(error=ScanError(kind, "C:")))
    assert code == EXIT_FAILED
    assert recorder.written == [
        (
            DIRECTORY,
            {"version": 1, "ok": False, "kind": kind, "detail": "C:"},
        )
    ]


def test_an_error_kind_that_is_not_in_the_closed_list_becomes_unexpected():
    recorder = Recorder(error=ScanError("rm -rf /", "x"))
    code, recorder = run_main(["scan", "C:"], recorder)
    assert code == EXIT_FAILED
    assert recorder.written[0][1]["kind"] == "unexpected"


def test_the_detail_is_cut_to_the_maximum_length():
    recorder = Recorder(error=ScanError("not_a_folder", "x" * 1000))
    _, recorder = run_main(["scan", "C:"], recorder)
    assert len(recorder.written[0][1]["detail"]) == MAX_DETAIL_CHARS


def test_an_unexpected_failure_reports_only_the_error_class_not_its_text():
    recorder = Recorder(error=ValueError("texto con datos del equipo"))
    code, recorder = run_main(["scan", "C:"], recorder)
    assert code == EXIT_FAILED
    assert recorder.written[0][1] == {
        "version": 1,
        "ok": False,
        "kind": "unexpected",
        "detail": "ValueError",
    }


def test_an_interruption_is_not_swallowed_and_nothing_is_written():
    recorder = Recorder(error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run_main(["scan", "C:"], recorder)
    assert recorder.written == []


def test_not_being_able_to_write_the_result_is_a_failure_not_a_crash():
    code, _ = run_main(["scan", "C:"], Recorder(write_error=PermissionError(13, "x")))
    assert code == EXIT_FAILED


def test_it_reads_the_real_command_line_when_no_arguments_are_given(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(sys, "argv", ["entrada", "scan", "D:"])
    code = main(
        is_admin=lambda: True,
        data_dir=lambda: DIRECTORY,
        scan=recorder.scan,
        write=recorder.write,
    )
    assert code == EXIT_OK
    assert recorder.scanned == [("D:", DIRECTORY)]


def test_it_never_prints_anything(capsys):
    run_main(["scan", "C:"])
    run_main(["scan", "C:"], Recorder(error=ValueError("x")))
    run_main(["nope"])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_the_real_pieces_are_the_defaults_without_calling_them():
    parameters = inspect.signature(main).parameters
    assert parameters["is_admin"].default is windows.is_admin
    assert parameters["data_dir"].default is windows.default_data_dir
    assert parameters["scan"].default is scan_service.run_scan
    assert parameters["write"].default is elevation_service.write_result
