"""Asigna la regla más específica del catálogo a una carpeta (T2.3).

Pura: no lee el disco ni las variables de entorno reales. `environment` da el valor
de cada variable (`%USERPROFILE%`, `%LOCALAPPDATA%`, `%APPDATA%`, `%TEMP%`) que
puedan traer los patrones de las reglas; quien llama la arma de verdad
(`platform/windows.py::environment_variables`). Los marcadores entre `<` y `>`
(`<volumen>`, `<app>`...) de la tabla del catálogo se tratan como `*`: un solo
elemento de la ruta, cualquiera sea.
"""

import fnmatch
import re
from collections.abc import Mapping, Sequence

from discoclaro.domain.rules import Rule

ENVIRONMENT_VARIABLES = ("%USERPROFILE%", "%LOCALAPPDATA%", "%APPDATA%", "%TEMP%")
_PLACEHOLDER = re.compile(r"<[^>]+>")
_SEPARATORS = re.compile(r"[\\/]+")

# Lo que vale cada coincidencia, para que gane la más específica: primero cuántos
# elementos de la ruta son literales (sin comodín), y a igualdad, el patrón expandido
# más largo. Entre dos igual de específicas, gana la que aparece antes en `rules`.
_Score = tuple[int, int]


def classify(
    path: str, rules: Sequence[Rule], environment: Mapping[str, str]
) -> Rule | None:
    """La regla más específica de `rules` cuyo patrón coincide con `path`.

    `None` si ninguna coincide. No sigue enlaces ni toca el disco: compara texto
    contra texto.
    """
    best: Rule | None = None
    best_score: _Score | None = None
    for rule in rules:
        score = _best_score(rule, path, environment)
        if score is not None and (best_score is None or score > best_score):
            best, best_score = rule, score
    return best


def _best_score(rule: Rule, path: str, environment: Mapping[str, str]) -> _Score | None:
    scores = [
        score
        for pattern in rule.paths
        if (score := _match(pattern, path, environment)) is not None
    ]
    return max(scores) if scores else None


def _match(pattern: str, path: str, environment: Mapping[str, str]) -> _Score | None:
    expanded = _expand(pattern, environment)
    pattern_segments = _segments(expanded)
    if not _match_segments(pattern_segments, _segments(path)):
        return None
    literal = sum(1 for segment in pattern_segments if "*" not in segment)
    return (literal, len(expanded))


def _expand(pattern: str, environment: Mapping[str, str]) -> str:
    for variable in ENVIRONMENT_VARIABLES:
        value = environment.get(variable)
        if value is not None:
            pattern = pattern.replace(variable, value)
    return _PLACEHOLDER.sub("*", pattern)


def _segments(path: str) -> list[str]:
    return [part for part in _SEPARATORS.split(path.strip("\\/")) if part]


def _match_segments(pattern: list[str], path: list[str]) -> bool:
    """Compara segmento a segmento; `**` se come cero o más segmentos."""
    if not pattern:
        return not path
    head, *rest = pattern
    if head == "**":
        if _match_segments(rest, path):
            return True
        return bool(path) and _match_segments(pattern, path[1:])
    if not path:
        return False
    if not fnmatch.fnmatchcase(path[0].lower(), head.lower()):
        return False
    return _match_segments(rest, path[1:])
