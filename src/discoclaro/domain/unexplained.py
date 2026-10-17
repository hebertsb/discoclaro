"""Espacio no explicado: lo que el volumen usa y el escaneo no encontró.

`no explicado = usado − (escaneado − repetidos) − archivos de sistema`.

- Lo escaneado es lo que ocupan los archivos **en disco**, y cada carpeta cuenta su
  contenido entero, enlaces duros incluidos. Por eso se descuentan los `repetidos`: el
  mismo contenido con otro nombre ocupa el disco una sola vez.
- Los archivos de sistema se miden aparte y se restan para que no cuenten como misterio.

El resultado puede ser negativo (lo escaneado supera lo usado): por ejemplo, si algo
cambió en el disco durante el recorrido.
"""

from dataclasses import dataclass

from discoclaro.domain.models import Snapshot

DEFAULT_THRESHOLD_PERCENT = 5.0


@dataclass(frozen=True, slots=True)
class Unexplained:
    """El espacio no explicado de un volumen y si merece un aviso."""

    size_bytes: int
    percent_of_used: float
    exceeds_threshold: bool


def compute_unexplained(
    used_bytes: int,
    scanned_bytes: int,
    system_files_bytes: int,
    threshold_percent: float = DEFAULT_THRESHOLD_PERCENT,
    *,
    duplicate_bytes: int = 0,
) -> Unexplained:
    """Calcula el espacio no explicado.

    `duplicate_bytes` es lo que ocupan de más los enlaces duros dentro de
    `scanned_bytes`. Hay que avisar (`exceeds_threshold`) cuando sobra espacio y es
    mayor que `threshold_percent` por ciento de lo usado, sin contar el caso justo en el
    límite.
    """
    if min(used_bytes, scanned_bytes, system_files_bytes, duplicate_bytes) < 0:
        raise ValueError("sizes cannot be negative")
    if duplicate_bytes > scanned_bytes:
        raise ValueError("duplicate bytes cannot exceed the scanned bytes")
    if threshold_percent < 0:
        raise ValueError("threshold cannot be negative")
    size = used_bytes - (scanned_bytes - duplicate_bytes) - system_files_bytes
    percent = 100 * size / used_bytes if used_bytes else 0.0
    return Unexplained(
        size_bytes=size,
        percent_of_used=percent,
        exceeds_threshold=size > 0 and percent > threshold_percent,
    )


def unexplained_of(
    snapshot: Snapshot, threshold_percent: float = DEFAULT_THRESHOLD_PERCENT
) -> Unexplained | None:
    """El no explicado de una foto, o `None` si no es de un volumen completo.

    En un escaneo parcial de una carpeta no tiene sentido compararlo con lo usado del
    volumen.
    """
    if not snapshot.is_volume_scan:
        return None
    return compute_unexplained(
        snapshot.used_bytes,
        snapshot.scanned_bytes,
        snapshot.system_files_bytes,
        threshold_percent,
        duplicate_bytes=snapshot.duplicate_bytes,
    )
