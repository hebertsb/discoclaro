"""Diferencia entre dos inventarios de programas (T2B.3): pura.

Compara dos listas de `Program` (la de antes y la de ahora) por nombre, y dice qué se
instaló, qué se desinstaló y a quién le cambió la versión. No sabe nada de la base ni
del registro: eso ya lo dieron `platform/windows.py` y `adapters/store.py`.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from discoclaro.domain.models import Program


@dataclass(frozen=True, slots=True)
class ProgramUpdate:
    """Un programa que sigue instalado pero cambió de versión entre los dos
    inventarios (el nombre es igual en los dos; `old_version`/`new_version`
    pueden ser `None` si el registro no la informaba en ese momento)."""

    name: str
    old_version: str | None
    new_version: str | None


@dataclass(frozen=True, slots=True)
class InventoryDiff:
    """Qué cambió entre dos inventarios: instalados, desinstalados y actualizados."""

    installed: tuple[Program, ...]
    uninstalled: tuple[Program, ...]
    updated: tuple[ProgramUpdate, ...]


def diff_inventories(
    before: Sequence[Program], after: Sequence[Program]
) -> InventoryDiff:
    """Compara dos inventarios por nombre.

    `installed` son los de `after` que no estaban en `before`; `uninstalled`, los de
    `before` que ya no están en `after`; `updated`, los que están en los dos pero con
    otra `version`. Dos programas con el mismo nombre en un mismo inventario (una
    instalación de máquina y otra de usuario, por ejemplo) se tratan como uno solo,
    igual que al guardarlos en la base (T2B.2): se compara con el último que
    apareció con ese nombre.
    """
    before_by_name = {program.name: program for program in before}
    after_by_name = {program.name: program for program in after}

    installed = tuple(
        program for program in after if program.name not in before_by_name
    )
    uninstalled = tuple(
        program for program in before if program.name not in after_by_name
    )
    updated = tuple(
        ProgramUpdate(
            name=name,
            old_version=before_by_name[name].version,
            new_version=after_by_name[name].version,
        )
        for name in after_by_name
        if name in before_by_name
        and before_by_name[name].version != after_by_name[name].version
    )
    return InventoryDiff(installed=installed, uninstalled=uninstalled, updated=updated)
