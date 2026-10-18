"""Carga el catálogo de reglas desde texto o archivos TOML (T2.1, T2.2, T2.11).

Una regla es siempre un dato, nunca código: esto solo arma objetos `Rule`, valida su
forma (campos obligatorios, valores de `risk` y de `reacquire`) y su seguridad
(metacaracteres de shell, zonas protegidas, largo de los campos), y nunca interpreta
ni ejecuta nada de lo que lee. `task_id` todavía no se valida contra un catálogo
cerrado de tareas: ese catálogo es de la fase 4 (nota de dependencia, sección 7).
"""

import re
import tomllib
from importlib.resources import files
from pathlib import Path

from discoclaro.domain.rules import Reacquire, Rule, SuggestedAction

_REQUIRED_STR_FIELDS = ("id", "name", "category", "risk", "explanation")
BUILTIN_RULES_PACKAGE = "discoclaro"
BUILTIN_RULES_FOLDER = "rules"
_BUILTIN_PACKAGE = "discoclaro.rules"

# Un campo más largo que esto no es un texto legítimo del catálogo (el más largo de
# verdad hoy tiene 109 caracteres): se rechaza en vez de mostrarlo o guardarlo.
MAX_FIELD_LENGTH = 300

# Metacaracteres de shell que no debería traer un comando sugerido (T2.11): aunque
# `suggested_action.text` nunca se ejecuta, no hay motivo para que una orden copiable
# encadene, redirija o sustituya nada. El punto y coma se deja afuera a propósito: el
# catálogo real lo usa como puntuación normal del español (regla `ollama-models`,
# tomada igual de la tabla de la sección 6), y rechazarlo rompería datos reales sin
# ganar seguridad, porque nada de esto se ejecuta nunca.
_FORBIDDEN_COMMAND_CHARS = ("&", "|", ">", "`", "$(")

# Las 4 reglas `do_not_touch` reales (sección 6 del plan): si el TOML dijera otra
# cosa, se ignora y se fuerza este riesgo desde el código, no desde los datos.
FORCED_DO_NOT_TOUCH_IDS = frozenset(
    {"hiberfil", "pagefile", "windows-installer", "windows-backup"}
)

# Zonas protegidas (sección 8 del plan) que un patrón nunca debería marcar como
# `safe`: la raíz de un disco, o esa carpeta exacta justo debajo de la raíz. No
# incluye "copias de seguridad" porque no tienen una ruta fija que comparar: ya las
# cubre la regla `windows-backup`, forzada arriba a `do_not_touch`.
_PROTECTED_SUFFIXES = (
    (),  # la raíz del disco misma
    ("Windows",),
    ("Program Files",),
    ("Program Files (x86)",),
    ("ProgramData",),
)
_PROTECTED_WHOLE_PATHS = frozenset(
    {"%USERPROFILE%", "%LOCALAPPDATA%\\discoclaro", "%APPDATA%\\discoclaro"}
)
_DRIVE_ROOT_TOKEN = re.compile(r"^[A-Za-z]:$")
_PLACEHOLDER_SEGMENT = re.compile(r"^<[^>]+>$")


class RuleError(Exception):
    """Una regla (o el archivo que la trae) no tiene el formato esperado.

    `rule_id` es el id de la regla si se llegó a leer, o `""` si ni eso se pudo.
    """

    def __init__(self, message: str, rule_id: str = "") -> None:
        super().__init__(message)
        self.rule_id = rule_id


def load_rules_from_text(
    text: str, source: str = "<texto>", *, verified: bool = True
) -> list[Rule]:
    """Lee las reglas `[[rule]]` de un TOML ya en memoria.

    Lanza `RuleError`, con el origen y el id de la regla cuando se conoce, si algo no
    cumple el formato o la seguridad esperada: TOML inválido, un campo obligatorio
    que falta o vale otra cosa, un `risk`/`reacquire.kind` no permitido, un campo
    demasiado largo, un comando con metacaracteres de shell, una ruta de zona
    protegida marcada `safe`, o un id repetido dentro de este mismo texto. `verified`
    marca si el origen es el propio catálogo de discoclaro (`True`) o algo que el
    usuario agregó aparte (`False`).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RuleError(f"{source}: invalid TOML ({error})") from error
    raw_rules = data.get("rule", [])
    if not isinstance(raw_rules, list):
        raise RuleError(f"{source}: 'rule' must be a list of [[rule]] tables")
    rules = [_build_rule(raw, source, verified) for raw in raw_rules]
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise RuleError(f"{source}: duplicate rule id: {rule.id!r}", rule.id)
        seen.add(rule.id)
    return rules


def load_rules_file(path: str | Path, *, verified: bool = False) -> list[Rule]:
    """Lee las reglas de un archivo TOML. Lanza `RuleError` si no se puede leer.

    Pensado para el archivo aparte donde el usuario agrega sus propias reglas: por
    eso `verified` empieza en `False` (nadie del proyecto las revisó).
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuleError(
            f"{path}: could not read it ({type(error).__name__})"
        ) from error
    return load_rules_from_text(text, source=str(path), verified=verified)


def load_builtin_catalog() -> list[Rule]:
    """El catálogo inicial que trae discoclaro (sección 6 del plan), un archivo por
    categoría en `discoclaro/rules/`. Lanza `RuleError` si algún archivo no tiene el
    formato esperado, o si dos reglas de archivos distintos repiten el mismo id.
    """
    directory = files(BUILTIN_RULES_PACKAGE).joinpath(BUILTIN_RULES_FOLDER)
    rules: list[Rule] = []
    seen: dict[str, str] = {}
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".toml"):
            continue
        text = entry.read_text(encoding="utf-8")
        for rule in load_rules_from_text(text, entry.name, verified=True):
            if rule.id in seen:
                raise RuleError(
                    f"{entry.name}: duplicate rule id, already used in "
                    f"{seen[rule.id]}: {rule.id!r}",
                    rule.id,
                )
            seen[rule.id] = entry.name
            rules.append(rule)
    return rules


def _build_rule(raw: object, source: str, verified: bool) -> Rule:
    if not isinstance(raw, dict):
        raise RuleError(f"{source}: each rule must be a table")
    rule_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
    try:
        for field in _REQUIRED_STR_FIELDS:
            _require_str(raw, field)
        paths = _require_str_list(raw, "paths")
        risk = raw["risk"]
        if raw["id"] in FORCED_DO_NOT_TOUCH_IDS:
            risk = "do_not_touch"
        elif risk == "safe" and any(_is_protected_zone(path) for path in paths):
            raise ValueError("a protected location cannot be marked 'safe'")
        # El catálogo cerrado de tareas (fase 4) todavía no existe (nota de
        # dependencia de la sección 7): hasta entonces, `task_id` no se valida
        # contra nada, solo se guarda. Cuando ese catálogo exista, compararlo acá.
        task_id = _optional_str(raw, "task_id")
        return Rule(
            id=raw["id"],
            name=raw["name"],
            paths=paths,
            category=raw["category"],
            risk=risk,
            explanation=raw["explanation"],
            suggested_action=_build_action(raw.get("suggested_action")),
            reacquire=_build_reacquire(raw.get("reacquire")),
            task_id=task_id,
            verified=verified,
        )
    except (TypeError, ValueError) as error:
        raise RuleError(f"{source}: {error}", rule_id) from error


def _is_protected_zone(path: str) -> bool:
    """Si `path` apunta a la raíz de un disco o a una de sus zonas protegidas."""
    if path in _PROTECTED_WHOLE_PATHS:
        return True
    segments = path.split("\\")
    first = segments[0]
    if not (
        _DRIVE_ROOT_TOKEN.fullmatch(first) or _PLACEHOLDER_SEGMENT.fullmatch(first)
    ):
        return False
    rest = tuple(segments[1:])
    return any(
        len(rest) == len(suffix)
        and all(a.casefold() == b.casefold() for a, b in zip(rest, suffix, strict=True))
        for suffix in _PROTECTED_SUFFIXES
    )


def _require_str(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{field}' must be a non-empty string")
    if len(value) > MAX_FIELD_LENGTH:
        raise ValueError(f"'{field}' is longer than {MAX_FIELD_LENGTH} characters")
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
    if any(len(item) > MAX_FIELD_LENGTH for item in value):
        raise ValueError(
            f"'{field}' has an item longer than {MAX_FIELD_LENGTH} characters"
        )
    return tuple(value)


def _build_action(raw: object) -> SuggestedAction | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'suggested_action' must be a table")
    action_type = _require_str(raw, "type")
    text = _require_str(raw, "text")
    if action_type == "command" and any(
        char in text for char in _FORBIDDEN_COMMAND_CHARS
    ):
        raise ValueError(f"'suggested_action.text' has a forbidden character: {text!r}")
    return SuggestedAction(type=action_type, text=text, note=_optional_str(raw, "note"))


def _build_reacquire(raw: object) -> Reacquire | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'reacquire' must be a table")
    return Reacquire(kind=_require_str(raw, "kind"), cost=_optional_str(raw, "cost"))
