"""Pruebas del formato de números y texto."""

import pytest

from discoclaro.domain.formatting import (
    format_bytes,
    format_duration,
    format_int,
    safe_text,
)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1024**2, "1.0 MiB"),
        (6_749_417_472, "6.3 GiB"),
        (51_539_607_552, "48.0 GiB"),
        (1024**4, "1.0 TiB"),
        (1024**5, "1.0 PiB"),
        (1024**6, "1024.0 PiB"),
    ],
)
def test_format_bytes(size, expected):
    assert format_bytes(size) == expected


def test_format_bytes_does_not_show_1024_of_a_unit():
    assert format_bytes(1024**2 - 52) == "1023.9 KiB"
    assert format_bytes(1024**2 - 51) == "1.0 MiB"
    assert format_bytes(1024**2 - 1) == "1.0 MiB"
    assert format_bytes(1024**2 - 1024) == "1023.0 KiB"


def test_format_bytes_uses_the_decimal_separator_it_is_given():
    assert format_bytes(1536, ",") == "1,5 KiB"
    assert format_bytes(500) == "500 B"


def test_format_bytes_rejects_negative_sizes():
    with pytest.raises(ValueError):
        format_bytes(-1)


@pytest.mark.parametrize(
    ("value", "separator", "expected"),
    [
        (0, ",", "0"),
        (999, ",", "999"),
        (1000, ",", "1,000"),
        (1_203_998, ",", "1,203,998"),
        (1_203_998, ".", "1.203.998"),
        (1_203_998, " ", "1 203 998"),
        (-1234, ".", "-1.234"),
    ],
)
def test_format_int(value, separator, expected):
    assert format_int(value, separator) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 ms"),
        (0.0314, "31 ms"),
        (0.9994, "999 ms"),
        (0.9996, "1.0 s"),
        (1, "1.0 s"),
        (45.2, "45.2 s"),
        (59.94, "59.9 s"),
        (59.96, "1 min 00 s"),
        (60, "1 min 00 s"),
        (192, "3 min 12 s"),
        (3599.4, "59 min 59 s"),
        (3599.6, "1 h 00 min"),
        (3900, "1 h 05 min"),
        (90000, "25 h 00 min"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_format_duration_decimal_separator_and_negatives():
    assert format_duration(45.2, ",") == "45,2 s"
    with pytest.raises(ValueError):
        format_duration(-0.1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("D:\\carpeta normal", "D:\\carpeta normal"),
        ("ñandú 日本語 📁", "ñandú 日本語 📁"),
        ("a\x1b[31mred\x1b[0m", "a?[31mred?[0m"),
        ("line\nbreak\rhere\tx", "line?break?here?x"),
        ("rtl\u202eoverride", "rtl?override"),
        ("zero\u200bwidth", "zero?width"),
        ("sep\u2028arator\u2029s", "sep?arator?s"),
        ("lone\ud800surrogate", "lone?surrogate"),
        ("private\ue000use", "private?use"),
        ("nul\x00char", "nul?char"),
        ("", ""),
    ],
)
def test_safe_text(text, expected):
    assert safe_text(text) == expected
