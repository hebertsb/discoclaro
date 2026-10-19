"""Pruebas del servicio de programación (T2.8, T2.13): nunca tocan el Programador de
tareas real ni el disco; `create`/`delete`/`get_command` siempre se reemplazan.
"""

import inspect
import sys

import pytest

from discoclaro.domain.models import Volume
from discoclaro.platform import windows
from discoclaro.services import schedule_service
from discoclaro.services.schedule_service import (
    CHECK_TASK_NAME,
    PROGRAMS_HOURLY_TASK_NAME,
    PROGRAMS_ONEVENT_TASK_NAME,
    SCAN_TASK_NAME,
    ScheduleError,
    ScheduleStatus,
    TaskStatus,
    install,
    remove,
    status,
)

PYTHON = "C:\\venv\\python.exe"

VOLUMES = [Volume("C:", "C:\\", 1000, 400, 600), Volume("D:", "D:\\", 500, 100, 400)]


def fake_volumes():
    return list(VOLUMES)


def fake_create():
    calls = []
    return calls, lambda name, command, **kwargs: calls.append((name, command, kwargs))


def test_install_creates_the_scan_task_with_the_resolved_volume_name():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    scan_call = next(call for call in calls if call[0] == SCAN_TASK_NAME)
    assert scan_call[1][-2:] == ["scan", "C:"]


def test_install_creates_the_check_task_with_no_target():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    check_call = next(call for call in calls if call[0] == CHECK_TASK_NAME)
    assert check_call[1][-1] == "check"


def test_install_uses_the_given_python_and_the_module_form():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create, python="C:\\py\\python.exe")
    for _, command, _ in calls:
        assert command[:3] == ["C:\\py\\python.exe", "-m", "discoclaro"]


def test_install_resolves_a_folder_target_to_its_full_path():
    calls, create = fake_create()
    install("C:\\Users\\x", volumes=fake_volumes, create=create)
    scan_call = next(call for call in calls if call[0] == SCAN_TASK_NAME)
    assert scan_call[1][-1] == "C:\\Users\\x"


def test_install_passes_the_default_schedule_and_time_to_scan_and_check():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    for name, _, kwargs in calls:
        if name in (SCAN_TASK_NAME, CHECK_TASK_NAME):
            assert kwargs == {
                "schedule": schedule_service.DEFAULT_SCHEDULE,
                "start_time": schedule_service.DEFAULT_START_TIME,
            }


def test_install_creates_the_programs_hourly_task():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    call = next(call for call in calls if call[0] == PROGRAMS_HOURLY_TASK_NAME)
    assert call[1][-1] == "programs-check"
    assert call[2] == {"schedule": "HOURLY"}


def test_install_creates_the_programs_onevent_task_for_msi_installs():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    call = next(call for call in calls if call[0] == PROGRAMS_ONEVENT_TASK_NAME)
    assert call[1][-1] == "programs-check"
    assert call[2] == {
        "schedule": "ONEVENT",
        "event_channel": schedule_service.MSI_INSTALL_EVENT_CHANNEL,
        "event_query": schedule_service.MSI_INSTALL_EVENT_QUERY,
    }


def test_install_raises_for_a_target_not_on_a_fixed_disk():
    _, create = fake_create()
    with pytest.raises(ScheduleError) as error:
        install("Z:\\x", volumes=fake_volumes, create=create)
    assert error.value.kind == "not_fixed_disk"


def test_install_wraps_a_schtasks_failure():
    def failing_create(name, command, **kwargs):
        raise windows.ScheduledTaskError(1, "access denied")

    with pytest.raises(ScheduleError) as error:
        install("C:", volumes=fake_volumes, create=failing_create)
    assert error.value.kind == "schedule_failed"


def test_remove_calls_delete_for_every_task_name():
    calls = []
    remove(delete=lambda name: calls.append(name) or True)
    assert calls == [
        SCAN_TASK_NAME,
        CHECK_TASK_NAME,
        PROGRAMS_HOURLY_TASK_NAME,
        PROGRAMS_ONEVENT_TASK_NAME,
    ]


def test_remove_returns_true_when_either_task_existed():
    assert remove(delete=lambda name: name == CHECK_TASK_NAME) is True


def test_remove_returns_false_when_neither_task_existed():
    assert remove(delete=lambda name: False) is False


def fake_get_command(registered):
    """`registered` es {nombre: (comando, argumentos)}; lo demás es "no instalada"."""
    return lambda name: registered.get(name)


def test_status_with_nothing_installed():
    assert status(python=PYTHON, get_command=fake_get_command({})) == ScheduleStatus(
        scan=TaskStatus(installed=False),
        check=TaskStatus(installed=False),
        programs_hourly=TaskStatus(installed=False),
        programs_onevent=TaskStatus(installed=False),
    )


def test_status_reports_each_task_installed_and_matching():
    programs_check_args = "-m discoclaro programs-check"
    registered = {
        SCAN_TASK_NAME: (PYTHON, "-m discoclaro scan C:"),
        CHECK_TASK_NAME: (PYTHON, "-m discoclaro check"),
        PROGRAMS_HOURLY_TASK_NAME: (PYTHON, programs_check_args),
        PROGRAMS_ONEVENT_TASK_NAME: (PYTHON, programs_check_args),
    }
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result == ScheduleStatus(
        scan=TaskStatus(installed=True, altered=False),
        check=TaskStatus(installed=True, altered=False),
        programs_hourly=TaskStatus(installed=True, altered=False),
        programs_onevent=TaskStatus(installed=True, altered=False),
    )


def test_status_programs_check_is_altered_with_extra_arguments():
    registered = {
        PROGRAMS_HOURLY_TASK_NAME: (PYTHON, "-m discoclaro programs-check --extra")
    }
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.programs_hourly == TaskStatus(installed=True, altered=True)


def test_status_reports_only_one_task_installed():
    registered = {SCAN_TASK_NAME: (PYTHON, "-m discoclaro scan C:")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.scan.installed is True
    assert result.check.installed is False


def test_status_scan_accepts_any_single_target():
    for target in ("D:", "C:\\Users\\x", '"C:\\My Folder"'):
        registered = {SCAN_TASK_NAME: (PYTHON, f"-m discoclaro scan {target}")}
        result = status(python=PYTHON, get_command=fake_get_command(registered))
        assert result.scan == TaskStatus(installed=True, altered=False)


def test_status_scan_is_altered_when_the_executable_differs():
    registered = {SCAN_TASK_NAME: ("C:\\evil.exe", "-m discoclaro scan C:")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.scan == TaskStatus(installed=True, altered=True)


def test_status_scan_is_altered_with_extra_text_after_the_target():
    registered = {SCAN_TASK_NAME: (PYTHON, "-m discoclaro scan C: & del *")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.scan == TaskStatus(installed=True, altered=True)


def test_status_scan_is_altered_with_no_target_at_all():
    registered = {SCAN_TASK_NAME: (PYTHON, "-m discoclaro scan")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.scan == TaskStatus(installed=True, altered=True)


def test_status_scan_is_altered_pointing_at_another_module():
    registered = {SCAN_TASK_NAME: (PYTHON, "-m otherpackage scan C:")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.scan == TaskStatus(installed=True, altered=True)


def test_status_check_must_match_exactly_with_no_extra_arguments():
    registered = {CHECK_TASK_NAME: (PYTHON, "-m discoclaro check --extra")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.check == TaskStatus(installed=True, altered=True)


def test_status_check_is_altered_when_the_executable_differs():
    registered = {CHECK_TASK_NAME: ("C:\\evil.exe", "-m discoclaro check")}
    result = status(python=PYTHON, get_command=fake_get_command(registered))
    assert result.check == TaskStatus(installed=True, altered=True)


def test_the_real_adapters_are_the_defaults_without_calling_them():
    assert inspect.signature(install).parameters["python"].default is sys.executable
    assert (
        inspect.signature(install).parameters["create"].default
        is windows.create_scheduled_task
    )
    assert (
        inspect.signature(remove).parameters["delete"].default
        is windows.remove_scheduled_task
    )
    assert inspect.signature(status).parameters["python"].default is sys.executable
    assert (
        inspect.signature(status).parameters["get_command"].default
        is windows.scheduled_task_command
    )
