"""Detección rápida de archivos duplicados por tamaño exacto y huella parcial SHA-256.

Combina los archivos grandes registrados en la última foto (`large_files`) con un
muestreo de las carpetas de usuario más propensas a acumular copias repetidas
(`Downloads`, `Desktop`, `Documents`), agrupando primero por tamaño en bytes y
verificando después con un hash SHA-256 de muestra (primeros 64 KB + mitad +
últimos 64 KB) para que incluso archivos de varios GB se comparen en milisegundos.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.store import Store
from discoclaro.platform import windows
from discoclaro.services import tree_service

SAMPLE_CHUNK_BYTES = 64 * 1024
MIN_DUPLICATE_BYTES = 1 * 1024 * 1024  # 1 MB
# OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS
_CLOUD_ONLY_ATTRS = 0x1000 | 0x00040000 | 0x00400000
_DUP_CACHE_TTL_S = 120.0
_dup_cache: dict[str, tuple[float, DuplicatesOverview]] = {}


def _is_cloud_placeholder(path: str) -> bool:
    """Detecta archivos solo-nube (OneDrive Files On-Demand) para no disparar
    descargas lentas al calcular la huella SHA-256."""
    try:
        st = os.stat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & _CLOUD_ONLY_ATTRS)
    except OSError:
        return True


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """Grupo de archivos con el mismo tamaño y contenido muestreado."""

    filename: str
    size_bytes: int
    reclaimable_bytes: int
    fingerprint: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DuplicatesOverview:
    """Resumen de duplicados detectados en una unidad."""

    volume: str
    total_reclaimable_bytes: int
    groups: tuple[DuplicateGroup, ...]


def _fast_file_fingerprint(path: str, size_bytes: int) -> str | None:
    """Calcula un hash SHA-256 rápido leyendo hasta 3 bloques de 64 KB (inicio,
    centro y final del archivo). Si el archivo es un placeholder solo-nube o no
    está accesible en disco, usa un identificador por nombre base y tamaño."""
    if _is_cloud_placeholder(path):
        base = ntpath.basename(path).lower()
        return f"meta:{base}:{size_bytes}"
    try:
        with open(path, "rb") as fh:
            hasher = hashlib.sha256()
            hasher.update(str(size_bytes).encode("ascii"))
            head = fh.read(SAMPLE_CHUNK_BYTES)
            hasher.update(head)
            if size_bytes > SAMPLE_CHUNK_BYTES * 2:
                fh.seek(size_bytes // 2)
                hasher.update(fh.read(SAMPLE_CHUNK_BYTES))
                fh.seek(max(0, size_bytes - SAMPLE_CHUNK_BYTES))
                hasher.update(fh.read(SAMPLE_CHUNK_BYTES))
            return hasher.hexdigest()[:16]
    except OSError:
        base = ntpath.basename(path).lower()
        return f"meta:{base}:{size_bytes}"


def _scan_user_common_files(volume_prefix: str) -> list[tuple[str, int]]:
    """Escanea hasta 2 niveles en Downloads, Desktop y Documents del usuario si
    pertenecen a la unidad consultada, para detectar instaladores (.exe, .msi,
    .iso, .zip) o documentos pesados repetidos desde1 MB."""
    home = Path.home()
    if not str(home).upper().startswith(volume_prefix.upper()):
        return []
    found: list[tuple[str, int]] = []
    for folder_name in ("Downloads", "Desktop", "Documents"):
        folder = home / folder_name
        if not folder.is_dir():
            continue
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            if st.st_size >= MIN_DUPLICATE_BYTES:
                                found.append((entry.path, int(st.st_size)))
                        elif entry.is_dir(follow_symlinks=False):
                            with os.scandir(entry.path) as subentries:
                                for sub in subentries:
                                    if sub.is_file(follow_symlinks=False):
                                        st = sub.stat(follow_symlinks=False)
                                        if st.st_size >= MIN_DUPLICATE_BYTES:
                                            found.append((sub.path, int(st.st_size)))
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def find_duplicates(
    volume: str | None = None,
    *,
    data_dir: Path | None = None,
    limit: int = 40,
) -> DuplicatesOverview:
    """Encuentra grupos de archivos duplicados en la unidad seleccionada."""
    import time

    resolved_vol = (volume or "C:").strip().upper()
    if not resolved_vol.endswith(":"):
        resolved_vol = resolved_vol.rstrip("\\")
    cache_key = f"{data_dir}:{resolved_vol}:{limit}"
    cached = _dup_cache.get(cache_key)
    if cached is not None and (time.monotonic() - cached[0]) < _DUP_CACHE_TTL_S:
        return cached[1]

    deadline = time.monotonic() + 6.0
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / "discoclaro.db"
    candidates: dict[str, tuple[str, int]] = {}

    if database.is_file():
        with Store.open_readonly(database) as store:
            all_snaps = store.snapshots()
            if all_snaps:
                snap = tree_service._pick_snapshot_for_volume(all_snaps, volume)
                resolved_vol = snap.volume
                for lf in store.large_files(snap.id):
                    if lf.size_bytes >= MIN_DUPLICATE_BYTES:
                        candidates[lf.path.lower()] = (lf.path, lf.size_bytes)

    for path_str, size_bytes in _scan_user_common_files(resolved_vol):
        candidates.setdefault(path_str.lower(), (path_str, size_bytes))

    by_size: dict[int, list[str]] = defaultdict(list)
    for orig_path, sz in candidates.values():
        by_size[sz].append(orig_path)

    groups: list[DuplicateGroup] = []
    # Prioriza tamaños grandes primero para encontrar los duplicados de mayor impacto
    for sz, paths in sorted(by_size.items(), key=lambda kv: kv[0], reverse=True):
        if len(paths) < 2:
            continue
        if time.monotonic() > deadline:
            break
        by_fp: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            fp = _fast_file_fingerprint(p, sz)
            if fp is not None:
                by_fp[fp].append(p)
        for fp, matching_paths in by_fp.items():
            if len(matching_paths) < 2:
                continue
            sorted_paths = tuple(sorted(matching_paths))
            reclaimable = sz * (len(sorted_paths) - 1)
            groups.append(
                DuplicateGroup(
                    filename=ntpath.basename(sorted_paths[0]),
                    size_bytes=sz,
                    reclaimable_bytes=reclaimable,
                    fingerprint=fp,
                    paths=sorted_paths,
                )
            )

    groups.sort(key=lambda g: g.reclaimable_bytes, reverse=True)
    top_groups = tuple(groups[:limit])
    total_reclaimable = sum(g.reclaimable_bytes for g in top_groups)
    overview = DuplicatesOverview(
        volume=resolved_vol,
        total_reclaimable_bytes=total_reclaimable,
        groups=top_groups,
    )
    _dup_cache[cache_key] = (time.monotonic(), overview)
    return overview
