"""Pruebas del inspector de Steam (T2C.1): `steam_path`/`read_text`/
`list_appmanifests` siempre se reemplazan, nunca se toca el disco real."""

from discoclaro.adapters.inspectors import steam as steam_inspector

LIBRARYFOLDERS = """"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam"
\t\t"apps"
\t\t{
\t\t\t"228980"\t\t"498906162"
\t\t}
\t}
\t"1"
\t{
\t\t"path"\t\t"D:\\\\SteamLibrary"
\t\t"apps"
\t\t{
\t\t\t"730"\t\t"73922650178"
\t\t}
\t}
}
"""

APPMANIFEST_228980 = """"AppState"
{
\t"appid"\t\t"228980"
\t"name"\t\t"Steamworks Common Redistributables"
\t"SizeOnDisk"\t\t"498906162"
}
"""

APPMANIFEST_730 = """"AppState"
{
\t"appid"\t\t"730"
\t"name"\t\t"Counter-Strike 2"
\t"SizeOnDisk"\t\t"73922650178"
}
"""


def fake_files():
    """Un sistema de archivos falso mínimo: dos bibliotecas reales, cada una
    con un `appmanifest_*.acf`."""
    files = {
        "C:\\Program Files (x86)\\Steam\\steamapps\\libraryfolders.vdf": LIBRARYFOLDERS,
        "C:\\Program Files (x86)\\Steam\\steamapps\\appmanifest_228980.acf": (
            APPMANIFEST_228980
        ),
        "D:\\SteamLibrary\\steamapps\\appmanifest_730.acf": APPMANIFEST_730,
    }
    manifests = {
        "C:\\Program Files (x86)\\Steam": [
            "C:\\Program Files (x86)\\Steam\\steamapps\\appmanifest_228980.acf"
        ],
        "D:\\SteamLibrary": ["D:\\SteamLibrary\\steamapps\\appmanifest_730.acf"],
    }

    def read_text(path):
        return files.get(path)

    def list_appmanifests(library):
        return manifests.get(library, [])

    return read_text, list_appmanifests


def test_inspect_parses_real_libraryfolders_and_appmanifest_shapes():
    read_text, list_appmanifests = fake_files()
    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Program Files (x86)\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    assert insight.program == "steam"
    by_name = {item.name: item.size_bytes for item in insight.items}
    assert by_name == {
        "Steamworks Common Redistributables": 498906162,
        "Counter-Strike 2": 73922650178,
    }


def test_inspect_is_none_when_steam_is_not_installed():
    insight = steam_inspector.inspect(
        steam_path=lambda: None,
        read_text=lambda path: None,
        list_appmanifests=lambda library: [],
    )
    assert insight is None


def test_inspect_falls_back_to_the_install_path_without_libraryfolders():
    """Si no se puede leer `libraryfolders.vdf`, al menos se mira la propia
    carpeta de instalación como una biblioteca."""

    def read_text(path):
        if path.endswith("appmanifest_228980.acf"):
            return APPMANIFEST_228980
        return None

    def list_appmanifests(library):
        if library == "C:\\Steam":
            return ["C:\\Steam\\steamapps\\appmanifest_228980.acf"]
        return []

    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    assert [item.name for item in insight.items] == [
        "Steamworks Common Redistributables"
    ]


def test_inspect_is_none_when_no_game_can_be_measured():
    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=lambda path: None,
        list_appmanifests=lambda library: [],
    )
    assert insight is None


def test_inspect_skips_a_manifest_missing_the_size():
    def read_text(path):
        return '"AppState"\n{\n\t"name"\t\t"Something"\n}\n'

    def list_appmanifests(library):
        return ["C:\\Steam\\steamapps\\appmanifest_1.acf"]

    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    assert insight is None


def test_inspect_skips_a_manifest_with_a_malformed_size():
    def read_text(path):
        return (
            '"AppState"\n{\n\t"name"\t\t"Something"\n\t"SizeOnDisk"\t\t"not-a-number"'
            "\n}\n"
        )

    def list_appmanifests(library):
        return ["C:\\Steam\\steamapps\\appmanifest_1.acf"]

    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    assert insight is None


def test_inspect_skips_a_manifest_with_a_zero_size():
    def read_text(path):
        return '"AppState"\n{\n\t"name"\t\t"Something"\n\t"SizeOnDisk"\t\t"0"\n}\n'

    def list_appmanifests(library):
        return ["C:\\Steam\\steamapps\\appmanifest_1.acf"]

    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    assert insight is None


def test_inspect_skips_a_manifest_that_cannot_be_read():
    def list_appmanifests(library):
        return ["C:\\Steam\\steamapps\\appmanifest_1.acf"]

    insight = steam_inspector.inspect(
        steam_path=lambda: "C:\\Steam",
        read_text=lambda path: None,
        list_appmanifests=list_appmanifests,
    )
    assert insight is None


def test_installed_games_keeps_the_library_of_each_game():
    read_text, list_appmanifests = fake_files()
    games = steam_inspector.installed_games(
        steam_path=lambda: "C:\\Program Files (x86)\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    by_name = {game.name: game.library for game in games}
    assert by_name == {
        "Steamworks Common Redistributables": "C:\\Program Files (x86)\\Steam",
        "Counter-Strike 2": "D:\\SteamLibrary",
    }


def test_installed_games_is_empty_when_steam_is_not_installed():
    games = steam_inspector.installed_games(
        steam_path=lambda: None,
        read_text=lambda path: None,
        list_appmanifests=lambda library: [],
    )
    assert games == []


def test_misplaced_games_finds_the_real_case(monkeypatch):
    read_text, list_appmanifests = fake_files()
    games = steam_inspector.installed_games(
        steam_path=lambda: "C:\\Program Files (x86)\\Steam",
        read_text=read_text,
        list_appmanifests=list_appmanifests,
    )
    found = steam_inspector.misplaced_games(
        games=lambda: games, system_drive=lambda: "C:"
    )
    assert [m.name for m in found] == ["Steamworks Common Redistributables"]
    assert found[0].other_drives == ("D:",)


def test_misplaced_games_is_empty_with_no_games():
    found = steam_inspector.misplaced_games(games=lambda: [], system_drive=lambda: "C:")
    assert found == []


def test_misplaced_games_is_empty_when_everything_is_on_one_drive():
    games = [
        steam_inspector.SteamGame(
            name="x", library="C:\\Program Files (x86)\\Steam", size_bytes=1
        )
    ]
    found = steam_inspector.misplaced_games(
        games=lambda: games, system_drive=lambda: "C:"
    )
    assert found == []
