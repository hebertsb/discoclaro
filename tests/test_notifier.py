"""Pruebas del adaptador de notificaciones: con un `toaster_factory` falso.

Nunca muestra un toast real en estas pruebas (eso se comprobó a mano, una vez, con la
librería real).
"""

import inspect

from windows_toasts import WindowsToaster

from discoclaro.adapters.notifier import send_notification


class FakeToaster:
    def __init__(self, application):
        self.application = application
        self.shown = []

    def show_toast(self, toast):
        self.shown.append(toast)


class FailingToaster:
    def __init__(self, application):
        raise RuntimeError("no hay soporte de notificaciones en este equipo")


def test_send_notification_creates_the_toaster_with_the_application_name():
    toasters = []

    def factory(application):
        toaster = FakeToaster(application)
        toasters.append(toaster)
        return toaster

    send_notification(
        "título", "mensaje", application="discoclaro", toaster_factory=factory
    )
    assert toasters[0].application == "discoclaro"


def test_send_notification_shows_the_title_and_the_message():
    toaster = FakeToaster("discoclaro")
    send_notification("título", "mensaje", toaster_factory=lambda app: toaster)
    (shown,) = toaster.shown
    assert list(shown.text_fields) == ["título", "mensaje"]


def test_send_notification_returns_true_when_it_was_shown():
    toaster = FakeToaster("discoclaro")
    assert send_notification("t", "m", toaster_factory=lambda app: toaster) is True


def test_send_notification_returns_false_when_the_backend_fails():
    assert send_notification("t", "m", toaster_factory=FailingToaster) is False


def test_send_notification_never_raises_when_the_backend_fails():
    send_notification("t", "m", toaster_factory=FailingToaster)


def test_the_real_windows_toaster_is_the_default_without_calling_it():
    parameters = inspect.signature(send_notification).parameters
    assert parameters["toaster_factory"].default is WindowsToaster


def test_the_application_name_defaults_to_discoclaro():
    parameters = inspect.signature(send_notification).parameters
    assert parameters["application"].default == "discoclaro"
