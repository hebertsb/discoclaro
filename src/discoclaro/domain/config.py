"""Los límites que el usuario puede fijar en su `config.toml` (T2.6): modelo puro.

Son datos, nunca código: nada de aquí decide si hay que avisar ni toca el disco.
`services/check_service.py` (T2.7) es quien compara estos límites con una foto real.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

GIB = 1024**3
DEFAULT_WARN_AT_PERCENT = 85.0
DEFAULT_MIN_DISPLAY_GB = 1.0
MIN_PERCENT = 0.0
MAX_PERCENT = 100.0


@dataclass(frozen=True, kw_only=True)
class VolumeLimit:
    """Límites de un disco: cuánto libre mínimo y a qué porcentaje usado avisar."""

    min_free_gb: float | None = None
    warn_at_percent: float = DEFAULT_WARN_AT_PERCENT


@dataclass(frozen=True, kw_only=True)
class Budget:
    """Presupuesto máximo, en GB, de una carpeta o de una categoría del catálogo."""

    max_gb: float


@dataclass(frozen=True, kw_only=True)
class GrowthLimits:
    """A partir de qué crecimiento entre fotos, o de qué tamaño nuevo, avisar."""

    max_growth_gb: float | None = None
    new_file_gb: float | None = None


@dataclass(frozen=True, kw_only=True)
class Config:
    """Toda la configuración del usuario, ya leída y validada."""

    volumes: Mapping[str, VolumeLimit] = field(default_factory=dict)
    budgets: Mapping[str, Budget] = field(default_factory=dict)
    growth: GrowthLimits = field(default_factory=GrowthLimits)
    min_display_gb: float = DEFAULT_MIN_DISPLAY_GB

    def min_display_bytes(self) -> int:
        """`min_display_gb` en bytes, para comparar directo con tamaños del escaneo."""
        return round(self.min_display_gb * GIB)
