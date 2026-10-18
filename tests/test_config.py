"""Pruebas del lector de `config.toml` (T2.6): siempre sobre texto o `tmp_path`.

Nunca toca `%APPDATA%` real salvo en las dos pruebas marcadas que lo inyectan.
"""

import pytest

from discoclaro.adapters.config import (
    ConfigError,
    load_config_file,
    load_config_from_text,
    load_user_config,
)
from discoclaro.domain.config import Budget, Config, GrowthLimits, VolumeLimit


def test_an_empty_file_is_all_defaults():
    assert load_config_from_text("") == Config()


def test_invalid_toml_is_reported_with_the_source():
    with pytest.raises(ConfigError, match="origen.toml"):
        load_config_from_text("not = [valid", source="origen.toml")


def test_a_volume_table_is_read():
    config = load_config_from_text(
        '[volumes."C:"]\nmin_free_gb = 40\nwarn_at_percent = 85\n'
    )
    assert config.volumes == {"C:": VolumeLimit(min_free_gb=40, warn_at_percent=85)}


def test_a_volume_without_a_percent_uses_the_default():
    config = load_config_from_text('[volumes."C:"]\nmin_free_gb = 40\n')
    assert config.volumes["C:"].warn_at_percent == 85.0


@pytest.mark.parametrize("key", ["C", "C:\\Users", "1:", ""])
def test_a_volume_key_that_does_not_look_like_a_drive_is_rejected(key):
    with pytest.raises(ConfigError):
        load_config_from_text(f'[volumes."{key}"]\nmin_free_gb = 1\n')


def test_a_volume_key_with_a_trailing_backslash_is_normalized():
    config = load_config_from_text('[volumes."C:\\\\"]\nmin_free_gb = 1\n')
    assert set(config.volumes) == {"C:"}


def test_a_volume_key_is_normalized_to_uppercase():
    config = load_config_from_text('[volumes."c:"]\nmin_free_gb = 1\n')
    assert set(config.volumes) == {"C:"}


@pytest.mark.parametrize("percent", [-1, 101])
def test_a_percent_outside_0_to_100_is_rejected(percent):
    with pytest.raises(ConfigError):
        load_config_from_text(f'[volumes."C:"]\nwarn_at_percent = {percent}\n')


def test_a_negative_minimum_free_space_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text('[volumes."C:"]\nmin_free_gb = -1\n')


def test_budgets_are_read_by_name():
    config = load_config_from_text(
        '[budgets]\n"ai-models" = { max_gb = 30 }\n"Downloads" = { max_gb = 10 }\n'
    )
    assert config.budgets == {
        "ai-models": Budget(max_gb=30),
        "Downloads": Budget(max_gb=10),
    }


def test_a_budget_without_a_maximum_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text('[budgets]\n"ai-models" = {}\n')


def test_growth_limits_are_read():
    config = load_config_from_text("[growth]\nmax_growth_gb = 5\nnew_file_gb = 2\n")
    assert config.growth == GrowthLimits(max_growth_gb=5, new_file_gb=2)


def test_min_display_gb_is_read_from_the_root():
    assert load_config_from_text("min_display_gb = 2.5").min_display_gb == 2.5


def test_a_negative_min_display_gb_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text("min_display_gb = -1")


def test_retention_days_is_read_from_the_root():
    assert load_config_from_text("retention_days = 7").retention_days == 7


def test_a_negative_retention_days_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text("retention_days = -1")


def test_retention_days_zero_is_accepted():
    assert load_config_from_text("retention_days = 0").retention_days == 0


def test_a_fractional_retention_days_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text("retention_days = 7.5")


def test_unknown_keys_are_ignored_for_forward_compatibility():
    config = load_config_from_text('something_new = "x"\n[volumes."C:"]\nmore = 1\n')
    assert config.volumes == {"C:": VolumeLimit()}


def test_every_alert_is_enabled_without_an_alerts_section():
    assert load_config_from_text("").disabled_alerts == frozenset()


def test_an_alert_set_to_false_is_disabled():
    config = load_config_from_text("[alerts]\nbig_new_file = false\n")
    assert config.disabled_alerts == frozenset({"big_new_file"})


def test_an_alert_set_to_true_stays_enabled():
    config = load_config_from_text("[alerts]\nbig_new_file = true\n")
    assert config.disabled_alerts == frozenset()


def test_an_unknown_alert_kind_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text("[alerts]\nnot_a_real_kind = false\n")


def test_an_alert_value_that_is_not_a_boolean_is_rejected():
    with pytest.raises(ConfigError):
        load_config_from_text("[alerts]\nbig_new_file = 1\n")


@pytest.mark.parametrize(
    "text",
    [
        "volumes = 1",
        "budgets = 1",
        "growth = 1",
        "alerts = 1",
        '[volumes]\n"C:" = 1',
        '[budgets]\n"x" = 1',
    ],
)
def test_a_section_that_is_not_a_table_is_rejected(text):
    with pytest.raises(ConfigError):
        load_config_from_text(text)


def test_load_config_file_reads_a_real_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[volumes."C:"]\nmin_free_gb = 40\n', encoding="utf-8")
    config = load_config_file(path)
    assert config.volumes["C:"].min_free_gb == 40


def test_load_config_file_reports_a_missing_file():
    with pytest.raises(ConfigError):
        load_config_file("C:\\does\\not\\exist\\config.toml")


def test_load_user_config_without_a_file_uses_the_defaults(tmp_path):
    assert load_user_config(tmp_path) == Config()


def test_load_user_config_reads_the_file_in_the_given_folder(tmp_path):
    (tmp_path / "config.toml").write_text("min_display_gb = 3\n", encoding="utf-8")
    assert load_user_config(tmp_path).min_display_gb == 3


def test_load_user_config_uses_the_default_folder_when_none_is_given(
    tmp_path, monkeypatch
):
    from discoclaro.platform import windows

    monkeypatch.setattr(windows, "default_config_dir", lambda: tmp_path)
    (tmp_path / "config.toml").write_text("min_display_gb = 7\n", encoding="utf-8")
    assert load_user_config().min_display_gb == 7
