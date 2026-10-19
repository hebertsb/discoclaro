"""Qué usa cada proyecto (T2C.3): puro.

`adapters/projects.py` lee los archivos de cada proyecto configurado y arma un
`ProjectUsage` por carpeta; este módulo solo junta esos resultados para
responder "¿quién usa esto?", sin saber nada de archivos ni de carpetas.
"""

from collections.abc import Sequence
from dataclasses import dataclass

# Las tres cosas que puede referenciar un proyecto, según la Fase 2C.
REFERENCE_KINDS = ("image", "model", "package")


@dataclass(frozen=True, slots=True)
class ProjectReference:
    """Algo que un proyecto referencia: una imagen de Docker, un modelo de
    IA, o un paquete (`kind` es una de `REFERENCE_KINDS`)."""

    kind: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in REFERENCE_KINDS:
            raise ValueError(f"unknown reference kind: {self.kind!r}")
        if not self.name:
            raise ValueError("a reference needs a name")


@dataclass(frozen=True, slots=True)
class ProjectUsage:
    """Lo que un proyecto (por su nombre, no toda la ruta) referencia."""

    project: str
    references: tuple[ProjectReference, ...]

    def __post_init__(self) -> None:
        if not self.project:
            raise ValueError("a project usage needs a project name")


def used_by(usages: Sequence[ProjectUsage], kind: str, name: str) -> tuple[str, ...]:
    """Los nombres de los proyectos que referencian `name` de tipo `kind`,
    sin distinguir mayúsculas; vacío si ninguno lo usa."""
    folded = name.casefold()
    return tuple(
        usage.project
        for usage in usages
        if any(
            reference.kind == kind and reference.name.casefold() == folded
            for reference in usage.references
        )
    )
