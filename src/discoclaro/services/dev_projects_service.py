"""Escáner de proyectos de programación antiguos y sus carpetas compiladas/dependencias.

Identifica proyectos reales (por manifiestos como `package.json`, `pyproject.toml`,
`pubspec.yaml`, `Cargo.toml`, `pom.xml`, `build.gradle`, `*.csproj` o `.git`),
calcula cuántos días llevan sin modificarse a partir de su código fuente/manifiesto
y lista **únicamente** las carpetas regenerables/compiladas (`node_modules`, `.venv`,
`venv`, `dist`, `build`, `.next`, `.nuxt`, `target`, `__pycache__`, `.gradle`,
`obj`, `.dart_tool`) para liberar decenas de GB sin tocar nunca el código fuente.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from discoclaro.adapters.config import load_user_config
from discoclaro.adapters.scanner import scan_tree
from discoclaro.adapters.store import Store
from discoclaro.platform import windows

PROJECT_MARKERS = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "pubspec.yaml",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "composer.json",
        "go.mod",
        "cmakelists.txt",
        ".git",
    }
)

CLEANABLE_ARTIFACT_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "target",
        "__pycache__",
        ".gradle",
        ".dart_tool",
        "obj",
        ".parcel-cache",
        ".turbo",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

SKIP_WALK_DIRS = frozenset(
    {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "$recycle.bin",
        "system volume information",
        "appdata",
        ".git",
        *CLEANABLE_ARTIFACT_DIRS,
    }
)

STALE_DAYS_THRESHOLD = 30
MIN_ARTIFACT_BYTES = 2 * 1024 * 1024  # 2 MB


@dataclass(frozen=True, slots=True)
class ProjectArtifact:
    """Una carpeta compilada o de dependencias regenerable dentro de un proyecto."""

    name: str
    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DevProjectEntry:
    """Un proyecto de software detectado con sus artefactos limpiables."""

    name: str
    root_path: str
    ecosystem: str
    last_modified_iso: str
    days_inactive: int
    is_stale: bool
    reclaimable_bytes: int
    artifacts: tuple[ProjectArtifact, ...]


@dataclass(frozen=True, slots=True)
class DevProjectsOverview:
    """Resumen completo de proyectos escaneados y espacio recuperable."""

    total_reclaimable_bytes: int
    stale_reclaimable_bytes: int
    projects: tuple[DevProjectEntry, ...]


def _detect_ecosystem(entries_lower: set[str]) -> str:
    if "pubspec.yaml" in entries_lower:
        return "Flutter / Dart"
    if "package.json" in entries_lower:
        return "Node.js / Web"
    if (
        "pyproject.toml" in entries_lower
        or "requirements.txt" in entries_lower
        or "setup.py" in entries_lower
    ):
        return "Python"
    if "cargo.toml" in entries_lower:
        return "Rust"
    if (
        "pom.xml" in entries_lower
        or "build.gradle" in entries_lower
        or "build.gradle.kts" in entries_lower
    ):
        return "Java / Gradle"
    if "go.mod" in entries_lower:
        return "Go"
    if "composer.json" in entries_lower:
        return "PHP"
    return "Código / Git"


def _project_last_activity_ts(
    project_dir: Path, entry_names: list[os.DirEntry[str]]
) -> float:
    """Estima la última vez que el usuario tocó el proyecto revisando archivos
    raíz y subcarpetas de código (`src`, `lib`, `app`) sin contar artefactos."""
    latest = 0.0
    try:
        latest = project_dir.stat().st_mtime
    except OSError:
        pass
    for entry in entry_names:
        low = entry.name.lower()
        if low in CLEANABLE_ARTIFACT_DIRS:
            continue
        try:
            st = entry.stat(follow_symlinks=False)
            if st.st_mtime > latest:
                latest = st.st_mtime
        except OSError:
            continue
    return latest if latest > 0 else time.time()


def _quick_dir_size(path: str, snapshot_sizes: dict[str, int]) -> int:
    """Obtiene el tamaño de una carpeta artefacto usando primero el mapa de
    `discoclaro.db` (<1 ms) y si no está, midiendo con `scan_tree`."""
    norm = os.path.normcase(os.path.abspath(path))
    if norm in snapshot_sizes:
        return snapshot_sizes[norm]
    try:
        res = scan_tree(path)
        return res.nodes[-1].size_bytes if res.nodes else 0
    except OSError:
        return 0


def _load_snapshot_size_map(data_dir: Path | None) -> dict[str, int]:
    """Carga un mapa rápido `ruta_lower -> size_bytes` de las fotos más recientes."""
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / "discoclaro.db"
    if not database.is_file():
        return {}
    sizes: dict[str, int] = {}
    try:
        with Store.open_readonly(database) as store:
            seen_vols: set[str] = set()
            for snap in store.snapshots():
                if not snap.is_volume_scan or snap.volume in seen_vols:
                    continue
                seen_vols.add(snap.volume)
                for node in store.nodes(snap.id):
                    low_name = os.path.basename(node.path).lower()
                    if low_name in CLEANABLE_ARTIFACT_DIRS:
                        sizes[os.path.normcase(os.path.abspath(node.path))] = (
                            node.size_bytes
                        )
    except Exception:
        return {}
    return sizes


def _candidate_search_roots(data_dir: Path | None) -> list[Path]:
    """Carpetas raíz donde buscar proyectos de desarrollo en C: y D:."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            if p.is_dir():
                key = os.path.normcase(str(p.resolve()))
                if key not in seen:
                    seen.add(key)
                    roots.append(p)
        except OSError:
            pass

    with contextlib.suppress(Exception):
        cfg = load_user_config()
        for folder in cfg.project_folders:
            _add(Path(folder))

    # Carpeta padre del workspace actual si existe
    cwd_parent = Path.cwd().parent
    _add(cwd_parent)

    home = Path.home()
    for sub in (
        "Projects",
        "Proyectos",
        "Documents",
        "Desktop",
        "source",
        "repos",
        "dev",
        "workspace",
        "Code",
    ):
        _add(home / sub)

    # Raíces de unidades secundarias (D:\, E:\) hasta profundidad controlada
    for drive in ("D:\\", "E:\\"):
        p = Path(drive)
        if p.is_dir():
            _add(p)

    return roots


def scan_dev_projects(
    data_dir: Path | None = None,
    *,
    roots: list[Path] | None = None,
    max_depth: int = 4,
    stale_days: int = STALE_DAYS_THRESHOLD,
) -> DevProjectsOverview:
    """Escanea proyectos de desarrollo y devuelve los artefactos compilados o de
    dependencias eliminables junto con los días de inactividad de cada proyecto."""
    search_roots = _candidate_search_roots(data_dir) if roots is None else roots
    snapshot_sizes = _load_snapshot_size_map(data_dir)
    now_ts = time.time()

    found_projects: dict[str, DevProjectEntry] = {}

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            return

        names_lower = {e.name.lower() for e in entries}
        is_project = bool(names_lower & PROJECT_MARKERS)

        if is_project:
            artifacts: list[ProjectArtifact] = []
            for entry in entries:
                if (
                    entry.name.lower() in CLEANABLE_ARTIFACT_DIRS
                    and entry.is_dir(follow_symlinks=False)
                ):
                    sz = _quick_dir_size(entry.path, snapshot_sizes)
                    if sz >= MIN_ARTIFACT_BYTES:
                        artifacts.append(
                            ProjectArtifact(
                                name=entry.name,
                                path=entry.path,
                                size_bytes=sz,
                            )
                        )
            if artifacts:
                artifacts.sort(key=lambda a: a.size_bytes, reverse=True)
                total_art = sum(a.size_bytes for a in artifacts)
                last_ts = _project_last_activity_ts(current, entries)
                days_old = max(0, int((now_ts - last_ts) // 86400))
                norm_root = os.path.normcase(str(current.resolve()))
                found_projects[norm_root] = DevProjectEntry(
                    name=current.name or str(current),
                    root_path=str(current),
                    ecosystem=_detect_ecosystem(names_lower),
                    last_modified_iso=datetime.fromtimestamp(
                        last_ts, tz=UTC
                    ).isoformat(),
                    days_inactive=days_old,
                    is_stale=days_old >= stale_days,
                    reclaimable_bytes=total_art,
                    artifacts=tuple(artifacts),
                )

        # Continuar buscando subproyectos (monorepo o subcarpetas)
        if depth < max_depth:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                low = entry.name.lower()
                if low in SKIP_WALK_DIRS or low.startswith("."):
                    continue
                _walk(Path(entry.path), depth + 1)

    for root in search_roots:
        _walk(root, 0)

    ordered = sorted(
        found_projects.values(),
        key=lambda p: (p.is_stale, p.reclaimable_bytes),
        reverse=True,
    )
    total_reclaimable = sum(p.reclaimable_bytes for p in ordered)
    stale_reclaimable = sum(p.reclaimable_bytes for p in ordered if p.is_stale)
    return DevProjectsOverview(
        total_reclaimable_bytes=total_reclaimable,
        stale_reclaimable_bytes=stale_reclaimable,
        projects=tuple(ordered),
    )
