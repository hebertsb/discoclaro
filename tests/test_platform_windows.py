"""Pruebas de lo específico de Windows, con discos simulados (nunca reales)."""

import inspect
import logging
import os
import stat
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
