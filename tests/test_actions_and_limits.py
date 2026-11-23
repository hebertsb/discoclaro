"""Pruebas unitarias de `actions_service`, `limits_service` y la política de rutas."""

import types

import pytest

from discoclaro.domain.config import Budget, Config, VolumeLimit
from discoclaro.domain.footprint import FolderCandidate
from discoclaro.domain.models import Program, Volume
from discoclaro.domain.policy import is_safe_deletion_path
from discoclaro.services import limits_service
from discoclaro.services.actions_service import (
    ActionError,
    execute_terminal_command,
    parse_uninstall_command,
    run_program_uninstall,
    trash_paths_verified,
)


@pytest.mark.parametrize(
    ("bad_path", "reason"),
    [
        ("", "empty_path"),
        ("   ", "empty_path"),
        ("C:\\Users\\x\\carpeta.", "trailing_dot_or_space"),
        ("\\\\servidor\\share\\carpeta", "unc_or_device_prefix"),
        ("\\\\?\\C:\\carpeta", "unc_or_device_prefix"),
        ("relativa\\carpeta", "not_drive_path"),
        ("C:relativa", "relative_path"),
        ("C:\\", "drive_root"),
        ("C:\\Users\\..\\Windows", "parent_traversal"),
        ("C:\\Users\\x\\CON", "reserved_device"),
        ("C:\\Users\\x\\nul.txt", "reserved_device"),
        ("C:\\Windows", "protected_system_root"),
        ("C:\\Windows\\System32", "protected_system_folder"),
        ("C:\\Program Files", "protected_system_root"),
        ("C:\\Users\\x", "user_profile_root"),
        ("C:\\Users\\x\\AppData\\Local\\discoclaro", "discoclaro_own_data"),
        (
            "C:\\Users\\x\\AppData\\Local\\discoclaro\\discoclaro.db",
            "discoclaro_own_data",
        ),
    ],
)
def test_is_safe_deletion_path_rejects_protected_and_hostile_paths(bad_path, reason):
    ok, actual_reason = is_safe_deletion_path(
        bad_path,
        user_profile="C:\\Users\\x",
        app_data_dirs=("C:\\Users\\x\\AppData\\Local\\discoclaro",),
    )
    assert ok is False
    assert actual_reason == reason


@pytest.mark.parametrize(
    "good_path",
    [
        "C:\\Users\\x\\AppData\\Local\\Ollama",
        "C:\\Users\\x\\AppData\\Local\\Programs\\Ollama",
        "C:\\Users\\x\\AppData\\Roaming\\Acme",
        "C:\\Users\\x\\AppData\\Local\\Temp\\acme_cache",
        "C:\\Program Files\\Acme",
    ],
)
def test_is_safe_deletion_path_accepts_program_and_temp_subfolders(good_path):
    ok, reason = is_safe_deletion_path(
        good_path,
        user_profile="C:\\Users\\x",
        app_data_dirs=("C:\\Users\\x\\AppData\\Local\\discoclaro",),
    )
    assert ok is True
    assert reason == "ok"


def test_parse_uninstall_command_splits_quoted_executable_and_args(tmp_path):
    exe = tmp_path / "unins000.exe"
    exe.write_bytes(b"x")
    parts = parse_uninstall_command(f'"{exe}" /SILENT /NORESTART')
    assert parts == [str(exe), "/SILENT", "/NORESTART"]


def test_run_program_uninstall_detects_all_remaining_traces_including_temp_and_programs(
    tmp_path,
):
    exe = tmp_path / "uninst.exe"
    exe.write_bytes(b"x")
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    temp = tmp_path / "temp"
    for d in (local / "Programs" / "Acme", roaming / "Acme", temp / "AcmeCache"):
        d.mkdir(parents=True)

    prog = Program(
        name="Acme Studio",
        version="2.0",
        publisher="Acme",
        install_location=str(local / "Programs" / "Acme"),
        install_date="20261018",
        estimated_size_bytes=4096,
        uninstall_string=f'"{exe}" /S',
    )
    sizes = {
        str(local / "Programs" / "Acme"): 2000,
        str(roaming / "Acme"): 1000,
        str(temp / "AcmeCache"): 500,
    }

    def fake_inspect(p: str):
        if p in sizes:
            return FolderCandidate(path=p, size_bytes=sizes[p], last_modified=None)
        return None

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    result = run_program_uninstall(
        "Acme Studio",
        read_programs=lambda: [prog],
        environment=lambda: {
            "%LOCALAPPDATA%": str(local),
            "%APPDATA%": str(roaming),
            "%TEMP%": str(temp),
        },
        inspect=fake_inspect,
        confirm=lambda title, msg: True,
        run=fake_run,
        environ={},
    )
    assert result.program_name == "Acme Studio"
    assert result.uninstaller_exit_code == 0
    assert len(calls) == 1
    found_paths = {f.path for f in result.remaining_footprints}
    assert found_paths == {
        str(local / "Programs" / "Acme"),
        str(roaming / "Acme"),
        str(temp / "AcmeCache"),
    }
    assert result.total_remaining_bytes == 3500


def test_run_program_uninstall_stops_when_user_declines_native_dialog(tmp_path):
    exe = tmp_path / "uninst.exe"
    exe.write_bytes(b"x")
    prog = Program("Acme", "1.0", "Acme", None, None, None, f'"{exe}"')
    with pytest.raises(ActionError) as exc:
        run_program_uninstall(
            "Acme",
            read_programs=lambda: [prog],
            environment=dict,
            inspect=lambda _: None,
            confirm=lambda title, msg: False,
            environ={},
        )
    assert exc.value.kind == "action_declined"


def test_trash_paths_verified_sends_to_recycle_bin_and_verifies_removal(tmp_path):
    existing = {"C:\\Users\\x\\AppData\\Local\\Acme": 4096}

    def fake_inspect(p: str):
        if p in existing:
            return FolderCandidate(path=p, size_bytes=existing[p], last_modified=None)
        return None

    def fake_trash(p: str) -> bool:
        existing.pop(p, None)
        return True

    batch = trash_paths_verified(
        ["C:\\Users\\x\\AppData\\Local\\Acme"],
        environment=lambda: {"%USERPROFILE%": "C:\\Users\\x"},
        inspect=fake_inspect,
        confirm=lambda title, msg: True,
        send_to_trash=fake_trash,
        ensure_safe=lambda _: None,
        environ={},
    )
    assert batch.total_freed_bytes == 4096
    assert batch.items[0].removed is True


def test_execute_terminal_command_requires_native_confirmation():
    with pytest.raises(ActionError) as exc:
        execute_terminal_command(
            "git status",
            confirm=lambda title, msg: False,
            resolve=lambda _: "C:\\Program Files\\Git\\cmd\\git.exe",
            environ={},
        )
    assert exc.value.kind == "action_declined"


def test_execute_terminal_command_runs_without_shell_when_confirmed():
    recorded = []

    def fake_run(parts, **kwargs):
        recorded.append((parts, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    res = execute_terminal_command(
        "docker system df",
        confirm=lambda title, msg: True,
        run=fake_run,
        resolve=lambda _: "C:\\Program Files\\Docker\\docker.exe",
        environ={},
    )
    assert res.exit_code == 0
    assert res.stdout == "ok\n"
    assert recorded[0][0] == ["C:\\Program Files\\Docker\\docker.exe", "system", "df"]
    assert "shell" not in recorded[0][1]


def test_readonly_mode_blocks_every_action():
    env = {"DISCOCLARO_READONLY": "1"}
    with pytest.raises(ActionError) as exc1:
        execute_terminal_command("docker system df", environ=env)
    assert exc1.value.kind == "readonly_mode"

    with pytest.raises(ActionError) as exc2:
        trash_paths_verified(["C:\\Users\\x\\AppData\\Local\\Acme"], environ=env)
    assert exc2.value.kind == "readonly_mode"


def test_limits_service_evaluates_volumes_and_budgets(tmp_path):
    cfg = Config(
        volumes={"C:": VolumeLimit(min_free_gb=50.0, warn_at_percent=80.0)},
        budgets={"cache": Budget(max_gb=10.0)},
    )
    vols = [Volume("C:", "C:\\", 100 * 1024**3, 85 * 1024**3, 15 * 1024**3)]
    overview = limits_service.run_limits_overview(
        tmp_path,
        load_config=lambda _: cfg,
        list_volumes=lambda: vols,
        rules=lambda: (),
        environment=dict,
    )
    assert len(overview.volumes) == 1
    assert overview.volumes[0].status == "exceeded"
    assert len(overview.budgets) == 1
    assert overview.budgets[0].target == "cache"
