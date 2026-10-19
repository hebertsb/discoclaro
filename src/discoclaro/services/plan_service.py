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

import os
import sqlite3
from collections.abc import Callable, Sequence
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
    """Arma el plan de verdad: pregunta a cada inspector, saca lo protegido y
    lo que un proyecto configurado usa. Lanza `PlanError` si no se pudo abrir
    la base (para leer las decisiones guardadas, T2C.4)."""
    config = load_config(config_dir)
    ollama_found = ollama_insight()
    known_models = frozenset(
        item.name for item in (ollama_found.items if ollama_found else ())
    )
    usages = scan_projects(config.project_folders, known_models=known_models)

    candidates: list[PlanItem] = []
    candidates += _docker_items(docker_insight())
    candidates += _cache_items("pip", pip_insight())
    candidates += _cache_items("uv", uv_insight())
    candidates += _cache_items("npm", npm_insight())
    candidates += _ollama_items(ollama_found, usages)
    candidates += _steam_items(steam_games())

    directory = windows.default_data_dir() if data_dir is None else data_dir
    database = os.path.join(str(directory), DATABASE_NAME)
    try:
        store = Store.open(database)
    except (OSError, sqlite3.Error, StoreError) as error:
        raise PlanError("cannot_open_database", str(error)) from error
    with store:
        excluded = _protected_targets(store, candidates)
    return build_plan(candidates, is_excluded=excluded)
