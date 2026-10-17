"""Pruebas de los datos de una regla: sin TOML, sin disco."""

import pytest

from discoclaro.domain.rules import Reacquire, Rule, SuggestedAction


def make_rule(**changes):
    fields = {
        "id": "pip-cache",
        "name": "Caché de pip",
        "paths": ("%LOCALAPPDATA%\\pip\\Cache",),
        "category": "cache",
        "risk": "safe",
        "explanation": "pip vuelve a descargar lo que falte.",
    }
    return Rule(**{**fields, **changes})


def test_a_minimal_rule_is_valid():
    rule = make_rule()
    assert rule.suggested_action is None
    assert rule.reacquire is None
    assert rule.task_id is None


@pytest.mark.parametrize("field", ["id", "name", "category", "explanation"])
def test_a_required_text_field_cannot_be_empty(field):
    with pytest.raises(ValueError):
        make_rule(**{field: ""})


def test_at_least_one_path_is_required():
    with pytest.raises(ValueError):
        make_rule(paths=())


def test_an_empty_path_in_the_list_is_rejected():
    with pytest.raises(ValueError):
        make_rule(paths=("%TEMP%", ""))


@pytest.mark.parametrize("risk", ["safe", "review", "do_not_touch"])
def test_the_three_risk_levels_are_accepted(risk):
    assert make_rule(risk=risk).risk == risk


def test_an_unknown_risk_level_is_rejected():
    with pytest.raises(ValueError):
        make_rule(risk="dangerous")


def test_a_suggested_action_needs_a_type_and_a_text():
    action = SuggestedAction(type="command", text="pip cache purge")
    assert make_rule(suggested_action=action).suggested_action == action
    with pytest.raises(ValueError):
        SuggestedAction(type="command", text="")
    with pytest.raises(ValueError):
        SuggestedAction(type="", text="pip cache purge")


@pytest.mark.parametrize("kind", ["none", "download", "rebuild"])
def test_the_three_reacquire_kinds_are_accepted(kind):
    assert Reacquire(kind=kind).kind == kind


def test_an_unknown_reacquire_kind_is_rejected():
    with pytest.raises(ValueError):
        Reacquire(kind="instant")


def test_a_full_rule_round_trips_every_field():
    action = SuggestedAction(
        type="command", text="docker builder prune -af", note="Después compactar."
    )
    reacquire = Reacquire(kind="rebuild", cost="Docker vuelve a bajar lo borrado")
    rule = make_rule(
        id="docker-vhdx",
        name="Disco virtual de Docker",
        paths=("%LOCALAPPDATA%\\Docker\\wsl\\disk\\docker_data.vhdx",),
        category="containers",
        risk="review",
        explanation="Docker guarda aquí imágenes y volúmenes.",
        suggested_action=action,
        reacquire=reacquire,
        task_id="compact_docker_vhdx",
    )
    assert rule.suggested_action == action
    assert rule.reacquire == reacquire
    assert rule.task_id == "compact_docker_vhdx"
