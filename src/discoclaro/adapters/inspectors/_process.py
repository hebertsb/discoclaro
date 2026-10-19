"""Correr el comando de un inspector con límites de seguridad (T2C.1).

Compartido por todos los inspectores: catálogo cerrado de comandos de lectura,
ruta completa del ejecutable (nunca el nombre suelto, para no confiar en el
`PATH` del momento), tiempo máximo y tamaño máximo de salida. `command` se pasa
siempre como lista de argumentos, nunca como un texto para una terminal. Si el
comando falla, tarda demasiado o la salida es más grande de lo razonable, se
trata igual que "no hay nada que decir": ni es una falla del escaneo ni se
intenta adivinar con una salida a medias.
"""

import shutil
import subprocess
from collections.abc import Callable, Sequence

TIMEOUT_S = 15.0

# Ninguno de estos comandos da, de verdad, más que unos pocos KB (listas de
# modelos, cachés, tablas de espacio). Un múltiplo generoso de eso basta para
# aceptar cualquier salida real y rechazar una que se desbocó.
MAX_OUTPUT_BYTES = 1_000_000


def run_command(
    command: Sequence[str],
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """La salida de `command`, o `None` si falló, tardó de más, o la salida es
    más grande de lo esperado. `command[0]` tiene que ser la ruta completa del
    ejecutable, ya resuelta por quien llama. `run` se puede inyectar para
    probar sin ejecutar nada de verdad."""
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        return None
    return result.stdout


def resolve_executable(
    name: str, *, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """La ruta completa de `name` en el `PATH` de ahora, o `None` si no está.
    Se resuelve una sola vez, antes de armar el comando: nunca se le pasa el
    nombre suelto a `run_command`, para no depender de que el `PATH` siga
    siendo el mismo entre que se resuelve y que se ejecuta."""
    return which(name)
