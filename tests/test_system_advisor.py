"""Pruebas del asesor de archivos del sistema (T2C.6): puras."""

import pytest

from discoclaro.domain.system_advisor import PageFileAdvice, RestoreAdvice


def test_a_page_file_advice_needs_a_path():
    with pytest.raises(ValueError):
        PageFileAdvice(path="", configured_bytes=None, peak_used_bytes=None)


def test_a_page_file_advice_rejects_a_negative_configured_size():
    with pytest.raises(ValueError):
        PageFileAdvice(
            path="C:\\pagefile.sys", configured_bytes=-1, peak_used_bytes=None
        )


def test_a_page_file_advice_rejects_a_negative_peak():
    with pytest.raises(ValueError):
        PageFileAdvice(
            path="C:\\pagefile.sys", configured_bytes=None, peak_used_bytes=-1
        )


def test_a_page_file_advice_allows_none_for_system_managed():
    advice = PageFileAdvice(
        path="C:\\pagefile.sys", configured_bytes=None, peak_used_bytes=100
    )
    assert advice.configured_bytes is None


def test_a_restore_advice_needs_a_drive():
    with pytest.raises(ValueError):
        RestoreAdvice(drive="", configured_bytes=0, used_bytes=None)


def test_a_restore_advice_rejects_a_negative_configured_size():
    with pytest.raises(ValueError):
        RestoreAdvice(drive="C:", configured_bytes=-1, used_bytes=None)


def test_a_restore_advice_rejects_a_negative_used_size():
    with pytest.raises(ValueError):
        RestoreAdvice(drive="C:", configured_bytes=0, used_bytes=-1)


def test_a_restore_advice_allows_none_used_for_no_restore_points():
    advice = RestoreAdvice(drive="C:", configured_bytes=100, used_bytes=None)
    assert advice.used_bytes is None
