"""Pruebas de la verificación de integridad: siempre con un `dist-info` sintético.

Nunca se compara contra la instalación real: `Distribution.at(ruta)` arma una
distribución de `importlib.metadata` a partir de una carpeta cualquiera en `tmp_path`.
"""

import base64
import hashlib
import json
from importlib import metadata as importlib_metadata

import pytest

from discoclaro.adapters.package_integrity import (
    DistributionNotFound,
    IntegrityCheck,
    check_installed_files,
    inspect_distribution,
    is_writable_by_the_user,
)


def _hash_of(data: bytes, algorithm="sha256") -> str:
    digest = hashlib.new(algorithm, data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def make_distribution(tmp_path, *, editable=False, files=None, record_lines=None):
    """Arma un `dist-info` de verdad en `tmp_path`, como lo dejaría un instalador."""
    site_packages = tmp_path / "site-packages"
    package_dir = site_packages / "mypkg"
    package_dir.mkdir(parents=True)
    files = files or {"__init__.py": b"x = 1\n"}
    for name, content in files.items():
        (package_dir / name).write_bytes(content)

    dist_info = site_packages / "mypkg-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: mypkg\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": "file:///x", "dir_info": {"editable": editable}}),
        encoding="utf-8",
    )
    if record_lines is None:
        record_lines = [
            f"mypkg/{name},sha256={_hash_of(content)},{len(content)}"
            for name, content in files.items()
        ]
    record_lines.append("mypkg-1.0.dist-info/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(record_lines) + "\n", encoding="utf-8")
    return importlib_metadata.Distribution.at(dist_info)


def test_a_fresh_install_with_matching_hashes_is_ok(tmp_path):
    dist = make_distribution(tmp_path)
    check = inspect_distribution(dist)
    assert check == IntegrityCheck(
        ok=True, editable=False, mismatches=(), base_dir=check.base_dir
    )
    assert check.base_dir == tmp_path / "site-packages"


def test_a_file_changed_after_install_is_a_mismatch(tmp_path):
    dist = make_distribution(tmp_path)
    (tmp_path / "site-packages" / "mypkg" / "__init__.py").write_bytes(b"x = 2\n")
    check = inspect_distribution(dist)
    assert check.ok is False
    assert check.mismatches == ("mypkg/__init__.py",)


def test_a_file_that_was_removed_after_install_is_a_mismatch(tmp_path):
    dist = make_distribution(tmp_path)
    (tmp_path / "site-packages" / "mypkg" / "__init__.py").unlink()
    check = inspect_distribution(dist)
    assert check.ok is False
    assert check.mismatches == ("mypkg/__init__.py",)


def test_several_files_can_be_checked_and_several_can_mismatch(tmp_path):
    dist = make_distribution(
        tmp_path, files={"a.py": b"aaa", "b.py": b"bbb", "c.py": b"ccc"}
    )
    (tmp_path / "site-packages" / "mypkg" / "b.py").write_bytes(b"tampered")
    check = inspect_distribution(dist)
    assert check.ok is False
    assert check.mismatches == ("mypkg/b.py",)


def test_an_editable_install_is_ok_without_checking_any_file(tmp_path):
    dist = make_distribution(tmp_path, editable=True)
    (tmp_path / "site-packages" / "mypkg" / "__init__.py").write_bytes(b"tampered")
    check = inspect_distribution(dist)
    assert check == IntegrityCheck(
        ok=True, editable=True, mismatches=(), base_dir=check.base_dir
    )


def test_a_record_with_no_hash_for_a_file_does_not_count_as_a_mismatch(tmp_path):
    """El propio RECORD no tiene hash de sí mismo; eso es normal, no una alarma."""
    dist = make_distribution(tmp_path)
    check = inspect_distribution(dist)
    assert "mypkg-1.0.dist-info/RECORD" not in check.mismatches


def test_an_unknown_hash_algorithm_is_treated_as_a_mismatch(tmp_path):
    content = b"x = 1\n"
    dist = make_distribution(
        tmp_path,
        files={"__init__.py": content},
        record_lines=[f"mypkg/__init__.py,md5=notreal,{len(content)}"],
    )
    check = inspect_distribution(dist)
    assert check.mismatches == ("mypkg/__init__.py",)


def test_no_record_at_all_is_not_ok(tmp_path):
    site_packages = tmp_path / "site-packages"
    dist_info = site_packages / "mypkg-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: mypkg\n", encoding="utf-8")
    check = inspect_distribution(importlib_metadata.Distribution.at(dist_info))
    assert check.ok is False
    assert check.editable is False


def test_check_installed_files_raises_for_an_unknown_package():
    with pytest.raises(DistributionNotFound):
        check_installed_files("this-package-does-not-exist-at-all")


def test_check_installed_files_reads_the_real_discoclaro_install():
    """Este repo está instalado en modo editable: debe dar `ok` sin comprobar nada."""
    check = check_installed_files("discoclaro")
    assert check.ok is True
    assert check.editable is True


def test_is_writable_by_the_user_on_a_real_writable_folder(tmp_path):
    assert is_writable_by_the_user(tmp_path) is True


def test_is_writable_by_the_user_is_false_when_the_folder_does_not_exist(tmp_path):
    assert is_writable_by_the_user(tmp_path / "no_existe") is False


def test_is_writable_by_the_user_does_not_leave_the_probe_file_behind(tmp_path):
    is_writable_by_the_user(tmp_path)
    assert list(tmp_path.iterdir()) == []
