"""Listar una carpeta en Windows con tamaño en disco e identificador de cada archivo.

`os.scandir` solo entrega el tamaño lógico. La llamada `GetFileInformationByHandleEx`
con `FileIdExtdDirectoryInfo` devuelve, para cada elemento de la carpeta y en una sola
llamada: el tamaño lógico, el tamaño **en disco** (`AllocationSize`), los atributos, la
etiqueta de reanálisis y un identificador del contenido (`FileId`) que comparten los
enlaces duros. Así hacen las herramientas como WinDirStat.

Este módulo tiene una parte pura (`parse_entries`, `extended_path`), que se prueba en
cualquier sistema, y otra que llama a Windows (`enumerate_directory`).
"""

import ctypes
import errno
import functools
import stat
import struct
from collections.abc import Callable, Iterator
from ctypes import wintypes

from discoclaro.domain.models import DirectoryEntry

FILE_ID_EXTD_DIRECTORY_INFO = 19
FILE_ID_EXTD_DIRECTORY_RESTART_INFO = 20
ERROR_NO_MORE_FILES = 18
ERROR_INVALID_PARAMETER = 87

FILE_LIST_DIRECTORY = 0x1
FILE_SHARE_ALL = 0x7  # leer, escribir y borrar: no estorbar a nadie
OPEN_EXISTING = 3
# FILE_FLAG_BACKUP_SEMANTICS (permite abrir carpetas) y
# FILE_FLAG_OPEN_REPARSE_POINT (no sigue enlaces).
OPEN_FOLDER_FLAGS = 0x02000000 | 0x00200000
BUFFER_BYTES = 1 << 20
INVALID_HANDLE = ctypes.c_void_p(-1).value

IO_REPARSE_TAG_SYMLINK = 0xA000000C
_DIRECTORY = stat.FILE_ATTRIBUTE_DIRECTORY
_REPARSE_POINT = stat.FILE_ATTRIBUTE_REPARSE_POINT

# Parte fija de FILE_ID_EXTD_DIR_INFO, hasta ReparsePointTag: 72 bytes. Siguen el
# FileId (16 bytes, desde el 72) y el nombre en UTF-16 (desde el 88).
_HEADER = struct.Struct("<II6q4I")
_ID_OFFSET = 72
_NAME_OFFSET = 88


def extended_path(path: str) -> str:
    """La ruta con el prefijo `\\\\?\\`, que evita el límite de 260 caracteres."""
    path = path.replace("/", "\\")
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def parse_entries(
    buffer: bytes | bytearray | memoryview, folder: str
) -> Iterator[DirectoryEntry]:
    """Interpreta un bloque devuelto por Windows (estructuras `FILE_ID_EXTD_DIR_INFO`).

    Omite `.` y `..`. Los directorios que son puntos de reanálisis (enlaces, uniones,
    puntos de montaje, carpetas de la nube) y los enlaces simbólicos a archivos salen
    como `link`: no se siguen. De un archivo se toma el tamaño **en disco**. Lanza
    `OSError` si el bloque está mal formado.
    """
    size = len(buffer)
    prefix = folder.rstrip("\\") + "\\"
    offset = 0
    while True:
        if offset + _NAME_OFFSET > size:
            raise OSError(errno.EIO, "truncated directory listing")
        (next_offset, _, _, _, _, _, _, allocation, attributes, name_length, _, tag) = (
            _HEADER.unpack_from(buffer, offset)
        )
        name_start = offset + _NAME_OFFSET
        if name_start + name_length > size:
            raise OSError(errno.EIO, "directory listing name is out of bounds")
        name = bytes(buffer[name_start : name_start + name_length]).decode(
            "utf-16-le", "surrogatepass"
        )
        if name not in (".", ".."):
            raw_id = bytes(buffer[offset + _ID_OFFSET : offset + _NAME_OFFSET])
            yield _classify(prefix, name, attributes, tag, allocation, raw_id)
        if next_offset == 0:
            return
        if next_offset < _NAME_OFFSET:
            raise OSError(errno.EIO, "directory listing entry is too small")
        offset += next_offset


def _classify(
    prefix: str, name: str, attributes: int, tag: int, allocation: int, raw_id: bytes
) -> DirectoryEntry:
    path = prefix + name
    if attributes & _DIRECTORY:
        kind = "link" if attributes & _REPARSE_POINT else "folder"
        return DirectoryEntry(name, path, kind)
    if attributes & _REPARSE_POINT and tag == IO_REPARSE_TAG_SYMLINK:
        return DirectoryEntry(name, path, "link")
    file_id = raw_id if any(raw_id) else None
    return DirectoryEntry(name, path, "file", allocation, file_id)


def enumerate_directory(
    path: str,
    *,
    api: object | None = None,
    last_error: Callable[[], int] | None = None,
) -> Iterator[DirectoryEntry]:
    """Lista los elementos de una carpeta con la llamada de Windows.

    Lanza `FileNotFoundError` si no existe, `NotADirectoryError` si es un archivo y
    `OSError` (o `PermissionError`) en cualquier otro fallo, tanto al abrirla como a
    mitad de la lista. Admite rutas de más de 260 caracteres. No es reentrante: usa un
    único bloque de memoria, así que hay que terminar de recorrer una lista antes de
    pedir otra. `api` y `last_error` se pueden inyectar para probar.
    """
    kernel32 = api or _load_api()
    get_last_error = last_error or ctypes.get_last_error
    handle = kernel32.CreateFileW(
        extended_path(path),
        FILE_LIST_DIRECTORY,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        OPEN_FOLDER_FLAGS,
        None,
    )
    if handle in (None, INVALID_HANDLE):
        raise _windows_error(get_last_error())
    try:
        buffer, view = _listing_buffer()
        info_class = FILE_ID_EXTD_DIRECTORY_RESTART_INFO
        while True:
            if not kernel32.GetFileInformationByHandleEx(
                handle, info_class, buffer, BUFFER_BYTES
            ):
                code = get_last_error()
                if code == ERROR_NO_MORE_FILES:
                    return
                if (
                    code == ERROR_INVALID_PARAMETER
                    and info_class == FILE_ID_EXTD_DIRECTORY_RESTART_INFO
                ):
                    raise NotADirectoryError(errno.ENOTDIR, "not a directory", path)
                raise _windows_error(code)
            info_class = FILE_ID_EXTD_DIRECTORY_INFO
            yield from parse_entries(view, path)
    finally:
        kernel32.CloseHandle(handle)


def _windows_error(code: int) -> OSError:
    """El `OSError` que corresponde a un código de error de Windows."""
    winerror = getattr(ctypes, "WinError", None)
    if winerror is not None:
        return winerror(code)
    return OSError(code, f"Windows error {code}")


@functools.cache
def _load_api() -> object:
    """`kernel32` con los tipos de las funciones usadas (solo existe en Windows)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


@functools.cache
def _listing_buffer() -> tuple[ctypes.Array[ctypes.c_char], memoryview]:
    """El bloque de memoria que reutiliza cada lista, para no pedir uno por carpeta."""
    buffer = ctypes.create_string_buffer(BUFFER_BYTES)
    return buffer, memoryview(buffer).cast("B")
