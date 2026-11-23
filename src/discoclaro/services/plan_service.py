"""Caso de uso "plan" (T2C.7): el veredicto por niveles.

Junta lo que ya saben los inspectores de programas (T2C.1: Docker, Ollama,
Steam, cachés de pip/uv/npm) en tres niveles (`domain/plan.py`), saca lo que
está protegido (T2C.4) y lo que un proyecto configurado dice usar (T2C.3): el
resultado son GB recuperables que de verdad nadie necesita ni decidió
guardar. Es el veredicto de la Fase 2C, no vuelve a escanear carpetas (eso
son las fases 1/2): junta programas, no archivos.

**Límite a propósito:** Docker solo da espacio recuperable por categoría
(imágenes, contenedores…), no por imagen suelta, así que "en uso por un
proyecto" no se le puede aplicar todavía a esas filas; si un proyecto
referencia una imagen de verdad, igual se cuenta como recuperable en el
nivel "seguro" hasta que un inspector de Docker más fino lo permita. Lo
mismo con las cachés de pip/uv/npm (son un solo número agregado, no
paquetes sueltos). Donde sí aplica de verdad es en los modelos de Ollama.
"""

import contextlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from discoclaro.adapters import projects
from discoclaro.adapters.config import load_user_config
from discoclaro.adapters.inspectors import docker, npm, ollama, pip, steam, uv
from discoclaro.adapters.inspectors.steam import SteamGame
from discoclaro.adapters.store import Store, StoreError
from discoclaro.domain.config import Config
from discoclaro.domain.decisions import is_protected
from discoclaro.domain.insights import Insight
from discoclaro.domain.inuse import ProjectUsage, used_by
from discoclaro.domain.placement import Misplaced
from discoclaro.domain.plan import Plan, PlanItem, build_plan, target_of
from discoclaro.platform import windows
from discoclaro.services.scan_service import DATABASE_NAME


class PlanError(Exception):
    """No se pudo armar el plan; `kind` es la clave del mensaje al usuario."""

    def __init__(self, kind: str, target: str = "") -> None:
        super().__init__(f"{kind}: {target!r}")
        self.kind = kind
        self.target = target


def _docker_items(insight: Insight | None) -> list[PlanItem]:
    if insight is None:
        return []
    return [
        PlanItem(
            level="safe",
            program="docker",
            name=item.name,
            size_bytes=item.size_bytes,
            reason="reclaimable",
            target=target_of("docker", item.name),
        )
        for item in insight.items
    ]


def _cache_items(program: str, insight: Insight | None) -> list[PlanItem]:
    if insight is None:
        return []
    return [
        PlanItem(
            level="safe",
            program=program,
            name=item.name,
            size_bytes=item.size_bytes,
            reason="cache",
            target=target_of(program, item.name),
        )
        for item in insight.items
    ]


def _ollama_items(
    insight: Insight | None, usages: Sequence[ProjectUsage]
) -> list[PlanItem]:
    if insight is None:
        return []
    items = []
    for item in insight.items:
        if used_by(usages, "model", item.name):
            continue
        items.append(
            PlanItem(
                level="review",
                program="ollama",
                name=item.name,
                size_bytes=item.size_bytes,
                reason="model",
                target=target_of("ollama", item.name),
            )
        )
    return items


def _steam_items(
    games: list[SteamGame],
    *,
    find_misplaced: Callable[..., list[Misplaced]] = steam.misplaced_games,
) -> list[PlanItem]:
    misplaced_names = {m.name for m in find_misplaced(games=lambda: games)}
    items = []
    for game in games:
        if game.name in misplaced_names:
            level, reason = "move", "misplaced"
        else:
            level, reason = "review", "game"
        items.append(
            PlanItem(
                level=level,
                program="steam",
                name=game.name,
                size_bytes=game.size_bytes,
                reason=reason,
                target=target_of("steam", game.name),
            )
        )
    return items


def _protected_targets(store: Store, candidates: Sequence[PlanItem]) -> set[str]:
    return {
        item.target
        for item in candidates
        if is_protected(store.decision_of(item.target))
    }


PLAN_CACHE_TTL_S = 600.0
_plan_cache: dict[str, tuple[float, Plan]] = {}
_plan_refreshing = False
_plan_lock = threading.Lock()


def _disk_cache_path(data_dir: Path | None) -> Path:
    directory = windows.default_data_dir() if data_dir is None else data_dir
    return Path(directory) / "plan_cache.json"


def _load_disk_cached_plan(data_dir: Path | None) -> tuple[float, Plan] | None:
    path = _disk_cache_path(data_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(raw.get("saved_at", 0.0))
        items = [
            PlanItem(
                level=row["level"],
                program=row["program"],
                name=row["name"],
                size_bytes=int(row["size_bytes"]),
                reason=row["reason"],
                target=row["target"],
            )
            for row in raw.get("items", [])
        ]
        return saved_at, build_plan(items, is_excluded=set())
    except Exception:
        return None


def _save_disk_cached_plan(data_dir: Path | None, plan: Plan) -> None:
    path = _disk_cache_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "items": [
                {
                    "level": i.level,
                    "program": i.program,
                    "name": i.name,
                    "size_bytes": i.size_bytes,
                    "reason": i.reason,
                    "target": i.target,
                }
                for i in plan.items
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _refresh_default_plan_async() -> None:
    global _plan_refreshing
    try:
        with contextlib.suppress(Exception):
            _compute_plan_fresh(
                None,
                None,
                load_config=load_user_config,
                docker_insight=docker.inspect,
                pip_insight=pip.inspect,
                uv_insight=uv.inspect,
                npm_insight=npm.inspect,
                ollama_insight=ollama.inspect,
                steam_games=steam.installed_games,
                scan_projects=projects.scan_projects,
                use_default_cache=True,
            )
    finally:
        _plan_refreshing = False


def run_plan(
    data_dir: Path | None = None,
    config_dir: Path | None = None,
    *,
    load_config: Callable[[Path | None], Config] = load_user_config,
    docker_insight: Callable[[], Insight | None] = docker.inspect,
    pip_insight: Callable[[], Insight | None] = pip.inspect,
    uv_insight: Callable[[], Insight | None] = uv.inspect,
    npm_insight: Callable[[], Insight | None] = npm.inspect,
    ollama_insight: Callable[[], Insight | None] = ollama.inspect,
    steam_games: Callable[[], list[SteamGame]] = steam.installed_games,
    scan_projects: Callable[..., list[ProjectUsage]] = projects.scan_projects,
) -> Plan:
    """Arma el plan de verdad: pregunta a cada inspector en paralelo, saca lo
    protegido y lo que un proyecto configurado usa. Cachea en memoria y en disco
    con refresco asíncrono (Stale-While-Revalidate) cuando usa los inspectores
    por defecto para que el dashboard cargue en <5 ms."""
    global _plan_refreshing
    use_default_cache = (
        data_dir is None
        and config_dir is None
        and docker_insight is docker.inspect
        and pip_insight is pip.inspect
        and uv_insight is uv.inspect
        and npm_insight is npm.inspect
        and ollama_insight is ollama.inspect
        and steam_games is steam.installed_games
    )
    now_mono = time.monotonic()
    if use_default_cache:
        with _plan_lock:
            if _plan_cache and "default" in _plan_cache:
                cached_at, cached_plan = _plan_cache["default"]
                if now_mono - cached_at < PLAN_CACHE_TTL_S:
                    return cached_plan
                if not _plan_refreshing:
                    _plan_refreshing = True
                    threading.Thread(
                        target=_refresh_default_plan_async, daemon=True
                    ).start()
                return cached_plan
            disk_loaded = _load_disk_cached_plan(data_dir)
            if disk_loaded is not None:
                saved_at, disk_plan = disk_loaded
                if _plan_cache is None:
                    globals()["_plan_cache"] = {}
                _plan_cache["default"] = (now_mono, disk_plan)
                is_expired = (time.time() - saved_at) >= PLAN_CACHE_TTL_S
                if is_expired and not _plan_refreshing:
                    _plan_refreshing = True
                    threading.Thread(
                        target=_refresh_default_plan_async, daemon=True
                    ).start()
                return disk_plan
            return _compute_plan_fresh(
                data_dir,
                config_dir,
                load_config=load_config,
                docker_insight=docker_insight,
                pip_insight=pip_insight,
                uv_insight=uv_insight,
                npm_insight=npm_insight,
                ollama_insight=ollama_insight,
                steam_games=steam_games,
                scan_projects=scan_projects,
                use_default_cache=True,
            )

    return _compute_plan_fresh(
        data_dir,
        config_dir,
        load_config=load_config,
        docker_insight=docker_insight,
        pip_insight=pip_insight,
        uv_insight=uv_insight,
        npm_insight=npm_insight,
        ollama_insight=ollama_insight,
        steam_games=steam_games,
        scan_projects=scan_projects,
        use_default_cache=use_default_cache,
    )


def _compute_plan_fresh(
    data_dir: Path | None,
    config_dir: Path | None,
    *,
    load_config: Callable[[Path | None], Config],
    docker_insight: Callable[[], Insight | None],
    pip_insight: Callable[[], Insight | None],
    uv_insight: Callable[[], Insight | None],
    npm_insight: Callable[[], Insight | None],
    ollama_insight: Callable[[], Insight | None],
    steam_games: Callable[[], list[SteamGame]],
    scan_projects: Callable[..., list[ProjectUsage]],
    use_default_cache: bool,
) -> Plan:
    now_mono = time.monotonic()
    config = load_config(config_dir)
    with ThreadPoolExecutor(max_workers=6) as pool:
        fut_ollama = pool.submit(ollama_insight)
        fut_docker = pool.submit(docker_insight)
        fut_pip = pool.submit(pip_insight)
        fut_uv = pool.submit(uv_insight)
        fut_npm = pool.submit(npm_insight)
        fut_steam = pool.submit(steam_games)

        ollama_found = fut_ollama.result()
        docker_found = fut_docker.result()
        pip_found = fut_pip.result()
        uv_found = fut_uv.result()
        npm_found = fut_npm.result()
        steam_found = fut_steam.result()

    known_models = frozenset(
        item.name for item in (ollama_found.items if ollama_found else ())
    )
    usages = scan_projects(config.project_folders, known_models=known_models)

    candidates: list[PlanItem] = []
    candidates += _docker_items(docker_found)
    candidates += _cache_items("pip", pip_found)
    candidates += _cache_items("uv", uv_found)
    candidates += _cache_items("npm", npm_found)
    candidates += _ollama_items(ollama_found, usages)
    candidates += _steam_items(steam_found)

    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = os.path.join(str(directory), DATABASE_NAME)
    try:
        store = Store.open(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise PlanError("cannot_open_database", str(error)) from error
    with store:
        excluded = _protected_targets(store, candidates)
    built = build_plan(candidates, is_excluded=excluded)
    if use_default_cache:
        if _plan_cache is None:
            globals()["_plan_cache"] = {}
        _plan_cache["default"] = (now_mono, built)
        _save_disk_cached_plan(data_dir, built)
    return built
