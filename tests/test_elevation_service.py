"""Pruebas del lado sin permisos del escaneo de administrador. Sin UAC real."""

import inspect
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from discoclaro.adapters.store import Store
from discoclaro.adapters.volumes import list_volumes
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.domain.unexplained import unexplained_of
from discoclaro.interfaces import elevated
from discoclaro.platform import windows
from discoclaro.services import scan_service
from discoclaro.services.elevation_service import (
    DEFAULT_TIMEOUT_S,
    ENTRY_MODULE,
    EXIT_FAILED,
    EXIT_OK,
    RESULT_NAME,
    run_admin_scan,
    write_result,
)
from discoclaro.services.scan_service import DATABASE_NAME, ScanError

VOLUMES = [Volume("C:", "C:\\", 1000, 400, 600), Volume("D:", "D:\\", 2000, 500, 1500)]
EXECUTABLE = "R:\\python\\python.exe"


def fake_volumes():
    return list(VOLUMES)


def good_result(**changes):
    result = {
        "version": 1,
        "ok": True,
        "snapshot_id": 1,
        "folders": 10,
        "files": 20,
        "skipped_links": 3,
        "nodes_saved": 4,
    }
    return {**result, **changes}


def failure_result(kind="not_a_folder", detail="C:\\"):
    return {"version": 1, "ok": False, "kind": kind, "detail": detail}


def save_snapshot(directory, **changes):
    fields = {
        "taken_at": datetime(2026, 10, 17, 12, 0, tzinfo=UTC),
        "volume": "C:",
        "total_bytes": 1000,
        "used_bytes": 400,
        "free_bytes": 600,
        "scanned_bytes": 300,
        "system_files_bytes": 40,
        "unreadable_bytes": 0,
        "unreadable_count": 2,
        "duration_s": 5.0,
        "is_admin": True,
    }
    with Store.open(directory / DATABASE_NAME) as store:
        return store.save_snapshot(
            Snapshot(**{**fields, **changes}), [Node("C:\\", None, 300, 7, 0)]
        )


class FakeRunner:
    """Un lanzador falso que anota la llamada y hace lo que le pidan."""

    def __init__(self, action=None, code=EXIT_OK, error=None):
        self.action = action
        self.code = code
        self.error = error
        self.calls = []

    def __call__(self, executable, arguments, directory, timeout_s):
        self.calls.append((executable, list(arguments), directory, timeout_s))
        if self.action:
            self.action()
        if self.error:
            raise self.error
        return self.code


def run(directory, runner, target="C:", **overrides):
    options = {
        "data_dir": directory,
        "runner": runner,
        "executable": EXECUTABLE,
        "volumes": fake_volumes,
        "is_admin": lambda: False,
    }
    options.update(overrides)
    return run_admin_scan(target, **options)


def test_the_command_is_fixed_and_only_the_drive_letter_changes(tmp_path):
    runner = FakeRunner(action=lambda: write_result(tmp_path, failure_result()))
    shown = []
    with pytest.raises(ScanError):
        run(tmp_path, runner, "c:", on_elevate=lambda *args: shown.append(args))
    arguments = ["-I", "-m", ENTRY_MODULE, "scan", "C:"]
    assert runner.calls == [(EXECUTABLE, arguments, "R:\\python", DEFAULT_TIMEOUT_S)]
    assert shown == [
        (
            "C:",
            "R:\\python\\python.exe -I -m discoclaro.interfaces.elevated scan C:",
        )
    ]
    assert ENTRY_MODULE == "discoclaro.interfaces.elevated"


def test_dots_in_the_target_are_resolved_before_anything_is_asked(tmp_path):
    runner = FakeRunner()
    with pytest.raises(ScanError):
        run(tmp_path, runner, "C:\\Windows\\..")
    assert runner.calls[0][1][-1] == "C:"


def test_a_successful_elevated_scan_is_read_back_from_the_database(tmp_path):
    ids = []

    def child_job():
        snapshot_id = save_snapshot(tmp_path)
        ids.append(snapshot_id)
        write_result(tmp_path, good_result(snapshot_id=snapshot_id))

    summary = run(tmp_path, FakeRunner(child_job))
    assert summary.snapshot.id == ids[0]
    assert summary.snapshot.volume == "C:"
    assert summary.snapshot.is_admin is True
    assert summary.is_volume
    assert summary.target == "C:\\"
    assert (summary.folders, summary.files, summary.skipped_links) == (10, 20, 3)
    assert summary.nodes_saved == 4
    assert summary.unexplained == unexplained_of(summary.snapshot)
    assert not (tmp_path / RESULT_NAME).exists()


def test_both_sides_agree_when_the_child_is_the_real_entry_point(tmp_path):
    def child(executable, arguments, directory, timeout_s):
        def fake_scan(volume, data_dir):
            snapshot_id = save_snapshot(data_dir, volume=volume)
            return SimpleNamespace(
                snapshot=SimpleNamespace(id=snapshot_id),
                folders=7,
                files=8,
                skipped_links=9,
                nodes_saved=1,
            )

        return elevated.main(
            arguments[3:],
            is_admin=lambda: True,
            data_dir=lambda: tmp_path,
            scan=fake_scan,
        )

    summary = run(tmp_path, child)
    assert (summary.folders, summary.files, summary.skipped_links) == (7, 8, 9)
    assert summary.snapshot.volume == "C:"


def test_if_this_process_is_already_elevated_the_scan_runs_here(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        scan_service,
        "run_scan",
        lambda target, data_dir: calls.append((target, data_dir)),
    )
    runner = FakeRunner()
    run(tmp_path, runner, "c:", is_admin=lambda: True)
    assert calls == [("C:", tmp_path)]
    assert runner.calls == []


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("C:\\Users", "admin_needs_volume"),
        ("D:/data/sub", "admin_needs_volume"),
        ("\\\\server\\share", "admin_needs_volume"),
        ("Z:", "not_fixed_disk"),
        ("Z:\\", "not_fixed_disk"),
        ("\\\\server\\share\\", "not_fixed_disk"),
    ],
)
def test_only_whole_fixed_disks_are_accepted_and_nothing_is_asked_otherwise(
    tmp_path, target, kind
):
    runner = FakeRunner()
    shown = []
    with pytest.raises(ScanError) as error:
        run(tmp_path, runner, target, on_elevate=lambda *args: shown.append(args))
    assert error.value.kind == kind
    assert runner.calls == []
    assert shown == []


def test_declining_the_permission_prompt_is_reported(tmp_path):
    runner = FakeRunner(error=windows.ElevationDeclined(1223, "declined"))
    with pytest.raises(ScanError) as error:
        run(tmp_path, runner)
    assert (error.value.kind, error.value.target) == ("elevation_declined", "C:")


def test_taking_too_long_is_reported(tmp_path):
    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(error=TimeoutError()))
    assert error.value.kind == "elevation_timeout"


def test_not_being_able_to_start_it_is_reported_with_the_system_message(tmp_path):
    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(error=OSError(5, "no se pudo")))
    assert error.value.kind == "elevation_failed"
    assert "no se pudo" in error.value.target


@pytest.mark.parametrize("code", [2, 3, 4, 7, 255])
def test_any_other_exit_code_is_a_failure_that_names_the_code(tmp_path, code):
    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(code=code))
    assert (error.value.kind, error.value.target) == ("elevation_failed", str(code))


def test_no_result_at_all_is_reported(tmp_path):
    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner())
    assert error.value.kind == "elevation_no_result"


def test_a_malformed_result_is_reported_and_removed(tmp_path):
    def child_job():
        (tmp_path / RESULT_NAME).write_text('{"ok": true}', encoding="utf-8")

    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(child_job))
    assert error.value.kind == "elevation_no_result"
    assert not (tmp_path / RESULT_NAME).exists()


def test_a_failure_reported_by_the_child_keeps_its_kind_and_detail(tmp_path):
    def child_job():
        write_result(tmp_path, failure_result("not_fixed_disk", "Z:\\x"))

    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(child_job, code=EXIT_FAILED))
    assert (error.value.kind, error.value.target) == ("not_fixed_disk", "Z:\\x")
    assert not (tmp_path / RESULT_NAME).exists()


@pytest.mark.parametrize(
    "snapshot_changes",
    [{"volume": "D:"}, {"is_admin": False}],
    ids=["other-volume", "not-admin"],
)
def test_a_snapshot_that_does_not_match_the_claim_is_not_trusted(
    tmp_path, snapshot_changes
):
    def child_job():
        snapshot_id = save_snapshot(tmp_path, **snapshot_changes)
        write_result(tmp_path, good_result(snapshot_id=snapshot_id))

    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(child_job))
    assert error.value.kind == "elevation_no_result"


def test_a_claimed_snapshot_that_does_not_exist_is_not_trusted(tmp_path):
    def child_job():
        save_snapshot(tmp_path)
        write_result(tmp_path, good_result(snapshot_id=99))

    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(child_job))
    assert error.value.kind == "elevation_no_result"


def test_a_missing_database_is_reported(tmp_path):
    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(lambda: write_result(tmp_path, good_result())))
    assert error.value.kind == "cannot_open_database"


def test_an_old_or_planted_result_is_deleted_before_launching(tmp_path):
    (tmp_path / RESULT_NAME).write_text(json.dumps(good_result()), encoding="utf-8")
    seen = []

    def child_job():
        seen.append(sorted(path.name for path in tmp_path.iterdir()))

    with pytest.raises(ScanError) as error:
        run(tmp_path, FakeRunner(child_job))
    assert seen == [[]]
    assert error.value.kind == "elevation_no_result"


def test_the_data_folder_is_created_if_it_does_not_exist(tmp_path):
    folder = tmp_path / "nuevo" / "datos"
    with pytest.raises(ScanError):
        run(folder, FakeRunner())
    assert folder.is_dir()


def test_without_a_given_folder_the_default_one_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(windows, "default_data_dir", lambda: tmp_path / "defecto")
    with pytest.raises(ScanError):
        run(None, FakeRunner())
    assert (tmp_path / "defecto").is_dir()


def test_the_real_pieces_are_the_defaults_without_calling_them():
    parameters = inspect.signature(run_admin_scan).parameters
    assert parameters["runner"].default is windows.run_elevated
    assert parameters["executable"].default == sys.executable
    assert parameters["volumes"].default is list_volumes
    assert parameters["is_admin"].default is windows.is_admin
    assert parameters["timeout_s"].default == DEFAULT_TIMEOUT_S
