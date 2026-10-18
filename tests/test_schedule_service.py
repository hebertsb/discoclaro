"""Pruebas del servicio de programación (T2.8): nunca tocan el Programador de
tareas real ni el disco; `create`/`delete`/`exists` siempre se reemplazan.
"""

import inspect
import sys

import pytest

from discoclaro.domain.models import Volume
from discoclaro.platform import windows
from discoclaro.services import schedule_service
from discoclaro.services.schedule_service import (
    CHECK_TASK_NAME,
    SCAN_TASK_NAME,
    ScheduleError,
    ScheduleStatus,
    install,
    remove,
    status,
)

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


def test_install_passes_the_default_schedule_and_time():
    calls, create = fake_create()
    install("C:", volumes=fake_volumes, create=create)
    for _, _, kwargs in calls:
        assert kwargs == {
            "schedule": schedule_service.DEFAULT_SCHEDULE,
            "start_time": schedule_service.DEFAULT_START_TIME,
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


def test_remove_calls_delete_for_both_task_names():
    calls = []
    remove(delete=lambda name: calls.append(name) or True)
    assert calls == [SCAN_TASK_NAME, CHECK_TASK_NAME]


def test_remove_returns_true_when_either_task_existed():
    assert remove(delete=lambda name: name == CHECK_TASK_NAME) is True


def test_remove_returns_false_when_neither_task_existed():
    assert remove(delete=lambda name: False) is False


def test_status_reports_each_task_separately():
    installed = {SCAN_TASK_NAME}
    result = status(exists=lambda name: name in installed)
    assert result == ScheduleStatus(scan_installed=True, check_installed=False)


def test_status_with_nothing_installed():
    assert status(exists=lambda name: False) == ScheduleStatus(
        scan_installed=False, check_installed=False
    )


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
    assert (
        inspect.signature(status).parameters["exists"].default
        is windows.scheduled_task_exists
    )
