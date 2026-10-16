"""Prueba de arquitectura: `domain/` no toca el exterior ni importa otras capas.

Lee los `import` con `ast`, sin ejecutar el código: los comentarios y los textos
no dan falsos positivos y los imports relativos se resuelven a su ruta real.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN = (
    "os",
    "sqlite3",
    "shutil",
    "subprocess",
    "socket",
    "discoclaro.services",
    "discoclaro.adapters",
    "discoclaro.interfaces",
    "discoclaro.platform",
)
DYNAMIC_IMPORTS = ("__import__", "import_module")


def _is_forbidden(module: str) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in FORBIDDEN)


def _imported_modules(node: ast.AST, package: str) -> list[str]:
    """Módulos que importa un nodo `import`, con los relativos ya resueltos."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    parts = package.split(".")
    base = parts[: max(len(parts) - (node.level - 1), 0)] if node.level else []
    module = ".".join([*base, *([node.module] if node.module else [])])
    # `from a import b` puede traer el submódulo `a.b`: se revisan ambos.
    submodules = [f"{module}.{alias.name}".lstrip(".") for alias in node.names]
    return [module, *submodules]


def _is_dynamic_import(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name in DYNAMIC_IMPORTS


def find_violations(source: str, package: str) -> list[str]:
    """Una línea por cada import prohibido de `source`, módulo de `package`."""
    violations = []
    for node in ast.walk(ast.parse(source)):
        forbidden = [m for m in _imported_modules(node, package) if _is_forbidden(m)]
        if forbidden:
            violations.append(f"line {node.lineno}: imports {', '.join(forbidden)}")
        if _is_dynamic_import(node):
            violations.append(f"line {node.lineno}: dynamic import")
    return violations


def scan_domain(src_root: Path) -> list[str]:
    """Revisa todos los .py de `domain/` bajo `src_root` y junta las infracciones."""
    problems = []
    for path in sorted((src_root / "discoclaro" / "domain").rglob("*.py")):
        relative = path.relative_to(src_root)
        package = ".".join(relative.parent.parts)
        source = path.read_text(encoding="utf-8")
        for violation in find_violations(source, package):
            problems.append(f"{relative.as_posix()}: {violation}")
    return problems


ALLOWED = [
    "from dataclasses import dataclass\nimport re\n",
    "from discoclaro.domain import models",
    "from . import models",
    "from .models import Node",
    "import discoclaro.domain.models",
    "from discoclaro import i18n",
    "import ossify\nfrom osmosis import x",
    "import platform",
    '# import os\nx = "import os"\n',
]

FORBIDDEN_SOURCES = [
    "import os",
    "import os.path",
    "from os import path",
    "from os.path import join",
    "import sqlite3",
    "import shutil",
    "import subprocess",
    "import socket",
    "import re, os",
    "from discoclaro.services import x",
    "import discoclaro.adapters.store",
    "from discoclaro import interfaces",
    "from discoclaro.interfaces.cli import main",
    "from discoclaro import platform",
    "from discoclaro.platform.windows import elevate",
    "def f():\n    import os\n",
    "if True:\n    import shutil\n",
    "__import__('os')",
    "import importlib\nimportlib.import_module('os')",
]


def test_the_test_looks_at_the_real_domain_package():
    assert (SRC / "discoclaro" / "domain" / "__init__.py").is_file()


def test_real_domain_has_no_forbidden_imports():
    assert scan_domain(SRC) == []


@pytest.mark.parametrize("source", ALLOWED)
def test_allowed_imports_pass(source):
    assert find_violations(source, "discoclaro.domain") == []


@pytest.mark.parametrize("source", FORBIDDEN_SOURCES)
def test_forbidden_imports_are_reported(source):
    assert find_violations(source, "discoclaro.domain")


def test_relative_imports_are_resolved_from_the_package_of_the_file():
    assert find_violations("from .. import services", "discoclaro.domain")
    assert find_violations("from ..adapters import store", "discoclaro.domain")
    assert find_violations("from ... import services", "discoclaro.domain.sub")
    assert find_violations("from . import services", "discoclaro.domain") == []


def test_scan_finds_violations_in_a_synthetic_tree(tmp_path):
    domain = tmp_path / "discoclaro" / "domain"
    (domain / "sub").mkdir(parents=True)
    (domain / "__init__.py").write_text("", encoding="utf-8")
    (domain / "ok.py").write_text("import re\n", encoding="utf-8")
    (domain / "bad.py").write_text("import os\n", encoding="utf-8")
    (domain / "sub" / "deep.py").write_text(
        "from ... import services\n", encoding="utf-8"
    )
    problems = scan_domain(tmp_path)
    assert len(problems) == 2
    assert any(p.startswith("discoclaro/domain/bad.py:") for p in problems)
    assert any(p.startswith("discoclaro/domain/sub/deep.py:") for p in problems)
