"""Pruebas de qué usa un proyecto (T2C.3): siempre sobre archivos sintéticos
reales en `tmp_path`, nunca una carpeta real del usuario."""

from discoclaro.adapters import projects
from discoclaro.adapters.projects import scan_project, scan_projects


def write(tmp_path, name, content):
    (tmp_path / name).write_text(content, encoding="utf-8")


def references_of(usage, kind):
    return sorted(r.name for r in usage.references if r.kind == kind)


def test_scan_project_uses_the_folder_name_as_the_project_name(tmp_path):
    project_dir = tmp_path / "mi-app"
    project_dir.mkdir()
    usage = scan_project(str(project_dir))
    assert usage.project == "mi-app"


def test_scan_project_reads_images_from_docker_compose(tmp_path):
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  db:\n    image: postgres:15\n  cache:\n    image: 'redis:7'\n",
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "image") == ["postgres:15", "redis:7"]


def test_scan_project_reads_images_from_the_modern_compose_file_name(tmp_path):
    write(tmp_path, "compose.yaml", "services:\n  db:\n    image: mysql:8\n")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "image") == ["mysql:8"]


def test_scan_project_reads_images_from_a_dockerfile(tmp_path):
    write(tmp_path, "Dockerfile", "FROM python:3.12-slim AS builder\nRUN echo hi\n")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "image") == ["python:3.12-slim"]


def test_scan_project_dockerfile_from_is_case_insensitive(tmp_path):
    write(tmp_path, "Dockerfile", "from alpine:3.19\n")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "image") == ["alpine:3.19"]


def test_scan_project_reads_packages_from_requirements(tmp_path):
    write(
        tmp_path,
        "requirements.txt",
        "# a comment\nflask==3.0.0\nrequests>=2.0\n\n-e ./local-package\nnumpy\n",
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == ["flask", "numpy", "requests"]


def test_scan_project_reads_dependencies_from_package_json(tmp_path):
    write(
        tmp_path,
        "package.json",
        '{"dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}',
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == ["react", "vite"]


def test_scan_project_ignores_a_package_json_that_is_not_valid_json(tmp_path):
    write(tmp_path, "package.json", "{not valid json")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == []


def test_scan_project_ignores_a_package_json_that_is_a_json_array(tmp_path):
    write(tmp_path, "package.json", "[1, 2, 3]")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == []


def test_scan_project_reads_dependencies_from_pubspec(tmp_path):
    write(
        tmp_path,
        "pubspec.yaml",
        "name: my_app\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n"
        "  http: ^1.0.0\n"
        "dev_dependencies:\n"
        "  flutter_test:\n"
        "    sdk: flutter\n",
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == ["flutter", "flutter_test", "http"]


def test_scan_project_pubspec_stops_the_section_at_a_dedented_line(tmp_path):
    write(
        tmp_path,
        "pubspec.yaml",
        "dependencies:\n  http: ^1.0.0\nflutter:\n  uses-material-design: true\n",
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == ["http"]


def test_scan_project_finds_a_known_model_mentioned_in_a_file(tmp_path):
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  app:\n    environment:\n      - OLLAMA_MODEL=llama3:8b\n",
    )
    usage = scan_project(str(tmp_path), known_models=frozenset({"llama3:8b", "phi3"}))
    assert references_of(usage, "model") == ["llama3:8b"]


def test_scan_project_model_match_is_case_insensitive(tmp_path):
    write(tmp_path, "Dockerfile", "RUN ollama pull LLAMA3:8B\n")
    usage = scan_project(str(tmp_path), known_models=frozenset({"llama3:8b"}))
    assert references_of(usage, "model") == ["llama3:8b"]


def test_scan_project_with_no_known_models_never_reports_any(tmp_path):
    write(tmp_path, "Dockerfile", "RUN ollama pull llama3:8b\n")
    usage = scan_project(str(tmp_path), known_models=frozenset())
    assert references_of(usage, "model") == []


def test_scan_project_with_no_files_at_all_has_no_references(tmp_path):
    usage = scan_project(str(tmp_path))
    assert usage.references == ()


def test_scan_project_a_file_too_big_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "MAX_FILE_BYTES", 10)
    write(tmp_path, "requirements.txt", "a-very-long-package-name-indeed\n")
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "package") == []


def test_scan_project_deduplicates_repeated_references(tmp_path):
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  a:\n    image: redis:7\n  b:\n    image: redis:7\n",
    )
    usage = scan_project(str(tmp_path))
    assert references_of(usage, "image") == ["redis:7"]


def test_scan_projects_scans_every_folder_in_order(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    write(first, "Dockerfile", "FROM a:1\n")
    write(second, "Dockerfile", "FROM b:1\n")
    usages = scan_projects([str(first), str(second)])
    assert [u.project for u in usages] == ["first", "second"]
    assert references_of(usages[0], "image") == ["a:1"]
    assert references_of(usages[1], "image") == ["b:1"]


def test_scan_projects_of_no_folders_is_empty():
    assert scan_projects([]) == []
