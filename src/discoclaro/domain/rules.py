"""Una regla del catálogo: qué carpeta es, de qué categoría y cuánto arriesga borrarla.

Puro: ni lee archivos ni sabe de TOML (eso es `adapters/rules.py`, T2.1). Una regla es
siempre un dato, nunca código: `suggested_action.text` es solo texto para mostrar o
copiar, jamás se ejecuta.
"""

from dataclasses import dataclass

RISK_LEVELS = frozenset({"safe", "review", "do_not_touch"})
REACQUIRE_KINDS = frozenset({"none", "download", "rebuild"})


@dataclass(frozen=True, slots=True)
class SuggestedAction:
    """Lo que se le sugiere hacer al usuario; nunca se ejecuta desde aquí.

    `type` describe la forma de la sugerencia (hoy solo `"command"`: una orden para
    copiar y correr a mano, o dentro del catálogo cerrado si `task_id` la enlaza).
    """

    type: str
    text: str
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.type or not self.text:
            raise ValueError("a suggested action needs a type and a text")


@dataclass(frozen=True, slots=True)
class Reacquire:
    """Cuánto cuesta recuperar lo que la regla deja borrar."""

    kind: str
    cost: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in REACQUIRE_KINDS:
            raise ValueError(f"unknown reacquire kind: {self.kind!r}")


@dataclass(frozen=True, slots=True)
class Rule:
    """Una regla del catálogo: identifica una carpeta y dice qué tan segura es tocarla.

    `risk`: `safe` se regenera sin pérdida, `review` lo decide el usuario y
    `do_not_touch` solo se informa, nunca se ejecuta desde la herramienta. `task_id`,
    si está, solo puede nombrar una tarea del catálogo cerrado (fase 4); nunca trae un
    comando propio.
    """

    id: str
    name: str
    paths: tuple[str, ...]
    category: str
    risk: str
    explanation: str
    suggested_action: SuggestedAction | None = None
    reacquire: Reacquire | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.category or not self.explanation:
            raise ValueError("a rule needs id, name, category and explanation")
        if not self.paths or any(not path for path in self.paths):
            raise ValueError("a rule needs at least one non-empty path")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"unknown risk level: {self.risk!r}")
