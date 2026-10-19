"""API local del dashboard (T3.1): `127.0.0.1`, de solo lectura.

Como toda interfaz, no lleva lógica propia: cada ruta llama a un servicio y traduce
su resultado a un modelo de datos simple. El endurecimiento real (token, `Host` y
`Origin`, cabeceras de seguridad) es T3.10; aquí solo se apaga lo que no cuesta nada
apagar desde el principio: no hay documentación pública (`/docs`, `/openapi.json`).
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from discoclaro.domain.categories import CategoryTotal
from discoclaro.domain.growth import CategoryGrowth, FolderGrowth
from discoclaro.domain.models import Node, Snapshot, Volume
from discoclaro.domain.plan import PlanItem
from discoclaro.domain.unexplained import Unexplained
from discoclaro.services import (
    growth_service,
    report_service,
    summary_service,
    volumes_service,
)
from discoclaro.services.growth_service import GrowthError
from discoclaro.services.plan_service import PlanError, run_plan
from discoclaro.services.report_service import ReportError
from discoclaro.services.snapshots_service import SnapshotsError, run_snapshots
from discoclaro.services.summary_service import SummaryError
from discoclaro.services.tree_service import TreeError, run_tree

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8347

_BAD_REQUEST_KINDS = frozenset(
    {"growth_invalid_since", "report_not_fixed_disk", "growth_not_fixed_disk"}
)
_SERVER_ERROR_KINDS = frozenset({"cannot_open_database"})


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


def _as_http_error(kind: str) -> HTTPException:
    if kind in _SERVER_ERROR_KINDS:
        return HTTPException(status_code=503, detail=kind)
    if kind in _BAD_REQUEST_KINDS:
        return HTTPException(status_code=400, detail=kind)
    return HTTPException(status_code=404, detail=kind)


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


def create_app(data_dir: Path | None = None) -> FastAPI:
    """La API, lista para montar (`TestClient` en las pruebas, `uvicorn` en serio).

    `data_dir` apunta a la base de discoclaro a usar; `None` es la real
    (`%LOCALAPPDATA%\\discoclaro`), y una carpeta de prueba en los tests.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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
    ) -> TreeOut:
        try:
            view = run_tree(path, snapshot_id, data_dir=data_dir)
        except TreeError as error:
            raise _as_http_error(error.kind) from error
        return TreeOut(
            snapshot_id=view.snapshot.id,
            volume=view.snapshot.volume,
            node=None if view.node is None else _node_out(view.node),
            children=[_node_out(child) for child in view.children],
        )

    @app.get("/categories", response_model=list[CategoryTotalOut])
    def get_categories(
        volume: str | None = Query(default=None),
    ) -> list[CategoryTotalOut]:
        try:
            report = report_service.run_report(
                volume, data_dir=data_dir, by_category=True
            )
        except ReportError as error:
            raise _as_http_error(error.kind) from error
        return [_category_out(total) for total in report.by_category or ()]

    @app.get("/growth", response_model=GrowthOut)
    def get_growth(
        volume: str | None = Query(default=None),
        since: str = Query(default=growth_service.DEFAULT_SINCE),
    ) -> GrowthOut:
        try:
            growth = growth_service.run_growth(
                volume, since, data_dir=data_dir, by_category=True
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

    return app


def run_api_server(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, data_dir: Path | None = None
) -> None:
    """Levanta la API de verdad con `uvicorn`, solo en `127.0.0.1` salvo que se
    pida otra cosa a propósito (nunca `0.0.0.0` por defecto)."""
    import uvicorn

    uvicorn.run(create_app(data_dir), host=host, port=port)
