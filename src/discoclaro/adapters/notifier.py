"""Notificaciones de Windows (T2.7), con la librería `windows-toasts` (aprobada).

Solo muestra el texto que ya armó quien llama (título y mensaje, ya traducidos y
saneados); nunca decide si hay que avisar ni interpreta lo que muestra. Si Windows no
puede mostrar el aviso (sin soporte, Centro de notificaciones desactivado, entorno sin
`explorer.exe`), se registra y se sigue: que no se vea una notificación no debe tumbar
el resto del chequeo.
"""

import logging
from collections.abc import Callable

from windows_toasts import Toast, WindowsToaster

logger = logging.getLogger(__name__)

APP_NAME = "discoclaro"

ToasterFactory = Callable[[str], WindowsToaster]


def send_notification(
    title: str,
    message: str,
    *,
    application: str = APP_NAME,
    toaster_factory: ToasterFactory = WindowsToaster,
) -> bool:
    """Muestra un toast de Windows con `title` y `message`.

    Devuelve si se pudo mostrar. `toaster_factory` se puede inyectar para probar sin
    tocar Windows.
    """
    try:
        toaster = toaster_factory(application)
        toaster.show_toast(Toast([title, message]))
    except Exception as error:
        logger.warning("notification not shown: %r", type(error).__name__)
        return False
    return True
