"""Pruebas del servicio de chequeo (T2.7): siempre sobre fotos guardadas en `tmp_path`.

`run_check` notifica de verdad, pero `notify` siempre se reemplaza por uno falso: nunca
muestra un toast real ni toca `%LOCALAPPDATA%` real.
"""

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from discoclaro.adapters import notifier
from discoclaro.adapters.store import Store
from discoclaro.domain.alerts import Alert
from discoclaro.domain.config import Budget, Config, GrowthLimits, VolumeLimit
from discoclaro.domain.models import LARGE_FILE_MIN_BYTES, LargeFile, Node, Snapshot
from discoclaro.domain.rules import Rule
from discoclaro.services import check_service
from discoclaro.services.check_service import CheckError, evaluate, run_check

GIB = 1024**3
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with Store.open(tmp_path / "test.db") as opened:
        yield opened


def save(store, volume, nodes=(), large_files=(), **overrides):
    fields = {
        "taken_at": T0,
        "volume": volume,
        "total_bytes": 100 * GIB,
        "used_bytes": 40 * GIB,
        "free_bytes": 60 * GIB,
        "scanned_bytes": 1,
        "system_files_bytes": 0,
        "unreadable_bytes": 0,
        "unreadable_count": 0,
        "duration_s": 1.0,
        "is_admin": False,
    }
    fields.update(overrides)
    return store.save_snapshot(Snapshot(**fields), list(nodes), list(large_files))


def rule(id, *paths, category):
    return Rule(
        id=id, name=id, paths=paths, category=category, risk="safe", explanation="x"
    )


def test_evaluate_with_the_default_config_finds_nothing(store):
    save(store, "C:")
    assert evaluate(store, Config()) == []


def test_volume_low_free_is_found_for_a_whole_disk_snapshot(store):
    save(store, "C:", free_bytes=10 * GIB)
    config = Config(volumes={"C:": VolumeLimit(min_free_gb=40)})
    alerts = evaluate(store, config)
    assert [a.kind for a in alerts] == ["volume_low_free"]
    assert alerts[0].target == "C:"


def test_volume_checks_are_skipped_without_a_configured_limit(store):
    save(store, "C:", free_bytes=1 * GIB)
    assert evaluate(store, Config()) == []


def test_a_disabled_alert_kind_never_fires(store):
    save(store, "C:", free_bytes=1 * GIB)
    config = Config(
        volumes={"C:": VolumeLimit(min_free_gb=40)},
        disabled_alerts=frozenset({"volume_low_free"}),
    )
    assert evaluate(store, config) == []


def test_disabling_one_kind_does_not_affect_the_others(store):
    save(
        store,
        "C:",
        total_bytes=100 * GIB,
        used_bytes=95 * GIB,
        free_bytes=5 * GIB,
    )
    config = Config(
        volumes={"C:": VolumeLimit(min_free_gb=40, warn_at_percent=85)},
        disabled_alerts=frozenset({"volume_low_free"}),
    )
    alerts = evaluate(store, config)
    assert [a.kind for a in alerts] == ["volume_near_limit"]


def test_volume_checks_are_skipped_for_a_folder_snapshot(store):
    save(store, "C:\\Users\\x", free_bytes=1 * GIB)
    config = Config(volumes={"C:": VolumeLimit(min_free_gb=40)})
    assert evaluate(store, config) == []


def test_volume_near_limit_is_found_from_used_and_total(store):
    save(store, "C:", total_bytes=100 * GIB, used_bytes=90 * GIB, free_bytes=10 * GIB)
    config = Config(volumes={"C:": VolumeLimit(warn_at_percent=85)})
    alerts = evaluate(store, config)
    assert [a.kind for a in alerts] == ["volume_near_limit"]


def test_both_volume_alerts_can_fire_together(store):
    save(store, "C:", total_bytes=100 * GIB, used_bytes=95 * GIB, free_bytes=5 * GIB)
    config = Config(volumes={"C:": VolumeLimit(min_free_gb=40, warn_at_percent=85)})
    alerts = evaluate(store, config)
    assert {a.kind for a in alerts} == {"volume_low_free", "volume_near_limit"}


def test_a_budget_matches_a_category_from_the_rule_catalog(store):
    nodes = [Node("C:\\models", None, 35 * GIB, 1, 0)]
    save(store, "C:\\models", nodes)
    config = Config(budgets={"ai-models": Budget(max_gb=30)})
    rules = [rule("a", "C:\\models", category="ai-models")]
    alerts = evaluate(store, config, rules=lambda: rules, environment=dict)
    assert [a.kind for a in alerts] == ["budget_exceeded"]
    assert alerts[0].target == "ai-models"


def test_a_budget_matches_an_exact_folder_path_case_insensitively(store):
    nodes = [Node("C:\\Downloads", None, 15 * GIB, 1, 0)]
    save(store, "C:\\Downloads", nodes)
    config = Config(budgets={"c:\\downloads": Budget(max_gb=10)})
    alerts = evaluate(store, config, rules=list, environment=dict)
    assert [a.kind for a in alerts] == ["budget_exceeded"]


def test_a_budget_with_no_matching_category_or_folder_is_skipped(store):
    nodes = [Node("C:\\other", None, 1, 1, 0)]
    save(store, "C:\\other", nodes)
    config = Config(budgets={"ai-models": Budget(max_gb=30)})
    assert evaluate(store, config, rules=list, environment=dict) == []


def test_rules_and_environment_are_not_called_without_budgets(store):
    save(store, "C:")

    def forbidden():
        raise AssertionError("no debería armar el catálogo ni el entorno")

    evaluate(store, Config(), rules=forbidden, environment=forbidden)


def test_big_new_file_uses_the_default_threshold_without_configuration(store):
    save(store, "C:\\models", large_files=[], taken_at=T0)
    save(
        store,
        "C:\\models",
        large_files=[LargeFile("C:\\models\\weights.bin", LARGE_FILE_MIN_BYTES)],
        taken_at=T0 + timedelta(hours=1),
    )
    alerts = evaluate(store, Config())
    assert [a.kind for a in alerts] == ["big_new_file"]
    assert alerts[0].limit_bytes == LARGE_FILE_MIN_BYTES


def test_big_new_file_uses_the_configured_threshold(store):
    save(store, "C:\\models", large_files=[], taken_at=T0)
    save(
        store,
        "C:\\models",
        large_files=[LargeFile("C:\\models\\weights.bin", 5 * GIB)],
        taken_at=T0 + timedelta(hours=1),
    )
    config = Config(growth=GrowthLimits(new_file_gb=10))
    assert evaluate(store, config) == []


def test_unexplained_grew_never_fires_without_a_configured_limit(store):
    save(store, "C:", used_bytes=40 * GIB, scanned_bytes=1, taken_at=T0)
    save(
        store,
        "C:",
        used_bytes=80 * GIB,
        scanned_bytes=1,
        taken_at=T0 + timedelta(hours=1),
    )
    assert evaluate(store, Config()) == []


def test_unexplained_grew_fires_when_configured_and_crossed(store):
    save(store, "C:", used_bytes=40 * GIB, scanned_bytes=1, taken_at=T0)
    save(
        store,
        "C:",
        used_bytes=80 * GIB,
        scanned_bytes=1,
        taken_at=T0 + timedelta(hours=1),
    )
    config = Config(growth=GrowthLimits(max_growth_gb=10))
    alerts = evaluate(store, config)
    assert [a.kind for a in alerts] == ["unexplained_grew"]


def test_unexplained_grew_is_skipped_for_a_folder_target(store):
    save(store, "C:\\x", used_bytes=40 * GIB, scanned_bytes=1, taken_at=T0)
    save(
        store,
        "C:\\x",
        used_bytes=80 * GIB,
        scanned_bytes=1,
        taken_at=T0 + timedelta(hours=1),
    )
    config = Config(growth=GrowthLimits(max_growth_gb=1))
    assert evaluate(store, config) == []


def test_growth_checks_need_at_least_two_snapshots(store):
    save(store, "C:", used_bytes=90 * GIB, scanned_bytes=1)
    config = Config(growth=GrowthLimits(max_growth_gb=1))
    assert evaluate(store, config) == []


def fake_notify():
    """Reemplaza el notificador real; devuelve (los llamados, la función)."""
    calls = []
    return calls, lambda title, message: calls.append((title, message))


def test_run_check_raises_without_any_database(tmp_path):
    _, notify = fake_notify()
    with pytest.raises(CheckError) as error:
        run_check(tmp_path / "missing", tmp_path, notify=notify)
    assert error.value.kind == "check_no_snapshots"


def test_run_check_evaluates_and_records_the_alert(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    (tmp_path / "config.toml").write_text(
        '[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8"
    )
    _, notify = fake_notify()
    alerts = run_check(data_dir, tmp_path, notify=notify)
    assert [a.kind for a in alerts] == ["volume_low_free"]
    with Store.open_readonly(data_dir / "discoclaro.db") as reopened:
        assert reopened.last_alert_at("volume_low_free", "C:") is not None


def test_run_check_notifies_with_the_translated_title_and_message(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    (tmp_path / "config.toml").write_text(
        '[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8"
    )
    calls, notify = fake_notify()
    run_check(data_dir, tmp_path, notify=notify)
    assert calls == [
        (
            "Poco espacio libre",
            "A C: le quedan 1,0 GiB libres (el mínimo configurado es 40,0 GiB).",
        )
    ]


def test_run_check_does_not_repeat_within_the_cooldown(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    (tmp_path / "config.toml").write_text(
        '[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8"
    )
    moments = iter([T0, T0 + timedelta(hours=1)])
    calls, notify = fake_notify()
    first = run_check(data_dir, tmp_path, now=lambda: next(moments), notify=notify)
    second = run_check(data_dir, tmp_path, now=lambda: next(moments), notify=notify)
    assert [a.kind for a in first] == ["volume_low_free"]
    assert second == []
    assert len(calls) == 1


def test_run_check_fires_again_after_the_cooldown(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    (tmp_path / "config.toml").write_text(
        '[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8"
    )
    moments = iter([T0, T0 + check_service.ALERT_COOLDOWN + timedelta(seconds=1)])
    _, notify = fake_notify()
    first = run_check(data_dir, tmp_path, now=lambda: next(moments), notify=notify)
    second = run_check(data_dir, tmp_path, now=lambda: next(moments), notify=notify)
    assert [a.kind for a in first] == ["volume_low_free"]
    assert [a.kind for a in second] == ["volume_low_free"]


def test_run_check_without_a_config_file_uses_the_defaults(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    _, notify = fake_notify()
    assert run_check(data_dir, tmp_path / "no_config_here", notify=notify) == []


def test_alerts_returned_are_plain_data(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with Store.open(data_dir / "discoclaro.db") as opened:
        save(opened, "C:", free_bytes=1 * GIB)
    (tmp_path / "config.toml").write_text(
        '[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8"
    )
    _, notify = fake_notify()
    alerts = run_check(data_dir, tmp_path, notify=notify)
    assert all(isinstance(a, Alert) for a in alerts)


def test_the_real_notifier_is_the_default_without_calling_it():
    parameters = inspect.signature(run_check).parameters
    assert parameters["notify"].default is notifier.send_notification
