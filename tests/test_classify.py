"""Pruebas de la asignación de reglas: sin disco, sin variables de entorno reales."""

import pytest

from discoclaro.domain.classify import classify
from discoclaro.domain.rules import Rule


def rule(id, *paths, category="cache", risk="safe"):
    return Rule(
        id=id,
        name=id,
        paths=paths,
        category=category,
        risk=risk,
        explanation="prueba",
    )


ENV = {
    "%USERPROFILE%": "C:\\Users\\x",
    "%LOCALAPPDATA%": "C:\\Users\\x\\AppData\\Local",
    "%APPDATA%": "C:\\Users\\x\\AppData\\Roaming",
    "%TEMP%": "C:\\Users\\x\\AppData\\Local\\Temp",
}


def test_a_literal_path_matches_exactly():
    r = rule("x", "C:\\a\\b")
    assert classify("C:\\a\\b", [r], {}) is r


def test_matching_is_not_case_sensitive():
    r = rule("x", "C:\\A\\B")
    assert classify("c:\\a\\b", [r], {}) is r


def test_an_unrelated_path_does_not_match():
    r = rule("x", "C:\\a\\b")
    assert classify("C:\\c\\d", [r], {}) is None


def test_no_rules_at_all_gives_none():
    assert classify("C:\\a", [], {}) is None


def test_environment_variables_are_expanded_before_matching():
    r = rule("pip-cache", "%LOCALAPPDATA%\\pip\\Cache")
    assert classify("C:\\Users\\x\\AppData\\Local\\pip\\Cache", [r], ENV) is r


def test_a_missing_environment_variable_never_matches():
    r = rule("x", "%LOCALAPPDATA%\\pip\\Cache")
    assert classify("C:\\Users\\x\\AppData\\Local\\pip\\Cache", [r], {}) is None


def test_a_single_star_matches_within_one_segment_only():
    r = rule("x", "C:\\a\\*\\c")
    assert classify("C:\\a\\b\\c", [r], {}) is r
    assert classify("C:\\a\\b\\d\\c", [r], {}) is None


def test_a_double_star_matches_zero_segments():
    r = rule("x", "C:\\a\\**\\c")
    assert classify("C:\\a\\c", [r], {}) is r


def test_a_double_star_matches_one_segment():
    r = rule("x", "C:\\a\\**\\c")
    assert classify("C:\\a\\b\\c", [r], {}) is r


def test_a_double_star_matches_many_segments():
    r = rule("x", "C:\\a\\**\\c")
    assert classify("C:\\a\\b1\\b2\\b3\\c", [r], {}) is r


def test_a_leading_double_star_matches_anywhere():
    r = rule("node-modules", "**\\node_modules")
    assert classify("C:\\projects\\x\\node_modules", [r], {}) is r
    assert classify("C:\\node_modules", [r], {}) is r


def test_angle_bracket_placeholders_act_like_a_single_segment_wildcard():
    r = rule("recycle-bin", "<volumen>\\$Recycle.Bin")
    assert classify("D:\\$Recycle.Bin", [r], {}) is r
    assert classify("C:\\a\\$Recycle.Bin", [r], {}) is None


def test_a_rule_with_several_paths_matches_if_any_one_of_them_does():
    r = rule("installers", "C:\\a\\*.exe", "C:\\a\\*.msi")
    assert classify("C:\\a\\setup.msi", [r], {}) is r


@pytest.mark.parametrize(
    ("winner_paths", "loser_paths"),
    [
        (("C:\\a\\b\\c",), ("C:\\a\\**",)),
        (("C:\\a\\b\\c",), ("**\\c",)),
        (("C:\\a\\b\\c",), ("C:\\a\\*\\c",)),
    ],
)
def test_the_rule_with_more_literal_segments_wins(winner_paths, loser_paths):
    winner = rule("winner", *winner_paths)
    loser = rule("loser", *loser_paths)
    assert classify("C:\\a\\b\\c", [loser, winner], {}) is winner
    assert classify("C:\\a\\b\\c", [winner, loser], {}) is winner


def test_with_the_same_literal_segments_the_longer_expanded_pattern_wins():
    short = rule("short", "C:\\ab\\c")
    long_ = rule("long", "C:\\abcdef\\c")
    assert classify("C:\\abcdef\\c", [short, long_], {}) is long_


def test_a_true_tie_is_won_by_whichever_comes_first():
    first = rule("first", "C:\\a\\b")
    second = rule("second", "C:\\a\\b")
    assert classify("C:\\a\\b", [first, second], {}) is first
    assert classify("C:\\a\\b", [second, first], {}) is second


def test_only_the_matching_rules_are_compared_for_specificity():
    unrelated = rule("unrelated", "D:\\z\\very\\long\\path\\that\\never\\matches")
    winner = rule("winner", "C:\\a")
    assert classify("C:\\a", [unrelated, winner], {}) is winner


def test_the_real_catalog_classifies_realistic_expanded_paths():
    """De punta a punta con T2.1-T2.3: el catálogo real, no uno de prueba."""
    from discoclaro.adapters.rules import load_builtin_catalog

    rules = load_builtin_catalog()
    env = {
        "%USERPROFILE%": "C:\\Users\\x",
        "%LOCALAPPDATA%": "C:\\Users\\x\\AppData\\Local",
        "%APPDATA%": "C:\\Users\\x\\AppData\\Roaming",
        "%TEMP%": "C:\\Users\\x\\AppData\\Local\\Temp",
    }
    cases = {
        "C:\\Users\\x\\AppData\\Local\\pip\\Cache": "pip-cache",
        "C:\\Users\\x\\AppData\\Local\\Docker\\wsl\\disk\\docker_data.vhdx": (
            "docker-vhdx"
        ),
        "C:\\projects\\discoclaro\\node_modules": "node-modules",
        "C:\\hiberfil.sys": "hiberfil",
        "C:\\Windows\\Installer": "windows-installer",
    }
    for path, expected_id in cases.items():
        found = classify(path, rules, env)
        assert found is not None, path
        assert found.id == expected_id, path
    assert classify("C:\\Users\\x\\Documents\\tesis.docx", rules, env) is None
