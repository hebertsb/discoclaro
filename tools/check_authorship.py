"""Revisa que nada de lo que se publica nombre a una herramienta de IA como autora.

Regla del proyecto: el repositorio debe verse escrito por su autor. Este módulo
reconoce los nombres de herramientas, las líneas de coautoría y las frases que
atribuyen el trabajo a una IA. Hablar de modelos de IA como tema del producto no
se marca.

Los patrones se arman por partes para que este archivo no contenga las palabras
que busca y pase su propia revisión.

Como hook de Git (`commit-msg`): `python tools/check_authorship.py --message-file RUTA`.
Se activa una vez por clon con `git config core.hooksPath .githooks`.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

_NAMES = ("cla" + "ude", "anthro" + "pic")
_TRAILER = "co-auth" + "ored-by"
_AI = r"(?:ia|ai|inteligencia\s+artificial|llm)"

PATTERNS = (
    re.compile("|".join((*_NAMES, _TRAILER)), re.IGNORECASE),
    re.compile(
        r"\b(?:generad[oa]|asistid[oa]|escrit[oa]|redactad[oa])\s+(?:con|por)\s+"
        rf"(?:la\s+|una\s+)?{_AI}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:generated|assisted|written|co-?authored)\s+(?:with|by)\s+"
        rf"(?:an?\s+)?{_AI}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bai[- ](?:assisted|generated)\b", re.IGNORECASE),
)


def find_mentions(text: str) -> list[str]:
    """Fragmentos de `text` que nombran a una herramienta de IA como autora."""
    found: list[str] = []
    for pattern in PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return found


def strip_git_comments(message: str) -> str:
    """Quita las líneas que empiezan con `#`, que git descarta del mensaje."""
    return "\n".join(line for line in message.splitlines() if not line.startswith("#"))


def main(argv: Sequence[str] | None = None) -> int:
    """Hook `commit-msg`: devuelve 1 si el mensaje nombra una herramienta de IA."""
    parser = argparse.ArgumentParser(description="Revisa el mensaje de un commit.")
    parser.add_argument("--message-file", required=True, type=Path)
    args = parser.parse_args(argv)
    raw = args.message_file.read_text(encoding="utf-8", errors="replace")
    found = find_mentions(strip_git_comments(raw))
    if not found:
        return 0
    print(
        "El mensaje del commit nombra a una herramienta de IA como autora:",
        file=sys.stderr,
    )
    for fragment in sorted(set(found)):
        print(f"  - {fragment}", file=sys.stderr)
    print(
        "Quítalo y vuelve a intentarlo: "
        "el repositorio debe verse escrito por su autor.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
