"""Carga el catálogo de reglas desde texto o archivos TOML (T2.1).

Una regla es siempre un dato, nunca código: esto solo arma objetos `Rule`, valida su
forma (campos obligatorios, valores de `risk` y de `reacquire`) y nunca interpreta ni
ejecuta nada de lo que lee. La validación de seguridad más estricta —metacaracteres de
shell, `task_id` contra el catálogo cerrado, zonas protegidas— es tarea aparte (T2.11);
aquí solo se cuida la forma.
"""

import tomllib
from pathlib import Path

from discoclaro.domain.rules import Reacquire, Rule, SuggestedAction

_REQUIRED_STR_FIELDS = ("id", "name", "category", "risk", "explanation")


class RuleError(Exception):
    """Una regla (o el archivo que la trae) no tiene el formato esperado.

    `rule_id` es el id de la regla si se llegó a leer, o `""` si ni eso se pudo.
    """

    def __init__(self, message: str, rule_id: str = "") -> None:
        super().__init__(message)
        self.rule_id = rule_id


def load_rules_from_text(text: str, source: str = "<texto>") -> list[Rule]:
    """Lee las reglas `[[rule]]` de un TOML ya en memoria.

    Lanza `RuleError`, con el origen y el id de la regla cuando se conoce, si algo no
    cumple el formato: TOML inválido, un campo obligatorio que falta o vale otra
    cosa, un `risk` o un `reacquire.kind` que no son los permitidos, o un id
    repetido dentro de este mismo texto.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RuleError(f"{source}: invalid TOML ({error})") from error
    raw_rules = data.get("rule", [])
    if not isinstance(raw_rules, list):
        raise RuleError(f"{source}: 'rule' must be a list of [[rule]] tables")
    rules = [_build_rule(raw, source) for raw in raw_rules]
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise RuleError(f"{source}: duplicate rule id: {rule.id!r}", rule.id)
        seen.add(rule.id)
    return rules


def load_rules_file(path: str | Path) -> list[Rule]:
    """Lee las reglas de un archivo TOML. Lanza `RuleError` si no se puede leer."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuleError(
            f"{path}: could not read it ({type(error).__name__})"
        ) from error
    return load_rules_from_text(text, source=str(path))


def _build_rule(raw: object, source: str) -> Rule:
    if not isinstance(raw, dict):
        raise RuleError(f"{source}: each rule must be a table")
    rule_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
    try:
        for field in _REQUIRED_STR_FIELDS:
            _require_str(raw, field)
        return Rule(
            id=raw["id"],
            name=raw["name"],
            paths=_require_str_list(raw, "paths"),
            category=raw["category"],
            risk=raw["risk"],
            explanation=raw["explanation"],
            suggested_action=_build_action(raw.get("suggested_action")),
            reacquire=_build_reacquire(raw.get("reacquire")),
            task_id=_optional_str(raw, "task_id"),
        )
    except (TypeError, ValueError) as error:
        raise RuleError(f"{source}: {error}", rule_id) from error


def _require_str(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{field}' must be a non-empty string")
    return value


def _optional_str(raw: dict[str, object], field: str) -> str | None:
    if field not in raw:
        return None
    return _require_str(raw, field)


def _require_str_list(raw: dict[str, object], field: str) -> tuple[str, ...]:
    value = raw.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"'{field}' must be a non-empty list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"'{field}' must only have non-empty strings")
    return tuple(value)


def _build_action(raw: object) -> SuggestedAction | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'suggested_action' must be a table")
    return SuggestedAction(
        type=_require_str(raw, "type"),
        text=_require_str(raw, "text"),
        note=_optional_str(raw, "note"),
    )


def _build_reacquire(raw: object) -> Reacquire | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'reacquire' must be a table")
    return Reacquire(kind=_require_str(raw, "kind"), cost=_optional_str(raw, "cost"))
