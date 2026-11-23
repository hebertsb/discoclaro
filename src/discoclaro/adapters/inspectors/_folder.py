"""Medir una carpeta de verdad (T2C.1), para los inspectores que no tienen un
comando propio y confiable para decir su propio tamaño (uv, npm: ver sus
módulos para el motivo de cada uno). Reusa el mismo medidor del escaneo
(`adapters/scanner.py`), no uno nuevo.
"""

import contextlib
import json
import os

from discoclaro.adapters.scanner import scan_tree
from discoclaro.adapters.store import Store
from discoclaro.platform import windows

_folder_size_cache: dict[str, tuple[int, int]] = {}
_disk_cache_loaded = False
_force_live_measure = False


def invalidate_folder_cache() -> None:
    """Invalida los tamaños cacheados de carpetas tras una limpieza o comando
    para que `measure_folder` mida el tamaño real en disco en vez de leer la
    foto anterior de `discoclaro.db`."""
    global _force_live_measure
    _folder_size_cache.clear()
    _force_live_measure = True
    with contextlib.suppress(OSError):
        _disk_cache_file().unlink(missing_ok=True)


def _disk_cache_file():
    return windows.default_data_dir() / "folder_sizes_cache.json"


def _ensure_disk_cache_loaded() -> None:
    global _disk_cache_loaded
    if _disk_cache_loaded:
        return
    _disk_cache_loaded = True
    with contextlib.suppress(Exception):
        p = _disk_cache_file()
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            for k, pair in raw.items():
                if isinstance(pair, list) and len(pair) == 2:
                    _folder_size_cache[k] = (int(pair[0]), int(pair[1]))


def _save_disk_cache() -> None:
    with contextlib.suppress(Exception):
        p = _disk_cache_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: [v[0], v[1]] for k, v in _folder_size_cache.items()}
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _size_from_latest_snapshot(
    norm_path: str, *, folder_mtime: float | None = None
) -> int | None:
    """Si la carpeta ya fue medida en la última foto de su unidad en
    `discoclaro.db` y no se modificó después de esa foto, devuelve su
    `size_bytes` en <1 ms sin volver a recorrer cientos de miles de archivos."""
    try:
        from datetime import datetime

        db_path = windows.default_data_dir() / "discoclaro.db"
        if not db_path.is_file():
            return None
        drive = os.path.splitdrive(norm_path)[0].upper()
        with Store.open_readonly(db_path) as store:
            snaps = [
                s
                for s in store.snapshots(drive if drive else None)
                if s.is_volume_scan
            ]
            if not snaps or snaps[0].id is None:
                return None
            if folder_mtime is not None:
                snap_ts = datetime.fromisoformat(snaps[0].taken_at).timestamp()
                if folder_mtime > snap_ts:
                    return None
            node = store.node_at_path(snaps[0].id, norm_path)
            if node is not None and node.size_bytes > 0:
                return node.size_bytes
    except Exception:
        return None
    return None


def measure_folder(path: str) -> int | None:
    """El tamaño total de `path`, o `None` si no existe, no es una carpeta o
    no se pudo leer."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isdir(path):
        return None
    _ensure_disk_cache_loaded()
    abs_path = os.path.abspath(path)
    norm = os.path.normcase(abs_path)
    cached = _folder_size_cache.get(norm)
    if cached is not None and cached[0] == st.st_mtime_ns:
        return cached[1]

    if not _force_live_measure:
        snap_size = _size_from_latest_snapshot(abs_path, folder_mtime=st.st_mtime)
        if snap_size is not None:
            _folder_size_cache[norm] = (st.st_mtime_ns, snap_size)
            _save_disk_cache()
            return snap_size

    try:
        result = scan_tree(path)
    except OSError:
        return None
    size = result.nodes[-1].size_bytes
    _folder_size_cache[norm] = (st.st_mtime_ns, size)
    _save_disk_cache()
    return size
