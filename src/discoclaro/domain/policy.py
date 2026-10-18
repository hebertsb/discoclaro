"""Saneado de texto externo (T2.12): nombres de archivo, rutas y cualquier otro
texto que no se haya escrito a mano en este código. Nunca se interpreta como código:
esto solo decide qué caracteres puede mostrar cada destino.

`domain/formatting.py::safe_text()` ya cubre la terminal (quita caracteres de control,
saltos de línea y secuencias de escape); las funciones de aquí la reusan primero y
agregan lo que falta: un tope de largo y el escapado de HTML y de XML (las
notificaciones de Windows se arman con XML por dentro, aunque por una API que ya
escapa sola: ver `adapters/notifier.py`).
"""

from discoclaro.domain.formatting import safe_text

# Más largo que cualquier ruta real (hasta el límite clásico de Windows, 260,
# multiplicado varias veces) pero acotado: ni una terminal ni una notificación
# necesitan mostrar más que esto de un solo texto externo.
MAX_DISPLAY_LENGTH = 1024

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
