"""Pruebas del caso de uso "plan" (T2C.7)."""

from datetime import UTC, datetime

import pytest

from discoclaro.adapters.inspectors.steam import SteamGame
from discoclaro.adapters.store import Store
from discoclaro.domain.config import Config
from discoclaro.domain.insights import Insight, InsightItem
from discoclaro.domain.inuse import ProjectReference, ProjectUsage
from discoclaro.domain.placement import Misplaced
from discoclaro.services.plan_service import (
    PlanError,
    _cache_items,
    _docker_items,
    _ollama_items,
    _protected_targets,
    _steam_items,
    run_plan,
)


def _insight(program: str, *items: tuple[str, int]) -> Insight:
    return Insight(
        program=program,
        items=tuple(InsightItem(name=n, size_bytes=s) for n, s in items),
    )


def test_docker_items_is_empty_without_an_insight():
    assert _docker_items(None) == []


def test_docker_items_puts_everything_in_the_safe_level():
    insight = _insight("docker", ("images", 100), ("build-cache", 50))

    items = _docker_items(insight)

    assert [i.level for i in items] == ["safe", "safe"]
    assert [i.target for i in items] == ["docker:images", "docker:build-cache"]
    assert [i.reason for i in items] == ["reclaimable", "reclaimable"]


def test_cache_items_is_empty_without_an_insight():
    assert _cache_items("pip", None) == []


def test_cache_items_uses_the_given_program_name():
    insight = _insight("pip", ("wheels", 10))

    items = _cache_items("uv", insight)

    assert items[0].program == "uv"
    assert items[0].target == "uv:wheels"
    assert items[0].reason == "cache"


def test_ollama_items_is_empty_without_an_insight():
    assert _ollama_items(None, []) == []


def test_ollama_items_skips_a_model_used_by_a_project():
    insight = _insight("ollama", ("llama3", 1000), ("mistral", 2000))
    usages = [
        ProjectUsage(
            project="mi-chat",
            references=(ProjectReference(kind="model", name="llama3"),),
        )
    ]

    items = _ollama_items(insight, usages)

    assert [i.name for i in items] == ["mistral"]
    assert items[0].level == "review"
    assert items[0].reason == "model"


def test_ollama_items_matching_is_case_insensitive():
    insight = _insight("ollama", ("Llama3", 1000))
    usages = [
        ProjectUsage(
            project="mi-chat",
            references=(ProjectReference(kind="model", name="llama3"),),
        )
    ]

    assert _ollama_items(insight, usages) == []


def test_steam_items_marks_a_misplaced_game_as_move():
    games = [
        SteamGame(name="Juego Grande", library="C:\\Steam", size_bytes=5000),
        SteamGame(name="Otro Juego", library="D:\\SteamLibrary", size_bytes=3000),
    ]
    misplaced = [
        Misplaced(
            name="Juego Grande",
            drive="C:",
            size_bytes=5000,
            other_drives=("D:",),
        )
    ]

    items = _steam_items(games, find_misplaced=lambda **_: misplaced)

    by_name = {i.name: i for i in items}
    assert by_name["Juego Grande"].level == "move"
    assert by_name["Juego Grande"].reason == "misplaced"
    assert by_name["Otro Juego"].level == "review"
    assert by_name["Otro Juego"].reason == "game"


def test_steam_items_is_empty_without_games():
    assert _steam_items([], find_misplaced=lambda **_: []) == []


def test_protected_targets_only_includes_protected_decisions(tmp_path):
    from discoclaro.domain.plan import PlanItem

    candidates = [
        PlanItem(
            level="safe",
            program="docker",
            name="images",
            size_bytes=1,
            reason="reclaimable",
            target="docker:images",
        ),
        PlanItem(
            level="review",
            program="ollama",
            name="llama3",
            size_bytes=1,
            reason="model",
            target="ollama:llama3",
        ),
    ]
    with Store.open(tmp_path / "discoclaro.db") as store:
        store.save_decision("docker:images", "protected", None, datetime.now(UTC))
        store.save_decision("ollama:llama3", "pending", None, datetime.now(UTC))

        excluded = _protected_targets(store, candidates)

    assert excluded == {"docker:images"}


def test_run_plan_combines_inspectors_and_excludes_protected_and_in_use(tmp_path):
    def fake_config(_config_dir):
        return Config(project_folders=("D:\\proyecto",))

    def fake_scan_projects(folders, *, known_models):
        assert folders == ("D:\\proyecto",)
        assert known_models == frozenset({"llama3", "mistral"})
        return [
            ProjectUsage(
                project="proyecto",
                references=(ProjectReference(kind="model", name="llama3"),),
            )
        ]

    with Store.open(tmp_path / "discoclaro.db") as store:
        store.save_decision("docker:build-cache", "protected", None, datetime.now(UTC))

    plan = run_plan(
        tmp_path,
        tmp_path,
        load_config=fake_config,
        docker_insight=lambda: _insight("docker", ("images", 100), ("build-cache", 50)),
        pip_insight=lambda: None,
        uv_insight=lambda: None,
        npm_insight=lambda: _insight("npm", ("cache", 30)),
        ollama_insight=lambda: _insight("ollama", ("llama3", 1000), ("mistral", 2000)),
        steam_games=lambda: [],
        scan_projects=fake_scan_projects,
    )

    targets = {item.target for item in plan.items}
    assert "docker:images" in targets
    assert "docker:build-cache" not in targets  # protegido
    assert "npm:cache" in targets
    assert "ollama:llama3" not in targets  # en uso por "proyecto"
    assert "ollama:mistral" in targets

    assert plan.total_bytes("safe") == 100 + 30  # docker:images + npm:cache
    assert plan.total_bytes("review") == 2000  # ollama:mistral


def test_run_plan_raises_plan_error_when_the_database_cannot_be_opened(tmp_path):
    missing_dir = tmp_path / "no_existe"

    with pytest.raises(PlanError):
        run_plan(
            missing_dir,
            tmp_path,
            load_config=lambda _: Config(),
            docker_insight=lambda: None,
            pip_insight=lambda: None,
            uv_insight=lambda: None,
            npm_insight=lambda: None,
            ollama_insight=lambda: None,
            steam_games=lambda: [],
            scan_projects=lambda folders, **kw: [],
        )
