"""Pruebas del aviso de instalación (T2B.5): siempre sobre `tmp_path`.

`check_programs` abre y escribe una base de verdad en `tmp_path`, pero el
inventario (`read_programs`), las variables de entorno, la medición de carpetas
(`inspect`) y el aviso (`notify`) siempre se reemplazan por valores falsos: nunca
se toca el registro, el disco real ni se muestra un toast de verdad.
"""

import inspect as inspect_module
from datetime import UTC, datetime

import pytest

from discoclaro.adapters import notifier
from discoclaro.adapters.store import Store
from discoclaro.domain.footprint import FolderCandidate
from discoclaro.domain.models import Program
from discoclaro.platform import windows
from discoclaro.services.programs_service import ProgramsCheckError, check_programs

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
    assert result == ()
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
    assert [p.name for p in result] == ["Acme App"]
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
    assert result == ()
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
