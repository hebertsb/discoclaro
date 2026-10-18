"""Pruebas del saneado de texto externo (T2.12): con nombres hostiles."""

import pytest

from discoclaro.domain.policy import (
    MAX_DISPLAY_LENGTH,
    escape_html,
    escape_xml,
    limit_length,
)


def test_limit_length_cuts_to_the_given_length():
    assert limit_length("abcdef", 3) == "abc"


def test_limit_length_keeps_a_shorter_text_as_is():
    assert limit_length("abc", 10) == "abc"


def test_limit_length_uses_the_default_when_not_given():
    too_long = "x" * (MAX_DISPLAY_LENGTH + 10)
    assert limit_length(too_long) == "x" * MAX_DISPLAY_LENGTH


def test_limit_length_rejects_a_negative_maximum():
    with pytest.raises(ValueError):
        limit_length("abc", -1)


@pytest.mark.parametrize("escape", [escape_html, escape_xml])
class TestBothEscapers:
    def test_a_script_tag_is_neutralized(self, escape):
        result = escape("<script>alert(1)</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_an_ampersand_is_escaped_once_not_twice(self, escape):
        assert escape("a & b") == "a &amp; b"

    def test_already_escaped_text_is_not_double_escaped(self, escape):
        # Si el "&" de "&lt;" se escapara después de haber escapado "<", se vería
        # "&amp;lt;" en vez de "&lt;": el orden importa, y esto lo comprueba.
        assert escape("<") == "&lt;"

    def test_a_percent_s_passes_through_unchanged(self, escape):
        """No es HTML/XML especial; que quede igual confirma que nada de esto usa
        formateo con % por dentro (eso sí sería un problema aparte)."""
        assert escape("%s") == "%s"

    def test_a_terminal_escape_sequence_is_neutralized(self, escape):
        assert "\x1b" not in escape("a\x1b[31mred\x1b[0m")

    def test_a_newline_cannot_fake_a_log_line(self, escape):
        result = escape("primera línea\nsegunda línea falsa")
        assert "\n" not in result

    def test_a_double_quote_is_escaped(self, escape):
        assert "&quot;" in escape('he said "hi"')

    def test_plain_text_is_unchanged(self, escape):
        assert escape("carpeta normal") == "carpeta normal"


def test_escape_html_uses_a_numeric_entity_for_a_single_quote():
    assert escape_html("it's") == "it&#39;s"


def test_escape_xml_uses_the_named_entity_for_a_single_quote():
    assert escape_xml("it's") == "it&apos;s"


def test_the_safe_order_is_limit_then_escape_not_the_reverse():
    """Escapar primero y cortar después puede partir una entidad a la mitad."""
    wrong_order = limit_length(escape_html("<"), 3)
    right_order = escape_html(limit_length("<", 3))
    assert wrong_order == "&lt"
    assert right_order == "&lt;"
