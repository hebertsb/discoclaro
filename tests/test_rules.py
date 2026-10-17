"""Pruebas del cargador de reglas: siempre texto o archivos en `tmp_path`."""

import pytest

from discoclaro.adapters.rules import RuleError, load_rules_file, load_rules_from_text
from discoclaro.domain.rules import Reacquire, SuggestedAction

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
