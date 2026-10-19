"""Pruebas de la medición de carpetas compartida por los inspectores (T2C.1):
siempre sobre árboles sintéticos en `tmp_path`."""

from discoclaro.adapters.inspectors._folder import measure_folder
from discoclaro.adapters.scanner import scan_tree


def test_measure_folder_matches_what_scan_tree_measures(tmp_path):
    """No repite la cuenta de bytes de `scan_tree` (ya probada aparte): solo
    confirma que `measure_folder` de verdad delega en ella y devuelve el total
    de la raíz."""
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * 50)
    expected = scan_tree(str(tmp_path)).nodes[-1].size_bytes
    assert measure_folder(str(tmp_path)) == expected
    assert expected > 0


def test_measure_folder_of_an_empty_folder_is_zero(tmp_path):
    assert measure_folder(str(tmp_path)) == 0


def test_measure_folder_of_a_missing_path_is_none(tmp_path):
    assert measure_folder(str(tmp_path / "missing")) is None


def test_measure_folder_of_a_file_not_a_folder_is_none(tmp_path):
    file_path = tmp_path / "x.txt"
    file_path.write_text("hi", encoding="utf-8")
    assert measure_folder(str(file_path)) is None
