"""Lee `config.toml` del usuario (T2.6): los límites son datos, nunca código.

Solo arma un `Config` ya validado y nunca interpreta ni ejecuta nada de lo que lee. Si
el archivo no existe todavía, no es un error: el usuario no fijó ningún límite, así que
se usan los valores por defecto (`Config()`).
"""

import re
import tomllib
from pathlib import Path

from discoclaro.domain.alerts import ALERT_KINDS
from discoclaro.domain.config import (
    DEFAULT_MIN_DISPLAY_GB,
    DEFAULT_WARN_AT_PERCENT,
    MAX_PERCENT,
    Budget,
    Config,
    GrowthLimits,
    VolumeLimit,
)
from discoclaro.domain.retention import DEFAULT_RETENTION_DAYS
from discoclaro.platform import windows

CONFIG_FILE_NAME = "config.toml"
_VOLUME_KEY = re.compile(r"^[A-Za-z]:$")


class ConfigError(Exception):
    """El archivo de configuración no tiene el formato esperado."""


def load_config_from_text(text: str, source: str = "<texto>") -> Config:
    """Lee un `config.toml` ya en memoria.

    Lanza `ConfigError`, con el origen, si el TOML no es válido o algún valor no
    cumple el formato esperado (una tabla donde no toca, un número fuera de rango).
    Las claves que no se reconocen se ignoran, para no romper con un archivo de una
    versión futura.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{source}: invalid TOML ({error})") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: the file must be a table")
    try:
        min_display_gb = _optional_number(data, "min_display_gb", "<root>")
        retention_days = _optional_int(data, "retention_days", "<root>")
        return Config(
            volumes=_build_volumes(data.get("volumes", {})),
            budgets=_build_budgets(data.get("budgets", {})),
            growth=_build_growth(data.get("growth", {})),
            min_display_gb=(
                DEFAULT_MIN_DISPLAY_GB if min_display_gb is None else min_display_gb
            ),
            disabled_alerts=_build_disabled_alerts(data.get("alerts", {})),
            retention_days=(
                DEFAULT_RETENTION_DAYS if retention_days is None else retention_days
            ),
            project_folders=_build_project_folders(data.get("project_folders", [])),
        )
    except ValueError as error:
        raise ConfigError(f"{source}: {error}") from error


def load_config_file(path: str | Path) -> Config:
    """Lee la configuración de un archivo. Lanza `ConfigError` si no se puede leer."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(
            f"{path}: could not read it ({type(error).__name__})"
        ) from error
    return load_config_from_text(text, source=str(path))


def load_user_config(config_dir: str | Path | None = None) -> Config:
    """La configuración del usuario, o los valores por defecto si no fijó ninguno.

    Busca `config.toml` en `config_dir` (por defecto, `%APPDATA%\\discoclaro`). Si la
    carpeta o el archivo todavía no existen, no es un error: se devuelve `Config()`.
    """
    directory = windows.default_config_dir() if config_dir is None else Path(config_dir)
    path = directory / CONFIG_FILE_NAME
    if not path.is_file():
        return Config()
    return load_config_file(path)


def _build_volumes(raw: object) -> dict[str, VolumeLimit]:
    if not isinstance(raw, dict):
        raise ValueError("'volumes' must be a table")
    volumes: dict[str, VolumeLimit] = {}
    for key, value in raw.items():
        name = _normalize_volume_key(key)
        path = f"volumes.{key}"
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be a table")
        warn_at_percent = _optional_number(
            value, "warn_at_percent", path, maximum=MAX_PERCENT
        )
        volumes[name] = VolumeLimit(
            min_free_gb=_optional_number(value, "min_free_gb", path),
            warn_at_percent=(
                DEFAULT_WARN_AT_PERCENT if warn_at_percent is None else warn_at_percent
            ),
        )
    return volumes


def _normalize_volume_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError(f"volumes key must be a string: {key!r}")
    normalized = key.rstrip("\\/")
    if not _VOLUME_KEY.fullmatch(normalized):
        raise ValueError(f"volumes key must look like 'C:': {key!r}")
    return normalized.upper()


def _build_budgets(raw: object) -> dict[str, Budget]:
    if not isinstance(raw, dict):
        raise ValueError("'budgets' must be a table")
    budgets: dict[str, Budget] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"budgets key must be a non-empty string: {key!r}")
        path = f"budgets.{key}"
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be a table")
        max_gb = _optional_number(value, "max_gb", path)
        if max_gb is None:
            raise ValueError(f"{path}.max_gb is required")
        budgets[key] = Budget(max_gb=max_gb)
    return budgets


def _build_growth(raw: object) -> GrowthLimits:
    if not isinstance(raw, dict):
        raise ValueError("'growth' must be a table")
    return GrowthLimits(
        max_growth_gb=_optional_number(raw, "max_growth_gb", "growth"),
        new_file_gb=_optional_number(raw, "new_file_gb", "growth"),
    )


def _build_project_folders(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError("'project_folders' must be a list")
    folders = []
    for index, value in enumerate(raw):
        if not isinstance(value, str) or not value:
            raise ValueError(f"project_folders[{index}] must be a non-empty string")
        folders.append(value)
    return tuple(folders)


def _build_disabled_alerts(raw: object) -> frozenset[str]:
    if not isinstance(raw, dict):
        raise ValueError("'alerts' must be a table")
    disabled = set()
    for kind, enabled in raw.items():
        if kind not in ALERT_KINDS:
            raise ValueError(f"alerts.{kind!r} is not a known alert kind")
        if not isinstance(enabled, bool):
            raise ValueError(f"alerts.{kind} must be true or false")
        if not enabled:
            disabled.add(kind)
    return frozenset(disabled)


def _optional_int(
    raw: dict[str, object], field: str, path: str, *, minimum: int = 0
) -> int | None:
    if field not in raw:
        return None
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path}.{field} must be a whole number")
    if value < minimum:
        raise ValueError(f"{path}.{field} must be at least {minimum}")
    return value


def _optional_number(
    raw: dict[str, object],
    field: str,
    path: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float | None:
    if field not in raw:
        return None
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{path}.{field} must be a number")
    value = float(value)
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"at least {minimum}"
        if maximum is not None:
            bound = f"between {minimum} and {maximum}"
        raise ValueError(f"{path}.{field} must be {bound}")
    return value


def _toml_key(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dump_config_to_toml(config: Config) -> str:
    """Serializa `Config` a un texto TOML limpio y válido."""
    lines: list[str] = [
        f"min_display_gb = {config.min_display_gb}",
        f"retention_days = {config.retention_days}",
    ]
    if config.project_folders:
        items = ", ".join(_toml_key(f) for f in config.project_folders)
        lines.append(f"project_folders = [{items}]")

    if config.growth.max_growth_gb is not None or config.growth.new_file_gb is not None:
        lines.append("")
        lines.append("[growth]")
        if config.growth.max_growth_gb is not None:
            lines.append(f"max_growth_gb = {config.growth.max_growth_gb}")
        if config.growth.new_file_gb is not None:
            lines.append(f"new_file_gb = {config.growth.new_file_gb}")

    if config.volumes:
        lines.append("")
        lines.append("[volumes]")
        for vol_name, limit in sorted(config.volumes.items()):
            fields = [f"warn_at_percent = {limit.warn_at_percent}"]
            if limit.min_free_gb is not None:
                fields.append(f"min_free_gb = {limit.min_free_gb}")
            lines.append(f"{_toml_key(vol_name)} = {{ {', '.join(fields)} }}")

    if config.budgets:
        lines.append("")
        lines.append("[budgets]")
        for target, budget in sorted(config.budgets.items()):
            lines.append(f"{_toml_key(target)} = {{ max_gb = {budget.max_gb} }}")

    if config.disabled_alerts:
        lines.append("")
        lines.append("[alerts]")
        for kind in sorted(config.disabled_alerts):
            lines.append(f"{kind} = false")

    return "\n".join(lines) + "\n"


def save_user_config(config: Config, config_dir: str | Path | None = None) -> Path:
    """Guarda `config` en `%APPDATA%\\discoclaro\\config.toml`, revalidando antes
    de escribir que el TOML generado sea válido y que la ruta sea segura."""
    directory = windows.default_config_dir() if config_dir is None else Path(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    windows.ensure_safe_to_write(directory)
    path = directory / CONFIG_FILE_NAME
    windows.ensure_safe_to_write(path)
    toml_text = dump_config_to_toml(config)
    load_config_from_text(toml_text, source=str(path))
    path.write_text(toml_text, encoding="utf-8")
    return path

