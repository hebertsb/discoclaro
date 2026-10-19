"""Pruebas de lo específico de Windows, con discos simulados (nunca reales)."""

import ctypes
import inspect
import logging
import os
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

from discoclaro.domain.models import SystemFile
from discoclaro.platform import windows

REMOVABLE, FIXED, REMOTE, CDROM = 2, 3, 4, 5


def fake_drives(types):
    """Devuelve las dos funciones que se inyectan: listar discos y pedir su tipo."""
    asked = []

    def list_drives():
        return list(types)

    def drive_type(root):
        asked.append(root)
        return types[root]

    return list_drives, drive_type, asked


def test_only_fixed_drives_are_kept_in_system_order():
    types = {
        "A:\\": REMOVABLE,
        "C:\\": FIXED,
        "D:\\": FIXED,
        "E:\\": CDROM,
        "Z:\\": REMOTE,
    }
    list_drives, drive_type, _ = fake_drives(types)
    assert windows.fixed_drive_roots(list_drives, drive_type) == ["C:\\", "D:\\"]


def test_no_fixed_drives_gives_an_empty_list():
    list_drives, drive_type, _ = fake_drives({"A:\\": REMOVABLE, "Z:\\": REMOTE})
    assert windows.fixed_drive_roots(list_drives, drive_type) == []


def test_no_drives_at_all_gives_an_empty_list():
    list_drives, drive_type, asked = fake_drives({})
    assert windows.fixed_drive_roots(list_drives, drive_type) == []
    assert asked == []


def test_it_asks_the_type_of_each_listed_drive_once():
    list_drives, drive_type, asked = fake_drives({"C:\\": FIXED, "D:\\": REMOTE})
    windows.fixed_drive_roots(list_drives, drive_type)
    assert asked == ["C:\\", "D:\\"]


@pytest.mark.parametrize("unknown_type", [0, 1, 6])
def test_unknown_missing_and_ram_drives_are_not_fixed(unknown_type):
    list_drives, drive_type, _ = fake_drives({"Q:\\": unknown_type})
    assert windows.fixed_drive_roots(list_drives, drive_type) == []


LOGGER = "discoclaro.platform.windows"


def regular(size):
    return types.SimpleNamespace(st_size=size, st_mode=stat.S_IFREG | 0o644)


def fake_lstat(table):
    """`lstat` falso: lo que no está en `table` no existe."""

    def lstat(path):
        if path not in table:
            raise FileNotFoundError(2, "no such file", path)
        result = table[path]
        if isinstance(result, Exception):
            raise result
        return result

    return lstat


def test_system_files_present_are_measured_in_canonical_order():
    table = {
        "C:\\hiberfil.sys": regular(200),
        "C:\\pagefile.sys": regular(1000),
        "C:\\swapfile.sys": regular(50),
    }
    assert windows.measure_system_files("C:\\", fake_lstat(table)) == [
        SystemFile("C:\\pagefile.sys", 1000),
        SystemFile("C:\\hiberfil.sys", 200),
        SystemFile("C:\\swapfile.sys", 50),
    ]


def test_missing_system_files_are_skipped_without_noise(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    table = {"D:\\pagefile.sys": regular(48)}
    assert windows.measure_system_files("D:\\", fake_lstat(table)) == [
        SystemFile("D:\\pagefile.sys", 48)
    ]
    assert caplog.records == []


def test_a_volume_without_system_files_gives_an_empty_list():
    assert windows.measure_system_files("E:\\", fake_lstat({})) == []


@pytest.mark.parametrize("root", ["D:\\", "D:", "D:/", "D:\\\\"])
def test_every_spelling_of_the_root_gives_the_same_path(root):
    table = {"D:\\pagefile.sys": regular(7)}
    assert windows.measure_system_files(root, fake_lstat(table)) == [
        SystemFile("D:\\pagefile.sys", 7)
    ]


def test_an_unreadable_system_file_is_logged_and_skipped(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    table = {
        "C:\\hiberfil.sys": PermissionError(13, "denied\nsecond line"),
        "C:\\swapfile.sys": regular(50),
    }
    result = windows.measure_system_files("C:\\", fake_lstat(table))
    assert result == [SystemFile("C:\\swapfile.sys", 50)]
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "hiberfil.sys" in messages[0]
    assert "\n" not in messages[0]


def test_folders_and_links_with_those_names_are_not_system_files():
    table = {
        "C:\\pagefile.sys": types.SimpleNamespace(st_size=5, st_mode=stat.S_IFDIR),
        "C:\\hiberfil.sys": types.SimpleNamespace(st_size=5, st_mode=stat.S_IFLNK),
        "C:\\swapfile.sys": types.SimpleNamespace(st_size=5, st_mode=0),
    }
    assert windows.measure_system_files("C:\\", fake_lstat(table)) == []


def test_the_known_names_are_the_three_system_files_in_lowercase():
    assert windows.SYSTEM_FILE_NAMES == ("pagefile.sys", "hiberfil.sys", "swapfile.sys")
    assert all(name == name.lower() for name in windows.SYSTEM_FILE_NAMES)


def test_the_real_lstat_is_the_default_without_calling_it():
    parameters = inspect.signature(windows.measure_system_files).parameters
    assert parameters["lstat"].default is os.lstat


def test_the_data_folder_is_discoclaro_inside_localappdata():
    environ = {"LOCALAPPDATA": "R:\\Datos\\Local"}
    expected = Path("R:\\Datos\\Local") / "discoclaro"
    assert windows.default_data_dir(environ) == expected


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"LOCALAPPDATA": ""},
        {"LOCALAPPDATA": "relativo\\carpeta"},
        {"LOCALAPPDATA": "\\sin\\unidad"},
    ],
)
def test_the_data_folder_refuses_a_missing_or_relative_localappdata(environ):
    with pytest.raises(OSError):
        windows.default_data_dir(environ)


def test_the_data_folder_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "R:\\Otra\\Carpeta")
    assert windows.default_data_dir() == Path("R:\\Otra\\Carpeta") / "discoclaro"


def test_the_config_folder_is_discoclaro_inside_appdata():
    environ = {"APPDATA": "R:\\Datos\\Roaming"}
    expected = Path("R:\\Datos\\Roaming") / "discoclaro"
    assert windows.default_config_dir(environ) == expected


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"APPDATA": ""},
        {"APPDATA": "relativo\\carpeta"},
        {"APPDATA": "\\sin\\unidad"},
    ],
)
def test_the_config_folder_refuses_a_missing_or_relative_appdata(environ):
    with pytest.raises(OSError):
        windows.default_config_dir(environ)


def test_the_config_folder_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv("APPDATA", "R:\\Otra\\Carpeta")
    assert windows.default_config_dir() == Path("R:\\Otra\\Carpeta") / "discoclaro"


@pytest.mark.parametrize(("answer", "expected"), [(1, True), (0, False), (4, True)])
def test_is_admin_follows_the_windows_answer(answer, expected):
    assert windows.is_admin(lambda: answer) is expected


@pytest.mark.parametrize(
    "error", [OSError("no shell32"), AttributeError("not windows")]
)
def test_is_admin_assumes_no_when_it_cannot_tell(error):
    def broken():
        raise error

    assert windows.is_admin(broken) is False


class FakeShell32:
    """`shell32` falso que anota con qué se lo llamó."""

    def __init__(self, succeeds=True, process_handle=123):
        self.succeeds = succeeds
        self.process_handle = process_handle
        self.calls = []

    def ShellExecuteExW(self, pointer):  # noqa: N802 - el nombre lo fija Windows
        info = pointer.contents
        self.calls.append(
            {
                "size": info.cbSize,
                "mask": info.fMask,
                "verb": info.lpVerb,
                "file": info.lpFile,
                "parameters": info.lpParameters,
                "directory": info.lpDirectory,
                "show": info.nShow,
            }
        )
        info.hProcess = self.process_handle
        return 1 if self.succeeds else 0


class FakeKernel32:
    """`kernel32` falso: espera, código de salida y cierre del manejador."""

    def __init__(self, wait_result=0, exit_code=0, exit_code_ok=True):
        self.wait_result = wait_result
        self.exit_code = exit_code
        self.exit_code_ok = exit_code_ok
        self.waited = []
        self.closed = []

    def WaitForSingleObject(self, handle, milliseconds):  # noqa: N802
        self.waited.append((handle, milliseconds))
        return self.wait_result

    def GetExitCodeProcess(self, handle, pointer):  # noqa: N802
        pointer.contents.value = self.exit_code
        return 1 if self.exit_code_ok else 0

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def elevate(shell32=None, kernel32=None, timeout_s=60, last_error=lambda: 0, **extra):
    shell32 = shell32 or FakeShell32()
    kernel32 = kernel32 or FakeKernel32()
    arguments = extra.pop("arguments", ["-I", "-m", "paquete.entrada", "scan", "C:"])
    return windows.run_elevated(
        "R:\\python\\python.exe",
        arguments,
        "R:\\python",
        timeout_s,
        apis=(shell32, kernel32),
        last_error=last_error,
    )


def test_the_permission_prompt_is_requested_for_exactly_the_given_program():
    shell32 = FakeShell32()
    elevate(shell32)
    (call,) = shell32.calls
    assert call["verb"] == "runas"
    assert call["file"] == "R:\\python\\python.exe"
    assert call["parameters"] == "-I -m paquete.entrada scan C:"
    assert call["directory"] == "R:\\python"
    assert call["show"] == windows.SW_HIDE
    assert call["mask"] & windows.SEE_MASK_NOCLOSEPROCESS
    assert call["size"] == ctypes.sizeof(windows._ShellExecuteInfo)


def test_arguments_with_spaces_are_quoted_not_split():
    shell32 = FakeShell32()
    elevate(shell32, arguments=["-m", "mod", "dos palabras"])
    assert shell32.calls[0]["parameters"] == '-m mod "dos palabras"'


def test_the_command_line_shown_is_the_one_windows_receives():
    parts = ["R:\\Mi python\\python.exe", "-m", "dos palabras", "C:"]
    assert windows.command_line(parts) == (
        '"R:\\Mi python\\python.exe" -m "dos palabras" C:'
    )
    assert windows.command_line(["a.exe", "scan", "C:"]) == "a.exe scan C:"


def test_the_exit_code_is_returned_and_the_handle_is_closed():
    kernel32 = FakeKernel32(exit_code=3)
    assert elevate(kernel32=kernel32, timeout_s=2.5) == 3
    assert kernel32.waited == [(123, 2500)]
    assert kernel32.closed == [123]


def test_a_declined_permission_prompt_is_its_own_error_and_nothing_is_waited_for():
    kernel32 = FakeKernel32()
    with pytest.raises(windows.ElevationDeclined) as error:
        elevate(FakeShell32(succeeds=False), kernel32, last_error=lambda: 1223)
    assert error.value.errno == 1223
    assert kernel32.waited == []
    assert kernel32.closed == []


def test_any_other_launch_failure_is_a_plain_os_error():
    with pytest.raises(OSError) as error:
        elevate(FakeShell32(succeeds=False), last_error=lambda: 5)
    assert not isinstance(error.value, windows.ElevationDeclined)
    assert error.value.errno == 5


def test_a_missing_process_handle_is_an_error():
    with pytest.raises(OSError):
        elevate(FakeShell32(process_handle=None))


def test_taking_too_long_raises_a_timeout_and_still_closes_the_handle():
    kernel32 = FakeKernel32(wait_result=windows.WAIT_TIMEOUT)
    with pytest.raises(TimeoutError):
        elevate(kernel32=kernel32)
    assert kernel32.closed == [123]


def test_not_being_able_to_read_the_exit_code_is_an_error_and_closes_the_handle():
    kernel32 = FakeKernel32(exit_code_ok=False)
    with pytest.raises(OSError):
        elevate(kernel32=kernel32, last_error=lambda: 6)
    assert kernel32.closed == [123]


def test_an_enormous_timeout_is_capped_to_what_windows_accepts():
    kernel32 = FakeKernel32()
    elevate(kernel32=kernel32, timeout_s=10**9)
    assert kernel32.waited[0][1] == windows.MAX_WAIT_MS


@pytest.mark.skipif(sys.platform != "win32", reason="el tamaño es el de Windows")
def test_the_structure_has_the_documented_size_and_the_real_apis_load():
    expected = 112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60
    assert ctypes.sizeof(windows._ShellExecuteInfo) == expected
    shell32, kernel32 = windows._load_elevation_apis()
    assert shell32.ShellExecuteExW.argtypes
    assert kernel32.WaitForSingleObject.restype is not None


def fake_info(attributes=0, nlink=1):
    return types.SimpleNamespace(st_file_attributes=attributes, st_nlink=nlink)


def test_a_path_that_does_not_exist_yet_has_nothing_to_protect():
    def lstat(path):
        raise FileNotFoundError(path)

    windows.ensure_safe_to_write("C:\\nuevo\\x.db", lstat)


def test_an_ordinary_file_is_safe_to_write():
    windows.ensure_safe_to_write("C:\\x.db", lambda path: fake_info())


def test_a_reparse_point_is_refused():
    info = fake_info(attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write("C:\\link", lambda path: info)


def test_a_file_with_more_than_one_name_is_refused():
    info = fake_info(nlink=2)
    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write("C:\\x.db", lambda path: info)


def test_not_being_able_to_check_at_all_is_refused():
    def lstat(path):
        raise PermissionError(path)

    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write("C:\\x.db", lstat)


@pytest.mark.skipif(sys.platform != "win32", reason="usa archivos reales de Windows")
def test_a_real_ordinary_file_in_a_tmp_folder_is_safe_to_write(tmp_path):
    target = tmp_path / "a.db"
    target.write_bytes(b"x")
    windows.ensure_safe_to_write(target)


@pytest.mark.skipif(sys.platform != "win32", reason="usa archivos reales de Windows")
def test_a_real_junction_is_refused(tmp_path):
    import shutil

    real = tmp_path / "real"
    real.mkdir()
    junction = tmp_path / "junction"
    cmd = shutil.which("cmd")
    assert cmd is not None
    subprocess.run(  # noqa: S603 - cmd con ruta completa, sin datos externos
        [cmd, "/c", "mklink", "/J", str(junction), str(real)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write(junction)


@pytest.mark.skipif(sys.platform != "win32", reason="usa archivos reales de Windows")
def test_a_real_hard_link_is_refused(tmp_path):
    original = tmp_path / "a.db"
    original.write_bytes(b"x")
    second_name = tmp_path / "b.db"
    os.link(original, second_name)
    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write(original)
    with pytest.raises(windows.UnsafeDataLocation):
        windows.ensure_safe_to_write(second_name)


def fake_keyboard(*keys):
    """Un teclado falso: `keys` son los bytes que entrega cada `getch()`."""
    pending = list(keys)
    return lambda: bool(pending), lambda: pending.pop(0)


def test_pause_requested_is_false_with_nothing_waiting():
    kbhit, getch = fake_keyboard()
    assert windows.pause_requested(kbhit, getch) is False


def test_pause_requested_is_true_when_the_pause_key_was_pressed():
    kbhit, getch = fake_keyboard(b"p")
    assert windows.pause_requested(kbhit, getch) is True


def test_pause_requested_ignores_the_case_of_the_key():
    kbhit, getch = fake_keyboard(b"P")
    assert windows.pause_requested(kbhit, getch) is True


def test_pause_requested_drains_every_waiting_key_without_blocking():
    kbhit, getch = fake_keyboard(b"x", b"y", b"p", b"z")
    assert windows.pause_requested(kbhit, getch) is True
    assert windows.pause_requested(kbhit, getch) is False


def test_pause_requested_ignores_a_different_key():
    kbhit, getch = fake_keyboard(b"x")
    assert windows.pause_requested(kbhit, getch) is False


def test_environment_variables_reads_the_four_known_ones():
    environ = {
        "USERPROFILE": "C:\\Users\\x",
        "LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local",
        "APPDATA": "C:\\Users\\x\\AppData\\Roaming",
        "TEMP": "C:\\Users\\x\\AppData\\Local\\Temp",
        "PATH": "C:\\Windows",
    }
    assert windows.environment_variables(environ) == {
        "%USERPROFILE%": "C:\\Users\\x",
        "%LOCALAPPDATA%": "C:\\Users\\x\\AppData\\Local",
        "%APPDATA%": "C:\\Users\\x\\AppData\\Roaming",
        "%TEMP%": "C:\\Users\\x\\AppData\\Local\\Temp",
    }


def test_environment_variables_omits_what_is_missing():
    assert windows.environment_variables({"USERPROFILE": "C:\\Users\\x"}) == {
        "%USERPROFILE%": "C:\\Users\\x"
    }
    assert windows.environment_variables({}) == {}


def test_environment_variables_reads_the_real_environment_by_default():
    found = windows.environment_variables()
    assert isinstance(found, dict)
    assert all(key in windows.ENVIRONMENT_VARIABLES for key in found)


def fake_run(returncode=0, stderr="", stdout=""):
    """Reemplaza `subprocess.run`; devuelve (los llamados, la función falsa)."""
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(
            returncode=returncode, stderr=stderr, stdout=stdout
        )

    return calls, run


def test_create_scheduled_task_calls_schtasks_with_a_list_of_arguments():
    calls, run = fake_run()
    windows.create_scheduled_task(
        "\\discoclaro\\scan",
        ["C:\\venv\\python.exe", "-m", "discoclaro", "scan", "C:"],
        schedule="DAILY",
        start_time="09:00",
        run=run,
    )
    (args, kwargs) = calls[0]
    assert args[0].lower().endswith("schtasks.exe")
    assert args[1:] == [
        "/create",
        "/tn",
        "\\discoclaro\\scan",
        "/tr",
        "C:\\venv\\python.exe -m discoclaro scan C:",
        "/sc",
        "DAILY",
        "/st",
        "09:00",
        "/f",
    ]
    assert kwargs["timeout"] == windows.SCHEDULED_TASK_TIMEOUT_S
    assert "shell" not in kwargs


def test_create_scheduled_task_quotes_a_path_with_spaces():
    calls, run = fake_run()
    windows.create_scheduled_task(
        "\\discoclaro\\scan",
        ["C:\\Program Files\\Python\\python.exe", "-m", "discoclaro", "check"],
        schedule="DAILY",
        start_time="09:00",
        run=run,
    )
    (args, _) = calls[0]
    assert args[args.index("/tr") + 1] == (
        '"C:\\Program Files\\Python\\python.exe" -m discoclaro check'
    )


def test_create_scheduled_task_without_a_start_time_omits_st():
    calls, run = fake_run()
    windows.create_scheduled_task(
        "\\discoclaro\\programs-onlogon",
        ["python.exe", "-m", "discoclaro", "programs-check"],
        schedule="ONLOGON",
        run=run,
    )
    (args, _) = calls[0]
    assert "/st" not in args
    assert args[1:] == [
        "/create",
        "/tn",
        "\\discoclaro\\programs-onlogon",
        "/tr",
        "python.exe -m discoclaro programs-check",
        "/sc",
        "ONLOGON",
        "/f",
    ]


def test_create_scheduled_task_with_an_event_query_uses_ec_and_mo():
    calls, run = fake_run()
    windows.create_scheduled_task(
        "\\discoclaro\\programs-onevent",
        ["python.exe", "-m", "discoclaro", "programs-check"],
        schedule="ONEVENT",
        event_channel="Application",
        event_query="*[System[Provider[@Name='MsiInstaller']]]",
        run=run,
    )
    (args, _) = calls[0]
    assert args[1:] == [
        "/create",
        "/tn",
        "\\discoclaro\\programs-onevent",
        "/tr",
        "python.exe -m discoclaro programs-check",
        "/sc",
        "ONEVENT",
        "/ec",
        "Application",
        "/mo",
        "*[System[Provider[@Name='MsiInstaller']]]",
        "/f",
    ]


def test_create_scheduled_task_raises_when_schtasks_fails():
    _, run = fake_run(returncode=1, stderr="ERROR: access denied")
    with pytest.raises(windows.ScheduledTaskError):
        windows.create_scheduled_task(
            "\\discoclaro\\scan", ["x"], schedule="DAILY", start_time="09:00", run=run
        )


def test_remove_scheduled_task_returns_true_when_it_existed():
    _, run = fake_run(returncode=0)
    assert windows.remove_scheduled_task("\\discoclaro\\scan", run=run) is True


def test_remove_scheduled_task_returns_false_when_it_did_not_exist():
    _, run = fake_run(returncode=1)
    assert windows.remove_scheduled_task("\\discoclaro\\scan", run=run) is False


def test_remove_scheduled_task_uses_delete_and_force():
    calls, run = fake_run()
    windows.remove_scheduled_task("\\discoclaro\\scan", run=run)
    (args, _) = calls[0]
    assert args[1:] == ["/delete", "/tn", "\\discoclaro\\scan", "/f"]


def test_scheduled_task_exists_is_true_when_schtasks_finds_it():
    _, run = fake_run(returncode=0)
    assert windows.scheduled_task_exists("\\discoclaro\\scan", run=run) is True


def test_scheduled_task_exists_is_false_when_schtasks_does_not_find_it():
    _, run = fake_run(returncode=1)
    assert windows.scheduled_task_exists("\\discoclaro\\scan", run=run) is False


def test_scheduled_task_exists_uses_query():
    calls, run = fake_run()
    windows.scheduled_task_exists("\\discoclaro\\scan", run=run)
    (args, _) = calls[0]
    assert args[1:] == ["/query", "/tn", "\\discoclaro\\scan"]


def test_the_real_subprocess_run_is_the_default_without_calling_it():
    for function in (
        windows.create_scheduled_task,
        windows.remove_scheduled_task,
        windows.scheduled_task_exists,
        windows.scheduled_task_command,
    ):
        parameters = inspect.signature(function).parameters
        assert parameters["run"].default is subprocess.run


_REAL_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\discoclaro\\scan</URI>
  </RegistrationInfo>
  <Actions Context="Author">
    <Exec>
      <Command>D:\\venv\\Scripts\\python.exe</Command>
      <Arguments>-m discoclaro scan C:</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def test_scheduled_task_command_reads_the_command_and_arguments_from_the_real_xml():
    _, run = fake_run(returncode=0, stdout=_REAL_TASK_XML)
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) == (
        "D:\\venv\\Scripts\\python.exe",
        "-m discoclaro scan C:",
    )


def test_scheduled_task_command_uses_the_xml_query():
    calls, run = fake_run(stdout=_REAL_TASK_XML)
    windows.scheduled_task_command("\\discoclaro\\scan", run=run)
    (args, _) = calls[0]
    assert args[1:] == ["/query", "/tn", "\\discoclaro\\scan", "/xml"]


def test_scheduled_task_command_is_none_when_the_task_does_not_exist():
    _, run = fake_run(returncode=1)
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) is None


def test_scheduled_task_command_is_none_for_invalid_xml():
    _, run = fake_run(stdout="not xml at all")
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) is None


def test_scheduled_task_command_is_none_without_an_exec_action():
    no_exec = """<?xml version="1.0"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Actions Context="Author"></Actions>
</Task>
"""
    _, run = fake_run(stdout=no_exec)
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) is None


def test_scheduled_task_command_is_none_with_a_doctype():
    """Un XML de tarea de verdad nunca trae DOCTYPE; se rechaza sin parsearlo."""
    hostile = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE Task [<!ENTITY x "y">]>\n'
        '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        "<Actions><Exec><Command>&x;</Command></Exec></Actions></Task>"
    )
    _, run = fake_run(stdout=hostile)
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) is None


def test_scheduled_task_command_treats_missing_arguments_as_empty():
    no_arguments = """<?xml version="1.0"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Actions Context="Author">
    <Exec><Command>C:\\python.exe</Command></Exec>
  </Actions>
</Task>
"""
    _, run = fake_run(stdout=no_arguments)
    assert windows.scheduled_task_command("\\discoclaro\\scan", run=run) == (
        "C:\\python.exe",
        "",
    )


def registry_entry(**fields):
    base = {"DisplayName": "Un programa"}
    base.update(fields)
    return base


def test_installed_programs_reads_the_basic_fields():
    entry = registry_entry(
        DisplayVersion="1.2.3",
        Publisher="Acme",
        InstallLocation="C:\\Program Files\\Acme",
        InstallDate="20261017",
        EstimatedSize=2048,
        UninstallString='"C:\\Program Files\\Acme\\uninst.exe"',
    )
    [program] = windows.installed_programs(read_entries=lambda: [entry])
    assert program.name == "Un programa"
    assert program.version == "1.2.3"
    assert program.publisher == "Acme"
    assert program.install_location == "C:\\Program Files\\Acme"
    assert program.install_date == "20261017"
    assert program.estimated_size_bytes == 2048 * 1024
    assert program.uninstall_string == '"C:\\Program Files\\Acme\\uninst.exe"'


def test_installed_programs_skips_entries_without_a_display_name():
    entries = [{"Publisher": "Acme"}, registry_entry(DisplayName="")]
    assert windows.installed_programs(read_entries=lambda: entries) == []


def test_installed_programs_fills_missing_optional_fields_with_none():
    [program] = windows.installed_programs(read_entries=lambda: [registry_entry()])
    assert program.version is None
    assert program.publisher is None
    assert program.install_location is None
    assert program.install_date is None
    assert program.estimated_size_bytes is None
    assert program.uninstall_string is None


def test_installed_programs_treats_an_empty_string_field_as_missing():
    [program] = windows.installed_programs(
        read_entries=lambda: [registry_entry(Publisher="")]
    )
    assert program.publisher is None


def test_installed_programs_ignores_an_estimated_size_of_the_wrong_type():
    [program] = windows.installed_programs(
        read_entries=lambda: [registry_entry(EstimatedSize="not a number")]
    )
    assert program.estimated_size_bytes is None


def test_installed_programs_processes_several_entries_in_order():
    entries = [
        registry_entry(DisplayName="Primero"),
        registry_entry(DisplayName="Segundo"),
    ]
    programs = windows.installed_programs(read_entries=lambda: entries)
    assert [p.name for p in programs] == ["Primero", "Segundo"]


def test_installed_programs_defaults_to_the_real_registry_without_reading_it():
    parameters = inspect.signature(windows.installed_programs).parameters
    assert parameters["read_entries"].default is windows._read_all_uninstall_entries


def test_steam_install_path_reads_the_real_registry_shape():
    def read_values(hive, path):
        assert hive == windows.winreg.HKEY_CURRENT_USER
        assert path == "Software\\Valve\\Steam"
        return {"SteamPath": "c:/program files (x86)/steam", "SteamExe": "x"}

    assert windows.steam_install_path(read_values=read_values) == (
        "c:/program files (x86)/steam"
    )


def test_steam_install_path_is_none_when_steam_is_not_installed():
    assert windows.steam_install_path(read_values=lambda hive, path: None) is None


def test_steam_install_path_is_none_when_the_value_is_missing():
    assert windows.steam_install_path(read_values=lambda hive, path: {}) is None


def test_steam_install_path_is_none_when_the_value_is_an_empty_string():
    assert (
        windows.steam_install_path(read_values=lambda hive, path: {"SteamPath": ""})
        is None
    )


def test_steam_install_path_is_none_when_the_value_has_the_wrong_type():
    assert (
        windows.steam_install_path(read_values=lambda hive, path: {"SteamPath": 12345})
        is None
    )


def test_steam_install_path_defaults_to_the_real_registry_without_reading_it():
    parameters = inspect.signature(windows.steam_install_path).parameters
    assert parameters["read_values"].default is windows._registry_subkey_values
