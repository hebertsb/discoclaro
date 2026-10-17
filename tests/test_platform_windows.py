"""Pruebas de lo específico de Windows, con discos simulados (nunca reales)."""

import pytest

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
