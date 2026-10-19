"""Inspector de Steam (T2C.1): qué juegos tiene instalados y cuánto ocupa cada uno.

Steam no tiene un comando de lectura para esto: en cambio, lee **solo como
texto** (nunca ejecuta nada) sus propios archivos, el mismo patrón que usará
T2C.3 para `docker-compose`/`package.json`. `libraryfolders.vdf` dice qué
bibliotecas hay (pueden estar en más de un disco); cada
`steamapps\\appmanifest_<id>.acf` de cada biblioteca dice el nombre y el
tamaño real en disco (`SizeOnDisk`) de un juego instalado. Son archivos VDF
(formato propio de Valve, clave-valor anidado); no se necesita un analizador
completo, solo sacar un par de claves puntuales con una expresión regular. Si
Steam no está instalado o no se puede leer nada, se omite sin error.
"""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from discoclaro.domain.insights import Insight, InsightItem
from discoclaro.domain.placement import Misplaced, StorageLocation, find_misplaced
from discoclaro.platform import windows

MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class SteamGame:
    """Un juego instalado, en qué biblioteca (carpeta raíz, no toda la ruta
    del juego) y cuánto ocupa (T2C.5, además de T2C.1)."""

    name: str
    library: str
    size_bytes: int


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    return None if len(text) > MAX_FILE_BYTES else text


def _list_appmanifests(library_path: str) -> list[str]:
    steamapps = os.path.join(library_path, "steamapps")
    try:
        with os.scandir(steamapps) as entries:
            return [
                entry.path
                for entry in entries
                if entry.is_file(follow_symlinks=False)
                and entry.name.lower().startswith("appmanifest_")
                and entry.name.lower().endswith(".acf")
            ]
    except OSError:
        return []


def _vdf_values(content: str, key: str) -> list[str]:
    """Todos los valores de `key` en un VDF simple (alcanza con esto para las
    claves puntuales que se necesitan: no hace falta un analizador completo
    del formato)."""
    pattern = rf'"{re.escape(key)}"\s+"((?:[^"\\]|\\.)*)"'
    return [match.replace("\\\\", "\\") for match in re.findall(pattern, content)]


def _library_paths(base: str, read_text: Callable[[str], str | None]) -> list[str]:
    content = read_text(os.path.join(base, "steamapps", "libraryfolders.vdf"))
    if content is None:
        return [base]
    paths = _vdf_values(content, "path")
    return paths if paths else [base]


def _parse_appmanifest(content: str) -> tuple[str, int] | None:
    names = _vdf_values(content, "name")
    sizes = _vdf_values(content, "SizeOnDisk")
    if not names or not sizes:
        return None
    try:
        size = int(sizes[0])
    except ValueError:
        return None
    if size <= 0:
        return None
    return names[0], size


def installed_games(
    *,
    steam_path: Callable[[], str | None] = windows.steam_install_path,
    read_text: Callable[[str], str | None] = _read_text,
    list_appmanifests: Callable[[str], list[str]] = _list_appmanifests,
) -> list[SteamGame]:
    """Cada juego instalado, con la biblioteca donde vive (T2C.5, además de
    T2C.1); vacía si Steam no está instalado o no hay nada que leer."""
    base = steam_path()
    if base is None:
        return []
    games = []
    for library in _library_paths(base, read_text):
        for manifest_path in list_appmanifests(library):
            content = read_text(manifest_path)
            if content is None:
                continue
            parsed = _parse_appmanifest(content)
            if parsed is not None:
                name, size = parsed
                games.append(SteamGame(name=name, library=library, size_bytes=size))
    return games


def inspect(
    *,
    steam_path: Callable[[], str | None] = windows.steam_install_path,
    read_text: Callable[[str], str | None] = _read_text,
    list_appmanifests: Callable[[str], list[str]] = _list_appmanifests,
) -> Insight | None:
    """La huella de los juegos instalados, o `None` si no hay nada que decir."""
    games = installed_games(
        steam_path=steam_path, read_text=read_text, list_appmanifests=list_appmanifests
    )
    if not games:
        return None
    items = tuple(InsightItem(name=g.name, size_bytes=g.size_bytes) for g in games)
    return Insight(program="steam", items=items)


def misplaced_games(
    *,
    games: Callable[[], list[SteamGame]] = installed_games,
    system_drive: Callable[[], str] = windows.system_drive,
) -> list[Misplaced]:
    """Los juegos instalados en la unidad del sistema mientras Steam también
    tiene bibliotecas en otras unidades (T2C.5): el caso real de la tarea, un
    juego grande en C: con una biblioteca ya registrada en D:."""
    locations = [
        StorageLocation(
            name=game.name,
            drive=os.path.splitdrive(game.library)[0],
            size_bytes=game.size_bytes,
        )
        for game in games()
        if os.path.splitdrive(game.library)[0]
    ]
    return find_misplaced(locations, system_drive())
