"""Pruebas de los volúmenes, con discos simulados (nunca reales)."""

import collections
import inspect
import logging
import shutil
import types

import pytest

from discoclaro.adapters import volumes
from discoclaro.domain.models import Volume

Usage = collections.namedtuple("Usage", "total used free")
LOGGER = "discoclaro.adapters.volumes"

TABLE = {
    "C:\\": Usage(500, 200, 300),
    "D:\\": Usage(1000, 100, 900),
}


def fake_usage(table):
    """`disk_usage` falso: devuelve lo de `table` o lanza la excepción puesta."""

    def disk_usage(root):
        result = table[root]
        if isinstance(result, Exception):
            raise result
        return result

    return disk_usage


@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("C:\\", "C:"),
        ("D:/", "D:"),
        ("C:", "C:"),
        ("/", "/"),
        ("\\\\?\\Volume{abc}\\", "\\\\?\\Volume{abc}"),
    ],
)
def test_volume_name_is_the_short_form_of_the_root(root, expected):
    assert volumes.volume_name(root) == expected


def test_read_volume_builds_a_volume_from_the_usage():
    volume = volumes.read_volume("C:\\", fake_usage(TABLE))
    assert volume == Volume("C:", "C:\\", 500, 200, 300)


def test_list_volumes_keeps_the_order_of_the_roots():
    result = volumes.list_volumes(["D:\\", "C:\\"], fake_usage(TABLE))
    assert [volume.name for volume in result] == ["D:", "C:"]
    assert result[0].total_bytes == 1000


def test_list_volumes_accepts_any_iterable_of_roots():
    result = volumes.list_volumes((root for root in ["C:\\"]), fake_usage(TABLE))
    assert [volume.name for volume in result] == ["C:"]


def test_no_roots_gives_an_empty_list():
    assert volumes.list_volumes([], fake_usage(TABLE)) == []


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "denied"),
        FileNotFoundError(2, "not found"),
        OSError(21, "device not ready"),
        OSError(),
    ],
)
def test_unreadable_volumes_are_skipped_and_logged(error, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    table = {**TABLE, "E:\\": error}
    result = volumes.list_volumes(["C:\\", "E:\\", "D:\\"], fake_usage(table))
    assert [volume.name for volume in result] == ["C:", "D:"]
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "E:" in messages[0]


def test_a_hostile_root_cannot_fake_a_log_line(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    hostile = "X:\\\nWARNING fake line\x1b[31m"
    table = {hostile: PermissionError(13, "denied\nsecond line")}
    assert volumes.list_volumes([hostile], fake_usage(table)) == []
    message = caplog.records[0].getMessage()
    assert "\n" not in message
    assert "\x1b" not in message


def test_errors_that_are_not_disk_errors_are_not_swallowed():
    table = {"C:\\": ValueError("a bug, not a disk problem")}
    with pytest.raises(ValueError):
        volumes.list_volumes(["C:\\"], fake_usage(table))


def test_impossible_numbers_from_the_system_are_not_swallowed():
    table = {"C:\\": Usage(-1, 0, 0)}
    with pytest.raises(ValueError):
        volumes.list_volumes(["C:\\"], fake_usage(table))


def test_default_roots_outside_windows_say_so(monkeypatch):
    monkeypatch.setattr(volumes, "sys", types.SimpleNamespace(platform="linux"))
    with pytest.raises(NotImplementedError):
        volumes.default_roots()
    with pytest.raises(NotImplementedError):
        volumes.list_volumes(disk_usage=fake_usage(TABLE))


def test_default_roots_on_windows_come_from_the_platform_module(monkeypatch):
    monkeypatch.setattr(volumes, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(volumes.windows, "fixed_drive_roots", lambda: ["C:\\", "D:\\"])
    result = volumes.list_volumes(disk_usage=fake_usage(TABLE))
    assert [volume.name for volume in result] == ["C:", "D:"]


def test_the_real_reader_is_the_default_without_calling_it():
    parameters = inspect.signature(volumes.list_volumes).parameters
    assert parameters["disk_usage"].default is shutil.disk_usage
    assert inspect.signature(volumes.read_volume).parameters["disk_usage"].default is (
        shutil.disk_usage
    )
