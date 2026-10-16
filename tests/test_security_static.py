"""Prueba de seguridad estática: el código de `src/` evita patrones peligrosos.

Complementa a `ruff` (reglas `S`). Lee el código con `ast`, sin ejecutarlo, y falla
ante SQL armado con texto, shell activado en una llamada, `eval`/`exec`, `os.system`,
`os.popen`, `yaml.load`, `pickle` y afines, y librerías de red (el proyecto no hace
llamadas a internet).

Una línea concreta se acepta a conciencia con el comentario `security: ok <motivo>`
en esa misma línea; el motivo es obligatorio.

Los ejemplos peligrosos de este archivo van partidos con una marca que `plain()` quita
al ejecutar, así el archivo no contiene código peligroso completo (un antivirus ya
marcó una vez un archivo de pruebas por eso).
"""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MARK = "§"

BANNED_CALLS = frozenset(
    {
        "eval",
        "exec",
        "builtins.eval",
        "builtins.exec",
        "os.system",
        "os.popen",
        "yaml.load",
    }
)
BANNED_MODULES = {
    "pickle": "deserialización insegura",
    "_pickle": "deserialización insegura",
    "cPickle": "deserialización insegura",
    "shelve": "deserialización insegura",
    "requests": "llamadas a internet",
    "httpx": "llamadas a internet",
    "aiohttp": "llamadas a internet",
    "urllib3": "llamadas a internet",
    "urllib.request": "llamadas a internet",
    "http.client": "llamadas a internet",
    "ftplib": "llamadas a internet",
    "smtplib": "llamadas a internet",
    "telnetlib": "llamadas a internet",
    "xmlrpc.client": "llamadas a internet",
}
SQL_METHODS = ("execute", "executemany")
SQL_START = re.compile(
    r"\s*(select|insert|update|delete|drop|create|alter|pragma)\s", re.IGNORECASE
)
ACCEPTED = re.compile(r"security:\s*ok\s+\S", re.IGNORECASE)


def plain(snippet: str) -> str:
    """Quita la marca que parte los ejemplos peligrosos en este archivo."""
    return snippet.replace(MARK, "")


def _dotted(node: ast.AST) -> str:
    """Nombre con puntos de `a.b.c`, o vacío si no es solo nombres y atributos."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _banned_module_reason(module: str) -> str | None:
    for name, reason in BANNED_MODULES.items():
        if module == name or module.startswith(f"{name}."):
            return reason
    return None


def _imported_modules(node: ast.AST) -> list[str]:
    """Módulos que trae un `import`; `from a import b` también cuenta `a.b`."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    return []


def _is_constant_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_constant_text(node.left) and _is_constant_text(node.right)
    return False


def _is_dynamic_text(node: ast.AST) -> bool:
    """True si el nodo arma un texto con datos: f-string, `%`, `+` o `.format()`."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(part, ast.FormattedValue) for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return not _is_constant_text(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and bool(node.args or node.keywords)
    return False


def _leading_text(node: ast.AST) -> str:
    """Primer pedazo literal de un texto armado, para ver si empieza como SQL."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        return _leading_text(node.values[0])
    if isinstance(node, ast.BinOp):
        return _leading_text(node.left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _leading_text(node.func.value)
    return ""


def _is_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def find_issues(source: str) -> list[tuple[int, str]]:
    """Devuelve (línea, problema) por cada patrón peligroso que haya en `source`."""
    lines = source.splitlines()
    found: set[tuple[int, str]] = set()

    def report(node: ast.AST, message: str) -> None:
        if not ACCEPTED.search(lines[node.lineno - 1]):
            found.add((node.lineno, message))

    for node in ast.walk(ast.parse(source)):
        for module in _imported_modules(node):
            reason = _banned_module_reason(module)
            if reason:
                report(node, f"importa {module} ({reason})")
            if module in BANNED_CALLS:
                report(node, f"importa {module}")
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in BANNED_CALLS:
                report(node, f"llama a {name}")
            for keyword in node.keywords:
                if keyword.arg == "shell" and not _is_false(keyword.value):
                    report(keyword.value, "shell activado en la llamada")
            method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            first = node.args[0] if node.args else None
            if method in SQL_METHODS and first and _is_dynamic_text(first):
                report(node, f"SQL armado con texto en {method}")
            if method == "executescript" and first and not _is_constant_text(first):
                report(node, "script SQL que no es un literal")
        if _is_dynamic_text(node) and SQL_START.match(_leading_text(node)):
            report(node, "SQL armado con texto")
    return sorted(found)


def scan_source_tree(src_root: Path) -> list[str]:
    """Revisa todos los .py de `discoclaro/` bajo `src_root` y junta los problemas."""
    problems = []
    for path in sorted((src_root / "discoclaro").rglob("*.py")):
        relative = path.relative_to(src_root).as_posix()
        for lineno, message in find_issues(path.read_text(encoding="utf-8")):
            problems.append(f"{relative}:{lineno}: {message}")
    return problems


SAFE_SOURCES = [
    'cur.execute("SELECT a FROM t WHERE b = ?", (x,))',
    'SQL = "SELECT a FROM t"\ncur.execute(SQL)',
    'cur.exec§utescript("CRE§ATE TABLE t (a INTEGER);")',
    'sql = "SELECT a " + "FROM t"',
    'msg = f"Updating {name}"',
    "import subprocess\nsubprocess.run(['a'], shell=False, timeout=5)",
    "import re, json, sqlite3\nimport urllib.parse",
    "import os\nos.path.join(a, b)",
    "import yaml\nyaml.safe_load(text)",
    "value = {'ev§al': 1}\nx.evaluate(y)",
]

UNSAFE_SOURCES = [
    "ev§al('1')",
    "ex§ec('x = 1')",
    "import pickle",
    "from pickle import loads",
    "import _pickle",
    "import shelve",
    "import os\nos.sys§tem('dir')",
    "import os\nos.po§pen('dir')",
    "from os import system",
    "import yaml\nyaml.lo§ad(text)",
    "import subprocess\nsubprocess.run(['a'], shell§=True)",
    "import subprocess\nsubprocess.run(['a'], shell§=flag)",
    "import requests",
    "import httpx",
    "import urllib.request",
    "from urllib import request",
    "import urllib3",
    "import http.client",
    "import smtplib",
    "import ftplib",
    'cur.exe§cute(f"SEL§ECT * FROM t WHERE a = {x}")',
    'cur.exe§cute("SEL§ECT * FROM t WHERE a = " + x)',
    'cur.exe§cute("SEL§ECT * FROM t WHERE a = %s" % x)',
    'cur.exe§cute("SEL§ECT * FROM {}".format(t))',
    'query = f"DEL§ETE FROM nodes WHERE path = {p}"',
    'query = "SEL§ECT a FROM t WHERE b = " + name',
    'cur.exe§cute(f"{query}")',
    "cur.exe§cutemany(sql + more, rows)",
    'conn.exe§cute("PRA§GMA user_version = " + str(v))',
    "cur.exec§utescript(script)",
    'cur.exec§utescript(f"CRE§ATE TABLE {name} (a)")',
]


def test_the_test_looks_at_real_source_files():
    assert any((SRC / "discoclaro").rglob("*.py"))


def test_real_source_tree_has_no_unsafe_patterns():
    assert scan_source_tree(SRC) == []


@pytest.mark.parametrize("source", SAFE_SOURCES)
def test_safe_code_passes(source):
    assert find_issues(plain(source)) == []


@pytest.mark.parametrize("source", UNSAFE_SOURCES)
def test_unsafe_code_is_reported(source):
    assert find_issues(plain(source))


def test_a_line_with_a_reason_is_accepted():
    source = "import requests  # security: ok servidor falso solo en pruebas"
    assert find_issues(source) == []


def test_acceptance_needs_a_reason_and_must_be_on_the_same_line():
    assert find_issues("import requests  # security: ok")
    assert find_issues("# security: ok motivo\nimport requests")


def test_report_has_the_line_number_of_the_problem():
    source = plain("import re\n\nev§al('1')\n")
    assert find_issues(source) == [(3, "llama a eval")]


def test_scan_finds_problems_in_a_synthetic_tree(tmp_path):
    package = tmp_path / "discoclaro" / "sub"
    package.mkdir(parents=True)
    (package / "ok.py").write_text("import re\n", encoding="utf-8")
    (package / "bad.py").write_text(plain("x = 1\nex§ec('y')\n"), encoding="utf-8")
    assert scan_source_tree(tmp_path) == [
        "discoclaro/sub/bad.py:2: llama a exec",
    ]
