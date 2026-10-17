"""Autoría limpia: nada de lo que se publica nombra a una herramienta de IA como autora.

Revisa nombres y contenido de los archivos versionados (y de los nuevos que aún no
se agregaron), los autores y mensajes del historial, y que el hook de commit rechace
los mensajes con esas menciones.

Los ejemplos con palabras prohibidas se arman por partes al ejecutar, así este
archivo tampoco las contiene y pasa su propia revisión.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from check_authorship import find_mentions, strip_git_comments

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".githooks"
GIT = shutil.which("git")
MAX_BYTES = 1_000_000

requires_git = pytest.mark.skipif(
    GIT is None or not (ROOT / ".git").exists(),
    reason="hace falta git y un repositorio",
)

TOOL = "Cla" + "ude"
COMPANY = "Anthro" + "pic"
TRAILER = "Co-Authored" + "-By"


def run_git(args, cwd=ROOT, env=None):
    """Ejecuta git con su ruta completa y los argumentos fijos de cada prueba."""
    return subprocess.run(  # noqa: S603 - git con ruta completa, sin datos externos
        [GIT, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def versioned_files():
    """Archivos versionados más los nuevos que todavía no se agregaron."""
    result = run_git(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    names = sorted({name for name in result.stdout.split("\0") if name})
    return [ROOT / name for name in names if (ROOT / name).is_file()]


def mentions_in_file(path):
    data = path.read_bytes()
    if b"\0" in data or len(data) > MAX_BYTES:
        return []  # binario o demasiado grande para ser texto de proyecto
    return find_mentions(data.decode("utf-8", errors="replace"))


@requires_git
def test_there_are_versioned_files_to_check():
    assert ROOT / "pyproject.toml" in versioned_files()


@requires_git
def test_no_versioned_file_names_an_ai_tool_as_author():
    problems = []
    for path in versioned_files():
        relative = path.relative_to(ROOT).as_posix()
        found = find_mentions(relative) + mentions_in_file(path)
        if found:
            problems.append(f"{relative}: {sorted(set(found))}")
    assert problems == []


@requires_git
def test_history_authors_and_messages_do_not_name_an_ai_tool():
    log = run_git(["log", "--format=%an <%ae>%n%cn <%ce>%n%B%x00"]).stdout
    assert [entry for entry in log.split("\0") if find_mentions(entry)] == []


def test_a_text_file_with_a_mention_is_detected_and_a_binary_is_skipped(tmp_path):
    text_file = tmp_path / "nota.md"
    text_file.write_text(f"Hecho con {TOOL}", encoding="utf-8")
    binary_file = tmp_path / "datos.bin"
    binary_file.write_bytes(b"\0" + TOOL.encode())
    assert mentions_in_file(text_file)
    assert mentions_in_file(binary_file) == []


MENTIONS = [
    TOOL,
    TOOL.lower(),
    COMPANY,
    f"{TRAILER}: Alguien <a@example.invalid>",
    f"Hecho con {TOOL} Code",
    "".join(("gene", "rado con IA")),
    "".join(("Gene", "rado por la inteligencia artificial")),
    "".join(("asis", "tido por IA")),
    "".join(("Redac", "tada con una IA")),
    "".join(("gene", "rated with AI")),
    "".join(("Writ", "ten by an AI")),
    "".join(("AI-", "assisted")),
    "".join(("ai ", "generated")),
]

CLEAN = [
    "Ollama guarda los modelos de IA en tu disco",
    "La inteligencia artificial local ocupa mucho espacio",
    "El informe lo genera el escáner",
    "Archivo generado por el escáner",
    "Archivo generado por la herramienta",
    "Datos generados por la herramienta",
    "Claudia revisó la licencia",
    "Estructura inicial del proyecto: capas, pyproject y .gitignore",
]


@pytest.mark.parametrize("text", MENTIONS)
def test_mentions_are_detected(text):
    assert find_mentions(text)


@pytest.mark.parametrize("text", CLEAN)
def test_normal_text_is_not_flagged(text):
    assert find_mentions(text) == []


def test_git_comment_lines_are_ignored():
    message = f"# {TOOL}\nCorrige el escáner\n# {COMPANY}"
    assert find_mentions(message)
    assert find_mentions(strip_git_comments(message)) == []


def test_hook_script_has_unix_line_endings():
    data = (HOOKS / "commit-msg").read_bytes()
    assert data.startswith(b"#!/bin/sh\n")
    assert b"\r" not in data


def clean_env(home):
    """Entorno sin la configuración de git del usuario."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    return env


@pytest.fixture
def scratch_repo(tmp_path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    env = clean_env(home)
    assert run_git(["init", "-q"], cwd=repo, env=env).returncode == 0
    return repo, env


def commit(repo, env, message):
    return run_git(
        [
            "-c",
            f"core.hooksPath={HOOKS.as_posix()}",
            "-c",
            "user.name=Prueba",
            "-c",
            "user.email=prueba@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            message,
        ],
        cwd=repo,
        env=env,
    )


def commit_count(repo, env):
    result = run_git(["rev-list", "--count", "--all"], cwd=repo, env=env)
    return int(result.stdout.strip() or 0)


@requires_git
def test_hook_accepts_a_clean_message(scratch_repo):
    repo, env = scratch_repo
    result = commit(repo, env, "Corrige el recorrido de carpetas")
    assert result.returncode == 0, result.stderr
    assert commit_count(repo, env) == 1


@requires_git
@pytest.mark.parametrize(
    "message",
    [
        f"Mejora el escáner\n\n{TRAILER}: Alguien <a@example.invalid>",
        f"Arreglo hecho con {TOOL}",
        "".join(("Gene", "rado por IA")),
    ],
    ids=["coauthor-line", "tool-name", "authorship-phrase"],
)
def test_hook_rejects_messages_that_name_an_ai_tool(scratch_repo, message):
    repo, env = scratch_repo
    result = commit(repo, env, message)
    assert result.returncode != 0
    assert "nombra a una herramienta de IA" in result.stderr
    assert commit_count(repo, env) == 0
