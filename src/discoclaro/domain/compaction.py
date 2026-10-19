""" "Liberado pero no recuperado" (T2C.2): puro.

El disco virtual de un programa (el `.vhdx` de Docker, por ejemplo) solo
crece: borrar cosas adentro no lo encoge solo. `CompactionGap` compara lo que
el programa dice usar por dentro con el tamaño real del archivo en el disco
del usuario; la diferencia es espacio que ya se liberó por dentro pero sigue
ocupado afuera, hasta que alguien compacte el archivo a mano.
"""

from dataclasses import dataclass

# 5 GB, elegido simple: la brecha real de la sesión de origen (de 48,5 a 30,3
# GB, T2.1) fue mucho mayor que esto; un valor chico solo generaría avisos por
# unos pocos cientos de MB de metadatos del propio sistema de archivos, que
# nunca se va a recuperar compactando.
DEFAULT_MIN_GAP_BYTES = 5 * 1024**3


@dataclass(frozen=True, slots=True)
class CompactionGap:
    """Lo real (`file_bytes`, el tamaño del archivo en disco) contra lo que el
    programa dice usar por dentro (`used_bytes`)."""

    path: str
    file_bytes: int
    used_bytes: int

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("a compaction gap needs a path")
        if self.file_bytes < 0 or self.used_bytes < 0:
            raise ValueError("sizes cannot be negative")

    @property
    def gap_bytes(self) -> int:
        """Lo liberado por dentro que todavía no se recuperó afuera; nunca
        negativo (un archivo más chico que lo usado no es una brecha, es
        que ya se compactó, o que se lo midió en un instante distinto)."""
        return max(0, self.file_bytes - self.used_bytes)


def needs_compaction(
    gap: CompactionGap, *, min_gap_bytes: int = DEFAULT_MIN_GAP_BYTES
) -> bool:
    """Si la brecha es grande como para avisar que falta compactar."""
    return gap.gap_bytes >= min_gap_bytes
