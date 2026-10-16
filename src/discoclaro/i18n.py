"""Catálogo de textos para el usuario.

Todo texto que ve el usuario sale de `locales/<idioma>.toml` y no se escribe en
el código. Las claves van en inglés y con puntos (`cli.commands.scan`); los
valores son texto plano con marcadores `{nombre}`.

Quien muestre el resultado debe escaparlo según el destino (terminal, HTML,
XML), sobre todo si le pasó datos externos como rutas o nombres de archivo.
"""

import re
import tomllib
from functools import cache
from importlib.resources import files

DEFAULT_LANGUAGE = "es"
SUPPORTED_LANGUAGES = ("es", "en")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_current_language = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    """Elige el idioma de `t()`; solo acepta los de SUPPORTED_LANGUAGES."""
    global _current_language
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language!r}")
    _current_language = language


def get_language() -> str:
    """Devuelve el idioma que usa `t()` ahora."""
    return _current_language


def t(key: str, **params: object) -> str:
    """Devuelve el texto de `key` en el idioma actual.

    Si la clave falta en ese idioma usa el español; si falta en ambos devuelve
    la propia clave, para que el error se vea sin romper el programa. Los
    `{nombre}` del texto se reemplazan con `params`, y los valores se insertan
    tal cual, sin interpretarlos.
    """
    template = _load_catalog(_current_language).get(key)
    if template is None:
        template = _load_catalog(DEFAULT_LANGUAGE).get(key, key)
    return _substitute(template, params)


def _substitute(template: str, params: dict[str, object]) -> str:
    """Reemplaza los `{nombre}` que tengan valor; los demás quedan como están."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(params[name]) if name in params else match.group(0)

    return _PLACEHOLDER.sub(replace, template)


def _flatten(table: dict[str, object], prefix: str = "") -> dict[str, str]:
    """Aplana las tablas anidadas de un TOML a claves con puntos."""
    flat: dict[str, str] = {}
    for name, value in table.items():
        key = f"{prefix}{name}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{key}."))
        elif isinstance(value, str):
            flat[key] = value
        else:
            raise TypeError(f"catalog value must be text: {key}")
    return flat


@cache
def _load_catalog(language: str) -> dict[str, str]:
    """Lee y aplana `locales/<language>.toml`; `language` ya viene validado."""
    path = files("discoclaro").joinpath("locales", f"{language}.toml")
    return _flatten(tomllib.loads(path.read_text(encoding="utf-8")))
