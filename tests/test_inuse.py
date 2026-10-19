"""Pruebas de qué usa cada proyecto (T2C.3): puras."""

import pytest

from discoclaro.domain.inuse import ProjectReference, ProjectUsage, used_by


def test_a_reference_needs_a_known_kind():
    with pytest.raises(ValueError):
        ProjectReference(kind="unknown", name="x")


def test_a_reference_needs_a_name():
    with pytest.raises(ValueError):
        ProjectReference(kind="image", name="")


def test_a_project_usage_needs_a_project_name():
    with pytest.raises(ValueError):
        ProjectUsage(project="", references=())


def test_used_by_finds_the_project_that_references_something():
    usages = [
        ProjectUsage(
            project="api",
            references=(ProjectReference(kind="image", name="postgres:15"),),
        ),
        ProjectUsage(project="web", references=()),
    ]
    assert used_by(usages, "image", "postgres:15") == ("api",)


def test_used_by_is_case_insensitive():
    usages = [
        ProjectUsage(
            project="api",
            references=(ProjectReference(kind="package", name="Flask"),),
        )
    ]
    assert used_by(usages, "package", "flask") == ("api",)


def test_used_by_matches_only_the_right_kind():
    usages = [
        ProjectUsage(
            project="api",
            references=(ProjectReference(kind="model", name="llama3"),),
        )
    ]
    assert used_by(usages, "image", "llama3") == ()


def test_used_by_returns_every_project_that_matches():
    usages = [
        ProjectUsage(
            project="api", references=(ProjectReference(kind="image", name="x"),)
        ),
        ProjectUsage(
            project="worker", references=(ProjectReference(kind="image", name="x"),)
        ),
    ]
    assert used_by(usages, "image", "x") == ("api", "worker")


def test_used_by_is_empty_when_nothing_matches():
    usages = [ProjectUsage(project="api", references=())]
    assert used_by(usages, "image", "x") == ()


def test_used_by_of_no_projects_is_empty():
    assert used_by([], "image", "x") == ()
