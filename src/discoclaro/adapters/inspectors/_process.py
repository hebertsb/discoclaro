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
import sys
from collections.abc import Callable, Sequence

TIMEOUT_S = 15.0

# Ninguno de estos comandos da, de verdad, más que unos pocos KB (listas de
# modelos, cachés, tablas de espacio). Un múltiplo generoso de eso basta para
# aceptar cualquier salida real y rechazar una que se desbocó.
MAX_OUTPUT_BYTES = 1_000_000

_NO_WINDOW_FLAGS = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


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
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "timeout": TIMEOUT_S,
            "errors": "replace",
        }
        if _NO_WINDOW_FLAGS and run is subprocess.run:
            kwargs["creationflags"] = _NO_WINDOW_FLAGS
            kwargs["stdin"] = subprocess.DEVNULL
        result = run(command, **kwargs)
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
    """La ruta completa de `name` en el `PATH` de ahora (o en rutas estándar de
    instalación en Windows si no está en el PATH del proceso en segundo plano),
    o `None` si no está."""
    found = which(name)
    if found is not None:
        return found
    if which is not shutil.which or sys.platform != "win32":
        return None
    import os
    from pathlib import Path

    low = name.lower()
    local_app = os.environ.get("LOCALAPPDATA", "")
    user_prof = os.environ.get("USERPROFILE", "")
    venv_scripts = Path(sys.executable).parent
    candidates: list[Path] = [
        venv_scripts / f"{low}.exe",
        venv_scripts / f"{low}.cmd",
    ]
    if low == "ollama" and local_app:
        candidates.append(Path(local_app) / "Programs" / "Ollama" / "ollama.exe")
    elif low == "uv" and user_prof:
        candidates.append(Path(user_prof) / ".local" / "bin" / "uv.exe")
        if local_app:
            candidates.append(Path(local_app) / "uv" / "uv.exe")
    elif low == "npm":
        candidates.append(Path(r"C:\Program Files\nodejs\npm.cmd"))
    elif low == "docker":
        candidates.append(
            Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        )
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None
