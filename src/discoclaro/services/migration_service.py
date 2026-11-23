"""Asistente de Migración (C: vs D:) y Detector de Discos Virtuales (.vhdx).

- Descubre archivos `.vhdx` de Docker Desktop y distribuciones WSL2 para
  permitir su compactación asistida.
- Compara la unidad del sistema (`C:`) con unidades secundarias con más espacio
  libre (`D:`, `E:`, etc.), detectando carpetas pesadas reubicables (modelos de
  IA Ollama, juegos Steam, Descargas, Videos, proyectos en C:) y proyectando
  cuánto bajaría el porcentaje de ocupación de `C:`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from discoclaro.adapters.store import Store
from discoclaro.platform import windows
from discoclaro.services import volumes_service


@dataclass(frozen=True, slots=True)
class VhdxDiskInfo:
    """Un disco virtual WSL2 / Docker Desktop detectado en el sistema."""

    name: str
    path: str
    size_bytes: int
    engine: str  # "Docker Desktop" | "WSL2"


@dataclass(frozen=True, slots=True)
class MigrationCandidate:
    """Una carpeta o recurso pesado en C: candidato a moverse a otra unidad."""

    id: str
    title: str
    category: str
    source_path: str
    suggested_destination: str
    size_bytes: int
    how_to_move: str


@dataclass(frozen=True, slots=True)
class MigrationAssistantOverview:
    """Comparativa C: vs unidad secundaria y candidatos de traslado."""

    source_volume: str
    source_total_bytes: int
    source_used_bytes: int
    source_free_bytes: int
    source_used_percent: float
    target_volume: str | None
    target_total_bytes: int
    target_used_bytes: int
    target_free_bytes: int
    target_used_percent: float
    total_movable_bytes: int
    projected_source_used_percent: float
    candidates: tuple[MigrationCandidate, ...]


def discover_vhdx_disks() -> tuple[VhdxDiskInfo, ...]:
    """Localiza discos virtuales `.vhdx` de Docker Desktop y WSL2."""
    found: dict[str, VhdxDiskInfo] = {}
    local_app = os.environ.get("LOCALAPPDATA", "")

    def _register(path: Path, name: str, engine: str) -> None:
        try:
            if path.is_file():
                sz = path.stat().st_size
                key = os.path.normcase(str(path.resolve()))
                if key not in found:
                    found[key] = VhdxDiskInfo(
                        name=name,
                        path=str(path),
                        size_bytes=sz,
                        engine=engine,
                    )
        except OSError:
            pass

    docker_vhdx = windows.docker_vhdx_path()
    if docker_vhdx:
        _register(
            Path(docker_vhdx), "Docker Desktop (docker_data.vhdx)", "Docker Desktop"
        )

    if local_app:
        la = Path(local_app)
        for rel, label in (
            (
                Path("Docker") / "wsl" / "disk" / "docker_data.vhdx",
                "Docker Desktop (docker_data.vhdx)",
            ),
            (
                Path("Docker") / "wsl" / "data" / "ext4.vhdx",
                "Docker Desktop (ext4.vhdx)",
            ),
            (
                Path("Docker") / "wsl" / "main" / "ext4.vhdx",
                "Docker Desktop Main (ext4.vhdx)",
            ),
        ):
            _register(la / rel, label, "Docker Desktop")

        packages_dir = la / "Packages"
        if packages_dir.is_dir():
            try:
                with os.scandir(packages_dir) as it:
                    for entry in it:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        vhdx = Path(entry.path) / "LocalState" / "ext4.vhdx"
                        if vhdx.is_file():
                            distro = entry.name.split("_")[0]
                            _register(vhdx, f"WSL2 ({distro})", "WSL2")
            except OSError:
                pass

    ordered = sorted(found.values(), key=lambda d: d.size_bytes, reverse=True)
    return tuple(ordered)


def _lookup_node_size(store: Store | None, snap_id: int | None, path: str) -> int:
    if store is not None and snap_id is not None:
        node = store.node_at_path(snap_id, path)
        if node is not None and node.size_bytes > 0:
            return node.size_bytes
    return 0


def build_migration_assistant(
    data_dir: Path | None = None,
) -> MigrationAssistantOverview:
    """Compara el disco del sistema (C:) con el disco secundario con más espacio
    libre (por ej. D:) y construye la lista de elementos pesados trasladables."""
    vols = volumes_service.run_volumes()
    sys_drive = (os.environ.get("SystemDrive", "C:") or "C:").upper()
    source = next(
        (v for v in vols if v.name.upper() == sys_drive), vols[0] if vols else None
    )
    others = [
        v for v in vols if source is None or v.name.upper() != source.name.upper()
    ]
    target = max(others, key=lambda v: v.free_bytes) if others else None

    if source is None:
        return MigrationAssistantOverview(
            source_volume="C:",
            source_total_bytes=0,
            source_used_bytes=0,
            source_free_bytes=0,
            source_used_percent=0.0,
            target_volume=None,
            target_total_bytes=0,
            target_used_bytes=0,
            target_free_bytes=0,
            target_used_percent=0.0,
            total_movable_bytes=0,
            projected_source_used_percent=0.0,
            candidates=(),
        )

    dst_prefix = f"{target.name}\\" if target else "D:\\"
    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = directory / "discoclaro.db"

    candidates: list[MigrationCandidate] = []
    home = Path.home()
    local_app = os.environ.get("LOCALAPPDATA", "")

    def _probe_candidates(store: Store | None, snap_id: int | None) -> None:
        checks = [
            (
                "ollama-models",
                "Modelos de IA Local (Ollama)",
                "Modelos IA",
                str(home / ".ollama" / "models"),
                f"{dst_prefix}OllamaModels",
                f"Mueve la carpeta a {dst_prefix}OllamaModels y crea la variable "
                f"de entorno OLLAMA_MODELS={dst_prefix}OllamaModels.",
            ),
            (
                "downloads",
                "Carpeta de Descargas (Instaladores, ZIPs, ISOs)",
                "Archivos de usuario",
                str(home / "Downloads"),
                f"{dst_prefix}Descargas",
                "En Explorador de Windows: clic derecho en Descargas → "
                "Propiedades → Ubicación → Mover a D:\\Descargas.",
            ),
            (
                "videos",
                "Carpeta de Videos y Grabaciones",
                "Multimedia",
                str(home / "Videos"),
                f"{dst_prefix}Videos",
                "En Explorador de Windows: clic derecho en Videos → "
                "Propiedades → Ubicación → Mover a D:\\Videos.",
            ),
            (
                "steam-library",
                "Biblioteca de Juegos Steam en C:",
                "Juegos",
                r"C:\Program Files (x86)\Steam\steamapps\common",
                f"{dst_prefix}SteamLibrary",
                "En Steam: Parámetros → Almacenamiento → añade una unidad "
                "en D: y pulsa «Mover» en cada juego.",
            ),
            (
                "docker-vhdx",
                "Disco virtual Docker Desktop / WSL2",
                "Contenedores",
                str(Path(local_app) / "Docker" / "wsl" / "disk") if local_app else "",
                f"{dst_prefix}DockerData",
                "En Docker Desktop: Settings → Resources → Advanced → "
                "Disk image location → cambia la ruta a D:\\DockerData.",
            ),
            (
                "android-sdk",
                "Android SDK y Emuladores",
                "Desarrollo",
                str(Path(local_app) / "Android" / "Sdk") if local_app else "",
                f"{dst_prefix}Android\\Sdk",
                "En Android Studio: Settings → Languages & Frameworks → "
                "Android SDK → Edit SDK Location hacia D:\\Android\\Sdk.",
            ),
        ]
        for cid, title, cat, src_p, dst_p, how in checks:
            if not src_p or not os.path.exists(src_p):
                continue
            sz = _lookup_node_size(store, snap_id, src_p)
            if sz <= 0 and cid == "docker-vhdx":
                for d in discover_vhdx_disks():
                    if d.engine == "Docker Desktop":
                        sz += d.size_bytes
            if sz >= 100 * 1024 * 1024:  # >= 100 MB
                candidates.append(
                    MigrationCandidate(
                        id=cid,
                        title=title,
                        category=cat,
                        source_path=src_p,
                        suggested_destination=dst_p,
                        size_bytes=sz,
                        how_to_move=how,
                    )
                )

    if database.is_file():
        try:
            with Store.open_readonly(database) as store:
                snaps = [
                    s
                    for s in store.snapshots(source.name)
                    if s.is_volume_scan
                ]
                snap_id = snaps[0].id if snaps else None
                _probe_candidates(store, snap_id)
        except Exception:
            _probe_candidates(None, None)
    else:
        _probe_candidates(None, None)

    candidates.sort(key=lambda c: c.size_bytes, reverse=True)
    total_movable = sum(c.size_bytes for c in candidates)
    src_pct = (
        round((source.used_bytes / source.total_bytes) * 100, 1)
        if source.total_bytes > 0
        else 0.0
    )
    projected_used = max(0, source.used_bytes - total_movable)
    proj_pct = (
        round((projected_used / source.total_bytes) * 100, 1)
        if source.total_bytes > 0
        else 0.0
    )
    tgt_pct = (
        round((target.used_bytes / target.total_bytes) * 100, 1)
        if (target and target.total_bytes > 0)
        else 0.0
    )

    return MigrationAssistantOverview(
        source_volume=source.name,
        source_total_bytes=source.total_bytes,
        source_used_bytes=source.used_bytes,
        source_free_bytes=source.free_bytes,
        source_used_percent=src_pct,
        target_volume=target.name if target else None,
        target_total_bytes=target.total_bytes if target else 0,
        target_used_bytes=target.used_bytes if target else 0,
        target_free_bytes=target.free_bytes if target else 0,
        target_used_percent=tgt_pct,
        total_movable_bytes=total_movable,
        projected_source_used_percent=proj_pct,
        candidates=tuple(candidates),
    )
