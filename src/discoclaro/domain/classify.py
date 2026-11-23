"""Asigna la regla más específica del catálogo a una carpeta (T2.3).

Pura: no lee el disco ni las variables de entorno reales. `environment` da el valor
de cada variable (`%USERPROFILE%`, `%LOCALAPPDATA%`, `%APPDATA%`, `%TEMP%`) que
puedan traer los patrones de las reglas; quien llama la arma de verdad
(`platform/windows.py::environment_variables`). Los marcadores entre `<` y `>`
(`<volumen>`, `<app>`...) de la tabla del catálogo se tratan como `*`: un solo
elemento de la ruta, cualquiera sea.
"""

import fnmatch
import functools
import re
from collections.abc import Mapping, Sequence

from discoclaro.domain.rules import Rule

ENVIRONMENT_VARIABLES = ("%USERPROFILE%", "%LOCALAPPDATA%", "%APPDATA%", "%TEMP%")
_PLACEHOLDER = re.compile(r"<[^>]+>")

# Lo que vale cada coincidencia, para que gane la más específica: primero cuántos
# elementos de la ruta son literales (sin comodín), y a igualdad, el patrón expandido
# más largo. Entre dos igual de específicas, gana la que aparece antes en `rules`.
_Score = tuple[int, int]
_CompiledPattern = tuple[tuple[str, ...], bool, int, _Score, str | None]


@functools.lru_cache(maxsize=1024)
def _compile_pattern(
    pattern: str, env_items: tuple[tuple[str, str], ...]
) -> _CompiledPattern:
    expanded = pattern
    for variable, value in env_items:
        expanded = expanded.replace(variable, value)
    expanded = _PLACEHOLDER.sub("*", expanded)
    raw_parts = expanded.replace("/", "\\").strip("\\").split("\\")
    segments = tuple(part.lower() for part in raw_parts if part)
    has_globstar = "**" in segments
    min_len = len(segments) - segments.count("**")
    literal_count = sum(1 for seg in segments if "*" not in seg)
    score: _Score = (literal_count, len(expanded))
    tail = (
        segments[-1]
        if (segments and "*" not in segments[-1] and "?" not in segments[-1])
        else None
    )
    return segments, has_globstar, min_len, score, tail


class CompiledCatalog:
    """Índice en memoria del catálogo de reglas expandido para clasificar decenas de
    miles de nodos en pocos milisegundos."""

    __slots__ = ("by_tail", "rules", "wildcard_tails")

    def __init__(
        self, rules: Sequence[Rule], environment: Mapping[str, str]
    ) -> None:
        self.rules = tuple(rules)
        env_items = tuple(
            (var, environment[var])
            for var in ENVIRONMENT_VARIABLES
            if var in environment
        )
        by_tail: dict[str, list[tuple[int, tuple[str, ...], bool, int, _Score]]] = {}
        wildcard_tails: list[tuple[int, tuple[str, ...], bool, int, _Score]] = []

        for rule_idx, rule in enumerate(self.rules):
            for pattern in rule.paths:
                segs, has_gs, min_len, score, tail = _compile_pattern(
                    pattern, env_items
                )
                if not segs:
                    continue
                entry = (rule_idx, segs, has_gs, min_len, score)
                if tail is not None:
                    by_tail.setdefault(tail, []).append(entry)
                else:
                    wildcard_tails.append(entry)

        self.by_tail = {k: tuple(v) for k, v in by_tail.items()}
        self.wildcard_tails = tuple(wildcard_tails)

    def classify_path(self, path: str) -> Rule | None:
        norm = path.replace("/", "\\").strip("\\").lower()
        if not norm:
            return None
        path_segments = [part for part in norm.split("\\") if part]
        if not path_segments:
            return None
        path_len = len(path_segments)
        path_tail = path_segments[-1]
        best_idx: int | None = None
        best_key: tuple[int, int, int] | None = None

        tail_group = self.by_tail.get(path_tail)
        if tail_group:
            for rule_idx, pat_segs, has_gs, min_len, score in tail_group:
                if not has_gs:
                    if len(pat_segs) != path_len:
                        continue
                elif path_len < min_len:
                    continue
                cand_key = (score[0], score[1], -rule_idx)
                if best_key is not None and cand_key <= best_key:
                    continue
                if _match_segments_fast(pat_segs, 0, path_segments, 0):
                    best_key = cand_key
                    best_idx = rule_idx

        for rule_idx, pat_segs, has_gs, min_len, score in self.wildcard_tails:
            if not has_gs:
                if len(pat_segs) != path_len:
                    continue
            elif path_len < min_len:
                continue
            cand_key = (score[0], score[1], -rule_idx)
            if best_key is not None and cand_key <= best_key:
                continue
            if _match_segments_fast(pat_segs, 0, path_segments, 0):
                best_key = cand_key
                best_idx = rule_idx

        return None if best_idx is None else self.rules[best_idx]


def classify(
    path: str, rules: Sequence[Rule], environment: Mapping[str, str]
) -> Rule | None:
    """La regla más específica de `rules` cuyo patrón coincide con `path`.

    `None` si ninguna coincide. No sigue enlaces ni toca el disco: compara texto
    contra texto.
    """
    return CompiledCatalog(rules, environment).classify_path(path)


def _match_segments_fast(
    pattern: tuple[str, ...], p_idx: int, path: list[str], s_idx: int
) -> bool:
    """Compara segmento a segmento sin crear sublistas; `**` consume 0 o más."""
    p_len = len(pattern)
    s_len = len(path)
    while p_idx < p_len:
        head = pattern[p_idx]
        if head == "**":
            if _match_segments_fast(pattern, p_idx + 1, path, s_idx):
                return True
            return s_idx < s_len and _match_segments_fast(
                pattern, p_idx, path, s_idx + 1
            )
        if s_idx >= s_len:
            return False
        cur = path[s_idx]
        if "*" not in head and "?" not in head:
            if cur != head:
                return False
        elif head != "*" and not fnmatch.fnmatchcase(cur, head):
            return False
        p_idx += 1
        s_idx += 1
    return s_idx == s_len
