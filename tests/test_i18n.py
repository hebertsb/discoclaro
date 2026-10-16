"""Pruebas del catálogo de textos."""

import pytest

from discoclaro import i18n
from discoclaro.i18n import t


@pytest.fixture(autouse=True)
def restore_language():
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


@pytest.fixture
def fake_catalogs(monkeypatch):
    catalogs = {
        "es": {
            "greeting": "Hola {name}",
            "pair": "{a} y {b}",
            "only_es": "solo en español",
        },
        "en": {"greeting": "Hello {name}", "pair": "{a} and {b}"},
    }
    monkeypatch.setattr(i18n, "_load_catalog", lambda language: catalogs[language])
    return catalogs


def test_default_language_is_spanish_and_reads_the_real_catalog():
    assert i18n.get_language() == "es"
    assert t("cli.description") != "cli.description"


def test_set_language_switches_the_catalog():
    spanish = t("cli.description")
    i18n.set_language("en")
    assert i18n.get_language() == "en"
    assert t("cli.description") not in (spanish, "cli.description")


@pytest.mark.parametrize("language", ["fr", "", "../es", "ES"])
def test_set_language_rejects_unsupported_languages(language):
    with pytest.raises(ValueError):
        i18n.set_language(language)
    assert i18n.get_language() == "es"


@pytest.mark.parametrize("language", i18n.SUPPORTED_LANGUAGES)
def test_real_catalogs_have_the_same_keys_as_the_default(language):
    default = i18n._load_catalog(i18n.DEFAULT_LANGUAGE)
    assert i18n._load_catalog(language).keys() == default.keys()


@pytest.mark.parametrize("language", i18n.SUPPORTED_LANGUAGES)
def test_real_catalogs_use_the_same_placeholders_as_the_default(language):
    default = i18n._load_catalog(i18n.DEFAULT_LANGUAGE)
    catalog = i18n._load_catalog(language)
    for key, text in default.items():
        found = set(i18n._PLACEHOLDER.findall(catalog[key]))
        assert found == set(i18n._PLACEHOLDER.findall(text)), key


def test_placeholders_are_replaced(fake_catalogs):
    assert t("greeting", name="Ana") == "Hola Ana"


def test_missing_params_leave_the_placeholder(fake_catalogs):
    assert t("greeting") == "Hola {name}"


def test_missing_key_in_current_language_falls_back_to_spanish(fake_catalogs):
    i18n.set_language("en")
    assert t("only_es") == "solo en español"


def test_unknown_key_returns_the_key(fake_catalogs):
    assert t("no.such.key") == "no.such.key"


def test_param_values_are_inserted_without_being_reinterpreted(fake_catalogs):
    assert t("pair", a="{b}", b="x") == "{b} y x"
    assert t("greeting", name="{0.__class__}") == "Hola {0.__class__}"
    assert t("greeting", name="%s \n") == "Hola %s \n"


def test_attribute_access_in_templates_is_not_evaluated(monkeypatch):
    monkeypatch.setattr(
        i18n, "_load_catalog", lambda language: {"evil": "{name.__class__}"}
    )
    assert t("evil", name="x") == "{name.__class__}"


def test_flatten_turns_nested_tables_into_dotted_keys():
    nested = {"a": {"b": "x", "c": {"d": "y"}}, "e": "z"}
    assert i18n._flatten(nested) == {"a.b": "x", "a.c.d": "y", "e": "z"}


def test_flatten_rejects_values_that_are_not_text():
    with pytest.raises(TypeError):
        i18n._flatten({"a": {"b": 1}})
