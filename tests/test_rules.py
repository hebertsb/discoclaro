"""Pruebas del cargador de reglas: siempre texto o archivos en `tmp_path`."""

import json

import pytest

from discoclaro.adapters.rules import (
    MAX_FIELD_LENGTH,
    RuleError,
    load_builtin_catalog,
    load_rules_file,
    load_rules_from_text,
)
from discoclaro.domain.rules import RISK_LEVELS, Reacquire, SuggestedAction

MINIMAL = """
[[rule]]
id = "pip-cache"
name = "Caché de pip"
paths = ["%LOCALAPPDATA%\\\\pip\\\\Cache"]
category = "cache"
risk = "safe"
explanation = "pip vuelve a descargar lo que falte."
"""

FULL = """
[[rule]]
id = "docker-vhdx"
name = "Disco virtual de Docker"
paths = ["%LOCALAPPDATA%\\\\Docker\\\\wsl\\\\disk\\\\docker_data.vhdx"]
category = "containers"
risk = "review"
explanation = "Docker guarda aquí imágenes, contenedores y volúmenes."
task_id = "compact_docker_vhdx"

[[rule]]
id = "windows-backup"
name = "Copias de seguridad de Windows"
paths = ["<volumen>\\\\<NOMBRE-PC>\\\\Backup Set *"]
category = "backups"
risk = "do_not_touch"
explanation = "Solo se borra a mano, desde el Panel de control."

[rule.suggested_action]
type = "command"
text = "docker builder prune -af"
note = "Después hay que compactar el disco virtual."

[rule.reacquire]
kind = "rebuild"
cost = "Docker vuelve a bajar o construir lo que se borre"
"""


def test_a_minimal_rule_loads_with_no_optional_fields():
    [rule] = load_rules_from_text(MINIMAL)
    assert rule.id == "pip-cache"
    assert rule.paths == ("%LOCALAPPDATA%\\pip\\Cache",)
    assert rule.risk == "safe"
    assert rule.suggested_action is None
    assert rule.reacquire is None
    assert rule.task_id is None


def test_an_empty_catalog_gives_an_empty_list():
    assert load_rules_from_text("") == []
    assert load_rules_from_text("title = 'sin reglas'") == []


def test_several_rules_load_in_order():
    rules = load_rules_from_text(MINIMAL + FULL)
    assert [rule.id for rule in rules] == [
        "pip-cache",
        "docker-vhdx",
        "windows-backup",
    ]


def test_a_rule_with_a_table_inside_the_wrong_rule_keeps_its_own_fields():
    """`[rule.suggested_action]` después de `[[rule]]` pertenece a la última tabla."""
    rules = load_rules_from_text(FULL)
    docker, backup = rules
    assert docker.suggested_action is None
    assert backup.suggested_action == SuggestedAction(
        type="command",
        text="docker builder prune -af",
        note="Después hay que compactar el disco virtual.",
    )
    assert backup.reacquire == Reacquire(
        kind="rebuild", cost="Docker vuelve a bajar o construir lo que se borre"
    )


@pytest.mark.parametrize(
    "broken",
    [
        "this is not [valid toml",
        "rule = 'not a list'",
        "[[rule]]\nid = 1\nname = 'x'\npaths = ['a']\ncategory = 'c'\nrisk = 'safe'\n"
        "explanation = 'e'\n",
        "[[rule]]\nname = 'sin id'\npaths = ['a']\ncategory = 'c'\nrisk = 'safe'\n"
        "explanation = 'e'\n",
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = []\ncategory = 'c'\nrisk = 'safe'\n"
        "explanation = 'e'\n",
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['a']\ncategory = 'c'\n"
        "risk = 'maybe'\nexplanation = 'e'\n",
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['a']\ncategory = 'c'\nrisk = 'safe'\n"
        "explanation = 'e'\nreacquire = { kind = 'instant' }\n",
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['a']\ncategory = 'c'\nrisk = 'safe'\n"
        "explanation = 'e'\nsuggested_action = { type = 'command' }\n",
        "[[rule]]\n42\n",
    ],
)
def test_malformed_rules_are_rejected(broken):
    with pytest.raises(RuleError):
        load_rules_from_text(broken)


def test_a_duplicate_id_in_the_same_source_is_rejected():
    with pytest.raises(RuleError) as error:
        load_rules_from_text(MINIMAL + MINIMAL)
    assert error.value.rule_id == "pip-cache"


def test_the_error_carries_the_rule_id_when_it_could_be_read():
    broken = (
        "[[rule]]\nid = 'docker-vhdx'\nname = 'n'\npaths = ['a']\ncategory = 'c'\n"
        "risk = 'rare'\nexplanation = 'e'\n"
    )
    with pytest.raises(RuleError) as error:
        load_rules_from_text(broken)
    assert error.value.rule_id == "docker-vhdx"


def test_load_rules_file_reads_a_real_file(tmp_path):
    path = tmp_path / "cache.toml"
    path.write_text(MINIMAL, encoding="utf-8")
    [rule] = load_rules_file(path)
    assert rule.id == "pip-cache"


def test_load_rules_file_reports_a_missing_file(tmp_path):
    with pytest.raises(RuleError):
        load_rules_file(tmp_path / "no_existe.toml")


def test_the_builtin_catalog_loads_the_real_table_from_the_plan():
    """Carga de verdad los archivos reales de `discoclaro/rules/` (T2.2)."""
    rules = load_builtin_catalog()
    assert len(rules) == 22
    ids = [rule.id for rule in rules]
    assert len(set(ids)) == len(ids)
    assert "docker-vhdx" in ids
    for rule in rules:
        assert rule.risk in RISK_LEVELS
        assert rule.category


def test_the_builtin_catalog_has_the_four_do_not_touch_rules_from_the_table():
    rules = load_builtin_catalog()
    do_not_touch = {rule.id for rule in rules if rule.risk == "do_not_touch"}
    assert do_not_touch == {
        "windows-backup",
        "hiberfil",
        "pagefile",
        "windows-installer",
    }


def test_the_builtin_catalog_is_grouped_into_twelve_categories():
    rules = load_builtin_catalog()
    categories = {rule.category for rule in rules}
    assert len(categories) == 12


def test_the_worked_example_from_the_plan_matches_the_builtin_catalog():
    """El ejemplo de docker-vhdx de la sección 5 del plan tiene que seguir igual."""
    [docker] = [rule for rule in load_builtin_catalog() if rule.id == "docker-vhdx"]
    assert docker.paths == ("%LOCALAPPDATA%\\Docker\\wsl\\disk\\docker_data.vhdx",)
    assert docker.category == "containers"
    assert docker.risk == "review"
    assert docker.task_id == "compact_docker_vhdx"
    assert docker.reacquire == Reacquire(
        kind="rebuild", cost="Docker vuelve a bajar o construir lo que se borre"
    )


def _hostile_rule(**action_overrides):
    action = {"type": "command", "text": "x"}
    action.update(action_overrides)
    lines = [
        "[[rule]]",
        "id = 'x'",
        "name = 'n'",
        "paths = ['%TEMP%']",
        "category = 'c'",
        "risk = 'safe'",
        "explanation = 'e'",
        "",
        "[rule.suggested_action]",
    ]
    lines += [f"{key} = {value!r}" for key, value in action.items()]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("char", ["&", "|", ">", "`", "$("])
def test_a_command_with_a_forbidden_shell_metacharacter_is_rejected(char):
    with pytest.raises(RuleError):
        load_rules_from_text(_hostile_rule(text=f"rm -rf / {char} evil.sh"))


def test_a_semicolon_in_a_command_is_allowed():
    """Puntuación normal del español (regla real `ollama-models`), no se rechaza."""
    [rule] = load_rules_from_text(_hostile_rule(text="a; b"))
    assert rule.suggested_action.text == "a; b"


def test_a_forbidden_character_outside_a_command_type_is_allowed():
    [rule] = load_rules_from_text(_hostile_rule(type="note_only", text="a > b"))
    assert rule.suggested_action.text == "a > b"


def test_a_forbidden_character_in_the_note_is_not_checked():
    """Solo se exige del texto del comando, no de la nota aparte."""
    [rule] = load_rules_from_text(_hostile_rule(note="ver a > b"))
    assert rule.suggested_action.note == "ver a > b"


def test_task_id_is_not_validated_against_a_catalog_yet():
    """El catálogo cerrado de tareas es de la fase 4 (nota de la sección 7): hasta
    entonces, cualquier texto no vacío se guarda tal cual, sin compararlo con nada."""
    text = (
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['a']\ncategory = 'c'\n"
        "risk = 'safe'\nexplanation = 'e'\ntask_id = 'not-a-real-task-yet'\n"
    )
    [rule] = load_rules_from_text(text)
    assert rule.task_id == "not-a-real-task-yet"


@pytest.mark.parametrize(
    "path",
    [
        "<volumen>",
        "C:",
        "<volumen>\\Windows",
        "C:\\Windows",
        "<volumen>\\Program Files",
        "<volumen>\\Program Files (x86)",
        "<volumen>\\ProgramData",
        "%USERPROFILE%",
        "%LOCALAPPDATA%\\discoclaro",
        "%APPDATA%\\discoclaro",
    ],
)
def test_a_protected_zone_marked_safe_is_rejected(path):
    broken = (
        "[[rule]]\nid = 'x'\nname = 'n'\n"
        f"paths = [{json.dumps(path)}]\n"
        "category = 'c'\nrisk = 'safe'\nexplanation = 'e'\n"
    )
    with pytest.raises(RuleError):
        load_rules_from_text(broken)


@pytest.mark.parametrize("risk", ["review", "do_not_touch"])
def test_a_protected_zone_with_another_risk_is_accepted(risk):
    text = (
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['<volumen>\\\\Windows']\n"
        f"category = 'c'\nrisk = '{risk}'\nexplanation = 'e'\n"
    )
    [rule] = load_rules_from_text(text)
    assert rule.risk == risk


def test_a_folder_inside_a_protected_zone_marked_safe_is_accepted():
    """Solo la zona misma está protegida, no todo lo que cuelga de ella."""
    text = (
        "[[rule]]\nid = 'x'\nname = 'n'\npaths = ['<volumen>\\\\Windows\\\\Temp']\n"
        "category = 'c'\nrisk = 'safe'\nexplanation = 'e'\n"
    )
    [rule] = load_rules_from_text(text)
    assert rule.risk == "safe"


@pytest.mark.parametrize(
    "rule_id", ["hiberfil", "pagefile", "windows-installer", "windows-backup"]
)
def test_a_forced_do_not_touch_id_is_corrected_even_if_the_data_says_safe(rule_id):
    text = (
        f"[[rule]]\nid = '{rule_id}'\nname = 'n'\npaths = ['a']\ncategory = 'c'\n"
        "risk = 'safe'\nexplanation = 'e'\n"
    )
    [rule] = load_rules_from_text(text)
    assert rule.risk == "do_not_touch"


@pytest.mark.parametrize("field", ["id", "name", "category", "explanation"])
def test_a_field_longer_than_the_limit_is_rejected(field):
    too_long = "x" * (MAX_FIELD_LENGTH + 1)
    values = {
        "id": "x",
        "name": "n",
        "category": "c",
        "risk": "safe",
        "explanation": "e",
    }
    values[field] = too_long
    text = (
        f"[[rule]]\nid = {values['id']!r}\nname = {values['name']!r}\n"
        f"paths = ['a']\ncategory = {values['category']!r}\n"
        f"risk = {values['risk']!r}\nexplanation = {values['explanation']!r}\n"
    )
    with pytest.raises(RuleError):
        load_rules_from_text(text)


def test_a_path_longer_than_the_limit_is_rejected():
    too_long = "a" * (MAX_FIELD_LENGTH + 1)
    text = (
        f"[[rule]]\nid = 'x'\nname = 'n'\npaths = [{too_long!r}]\ncategory = 'c'\n"
        "risk = 'safe'\nexplanation = 'e'\n"
    )
    with pytest.raises(RuleError):
        load_rules_from_text(text)


def test_a_field_exactly_at_the_limit_is_accepted():
    exact = "x" * MAX_FIELD_LENGTH
    text = (
        f"[[rule]]\nid = 'x'\nname = {exact!r}\npaths = ['a']\ncategory = 'c'\n"
        "risk = 'safe'\nexplanation = 'e'\n"
    )
    [rule] = load_rules_from_text(text)
    assert rule.name == exact


def test_the_length_limit_also_applies_to_the_suggested_action_text():
    """El mismo límite vale para cualquier texto, no solo los cuatro campos base."""
    too_long = "x" * (MAX_FIELD_LENGTH + 1)
    with pytest.raises(RuleError):
        load_rules_from_text(_hostile_rule(text=too_long))


def test_rules_from_text_are_verified_by_default():
    [rule] = load_rules_from_text(MINIMAL)
    assert rule.verified is True


def test_rules_from_text_can_be_marked_unverified():
    [rule] = load_rules_from_text(MINIMAL, verified=False)
    assert rule.verified is False


def test_load_rules_file_marks_its_rules_as_unverified_by_default(tmp_path):
    path = tmp_path / "mias.toml"
    path.write_text(MINIMAL, encoding="utf-8")
    [rule] = load_rules_file(path)
    assert rule.verified is False


def test_load_rules_file_can_be_marked_verified(tmp_path):
    path = tmp_path / "mias.toml"
    path.write_text(MINIMAL, encoding="utf-8")
    [rule] = load_rules_file(path, verified=True)
    assert rule.verified is True


def test_the_builtin_catalog_rules_are_all_verified():
    assert all(rule.verified for rule in load_builtin_catalog())
