"""Pruebas del modelo puro de configuración (T2.6): sin TOML ni disco."""

from discoclaro.domain.config import (
    DEFAULT_MIN_DISPLAY_GB,
    DEFAULT_WARN_AT_PERCENT,
    GIB,
    Budget,
    Config,
    GrowthLimits,
    VolumeLimit,
)


def test_a_default_config_has_no_volumes_nor_budgets():
    config = Config()
    assert config.volumes == {}
    assert config.budgets == {}


def test_a_default_config_has_no_growth_limits():
    config = Config()
    assert config.growth == GrowthLimits(max_growth_gb=None, new_file_gb=None)


def test_a_default_config_uses_the_default_noise_floor():
    assert Config().min_display_gb == DEFAULT_MIN_DISPLAY_GB


def test_min_display_bytes_converts_gb_to_bytes():
    assert Config(min_display_gb=2.5).min_display_bytes() == round(2.5 * GIB)


def test_min_display_bytes_rounds_to_the_nearest_byte():
    assert Config(min_display_gb=0.0000000001).min_display_bytes() == 0


def test_a_volume_limit_without_a_percent_uses_the_default():
    assert VolumeLimit().warn_at_percent == DEFAULT_WARN_AT_PERCENT


def test_a_volume_limit_can_have_no_minimum_free_space():
    assert VolumeLimit().min_free_gb is None


def test_a_budget_only_has_a_maximum():
    assert Budget(max_gb=30).max_gb == 30


def test_two_configs_with_the_same_values_are_equal():
    first = Config(volumes={"C:": VolumeLimit(min_free_gb=40)})
    second = Config(volumes={"C:": VolumeLimit(min_free_gb=40)})
    assert first == second
