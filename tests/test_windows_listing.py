"""Pruebas de la enumeración de carpetas de Windows."""

import ctypes
import os
import shutil
import struct
import sys

import pytest

from discoclaro.domain.models import DirectoryEntry
from discoclaro.platform import windows_listing as listing
from discoclaro.platform.windows_listing import (
    enumerate_directory,
    extended_path,
    parse_entries,
)

DIRECTORY = 0x10
REPARSE = 0x400
SYMLINK_TAG = 0xA000000C
WOF_TAG = 0x80000017
FILE_ID = bytes(range(1, 17))
HEADER = struct.Struct("<II6q4I")

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="usa la API de Windows"
)


def pack_entry(
    name,
    *,
    attributes=0,
    allocation=0,
    end_of_file=0,
    tag=0,
    file_id=bytes(16),
    last=False,
):
    """Arma a mano una estructura FILE_ID_EXTD_DIR_INFO, alineada a 8 bytes."""
    raw_name = name.encode("utf-16-le", "surrogatepass")
    size = 88 + len(raw_name)
    padded = (size + 7) // 8 * 8
    header = HEADER.pack(
        0 if last else padded,
        0,
        0,
        0,
        0,
        0,
        end_of_file,
        allocation,
        attributes,
        len(raw_name),
        0,
        tag,
    )
    return header + file_id + raw_name + bytes(padded - size)


def block(*entries):
    """Un bloque como el que devuelve Windows: las entradas encadenadas."""
    last = len(entries) - 1
    return b"".join(
        pack_entry(**entry, last=(index == last)) for index, entry in enumerate(entries)
    )


def parsed(raw, folder="C:\\dir"):
    return list(parse_entries(raw, folder))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:\\Windows", "\\\\?\\C:\\Windows"),
        ("C:\\", "\\\\?\\C:\\"),
        ("C:/Users/x", "\\\\?\\C:\\Users\\x"),
        ("\\\\?\\C:\\ya", "\\\\?\\C:\\ya"),
        ("\\\\server\\share\\x", "\\\\?\\UNC\\server\\share\\x"),
    ],
)
def test_extended_paths(path, expected):
    assert extended_path(path) == expected


def test_a_file_has_its_size_on_disk_and_its_id():
    raw = block(
        {"name": "a.txt", "allocation": 8192, "end_of_file": 5000, "file_id": FILE_ID}
    )
    assert parsed(raw) == [
        DirectoryEntry("a.txt", "C:\\dir\\a.txt", "file", 8192, FILE_ID)
    ]


def test_dot_entries_are_skipped():
    raw = block(
        {"name": ".", "attributes": DIRECTORY},
        {"name": "..", "attributes": DIRECTORY},
        {"name": "real", "allocation": 4096},
    )
    assert [entry.name for entry in parsed(raw)] == ["real"]


def test_folders_and_folders_that_are_reparse_points():
    raw = block(
        {"name": "carpeta", "attributes": DIRECTORY},
        {"name": "union", "attributes": DIRECTORY | REPARSE, "tag": 0xA0000003},
    )
    assert [(e.name, e.kind) for e in parsed(raw)] == [
        ("carpeta", "folder"),
        ("union", "link"),
    ]


def test_symlinks_to_files_are_links_but_other_reparse_files_are_files():
    raw = block(
        {"name": "enlace", "attributes": REPARSE, "tag": SYMLINK_TAG},
        {"name": "compacto", "attributes": REPARSE, "tag": WOF_TAG, "allocation": 100},
    )
    kinds = {entry.name: entry for entry in parsed(raw)}
    assert kinds["enlace"].kind == "link"
    assert (kinds["compacto"].kind, kinds["compacto"].size_bytes) == ("file", 100)


def test_an_all_zero_id_means_the_id_is_unknown():
    raw = block({"name": "x", "allocation": 10})
    assert parsed(raw)[0].file_id is None


def test_the_size_taken_is_the_one_on_disk_not_the_logical_one():
    raw = block({"name": "x", "allocation": 0, "end_of_file": 10**9})
    assert parsed(raw)[0].size_bytes == 0


@pytest.mark.parametrize(
    ("folder", "path"),
    [("C:\\", "C:\\x"), ("C:\\dir", "C:\\dir\\x"), ("C:\\dir\\", "C:\\dir\\x")],
)
def test_paths_are_built_with_a_single_backslash(folder, path):
    assert parsed(block({"name": "x"}), folder)[0].path == path


def test_names_with_unicode_and_with_lone_surrogates_come_back_unchanged():
    names = ["ñandú 日本語 📁", "a\ud800b", "con espacios  y 'comillas'"]
    raw = block(*({"name": name} for name in names))
    assert [entry.name for entry in parsed(raw)] == names


def test_any_buffer_type_is_accepted():
    raw = block({"name": "a"}, {"name": "b"})
    for buffer in (raw, bytearray(raw), memoryview(raw)):
        assert [entry.name for entry in parsed(buffer)] == ["a", "b"]


def test_a_long_chain_keeps_its_order():
    raw = block(*({"name": f"n{index}", "allocation": index} for index in range(200)))
    result = parsed(raw)
    assert [entry.name for entry in result] == [f"n{index}" for index in range(200)]
    assert [entry.size_bytes for entry in result] == list(range(200))


def test_a_block_that_is_cut_short_is_an_error():
    with pytest.raises(OSError):
        parsed(b"")
    with pytest.raises(OSError):
        parsed(block({"name": "abc"})[:60])


def test_a_name_that_runs_past_the_end_of_the_block_is_an_error():
    raw = HEADER.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 1000, 0, 0) + bytes(16) + b"xx"
    with pytest.raises(OSError):
        parsed(raw)


def test_an_entry_that_points_to_itself_cannot_loop_forever():
    raw = HEADER.pack(8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) + bytes(16) + bytes(8)
    with pytest.raises(OSError):
        parsed(raw)


class FakeKernel32:
    """`kernel32` falso que reparte bloques ya armados."""

    def __init__(self, batches=(), handle=123):
        self.batches = list(batches)
        self.handle = handle
        self.opened = None
        self.info_classes = []
        self.closed = []

    def CreateFileW(self, path, access, share, security, disposition, flags, template):  # noqa: N802, E501
        self.opened = (path, access, share, disposition, flags)
        return self.handle

    def GetFileInformationByHandleEx(self, handle, info_class, buffer, size):  # noqa: N802
        self.info_classes.append(info_class)
        if not self.batches:
            return 0
        data = self.batches.pop(0)
        ctypes.memmove(buffer, data, len(data))
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return 1


def listed(api, last_error=lambda: listing.ERROR_NO_MORE_FILES, path="C:\\dir"):
    return list(enumerate_directory(path, api=api, last_error=last_error))


def test_the_listing_is_read_in_several_calls_until_windows_says_there_is_no_more():
    api = FakeKernel32([block({"name": "a"}, {"name": "b"}), block({"name": "c"})])
    assert [entry.name for entry in listed(api)] == ["a", "b", "c"]
    assert api.info_classes == [20, 19, 19]
    assert api.closed == [123]


def test_the_folder_is_opened_with_the_long_path_prefix_and_without_following_links():
    api = FakeKernel32()
    listed(api)
    path, access, share, disposition, flags = api.opened
    assert path == "\\\\?\\C:\\dir"
    assert access == listing.FILE_LIST_DIRECTORY
    assert share == listing.FILE_SHARE_ALL
    assert disposition == listing.OPEN_EXISTING
    assert flags & 0x00200000


def test_an_empty_folder_gives_an_empty_list_and_closes_the_handle():
    api = FakeKernel32()
    assert listed(api) == []
    assert api.closed == [123]


def test_stopping_early_still_closes_the_handle():
    api = FakeKernel32([block({"name": "a"}, {"name": "b"})])
    generator = enumerate_directory("C:\\dir", api=api, last_error=lambda: 18)
    assert next(generator).name == "a"
    generator.close()
    assert api.closed == [123]


def test_a_folder_that_cannot_be_opened_is_an_os_error():
    api = FakeKernel32(handle=listing.INVALID_HANDLE)
    with pytest.raises(OSError):
        listed(api, last_error=lambda: 5)
    assert api.closed == []


def test_asking_to_list_a_file_is_reported_as_not_a_directory():
    api = FakeKernel32()
    with pytest.raises(NotADirectoryError):
        listed(api, last_error=lambda: listing.ERROR_INVALID_PARAMETER)
    assert api.closed == [123]


def test_the_same_error_after_the_first_call_is_not_a_missing_directory():
    api = FakeKernel32([block({"name": "a"})])
    with pytest.raises(OSError) as error:
        listed(api, last_error=lambda: listing.ERROR_INVALID_PARAMETER)
    assert not isinstance(error.value, NotADirectoryError)
    assert api.closed == [123]


def test_any_other_error_in_the_middle_is_an_os_error_and_closes_the_handle():
    api = FakeKernel32([block({"name": "a"})])
    with pytest.raises(OSError):
        listed(api, last_error=lambda: 5)
    assert api.closed == [123]


@windows_only
def test_a_real_folder_with_known_sizes_and_a_hard_link(tmp_path):
    (tmp_path / "vacio").write_bytes(b"")
    (tmp_path / "cinco_mil").write_bytes(b"x" * 5000)
    (tmp_path / "un_mib").write_bytes(b"x" * (1024 * 1024))
    os.link(tmp_path / "cinco_mil", tmp_path / "enlace_duro")
    (tmp_path / "carpeta").mkdir()
    found = {entry.name: entry for entry in enumerate_directory(str(tmp_path))}
    assert set(found) == {"vacio", "cinco_mil", "un_mib", "enlace_duro", "carpeta"}
    assert found["carpeta"].kind == "folder"
    for name in ("vacio", "cinco_mil", "un_mib", "enlace_duro"):
        assert found[name].kind == "file"
    assert found["vacio"].size_bytes == 0
    assert 5000 <= found["cinco_mil"].size_bytes < 5000 + 64 * 1024
    assert found["un_mib"].size_bytes >= 1024 * 1024
    assert found["cinco_mil"].file_id is not None
    assert found["cinco_mil"].file_id == found["enlace_duro"].file_id
    assert found["un_mib"].file_id != found["cinco_mil"].file_id
    assert found["cinco_mil"].path == str(tmp_path / "cinco_mil")


@windows_only
def test_a_real_junction_is_a_link_and_is_not_confused_with_a_folder(tmp_path):
    import _winapi

    target = tmp_path / "destino"
    target.mkdir()
    _winapi.CreateJunction(str(target), str(tmp_path / "union"))
    kinds = {e.name: e.kind for e in enumerate_directory(str(tmp_path))}
    assert kinds == {"destino": "folder", "union": "link"}


@windows_only
def test_real_symlinks_are_links(tmp_path):
    target = tmp_path / "destino"
    target.mkdir()
    (target / "f").write_bytes(b"x")
    try:
        os.symlink(target / "f", tmp_path / "enlace_archivo")
        os.symlink(target, tmp_path / "enlace_carpeta", target_is_directory=True)
    except OSError:
        pytest.skip("este equipo no permite crear enlaces simbólicos")
    kinds = {e.name: e.kind for e in enumerate_directory(str(tmp_path))}
    assert kinds["enlace_archivo"] == "link"
    assert kinds["enlace_carpeta"] == "link"
    assert kinds["destino"] == "folder"


@windows_only
def test_a_missing_folder_and_a_file_are_reported_with_the_right_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(enumerate_directory(str(tmp_path / "no-existe")))
    a_file = tmp_path / "archivo.txt"
    a_file.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        list(enumerate_directory(str(a_file)))


@windows_only
def test_an_empty_real_folder_gives_nothing(tmp_path):
    assert list(enumerate_directory(str(tmp_path))) == []


@windows_only
def test_a_path_longer_than_260_characters_can_be_listed(tmp_path):
    deep = tmp_path
    for _ in range(6):
        deep = deep / ("carpeta_larga_" + "x" * 40)
    try:
        os.makedirs(extended_path(str(deep)))
        with open(extended_path(str(deep / "dentro.txt")), "wb") as handle:
            handle.write(b"hola")
        assert len(str(deep)) > 260
        names = [entry.name for entry in enumerate_directory(str(deep))]
        assert names == ["dentro.txt"]
    finally:
        shutil.rmtree(extended_path(str(tmp_path / ("carpeta_larga_" + "x" * 40))))


@pytest.fixture
def small_buffer(monkeypatch):
    monkeypatch.setattr(listing, "BUFFER_BYTES", 4096)
    listing._listing_buffer.cache_clear()
    yield
    listing._listing_buffer.cache_clear()


@windows_only
def test_a_folder_bigger_than_the_buffer_is_listed_in_several_calls(
    tmp_path, small_buffer
):
    names = {
        f"archivo_con_un_nombre_bastante_largo_{index:04d}.txt" for index in range(300)
    }
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    found = [entry.name for entry in enumerate_directory(str(tmp_path))]
    assert len(found) == 300
    assert set(found) == names
