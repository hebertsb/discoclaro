"""Endurecimiento de la API local (`127.0.0.1`), modelos y puntos de entrada.

Incluye:
- Middleware de seguridad (T3.10): comprobación de `Host` (`127.0.0.1`,
  `localhost`, `testserver`), comprobación de `Origin` cuando viene en la
  petición, token de sesión opcional (`api_token`), límite de tamaño de cuerpo y
  cabeceras estrictas (`Content-Security-Policy`, `X-Content-Type-Options`,
  `Referrer-Policy`).
- Endpoints de consulta (`T3.1` a `T3.9`): `/volumes`, `/snapshots`, `/tree`,
  `/categories`, `/growth`, `/summary`, `/plan`, `/recommendations`,
  `/programs`, `/programs/footprints`, `/leftovers`, `/limits`.
- Endpoints de acción asistida (`T3.9` / Fase 4): `/actions/open-explorer`,
  `/actions/uninstall`, `/actions/trash`, `/actions/terminal`. Todos los que
  modifican o ejecutan algo pasan por `actions_service` con confirmación nativa
  obligatoria de Windows.
- Montaje automático del frontend compilado (`dashboard/dist`) cuando existe
  (`T3.8`).
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from discoclaro.adapters.rules import load_builtin_catalog
from discoclaro.domain.categories import (
    CategorizedFolder,
    CategoryTotal,
    categorized_folders,
)
from discoclaro.domain.footprint import Footprint
from discoclaro.domain.growth import CategoryGrowth, FolderGrowth
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.domain.plan import PlanItem
from discoclaro.domain.unexplained import Unexplained
from discoclaro.platform import windows
from discoclaro.services import (
    actions_service,
    growth_service,
    limits_service,
    programs_service,
    report_service,
    summary_service,
    tree_service,
    volumes_service,
)
from discoclaro.services.actions_service import ActionError
from discoclaro.services.growth_service import GrowthError
from discoclaro.services.plan_service import PlanError, run_plan
from discoclaro.services.programs_service import ProgramsCheckError
from discoclaro.services.report_service import ReportError
from discoclaro.services.snapshots_service import SnapshotsError, run_snapshots
from discoclaro.services.summary_service import SummaryError
from discoclaro.services.tree_service import TreeError, run_tree

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8347
MAX_REQUEST_BODY_BYTES = 65_536

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "testserver"})
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

_BAD_REQUEST_KINDS = frozenset(
    {
        "growth_invalid_since",
        "report_not_fixed_disk",
        "growth_not_fixed_disk",
        "leftovers_empty_name",
        "empty_paths",
        "unsafe_path",
        "terminal_empty_command",
        "terminal_command_too_long",
        "terminal_invalid_syntax",
        "terminal_directory_not_found",
        "uninstall_empty_command",
        "uninstall_missing_string",
    }
)
_FORBIDDEN_KINDS = frozenset({"readonly_mode", "action_declined"})
_SERVER_ERROR_KINDS = frozenset(
    {
        "cannot_open_database",
        "uninstall_failed",
        "uninstall_timeout",
        "terminal_timeout",
        "terminal_execution_failed",
    }
)

# Mapa de programa/motivo del plan a la regla correspondiente del catálogo TOML
# y su comando sugerido, para la vista Recomendaciones (T3.7) y la Terminal.
_PROGRAM_RULE_MAP = {
    "docker": "docker-vhdx",
    "pip": "pip-cache",
    "uv": "uv-cache",
    "npm": "npm-cache",
    "ollama": "ollama-models",
    "steam": "steam-game",
}


class VolumeOut(BaseModel):
    name: str
    root: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


class SnapshotOut(BaseModel):
    id: int
    volume: str
    taken_at: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    scanned_bytes: int
    duplicate_bytes: int
    system_files_bytes: int
    unreadable_count: int
    is_admin: bool


class NodeOut(BaseModel):
    path: str
    size_bytes: int
    n_files: int
    depth: int


class TreeOut(BaseModel):
    snapshot_id: int
    volume: str
    node: NodeOut | None
    children: list[NodeOut]


class CategoryTotalOut(BaseModel):
    category: str
    size_bytes: int
    folders: int


class CategorizedFolderOut(BaseModel):
    path: str
    size_bytes: int
    n_files: int
    category: str
    rule_id: str
    rule_name: str
    risk: str
    explanation: str
    suggested_command: str | None


class CategoriesDetailOut(BaseModel):
    volume: str
    snapshot_id: int
    totals: list[CategoryTotalOut]
    items: list[CategorizedFolderOut]


class FolderGrowthOut(BaseModel):
    path: str
    depth: int
    before_bytes: int
    after_bytes: int
    grown_bytes: int
    is_new: bool


class CategoryGrowthOut(BaseModel):
    category: str
    before_bytes: int
    after_bytes: int
    grown_bytes: int


class GrowthOut(BaseModel):
    before_snapshot_id: int
    after_snapshot_id: int
    before_taken_at: str
    after_taken_at: str
    folders: list[FolderGrowthOut]
    by_category: list[CategoryGrowthOut] | None


class UnexplainedOut(BaseModel):
    size_bytes: int
    percent_of_used: float
    exceeds_threshold: bool


class VolumeSummaryOut(BaseModel):
    volume: VolumeOut
    snapshot_id: int | None
    taken_at: str | None
    unexplained: UnexplainedOut | None
    days_until_full: float | None
    top_growth: FolderGrowthOut | None


class SummaryOut(BaseModel):
    volumes: list[VolumeSummaryOut]


class PlanItemOut(BaseModel):
    level: str
    program: str
    name: str
    size_bytes: int
    reason: str
    target: str


class PlanOut(BaseModel):
    safe_bytes: int
    review_bytes: int
    move_bytes: int
    items: list[PlanItemOut]


def _action_for_plan_item(
    item: PlanItem,
    rules_by_id: dict[str, object],
    env: dict[str, str],
) -> tuple[str | None, str | None, str | None]:
    """Devuelve `(suggested_command, suggested_note, location_path)` para un
    `PlanItem`, para que el usuario pueda ejecutar la limpieza o abrir la
    carpeta exacta donde cambiar/mover ese elemento."""
    local_app = env.get("%LOCALAPPDATA%", "")
    user_prof = env.get("%USERPROFILE%", "")

    if item.program == "pip":
        loc = str(Path(local_app) / "pip" / "Cache") if local_app else None
        return (
            "pip cache purge",
            "Elimina paquetes descargados en la caché de pip.",
            loc,
        )
    if item.program == "uv":
        loc = str(Path(local_app) / "uv" / "cache") if local_app else None
        return "uv cache clean", "Vacía la caché de paquetes de uv.", loc
    if item.program == "npm":
        loc = str(Path(local_app) / "npm-cache") if local_app else None
        return (
            "npm cache clean --force",
            "Limpia la caché local de paquetes npm.",
            loc,
        )
    if item.program == "docker":
        loc = str(Path(local_app) / "Docker" / "wsl" / "disk") if local_app else None
        name_low = item.name.lower()
        if "image" in name_low:
            cmd = "docker image prune -a -f"
        elif "container" in name_low:
            cmd = "docker container prune -f"
        elif "volume" in name_low:
            cmd = "docker volume prune -f"
        else:
            cmd = "docker builder prune -a -f"
        return (
            cmd,
            "Después de limpiar en Docker, compacta el disco virtual .vhdx "
            "para recuperar el espacio en Windows.",
            loc,
        )
    if item.program == "ollama":
        loc = str(Path(user_prof) / ".ollama" / "models") if user_prof else None
        return (
            f"ollama rm {item.name}",
            "Para mover los modelos a otro disco, cambia la variable "
            "de entorno OLLAMA_MODELS.",
            loc,
        )
    if item.program == "steam":
        from discoclaro.adapters.inspectors import steam as steam_inspector

        game_dir, game_drive, other_lib = steam_inspector.game_details(item.name)
        steam_dir = windows.steam_install_path()
        loc = (
            game_dir
            or (str(Path(steam_dir) / "steamapps" / "common") if steam_dir else None)
        )
        sys_drive = windows.system_drive().upper()
        if game_drive and game_drive != sys_drive:
            note = (
                f"Este juego de Steam ya vive en tu disco secundario {game_drive} "
                f"({loc}). No ocupa espacio en {sys_drive}; si ya no lo juegas, "
                "desinstálalo desde Steam → Biblioteca → Clic derecho → "
                "Administrar → Desinstalar."
            )
        elif other_lib:
            dst_drive = other_lib[:2].upper()
            note = (
                f"Está instalado en {game_drive or sys_drive} ({loc}). "
                f"Muévelo a tu biblioteca de Steam en {dst_drive} ({other_lib}): "
                f"abre Steam → Parámetros → Almacenamiento → selecciona "
                f"«{item.name}» → Mover a {dst_drive}."
            )
        else:
            note = (
                f"Está instalado en {game_drive or sys_drive} ({loc}). "
                f"En Steam → Parámetros → Almacenamiento → agrega una biblioteca en tu "
                f"disco secundario (D:\\SteamLibrary) y pulsa «Mover»."
            )
        return None, note, loc
    return None, None, None


class RecommendationOut(BaseModel):
    target: str
    level: str
    risk: str
    program: str
    name: str
    size_bytes: int
    rule_id: str | None
    explanation: str
    suggested_command: str | None
    suggested_note: str | None
    location_path: str | None = None


class FootprintOut(BaseModel):
    path: str
    size_bytes: int
    confidence: str
    reason: str


class ProgramItemOut(BaseModel):
    name: str
    version: str | None
    publisher: str | None
    install_location: str | None
    install_date: str | None
    estimated_size_bytes: int | None
    uninstall_string: str | None
    total_bytes: int
    footprints: list[FootprintOut]


class UninstalledProgramOut(BaseModel):
    name: str
    version: str | None
    publisher: str | None
    total_leftover_bytes: int
    footprints: list[FootprintOut]


class ProgramsOverviewOut(BaseModel):
    installed: list[ProgramItemOut]
    uninstalled_with_leftovers: list[UninstalledProgramOut]


class VolumeLimitStatusOut(BaseModel):
    volume: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    warn_at_percent: float
    min_free_bytes: int | None
    days_until_full: float | None
    status: str


class BudgetStatusOut(BaseModel):
    target: str
    kind: str
    used_bytes: int
    max_bytes: int
    used_percent: float
    status: str


class LimitsOverviewOut(BaseModel):
    volumes: list[VolumeLimitStatusOut]
    budgets: list[BudgetStatusOut]


class LargeFileOut(BaseModel):
    path: str
    size_bytes: int
    is_new: bool = False


class SearchResultsOut(BaseModel):
    volume: str
    snapshot_id: int
    folders: list[NodeOut]
    large_files: list[LargeFileOut]
    new_large_files: list[LargeFileOut]


class ScanVolumeIn(BaseModel):
    target: str
    restart: bool = False


class ScanVolumeOut(BaseModel):
    volume: str
    snapshot_id: int
    scanned_bytes: int
    folders: int
    files: int
    duration_s: float


class VolumeLimitUpdateIn(BaseModel):
    volume: str
    warn_at_percent: float
    min_free_gb: float | None = None


class BudgetUpdateIn(BaseModel):
    target: str
    max_gb: float | None = None


class PathActionIn(BaseModel):
    path: str


class ProgramUninstallIn(BaseModel):
    program_name: str


class TrashPathsIn(BaseModel):
    paths: list[str]


class TerminalCommandIn(BaseModel):
    command: str
    cwd: str | None = None


class UninstallFlowOut(BaseModel):
    program_name: str
    command_executed: str
    uninstaller_exit_code: int
    still_in_registry: bool
    remaining_footprints: list[FootprintOut]
    total_remaining_bytes: int


class TrashItemOut(BaseModel):
    path: str
    freed_bytes: int
    removed: bool
    reason: str


class DuplicateGroupOut(BaseModel):
    filename: str
    size_bytes: int
    reclaimable_bytes: int
    fingerprint: str
    paths: list[str]


class DuplicatesOverviewOut(BaseModel):
    volume: str
    total_reclaimable_bytes: int
    groups: list[DuplicateGroupOut]


class BatchCleanItemOut(BaseModel):
    name: str
    command_or_path: str
    estimated_bytes: int
    succeeded: bool
    detail: str


class BatchCleanOut(BaseModel):
    items: list[BatchCleanItemOut]
    total_estimated_bytes: int
    succeeded_count: int


class ScheduleTaskOut(BaseModel):
    name: str
    label: str
    schedule: str
    installed: bool
    altered: bool


class ScheduleOverviewOut(BaseModel):
    tasks: list[ScheduleTaskOut]
    all_installed: bool
    windowless_mode: bool
    target_volume: str


class ScheduleInstallIn(BaseModel):
    target: str = "C:"


class ProjectArtifactOut(BaseModel):
    name: str
    path: str
    size_bytes: int


class DevProjectOut(BaseModel):
    name: str
    root_path: str
    ecosystem: str
    last_modified_iso: str
    days_inactive: int
    is_stale: bool
    reclaimable_bytes: int
    artifacts: list[ProjectArtifactOut]


class DevProjectsOverviewOut(BaseModel):
    total_reclaimable_bytes: int
    stale_reclaimable_bytes: int
    projects: list[DevProjectOut]


class VhdxDiskOut(BaseModel):
    name: str
    path: str
    size_bytes: int
    engine: str


class VhdxCompactOut(BaseModel):
    path: str
    before_bytes: int
    after_bytes: int
    freed_bytes: int
    succeeded: bool
    detail: str


class CleanupHistoryEntryOut(BaseModel):
    timestamp: str
    action_type: str
    target: str
    freed_bytes: int
    detail: str


class CleanupHistoryOut(BaseModel):
    total_freed_bytes: int
    total_actions: int
    entries: list[CleanupHistoryEntryOut]


class MigrationCandidateOut(BaseModel):
    id: str
    title: str
    category: str
    source_path: str
    suggested_destination: str
    size_bytes: int
    how_to_move: str


class MigrationAssistantOut(BaseModel):
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
    candidates: list[MigrationCandidateOut]


class RecycleBinStatusOut(BaseModel):
    size_bytes: int
    item_count: int


class RecycleBinEmptyOut(BaseModel):
    before_bytes: int
    before_items: int
    after_bytes: int
    freed_bytes: int
    succeeded: bool


class PhysicalDiskHealthOut(BaseModel):
    device_id: str
    friendly_name: str
    serial_number: str | None
    firmware_version: str | None
    media_type: str
    bus_type: str
    partition_style: str | None
    drive_letters: list[str]
    health_status: str
    operational_status: str
    size_bytes: int
    logical_sector_size: int | None
    physical_sector_size: int | None
    temperature_c: int | None
    temperature_max_c: int | None
    wear_percent: int | None
    life_remaining_percent: int | None
    power_on_hours: int | None
    read_errors: int | None
    write_errors: int | None
    available_spare_percent: int | None = None
    written_tb: float | None = None
    smart_available: bool


class TrashBatchOut(BaseModel):
    items: list[TrashItemOut]
    total_freed_bytes: int


class TerminalCommandOut(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    cwd: str = "C:\\"


def _as_http_error(kind: str) -> HTTPException:
    if kind in _SERVER_ERROR_KINDS:
        return HTTPException(status_code=503, detail=kind)
    if kind in _FORBIDDEN_KINDS:
        return HTTPException(status_code=403, detail=kind)
    if kind in _BAD_REQUEST_KINDS:
        return HTTPException(status_code=400, detail=kind)
    return HTTPException(status_code=404, detail=kind)


def _is_allowed_host(host_header: str) -> bool:
    if not host_header:
        return False
    hostname = host_header.split(":")[0].strip().lower()
    return hostname in ALLOWED_HOSTS


def _is_allowed_origin(origin_header: str) -> bool:
    if not origin_header:
        return True
    lower = origin_header.strip().lower()
    for scheme in ("http://", "https://"):
        if lower.startswith(scheme):
            rest = lower[len(scheme) :].split("/")[0]
            host = rest.split(":")[0]
            return host in ALLOWED_HOSTS
    return False


def _volume_out(volume: Volume) -> VolumeOut:
    return VolumeOut(
        name=volume.name,
        root=volume.root,
        total_bytes=volume.total_bytes,
        used_bytes=volume.used_bytes,
        free_bytes=volume.free_bytes,
    )


def _snapshot_out(snapshot: Snapshot) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        volume=snapshot.volume,
        taken_at=snapshot.taken_at.isoformat(),
        total_bytes=snapshot.total_bytes,
        used_bytes=snapshot.used_bytes,
        free_bytes=snapshot.free_bytes,
        scanned_bytes=snapshot.scanned_bytes,
        duplicate_bytes=snapshot.duplicate_bytes,
        system_files_bytes=snapshot.system_files_bytes,
        unreadable_count=snapshot.unreadable_count,
        is_admin=snapshot.is_admin,
    )


def _node_out(node: Node) -> NodeOut:
    return NodeOut(
        path=node.path,
        size_bytes=node.size_bytes,
        n_files=node.n_files,
        depth=node.depth,
    )


_categories_detail_cache: dict[int, CategoriesDetailOut] = {}


def _categorized_folder_out(folder: CategorizedFolder) -> CategorizedFolderOut:
    return CategorizedFolderOut(
        path=folder.path,
        size_bytes=folder.size_bytes,
        n_files=folder.n_files,
        category=folder.category,
        rule_id=folder.rule_id,
        rule_name=folder.rule_name,
        risk=folder.risk,
        explanation=folder.explanation,
        suggested_command=folder.suggested_command,
    )


def _category_out(total: CategoryTotal) -> CategoryTotalOut:
    return CategoryTotalOut(
        category=total.category, size_bytes=total.size_bytes, folders=total.folders
    )


def _folder_growth_out(growth: FolderGrowth) -> FolderGrowthOut:
    return FolderGrowthOut(
        path=growth.path,
        depth=growth.depth,
        before_bytes=growth.before_bytes,
        after_bytes=growth.after_bytes,
        grown_bytes=growth.grown_bytes,
        is_new=growth.is_new,
    )


def _category_growth_out(growth: CategoryGrowth) -> CategoryGrowthOut:
    return CategoryGrowthOut(
        category=growth.category,
        before_bytes=growth.before_bytes,
        after_bytes=growth.after_bytes,
        grown_bytes=growth.grown_bytes,
    )


def _unexplained_out(unexplained: Unexplained) -> UnexplainedOut:
    return UnexplainedOut(
        size_bytes=unexplained.size_bytes,
        percent_of_used=unexplained.percent_of_used,
        exceeds_threshold=unexplained.exceeds_threshold,
    )


def _volume_summary_out(summary: summary_service.VolumeSummary) -> VolumeSummaryOut:
    return VolumeSummaryOut(
        volume=_volume_out(summary.volume),
        snapshot_id=None if summary.snapshot is None else summary.snapshot.id,
        taken_at=None
        if summary.snapshot is None
        else summary.snapshot.taken_at.isoformat(),
        unexplained=None
        if summary.unexplained is None
        else _unexplained_out(summary.unexplained),
        days_until_full=summary.days_until_full,
        top_growth=None
        if summary.top_growth is None
        else _folder_growth_out(summary.top_growth),
    )


def _plan_item_out(item: PlanItem) -> PlanItemOut:
    return PlanItemOut(
        level=item.level,
        program=item.program,
        name=item.name,
        size_bytes=item.size_bytes,
        reason=item.reason,
        target=item.target,
    )


def _footprint_out(footprint: Footprint) -> FootprintOut:
    return FootprintOut(
        path=footprint.path,
        size_bytes=footprint.size_bytes,
        confidence=footprint.confidence,
        reason=footprint.reason,
    )


def _build_recommendations(items: tuple[PlanItem, ...]) -> list[RecommendationOut]:
    rules_by_id = {rule.id: rule for rule in load_builtin_catalog()}
    env = windows.environment_variables()
    rows: list[RecommendationOut] = []
    for item in sorted(items, key=lambda i: i.size_bytes, reverse=True):
        rule_id = _PROGRAM_RULE_MAP.get(item.program)
        rule = rules_by_id.get(rule_id) if rule_id else None
        risk = "safe" if item.level == "safe" else "review"
        explanation = (
            rule.explanation
            if rule is not None
            else f"Espacio detectado en {item.program} ({item.reason})."
        )
        cmd, note, loc = _action_for_plan_item(item, rules_by_id, env)
        if (
            cmd is None
            and item.program != "steam"
            and rule is not None
            and rule.suggested_action is not None
            and rule.suggested_action.type == "command"
        ):
            cmd = rule.suggested_action.text
        if cmd is not None:
            from discoclaro.adapters.inspectors import _process

            exe_token = cmd.strip().split(" ", 1)[0].strip('"')
            if not exe_token or _process.resolve_executable(exe_token) is None:
                cmd = None
        if note is None and rule is not None and rule.suggested_action is not None:
            note = rule.suggested_action.note
        rows.append(
            RecommendationOut(
                target=item.target,
                level=item.level,
                risk=risk,
                program=item.program,
                name=item.name,
                size_bytes=item.size_bytes,
                rule_id=rule_id,
                explanation=explanation,
                suggested_command=cmd,
                suggested_note=note,
                location_path=loc,
            )
        )
    return rows


def create_app(
    data_dir: Path | None = None,
    *,
    api_token: str | None = None,
    static_dir: Path | None = None,
    read_programs: Callable[..., list] = windows.installed_programs,
) -> FastAPI:
    """La API, lista para montar (`TestClient` en las pruebas, `uvicorn` en serio).

    - `data_dir`: carpeta de la base (`None` = `%LOCALAPPDATA%\\discoclaro`).
    - `api_token`: si se fija (T3.10), toda ruta de API exige la cabecera
      `X-DiscoClaro-Token` (o `?token=`).
    - `static_dir`: si apunta a `dashboard/dist` compilado (T3.8), sirve el
      frontend en el mismo origen (`127.0.0.1`), sin CORS.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next) -> Response:
        host_header = request.headers.get("host", "")
        if not _is_allowed_host(host_header):
            return Response(
                content='{"detail":"forbidden_host"}',
                status_code=403,
                media_type="application/json",
            )
        origin_header = request.headers.get("origin", "")
        if origin_header and not _is_allowed_origin(origin_header):
            return Response(
                content='{"detail":"forbidden_origin"}',
                status_code=403,
                media_type="application/json",
            )
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return Response(
                    content='{"detail":"payload_too_large"}',
                    status_code=413,
                    media_type="application/json",
                )
        if api_token is not None and not request.url.path.startswith("/assets"):
            if request.url.path != "/" and not request.url.path.endswith(
                (".html", ".css", ".js", ".ico", ".svg")
            ):
                provided = request.headers.get(
                    "x-discoclaro-token"
                ) or request.query_params.get("token")
                if provided != api_token:
                    return Response(
                        content='{"detail":"unauthorized"}',
                        status_code=401,
                        media_type="application/json",
                    )

        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/volumes", response_model=list[VolumeOut])
    def get_volumes() -> list[VolumeOut]:
        return [_volume_out(volume) for volume in volumes_service.run_volumes()]

    @app.get("/snapshots", response_model=list[SnapshotOut])
    def get_snapshots(volume: str | None = Query(default=None)) -> list[SnapshotOut]:
        try:
            snapshots = run_snapshots(volume, data_dir=data_dir)
        except SnapshotsError as error:
            raise _as_http_error(error.kind) from error
        return [_snapshot_out(snapshot) for snapshot in snapshots]

    @app.get("/tree", response_model=TreeOut)
    def get_tree(
        path: str | None = Query(default=None),
        snapshot_id: int | None = Query(default=None),
        volume: str | None = Query(default=None),
    ) -> TreeOut:
        try:
            view = run_tree(path, snapshot_id, data_dir=data_dir, volume=volume)
        except TreeError as error:
            raise _as_http_error(error.kind) from error
        return TreeOut(
            snapshot_id=view.snapshot.id,
            volume=view.snapshot.volume,
            node=None if view.node is None else _node_out(view.node),
            children=[_node_out(child) for child in view.children],
        )

    @app.get("/search", response_model=SearchResultsOut)
    def get_search(
        q: str = Query(default=""),
        volume: str | None = Query(default=None),
        limit: int = Query(default=80, ge=1, le=300),
    ) -> SearchResultsOut:
        directory = windows.default_data_dir() if data_dir is None else data_dir
        database = directory / "discoclaro.db"
        if not database.is_file():
            raise HTTPException(status_code=404, detail="tree_no_snapshots")
        from discoclaro.adapters.store import Store
        from discoclaro.domain.growth import new_large_files as compute_new_files

        with Store.open_readonly(database) as store:
            all_snaps = store.snapshots()
            if not all_snaps:
                raise HTTPException(status_code=404, detail="tree_no_snapshots")
            target_snap = tree_service._pick_snapshot_for_volume(all_snaps, volume)
            folders = store.search_nodes(target_snap.id, q, limit=limit)
            files = store.search_large_files(target_snap.id, q, limit=limit)

            vol_history = store.snapshots(target_snap.volume)
            recent_new_set: set[str] = set()
            recent_list: list[LargeFileOut] = []
            if len(vol_history) >= 2:
                diff_new = compute_new_files(
                    store.large_files(vol_history[1].id),
                    store.large_files(vol_history[0].id),
                )
                q_low = q.strip().lower()
                for nf in diff_new:
                    recent_new_set.add(nf.path)
                    if not q_low or q_low in nf.path.lower():
                        recent_list.append(
                            LargeFileOut(
                                path=nf.path, size_bytes=nf.size_bytes, is_new=True
                            )
                        )

        return SearchResultsOut(
            volume=target_snap.volume,
            snapshot_id=target_snap.id,
            folders=[_node_out(n) for n in folders],
            large_files=[
                LargeFileOut(
                    path=f.path,
                    size_bytes=f.size_bytes,
                    is_new=f.path in recent_new_set,
                )
                for f in files
            ],
            new_large_files=recent_list[:limit],
        )

    @app.post("/actions/scan", response_model=ScanVolumeOut)
    def post_scan_volume(payload: ScanVolumeIn) -> ScanVolumeOut:
        from discoclaro.services import scan_service
        from discoclaro.services.scan_service import ScanError

        if not windows.confirm_native_dialog(
            "discoclaro — Iniciar análisis de unidad",
            f"¿Deseas analizar ahora mismo «{payload.target}» y guardar una "
            "nueva foto en la base local?",
        ):
            raise HTTPException(status_code=403, detail="action_declined")
        try:
            summary = scan_service.run_scan(
                payload.target, data_dir=data_dir, restart=payload.restart
            )
        except ScanError as error:
            raise _as_http_error(error.kind) from error
        summary_service._summary_cache.clear()
        report_service._report_cache.clear()
        growth_service._growth_cache.clear()
        return ScanVolumeOut(
            volume=summary.snapshot.volume,
            snapshot_id=summary.snapshot.id or 0,
            scanned_bytes=summary.snapshot.scanned_bytes,
            folders=summary.folders,
            files=summary.files,
            duration_s=summary.snapshot.duration_s,
        )

    @app.get("/categories", response_model=list[CategoryTotalOut])
    def get_categories(
        volume: str | None = Query(default=None),
    ) -> list[CategoryTotalOut]:
        if data_dir is None:
            import contextlib

            with contextlib.suppress(Exception):
                detail = get_categories_detail(volume=volume)
                return detail.totals
        try:
            report = report_service.run_report(
                volume, data_dir=data_dir, by_category=True
            )
        except ReportError as error:
            raise _as_http_error(error.kind) from error
        return [_category_out(total) for total in report.by_category or ()]

    @app.get("/categories/detail", response_model=CategoriesDetailOut)
    def get_categories_detail(
        volume: str | None = Query(default=None),
    ) -> CategoriesDetailOut:
        directory = windows.default_data_dir() if data_dir is None else data_dir
        database = directory / "discoclaro.db"
        if not database.is_file():
            raise HTTPException(status_code=404, detail="report_no_snapshots")
        from discoclaro.adapters.store import Store

        with Store.open_readonly(database) as store:
            all_snaps = store.snapshots()
            if not all_snaps:
                raise HTTPException(status_code=404, detail="report_no_snapshots")
            snap = tree_service._pick_snapshot_for_volume(all_snaps, volume)
            if snap.id is not None and snap.id in _categories_detail_cache:
                return _categories_detail_cache[snap.id]
            disk_cat_file = directory / f"categories_detail_{snap.id}.json"
            if snap.id is not None and disk_cat_file.is_file():
                import contextlib

                with contextlib.suppress(Exception):
                    cached_detail = CategoriesDetailOut.model_validate_json(
                        disk_cat_file.read_text(encoding="utf-8")
                    )
                    _categories_detail_cache[snap.id] = cached_detail
                    return cached_detail
            nodes = [n for n in store.nodes(snap.id) if n.depth <= 7]

        rules = load_builtin_catalog()
        env = windows.environment_variables()
        folders = categorized_folders(nodes, rules, env)
        buckets: dict[str, list[int]] = {}
        for item in folders:
            b = buckets.setdefault(item.category, [0, 0])
            b[0] += item.size_bytes
            b[1] += 1
        totals = sorted(
            (
                CategoryTotalOut(category=cat, size_bytes=s, folders=c)
                for cat, (s, c) in buckets.items()
            ),
            key=lambda x: x.size_bytes,
            reverse=True,
        )
        detail = CategoriesDetailOut(
            volume=snap.volume,
            snapshot_id=snap.id or 0,
            totals=totals,
            items=[_categorized_folder_out(f) for f in folders],
        )
        if snap.id is not None:
            _categories_detail_cache[snap.id] = detail
            try:
                disk_cat_file.write_text(
                    detail.model_dump_json(), encoding="utf-8"
                )
            except OSError:
                pass
        return detail

    @app.get("/growth", response_model=GrowthOut)
    def get_growth(
        volume: str | None = Query(default=None),
        since: str = Query(default=growth_service.DEFAULT_SINCE),
        by_category: bool = Query(default=False),
    ) -> GrowthOut:
        try:
            growth = growth_service.run_growth(
                volume, since, data_dir=data_dir, by_category=by_category
            )
        except GrowthError as error:
            raise _as_http_error(error.kind) from error
        return GrowthOut(
            before_snapshot_id=growth.before.id,
            after_snapshot_id=growth.after.id,
            before_taken_at=growth.before.taken_at.isoformat(),
            after_taken_at=growth.after.taken_at.isoformat(),
            folders=[_folder_growth_out(folder) for folder in growth.folders],
            by_category=None
            if growth.categories is None
            else [_category_growth_out(category) for category in growth.categories],
        )

    @app.get("/summary", response_model=SummaryOut)
    def get_summary() -> SummaryOut:
        try:
            summary = summary_service.run_summary(data_dir)
        except SummaryError as error:
            raise _as_http_error(error.kind) from error
        return SummaryOut(
            volumes=[_volume_summary_out(volume) for volume in summary.volumes]
        )

    @app.get("/plan", response_model=PlanOut)
    def get_plan() -> PlanOut:
        try:
            plan = run_plan(data_dir)
        except PlanError as error:
            raise _as_http_error(error.kind) from error
        return PlanOut(
            safe_bytes=plan.total_bytes("safe"),
            review_bytes=plan.total_bytes("review"),
            move_bytes=plan.total_bytes("move"),
            items=[_plan_item_out(item) for item in plan.items],
        )

    @app.get("/recommendations", response_model=list[RecommendationOut])
    def get_recommendations() -> list[RecommendationOut]:
        try:
            plan = run_plan(data_dir)
        except PlanError as error:
            raise _as_http_error(error.kind) from error
        return _build_recommendations(plan.items)

    @app.get("/programs", response_model=ProgramsOverviewOut)
    def get_programs() -> ProgramsOverviewOut:
        overview = programs_service.list_programs_overview(
            data_dir, read_programs=read_programs
        )
        return ProgramsOverviewOut(
            installed=[
                ProgramItemOut(
                    name=entry.program.name,
                    version=entry.program.version,
                    publisher=entry.program.publisher,
                    install_location=entry.program.install_location,
                    install_date=entry.program.install_date,
                    estimated_size_bytes=entry.program.estimated_size_bytes,
                    uninstall_string=entry.program.uninstall_string,
                    total_bytes=entry.total_bytes,
                    footprints=[_footprint_out(f) for f in entry.footprints],
                )
                for entry in overview.installed
            ],
            uninstalled_with_leftovers=[
                UninstalledProgramOut(
                    name=rem.program.name,
                    version=rem.program.version,
                    publisher=rem.program.publisher,
                    total_leftover_bytes=sum(f.size_bytes for f in rem.footprints),
                    footprints=[_footprint_out(f) for f in rem.footprints],
                )
                for rem in overview.uninstalled_with_leftovers
            ],
        )

    @app.get("/programs/footprints", response_model=list[FootprintOut])
    def get_program_footprints(
        name: str = Query(..., min_length=1),
    ) -> list[FootprintOut]:
        try:
            found = programs_service.inspect_program_footprints(
                name, read_programs=read_programs
            )
        except ProgramsCheckError as error:
            raise _as_http_error(error.kind) from error
        return [_footprint_out(f) for f in found]

    @app.get("/leftovers", response_model=list[FootprintOut])
    def get_leftovers(name: str = Query(..., min_length=1)) -> list[FootprintOut]:
        try:
            found = programs_service.find_leftovers(name)
        except ProgramsCheckError as error:
            raise _as_http_error(error.kind) from error
        return [_footprint_out(f) for f in found]

    @app.get("/limits", response_model=LimitsOverviewOut)
    def get_limits() -> LimitsOverviewOut:
        overview = limits_service.run_limits_overview(data_dir)
        return LimitsOverviewOut(
            volumes=[
                VolumeLimitStatusOut(
                    volume=v.volume,
                    total_bytes=v.total_bytes,
                    used_bytes=v.used_bytes,
                    free_bytes=v.free_bytes,
                    used_percent=v.used_percent,
                    warn_at_percent=v.warn_at_percent,
                    min_free_bytes=v.min_free_bytes,
                    days_until_full=v.days_until_full,
                    status=v.status,
                )
                for v in overview.volumes
            ],
            budgets=[
                BudgetStatusOut(
                    target=b.target,
                    kind=b.kind,
                    used_bytes=b.used_bytes,
                    max_bytes=b.max_bytes,
                    used_percent=b.used_percent,
                    status=b.status,
                )
                for b in overview.budgets
            ],
        )

    @app.post("/limits/volume", response_model=LimitsOverviewOut)
    def post_update_volume_limit(payload: VolumeLimitUpdateIn) -> LimitsOverviewOut:
        from dataclasses import replace

        from discoclaro.adapters.config import (
            ConfigError,
            load_user_config,
            save_user_config,
        )
        from discoclaro.domain.config import VolumeLimit

        if not (1.0 <= payload.warn_at_percent <= 100.0):
            raise HTTPException(status_code=400, detail="invalid_warn_percent")
        if payload.min_free_gb is not None and payload.min_free_gb < 0:
            raise HTTPException(status_code=400, detail="invalid_min_free_gb")
        vol_key = payload.volume.rstrip("\\/").upper()
        try:
            cfg = load_user_config()
            new_vols = dict(cfg.volumes)
            new_vols[vol_key] = VolumeLimit(
                min_free_gb=payload.min_free_gb,
                warn_at_percent=payload.warn_at_percent,
            )
            save_user_config(replace(cfg, volumes=new_vols))
        except (ConfigError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return get_limits()

    @app.post("/limits/budget", response_model=LimitsOverviewOut)
    def post_update_budget(payload: BudgetUpdateIn) -> LimitsOverviewOut:
        from dataclasses import replace

        from discoclaro.adapters.config import (
            ConfigError,
            load_user_config,
            save_user_config,
        )
        from discoclaro.domain.config import Budget

        target_name = payload.target.strip()
        if not target_name:
            raise HTTPException(status_code=400, detail="empty_budget_target")
        try:
            cfg = load_user_config()
            new_budgets = dict(cfg.budgets)
            if payload.max_gb is None or payload.max_gb <= 0:
                new_budgets.pop(target_name, None)
            else:
                new_budgets[target_name] = Budget(max_gb=payload.max_gb)
            save_user_config(replace(cfg, budgets=new_budgets))
        except (ConfigError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return get_limits()

    @app.post("/actions/check-alerts")
    def post_run_check_alerts() -> dict[str, object]:
        from discoclaro.services import check_service
        from discoclaro.services.check_service import CheckError

        try:
            fired = check_service.run_check(data_dir=data_dir)
        except CheckError as error:
            raise _as_http_error(error.kind) from error
        return {
            "alerts_fired": len(fired),
            "items": [
                {"kind": a.kind, "target": a.target}
                for a in fired
            ],
        }

    @app.post("/actions/open-explorer")
    def post_open_explorer(payload: PathActionIn) -> dict[str, bool]:
        opened = windows.open_in_explorer(payload.path)
        if not opened:
            raise HTTPException(status_code=400, detail="folder_not_found")
        return {"opened": True}

    @app.post("/actions/uninstall", response_model=UninstallFlowOut)
    def post_uninstall_program(payload: ProgramUninstallIn) -> UninstallFlowOut:
        try:
            result = actions_service.run_program_uninstall(
                payload.program_name, read_programs=read_programs
            )
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return UninstallFlowOut(
            program_name=result.program_name,
            command_executed=result.command_executed,
            uninstaller_exit_code=result.uninstaller_exit_code,
            still_in_registry=result.still_in_registry,
            remaining_footprints=[
                _footprint_out(f) for f in result.remaining_footprints
            ],
            total_remaining_bytes=result.total_remaining_bytes,
        )

    @app.post("/actions/trash", response_model=TrashBatchOut)
    def post_trash_paths(payload: TrashPathsIn) -> TrashBatchOut:
        try:
            result = actions_service.trash_paths_verified(payload.paths)
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return TrashBatchOut(
            items=[
                TrashItemOut(
                    path=item.path,
                    freed_bytes=item.freed_bytes,
                    removed=item.removed,
                    reason=item.reason,
                )
                for item in result.items
            ],
            total_freed_bytes=result.total_freed_bytes,
        )

    @app.post("/actions/terminal", response_model=TerminalCommandOut)
    def post_terminal_command(payload: TerminalCommandIn) -> TerminalCommandOut:
        try:
            result = actions_service.execute_terminal_command(
                payload.command, cwd=payload.cwd
            )
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return TerminalCommandOut(
            command=result.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            cwd=result.cwd,
        )

    @app.post("/actions/clean-safe", response_model=BatchCleanOut)
    def post_clean_safe_batch() -> BatchCleanOut:
        try:
            res = actions_service.clean_safe_batch(data_dir)
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return BatchCleanOut(
            items=[
                BatchCleanItemOut(
                    name=i.name,
                    command_or_path=i.command_or_path,
                    estimated_bytes=i.estimated_bytes,
                    succeeded=i.succeeded,
                    detail=i.detail,
                )
                for i in res.items
            ],
            total_estimated_bytes=res.total_estimated_bytes,
            succeeded_count=res.succeeded_count,
        )

    @app.get("/duplicates", response_model=DuplicatesOverviewOut)
    def get_duplicates(
        volume: str | None = Query(default=None),
    ) -> DuplicatesOverviewOut:
        from discoclaro.services import duplicates_service

        ov = duplicates_service.find_duplicates(volume, data_dir=data_dir)
        return DuplicatesOverviewOut(
            volume=ov.volume,
            total_reclaimable_bytes=ov.total_reclaimable_bytes,
            groups=[
                DuplicateGroupOut(
                    filename=g.filename,
                    size_bytes=g.size_bytes,
                    reclaimable_bytes=g.reclaimable_bytes,
                    fingerprint=g.fingerprint,
                    paths=list(g.paths),
                )
                for g in ov.groups
            ],
        )

    @app.get("/schedule", response_model=ScheduleOverviewOut)
    def get_schedule_status() -> ScheduleOverviewOut:
        from discoclaro.services import schedule_service

        schedule_service.migrate_tasks_to_windowless()
        st = schedule_service.status()
        scan_cmd = windows.scheduled_task_command(schedule_service.SCAN_TASK_NAME)
        target_vol = "C:"
        windowless = True
        if scan_cmd is not None:
            exe, args = scan_cmd
            if exe.lower().endswith("python.exe"):
                windowless = False
            parts = args.split("scan ", 1)
            if len(parts) == 2 and parts[1].strip():
                target_vol = parts[1].strip().strip('"')
        tasks = [
            ScheduleTaskOut(
                name=schedule_service.SCAN_TASK_NAME,
                label="Escaneo diario de unidad (scan)",
                schedule="Diario · 09:00",
                installed=st.scan.installed,
                altered=st.scan.altered,
            ),
            ScheduleTaskOut(
                name=schedule_service.CHECK_TASK_NAME,
                label="Verificación diaria de límites y alertas (check)",
                schedule="Diario · 09:00",
                installed=st.check.installed,
                altered=st.check.altered,
            ),
            ScheduleTaskOut(
                name=schedule_service.PROGRAMS_HOURLY_TASK_NAME,
                label="Vigilancia de programas instalados/desinstalados",
                schedule="Cada hora · Silencioso (pythonw)",
                installed=st.programs_hourly.installed,
                altered=st.programs_hourly.altered,
            ),
            ScheduleTaskOut(
                name=schedule_service.PROGRAMS_ONEVENT_TASK_NAME,
                label="Detector de instaladores MSI (EventID 1033)",
                schedule="Por evento MSI · Silencioso (pythonw)",
                installed=st.programs_onevent.installed,
                altered=st.programs_onevent.altered,
            ),
        ]
        all_inst = all(t.installed and not t.altered for t in tasks)
        return ScheduleOverviewOut(
            tasks=tasks,
            all_installed=all_inst,
            windowless_mode=windowless,
            target_volume=target_vol,
        )

    @app.post("/schedule/install", response_model=ScheduleOverviewOut)
    def post_schedule_install(payload: ScheduleInstallIn) -> ScheduleOverviewOut:
        from discoclaro.services import schedule_service
        from discoclaro.services.schedule_service import ScheduleError

        try:
            schedule_service.install(payload.target)
        except ScheduleError as error:
            raise _as_http_error(error.kind) from error
        return get_schedule_status()

    @app.post("/schedule/remove", response_model=ScheduleOverviewOut)
    def post_schedule_remove() -> ScheduleOverviewOut:
        from discoclaro.services import schedule_service

        schedule_service.remove()
        return get_schedule_status()

    @app.post("/actions/desktop-shortcut")
    def post_create_desktop_shortcut() -> dict[str, object]:
        path = windows.create_desktop_shortcut()
        if not path:
            raise HTTPException(status_code=500, detail="shortcut_creation_failed")
        return {"created": True, "path": path}

    @app.get("/dev-projects", response_model=DevProjectsOverviewOut)
    def get_dev_projects(
        stale_days: int = Query(default=30, ge=0, le=3650),
    ) -> DevProjectsOverviewOut:
        from discoclaro.services import dev_projects_service

        ov = dev_projects_service.scan_dev_projects(
            data_dir=data_dir, stale_days=stale_days
        )
        return DevProjectsOverviewOut(
            total_reclaimable_bytes=ov.total_reclaimable_bytes,
            stale_reclaimable_bytes=ov.stale_reclaimable_bytes,
            projects=[
                DevProjectOut(
                    name=p.name,
                    root_path=p.root_path,
                    ecosystem=p.ecosystem,
                    last_modified_iso=p.last_modified_iso,
                    days_inactive=p.days_inactive,
                    is_stale=p.is_stale,
                    reclaimable_bytes=p.reclaimable_bytes,
                    artifacts=[
                        ProjectArtifactOut(
                            name=a.name, path=a.path, size_bytes=a.size_bytes
                        )
                        for a in p.artifacts
                    ],
                )
                for p in ov.projects
            ],
        )

    @app.get("/vhdx", response_model=list[VhdxDiskOut])
    def get_vhdx_disks() -> list[VhdxDiskOut]:
        from discoclaro.services import migration_service

        return [
            VhdxDiskOut(
                name=d.name, path=d.path, size_bytes=d.size_bytes, engine=d.engine
            )
            for d in migration_service.discover_vhdx_disks()
        ]

    @app.post("/actions/compact-vhdx", response_model=VhdxCompactOut)
    def post_compact_vhdx(payload: PathActionIn) -> VhdxCompactOut:
        try:
            res = actions_service.compact_vhdx_assisted(payload.path)
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return VhdxCompactOut(
            path=res.path,
            before_bytes=res.before_bytes,
            after_bytes=res.after_bytes,
            freed_bytes=res.freed_bytes,
            succeeded=res.succeeded,
            detail=res.detail,
        )

    @app.get("/history", response_model=CleanupHistoryOut)
    def get_cleanup_history() -> CleanupHistoryOut:
        from discoclaro.services import history_service

        ov = history_service.load_history(data_dir=data_dir)
        return CleanupHistoryOut(
            total_freed_bytes=ov.total_freed_bytes,
            total_actions=ov.total_actions,
            entries=[
                CleanupHistoryEntryOut(
                    timestamp=e.timestamp,
                    action_type=e.action_type,
                    target=e.target,
                    freed_bytes=e.freed_bytes,
                    detail=e.detail,
                )
                for e in ov.entries
            ],
        )

    @app.get("/migration-assistant", response_model=MigrationAssistantOut)
    def get_migration_assistant() -> MigrationAssistantOut:
        from discoclaro.services import migration_service

        ov = migration_service.build_migration_assistant(data_dir=data_dir)
        return MigrationAssistantOut(
            source_volume=ov.source_volume,
            source_total_bytes=ov.source_total_bytes,
            source_used_bytes=ov.source_used_bytes,
            source_free_bytes=ov.source_free_bytes,
            source_used_percent=ov.source_used_percent,
            target_volume=ov.target_volume,
            target_total_bytes=ov.target_total_bytes,
            target_used_bytes=ov.target_used_bytes,
            target_free_bytes=ov.target_free_bytes,
            target_used_percent=ov.target_used_percent,
            total_movable_bytes=ov.total_movable_bytes,
            projected_source_used_percent=ov.projected_source_used_percent,
            candidates=[
                MigrationCandidateOut(
                    id=c.id,
                    title=c.title,
                    category=c.category,
                    source_path=c.source_path,
                    suggested_destination=c.suggested_destination,
                    size_bytes=c.size_bytes,
                    how_to_move=c.how_to_move,
                )
                for c in ov.candidates
            ],
        )

    @app.get("/recycle-bin", response_model=RecycleBinStatusOut)
    def get_recycle_bin_status() -> RecycleBinStatusOut:
        size_bytes, item_count = windows.recycle_bin_stats()
        return RecycleBinStatusOut(size_bytes=size_bytes, item_count=item_count)

    @app.post("/actions/empty-recycle-bin", response_model=RecycleBinEmptyOut)
    def post_empty_recycle_bin() -> RecycleBinEmptyOut:
        try:
            res = actions_service.empty_recycle_bin_verified()
        except ActionError as error:
            raise _as_http_error(error.kind) from error
        return RecycleBinEmptyOut(
            before_bytes=res.before_bytes,
            before_items=res.before_items,
            after_bytes=res.after_bytes,
            freed_bytes=res.freed_bytes,
            succeeded=res.succeeded,
        )

    def _disk_health_out(d) -> PhysicalDiskHealthOut:
        return PhysicalDiskHealthOut(
            device_id=d.device_id,
            friendly_name=d.friendly_name,
            serial_number=d.serial_number,
            firmware_version=d.firmware_version,
            media_type=d.media_type,
            bus_type=d.bus_type,
            partition_style=d.partition_style,
            drive_letters=list(d.drive_letters),
            health_status=d.health_status,
            operational_status=d.operational_status,
            size_bytes=d.size_bytes,
            logical_sector_size=d.logical_sector_size,
            physical_sector_size=d.physical_sector_size,
            temperature_c=d.temperature_c,
            temperature_max_c=d.temperature_max_c,
            wear_percent=d.wear_percent,
            life_remaining_percent=d.life_remaining_percent,
            power_on_hours=d.power_on_hours,
            read_errors=d.read_errors,
            write_errors=d.write_errors,
            available_spare_percent=d.available_spare_percent,
            written_tb=d.written_tb,
            smart_available=d.smart_available,
        )

    @app.get("/disk-health", response_model=list[PhysicalDiskHealthOut])
    def get_disk_health() -> list[PhysicalDiskHealthOut]:
        from discoclaro.services import disk_health_service

        disks = disk_health_service.read_physical_disks_health()
        return [_disk_health_out(d) for d in disks]

    @app.post("/actions/read-smart-admin", response_model=list[PhysicalDiskHealthOut])
    def post_read_smart_admin() -> list[PhysicalDiskHealthOut]:
        from discoclaro.services import disk_health_service

        disks = disk_health_service.read_physical_disks_health()
        if not any(d.smart_available for d in disks):
            disks = disk_health_service.read_smart_elevated()
        return [_disk_health_out(d) for d in disks]

    @app.post("/actions/launch-tray")
    def post_launch_tray() -> dict[str, object]:
        from discoclaro.platform import tray

        pid = tray.launch_system_tray(port=DEFAULT_PORT)
        return {"launched": True, "pid": pid}

    resolved_static = static_dir
    if resolved_static is None:
        import sys

        meipass = getattr(sys, "_MEIPASS", None)
        candidates = (
            [Path(meipass) / "dashboard" / "dist"] if meipass else []
        ) + [Path(__file__).resolve().parents[3] / "dashboard" / "dist"]
        for cand in candidates:
            if (cand / "index.html").is_file():
                resolved_static = cand
                break

    if resolved_static is not None and (resolved_static / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles

        assets_dir = resolved_static / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="dashboard-assets",
            )

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(
                resolved_static / "index.html",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )

    return app


def _prewarm_caches(data_dir: Path | None) -> None:
    """Carga en memoria las cachés persistidas en disco (<5 ms, sin bloquear el
    GIL) para que todas las pestañas respondan de forma instantánea."""
    import contextlib

    with contextlib.suppress(Exception):
        summary_service.run_summary(data_dir)
    with contextlib.suppress(Exception):
        run_plan(data_dir)


def run_api_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, data_dir: Path | None = None
) -> None:
    """Levanta la API de verdad con `uvicorn`, solo en `127.0.0.1` salvo que se
    pida otra cosa a propósito (nunca `0.0.0.0` por defecto). Compatible con
    `pythonw.exe` (donde `sys.stdout` y `sys.stderr` son `None`)."""
    import sys
    import threading

    import uvicorn

    threading.Thread(
        target=_prewarm_caches, args=(data_dir,), daemon=True
    ).start()
    log_cfg = (
        None
        if (sys.stdout is None or sys.stderr is None)
        else uvicorn.config.LOGGING_CONFIG
    )
    uvicorn.run(create_app(data_dir), host=host, port=port, log_config=log_cfg)
