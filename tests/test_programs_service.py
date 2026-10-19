"""Pruebas del aviso de instalación (T2B.5) y de la búsqueda de restos (T2B.6):
siempre sobre `tmp_path` o entornos falsos.

`check_programs` abre y escribe una base de verdad en `tmp_path`, pero el
inventario (`read_programs`), las variables de entorno, la medición de carpetas
(`inspect`) y el aviso (`notify`) siempre se reemplazan por valores falsos: nunca
se toca el registro, el disco real ni se muestra un toast de verdad.
`find_leftovers` nunca abre una base; solo recibe `environment`/`inspect` falsos.
"""

import inspect as inspect_module
from datetime import UTC, datetime

import pytest

from discoclaro.adapters import notifier
from discoclaro.adapters.store import Store
from discoclaro.domain.footprint import FolderCandidate
from discoclaro.domain.models import Program
from discoclaro.platform import windows
from discoclaro.services.programs_service import (
    ProgramsCheckError,
    ProgramsCheckResult,
    check_programs,
    find_leftovers,
)

T0 = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


def make_program(**changes):
    fields = {
        "name": "Acme App",
        "version": "1.0",
        "publisher": "Acme",
        "install_location": "C:\\Program Files\\Acme",
        "install_date": "20261018",
        "estimated_size_bytes": None,
        "uninstall_string": None,
    }
    return Program(**{**fields, **changes})


def fake_notify():
    calls = []
    return calls, lambda title, message: calls.append((title, message))


def fake_inspect(sizes):
    """Un `inspect` falso: solo conoce el tamaño de las rutas que se le dan."""

    def inspect(path):
        if path not in sizes:
            return None
        return FolderCandidate(path=path, size_bytes=sizes[path], last_modified=None)

    return inspect


def test_the_first_check_saves_the_baseline_without_notifying(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls, notify = fake_notify()
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="A"), make_program(name="B")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    assert result == ProgramsCheckResult(installed=(), uninstalled=())
    assert calls == []
    with Store.open_readonly(data_dir / "discoclaro.db") as store:
        assert {p.name for p in store.active_programs()} == {"A", "B"}


def test_a_newly_installed_program_is_detected_and_notified(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Stable")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Stable"),
            make_program(name="Acme App"),
        ],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 2048}),
        notify=notify,
    )
    assert [p.name for p in result.installed] == ["Acme App"]
    assert result.uninstalled == ()
    assert calls == [
        (
            "Programa instalado",
            "Se instaló Acme App en C:\\Program Files\\Acme (2,0 KiB).",
        )
    ]


def test_nothing_is_notified_when_the_inventory_does_not_change(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls, notify = fake_notify()

    def programs():
        return [make_program(name="Stable")]

    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=programs,
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=programs,
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    assert result == ProgramsCheckResult(installed=(), uninstalled=())
    assert calls == []


def test_an_install_with_no_measurable_folder_notifies_unknown_size(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    calls, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Ghost", install_location=None),
        ],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    assert calls == [
        (
            "Programa instalado",
            "Se instaló Ghost, pero no se encontró ninguna carpeta suya para medir.",
        )
    ]


def test_the_footprint_of_a_newly_installed_program_is_saved(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App"),
        ],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 2048}),
        notify=notify,
    )
    with Store.open_readonly(data_dir / "discoclaro.db") as store:
        rows = store._connection.execute(
            "SELECT path, size_bytes, confidence, reason FROM program_footprints"
        ).fetchall()
    assert rows == [("C:\\Program Files\\Acme", 2048, "high", "install_location")]


def test_an_uninstalled_programs_known_footprint_still_existing_is_notified(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App"),
        ],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 2048}),
        notify=notify,
    )
    calls, notify = fake_notify()
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 1024}),
        notify=notify,
    )
    assert result.installed == ()
    [leftovers] = result.uninstalled
    assert leftovers.program.name == "Acme App"
    assert [f.path for f in leftovers.footprints] == ["C:\\Program Files\\Acme"]
    assert leftovers.footprints[0].size_bytes == 1024
    assert leftovers.footprints[0].confidence == "high"
    assert leftovers.footprints[0].reason == "known_footprint"
    assert calls == [
        (
            "Programa desinstalado",
            "Se desinstaló Acme App; quedaron 1,0 KiB en 1 carpetas. "
            "Corré «discoclaro leftovers» para ver el detalle.",
        )
    ]


def test_an_uninstalled_program_with_nothing_left_notifies_clean(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App", install_location=None),
        ],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    calls, notify = fake_notify()
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    [leftovers] = result.uninstalled
    assert leftovers.footprints == ()
    assert calls == [
        (
            "Programa desinstalado",
            "Se desinstaló Acme App y no quedó ningún resto de tamaño apreciable.",
        )
    ]


def test_a_known_footprint_path_that_no_longer_exists_is_dropped(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App"),
        ],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 2048}),
        notify=notify,
    )
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    [leftovers] = result.uninstalled
    assert leftovers.footprints == ()


def test_an_uninstalled_program_without_a_known_footprint_still_searches_by_name(
    tmp_path,
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    (local / "Acme").mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Acme App", publisher="Acme", install_location=None)
        ],
        environment=lambda: {"%LOCALAPPDATA%": str(local)},
        inspect=fake_inspect({}),
        notify=notify,
    )
    calls, notify = fake_notify()
    inspect = fake_inspect({str(local / "Acme"): 4096})
    result = check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [],
        environment=lambda: {"%LOCALAPPDATA%": str(local)},
        inspect=inspect,
        notify=notify,
    )
    [leftovers] = result.uninstalled
    assert [f.path for f in leftovers.footprints] == [str(local / "Acme")]
    assert leftovers.footprints[0].confidence == "medium"
    assert leftovers.footprints[0].reason == "name_match"


def test_uninstall_replaces_the_saved_footprint_with_the_fresh_result(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App"),
        ],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 2048}),
        notify=notify,
    )
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=dict,
        inspect=fake_inspect({"C:\\Program Files\\Acme": 1024}),
        notify=notify,
    )
    with Store.open_readonly(data_dir / "discoclaro.db") as store:
        saved = store.footprints_of_program("Acme App")
    assert [f.size_bytes for f in saved] == [1024]


def test_a_related_folder_in_a_search_root_is_measured_and_reported(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    (local / "Acme").mkdir()
    (local / "Unrelated").mkdir()
    inspected = []

    def inspect(path):
        inspected.append(path)
        sizes = {"C:\\Program Files\\Acme": 2048, str(local / "Acme"): 512}
        if path not in sizes:
            return None
        return FolderCandidate(path=path, size_bytes=sizes[path], last_modified=None)

    _, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [make_program(name="Placeholder")],
        environment=lambda: {"%LOCALAPPDATA%": str(local)},
        inspect=inspect,
        notify=notify,
    )
    calls, notify = fake_notify()
    check_programs(
        data_dir,
        now=lambda: T0,
        read_programs=lambda: [
            make_program(name="Placeholder"),
            make_program(name="Acme App", publisher="Acme"),
        ],
        environment=lambda: {"%LOCALAPPDATA%": str(local)},
        inspect=inspect,
        notify=notify,
    )
    assert str(local / "Unrelated") not in inspected
    assert str(local / "Acme") in inspected
    assert calls == [
        (
            "Programa instalado",
            "Se instaló Acme App en C:\\Program Files\\Acme (2,0 KiB) "
            "y 512 B más en otras 1 carpetas.",
        )
    ]


def test_cannot_open_database_raises_programs_check_error(tmp_path):
    _, notify = fake_notify()
    with pytest.raises(ProgramsCheckError) as error:
        check_programs(tmp_path / "missing", read_programs=lambda: [], notify=notify)
    assert error.value.kind == "cannot_open_database"


def test_the_real_adapters_are_the_defaults_without_calling_them():
    parameters = inspect_module.signature(check_programs).parameters
    assert parameters["read_programs"].default is windows.installed_programs
    assert parameters["environment"].default is windows.environment_variables
    assert parameters["notify"].default is notifier.send_notification


def test_find_leftovers_with_an_empty_name_raises(tmp_path):
    with pytest.raises(ProgramsCheckError) as error:
        find_leftovers("", environment=dict, inspect=fake_inspect({}))
    assert error.value.kind == "leftovers_empty_name"


def test_find_leftovers_matches_a_related_folder_by_name(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "Buzz").mkdir()
    (local / "Unrelated").mkdir()
    inspect = fake_inspect({str(local / "Buzz"): 4096})
    found = find_leftovers(
        "Buzz", environment=lambda: {"%LOCALAPPDATA%": str(local)}, inspect=inspect
    )
    assert [f.path for f in found] == [str(local / "Buzz")]
    assert found[0].confidence == "medium"
    assert found[0].reason == "name_match"


def test_find_leftovers_returns_nothing_without_inventing_when_nothing_matches(
    tmp_path,
):
    local = tmp_path / "local"
    local.mkdir()
    (local / "Unrelated").mkdir()
    found = find_leftovers(
        "Buzz",
        environment=lambda: {"%LOCALAPPDATA%": str(local)},
        inspect=fake_inspect({}),
    )
    assert found == []


def test_find_leftovers_sorts_the_biggest_folder_first(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "Buzz").mkdir()
    (local / "Buzz Helper").mkdir()
    inspect = fake_inspect({str(local / "Buzz"): 100, str(local / "Buzz Helper"): 9999})
    found = find_leftovers(
        "Buzz", environment=lambda: {"%LOCALAPPDATA%": str(local)}, inspect=inspect
    )
    assert [f.size_bytes for f in found] == [9999, 100]


def test_find_leftovers_never_matches_install_location_or_near_install_date(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "Buzz").mkdir()
    inspect = fake_inspect({str(local / "Buzz"): 1})
    found = find_leftovers(
        "Buzz", environment=lambda: {"%LOCALAPPDATA%": str(local)}, inspect=inspect
    )
    assert {f.reason for f in found} <= {"name_match", "partial_name_match"}


def test_the_real_adapters_are_the_defaults_for_find_leftovers_too():
    parameters = inspect_module.signature(find_leftovers).parameters
    assert parameters["environment"].default is windows.environment_variables
