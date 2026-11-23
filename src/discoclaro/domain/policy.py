"""Saneado de texto externo (T2.12): nombres de archivo, rutas y cualquier otro
texto que no se haya escrito a mano en este código. Nunca se interpreta como código:
esto solo decide qué caracteres puede mostrar cada destino.

`domain/formatting.py::safe_text()` ya cubre la terminal (quita caracteres de control,
saltos de línea y secuencias de escape); las funciones de aquí la reusan primero y
agregan lo que falta: un tope de largo y el escapado de HTML y de XML (las
notificaciones de Windows se arman con XML por dentro, aunque por una API que ya
escapa sola: ver `adapters/notifier.py`).
"""

import ntpath

from discoclaro.domain.formatting import safe_text

# Más largo que cualquier ruta real (hasta el límite clásico de Windows, 260,
# multiplicado varias veces) pero acotado: ni una terminal ni una notificación
# necesitan mostrar más que esto de un solo texto externo.
MAX_DISPLAY_LENGTH = 1024

# Nombres de dispositivo reservados en Windows que nunca pueden ser un destino
# válido de borrado o movimiento (sección 8.2, T4.8).
RESERVED_DEVICE_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

# Carpetas que nunca se pueden borrar enteras desde la raíz de un disco.
PROTECTED_ROOT_FOLDERS = frozenset(
    {
        "windows",
        "program files",
        "program files (x86)",
        "programdata",
        "system volume information",
        "$recycle.bin",
        "recovery",
        "users",
    }
)

# El orden importa: el `&` se escapa primero, antes que cualquier otro carácter, para
# no volver a escapar las entidades que ya se acaban de crear (`&lt;` no se convierte
# después en `&amp;lt;`).
_HTML_ENTITIES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)
_XML_ENTITIES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&apos;"),
)


def limit_length(text: str, max_length: int = MAX_DISPLAY_LENGTH) -> str:
    """Corta `text` a lo sumo a `max_length` caracteres.

    Para no partir una entidad por la mitad (`&am` no es HTML ni XML válido), esto se
    llama ANTES de escapar, nunca después: `escape_html(limit_length(texto))`.
    """
    if max_length < 0:
        raise ValueError("max_length cannot be negative")
    return text[:max_length]


def escape_html(text: str) -> str:
    """Texto seguro para insertar dentro de HTML: nunca se interpreta como etiqueta
    ni como atributo. Antes, le quita lo mismo que `safe_text()` le quita a la
    terminal (control, saltos de línea, secuencias de escape)."""
    return _escape(safe_text(text), _HTML_ENTITIES)


def escape_xml(text: str) -> str:
    """Texto seguro para insertar dentro de XML armado a mano; igual que
    `escape_html`, pero la comilla simple se escapa como entidad XML (`&apos;`), no
    como la de HTML (`&#39;`)."""
    return _escape(safe_text(text), _XML_ENTITIES)


def _escape(text: str, entities: tuple[tuple[str, str], ...]) -> str:
    for character, entity in entities:
        text = text.replace(character, entity)
    return text


def is_safe_deletion_path(
    path: str,
    *,
    user_profile: str | None = None,
    app_data_dirs: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Verifica (función pura, sin disco) si `path` es una ruta candidata válida
    para enviar a la Papelera o limpiar (T4.8, sección 8.2).

    Rechaza rutas vacías, relativas, UNC (`\\\\servidor\\...`), prefijos `\\\\?\\`,
    segmentos `..`, nombres de dispositivo (`CON`, `NUL`, `COM1`…), raíces de
    disco (`C:\\`), carpetas de sistema (`C:\\Windows`, `C:\\Program Files`…), la
    raíz del perfil del usuario y las carpetas propias de discoclaro.
    Devuelve `(True, "ok")` o `(False, motivo)`.
    """
    if not path or not path.strip():
        return False, "empty_path"
    if path != path.rstrip(" ."):
        return False, "trailing_dot_or_space"
    if path.startswith("\\\\"):
        return False, "unc_or_device_prefix"
    drive, tail = ntpath.splitdrive(path)
    if not drive or len(drive) != 2 or not drive[0].isalpha() or drive[1] != ":":
        return False, "not_drive_path"
    if not tail.startswith(("\\", "/")):
        return False, "relative_path"

    raw_segments = [s for s in tail.replace("/", "\\").split("\\") if s]
    if not raw_segments:
        return False, "drive_root"
    for seg in raw_segments:
        if seg in (".", ".."):
            return False, "parent_traversal"
        stem = seg.split(".")[0].casefold()
        if stem in RESERVED_DEVICE_NAMES:
            return False, "reserved_device"

    normalized = ntpath.normpath(path).rstrip("\\/").casefold()
    top = raw_segments[0].casefold()
    if len(raw_segments) == 1 and top in PROTECTED_ROOT_FOLDERS:
        return False, "protected_system_root"
    if top in ("windows", "system volume information", "$recycle.bin"):
        # Dentro de Windows solo `C:\Windows\Temp\*` se permite en tareas fijas;
        # `Windows`, `System32` o `WinSxS` nunca son restos de un programa.
        is_windows_temp = (
            top == "windows"
            and len(raw_segments) >= 2
            and raw_segments[1].casefold() == "temp"
        )
        if not is_windows_temp:
            return False, "protected_system_folder"

    if user_profile:
        prof_norm = ntpath.normpath(user_profile).rstrip("\\/").casefold()
        if normalized == prof_norm:
            return False, "user_profile_root"

    for app_dir in app_data_dirs:
        if not app_dir:
            continue
        app_norm = ntpath.normpath(app_dir).rstrip("\\/").casefold()
        if normalized == app_norm or normalized.startswith(app_norm + "\\"):
            return False, "discoclaro_own_data"

    return True, "ok"

