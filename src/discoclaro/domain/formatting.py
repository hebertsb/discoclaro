"""Formato de números y texto para mostrar al usuario. Puro: sin disco ni red.

Los símbolos de unidad (`GiB`) no se traducen; el separador decimal y el de miles los
da quien llama, normalmente desde el catálogo de textos del idioma.
"""

import unicodedata

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")

# Categorías de Unicode que pueden falsear una terminal: controles (como ESC), de
# formato (como las marcas de dirección), separadores de línea y de párrafo, sustitutos
# sueltos y de uso privado.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Zl", "Zp"})


def format_bytes(size: int, decimal: str = ".") -> str:
    """Tamaño legible en unidades binarias: `512 B`, `1.5 KiB`, `474.7 GiB`."""
    if size < 0:
        raise ValueError("size cannot be negative")
    if size < 1024:
        return f"{size} B"
    value = float(size)
    unit = 0
    while unit < len(_UNITS) - 1 and (value >= 1024 or round(value, 1) >= 1024):
        value /= 1024
        unit += 1
    return f"{value:.1f}".replace(".", decimal) + f" {_UNITS[unit]}"


def format_decimal(value: float, decimal: str = ".", places: int = 1) -> str:
    """Número con decimales fijos y el separador dado: `6.2` o `6,2`."""
    return f"{value:.{places}f}".replace(".", decimal)


def format_int(value: int, thousands: str = ",") -> str:
    """Entero con separador de miles: `1203998` pasa a `1,203,998`."""
    return f"{value:,}".replace(",", thousands)


def format_duration(seconds: float, decimal: str = ".") -> str:
    """Duración legible: `31 ms`, `45.2 s`, `3 min 12 s` o `1 h 05 min`."""
    if seconds < 0:
        raise ValueError("duration cannot be negative")
    if round(seconds, 3) < 1:
        return f"{round(seconds * 1000)} ms"
    if round(seconds, 1) < 60:
        return f"{seconds:.1f}".replace(".", decimal) + " s"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min {secs:02d} s"


def safe_text(text: str) -> str:
    """Cambia por `?` los caracteres que podrían falsear lo que se ve en la terminal."""
    return "".join(
        "?" if unicodedata.category(char) in _UNSAFE_CATEGORIES else char
        for char in text
    )
