"""Lo que un programa dice de sí mismo sobre su propio espacio (T2C.1): puro.

`adapters/inspectors/` llena esto corriendo comandos de lectura de cada
programa (o leyendo sus propios archivos, como Steam); este módulo solo define
la forma común del resultado, sin saber nada de procesos ni de archivos.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InsightItem:
    """Una parte de lo que ocupa un programa, con su propio nombre: una
    categoría de Docker, un modelo de Ollama, un juego de Steam."""

    name: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("an insight item needs a name")
        if self.size_bytes < 0:
            raise ValueError("size cannot be negative")


@dataclass(frozen=True, slots=True)
class Insight:
    """Lo que un programa dice de sí mismo, en el momento de preguntarle."""

    program: str
    items: tuple[InsightItem, ...]

    def __post_init__(self) -> None:
        if not self.program:
            raise ValueError("an insight needs a program")

    @property
    def total_bytes(self) -> int:
        """La suma de todas las partes."""
        return sum(item.size_bytes for item in self.items)
