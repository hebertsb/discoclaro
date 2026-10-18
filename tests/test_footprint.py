"""Pruebas de los candidatos a huella de un programa (T2B.4): árboles sintéticos."""

from datetime import UTC, datetime, timedelta

from discoclaro.domain.footprint import (
    DEFAULT_NEAR_INSTALL_WINDOW,
    FolderCandidate,
    classify_footprint,
    footprints_of,
    might_be_related,
)
from discoclaro.domain.models import Program

INSTALLED_ON = datetime(2026, 10, 1, tzinfo=UTC)


def make_program(**changes):
    fields = {
        "name": "Acme App",
        "version": "1.0",
        "publisher": "Acme",
        "install_location": "C:\\Program Files\\Acme",
        "install_date": "20261001",
        "estimated_size_bytes": None,
        "uninstall_string": None,
    }
    return Program(**{**fields, **changes})


def candidate(path, size_bytes=100, last_modified=None):
    return FolderCandidate(
        path=path, size_bytes=size_bytes, last_modified=last_modified
    )


def test_the_registered_install_location_is_high_confidence():
    program = make_program()
    found = classify_footprint(program, candidate("C:\\Program Files\\Acme"))
    assert found.confidence == "high"
    assert found.reason == "install_location"


def test_the_install_location_match_ignores_a_trailing_backslash_and_case():
    program = make_program(install_location="C:\\Program Files\\Acme\\")
    found = classify_footprint(program, candidate("c:\\program files\\acme"))
    assert found.confidence == "high"


def test_a_known_footprint_path_is_high_confidence():
    program = make_program(install_location=None)
    found = classify_footprint(
        program,
        candidate("D:\\Games\\Acme"),
        known_footprint_paths=frozenset({"D:\\Games\\Acme"}),
    )
    assert found.confidence == "high"
    assert found.reason == "known_footprint"


def test_a_known_footprint_path_wins_over_a_name_match():
    program = make_program(install_location=None)
    found = classify_footprint(
        program,
        candidate("C:\\Users\\x\\AppData\\Roaming\\Acme App"),
        known_footprint_paths=frozenset({"C:\\Users\\x\\AppData\\Roaming\\Acme App"}),
    )
    assert found.reason == "known_footprint"


def test_install_location_wins_over_a_name_match_at_the_same_path():
    program = make_program(install_location="C:\\Program Files\\Acme App")
    found = classify_footprint(program, candidate("C:\\Program Files\\Acme App"))
    assert found.reason == "install_location"


def test_a_folder_named_exactly_like_the_program_is_medium_confidence():
    program = make_program(install_location=None, name="Acme App")
    found = classify_footprint(
        program, candidate("C:\\Users\\x\\AppData\\Roaming\\Acme App")
    )
    assert found.confidence == "medium"
    assert found.reason == "name_match"


def test_a_folder_named_exactly_like_the_publisher_is_medium_confidence():
    program = make_program(install_location=None, publisher="Acme")
    found = classify_footprint(program, candidate("C:\\ProgramData\\Acme"))
    assert found.confidence == "medium"
    assert found.reason == "name_match"


def test_a_folder_name_that_is_part_of_the_program_name_is_low_confidence():
    program = make_program(
        install_location=None, name="Acme App Suite", publisher="Acme Corp"
    )
    found = classify_footprint(program, candidate("D:\\Random\\Acme"))
    assert found.confidence == "low"
    assert found.reason == "partial_name_match"


def test_a_program_name_that_is_part_of_the_folder_name_is_low_confidence():
    program = make_program(install_location=None, name="Acme")
    found = classify_footprint(program, candidate("D:\\Random\\AcmeHelper"))
    assert found.confidence == "low"
    assert found.reason == "partial_name_match"


def test_a_folder_modified_near_the_install_date_is_low_confidence():
    program = make_program(install_location=None, name="Zzz", publisher="Zzz")
    found = classify_footprint(
        program,
        candidate(
            "D:\\Unrelated\\Folder", last_modified=INSTALLED_ON + timedelta(days=1)
        ),
    )
    assert found.confidence == "low"
    assert found.reason == "near_install_date"


def test_a_folder_modified_well_outside_the_install_window_does_not_match():
    program = make_program(install_location=None, name="Zzz", publisher="Zzz")
    found = classify_footprint(
        program,
        candidate(
            "D:\\Unrelated\\Folder",
            last_modified=INSTALLED_ON
            + DEFAULT_NEAR_INSTALL_WINDOW
            + timedelta(days=1),
        ),
    )
    assert found is None


def test_an_unrelated_folder_with_no_date_does_not_match():
    program = make_program(install_location=None, name="Zzz", publisher="Zzz")
    assert classify_footprint(program, candidate("D:\\Unrelated\\Folder")) is None


def test_a_malformed_install_date_never_crashes_and_is_just_ignored():
    program = make_program(
        install_location=None, name="Zzz", publisher="Zzz", install_date="not-a-date"
    )
    found = classify_footprint(
        program, candidate("D:\\Unrelated\\Folder", last_modified=INSTALLED_ON)
    )
    assert found is None


def test_a_calendar_impossible_install_date_never_crashes():
    program = make_program(
        install_location=None, name="Zzz", publisher="Zzz", install_date="20261345"
    )
    found = classify_footprint(
        program, candidate("D:\\Unrelated\\Folder", last_modified=INSTALLED_ON)
    )
    assert found is None


def test_a_path_inside_windows_is_never_a_footprint_even_with_a_name_match():
    program = make_program(install_location=None, name="System32")
    found = classify_footprint(program, candidate("C:\\Windows\\System32"))
    assert found is None


def test_a_path_inside_winsxs_is_never_a_footprint():
    program = make_program(install_location="C:\\Windows\\WinSxS\\Acme")
    found = classify_footprint(program, candidate("C:\\Windows\\WinSxS\\Acme"))
    assert found is None


def test_a_generic_shared_folder_name_is_never_a_footprint_on_its_own():
    program = make_program(install_location=None, name="Google", publisher="Google")
    found = classify_footprint(
        program, candidate("C:\\Users\\x\\AppData\\Local\\Google")
    )
    assert found is None


def test_footprints_of_filters_out_everything_that_does_not_match():
    program = make_program(install_location="C:\\Program Files\\Acme")
    candidates = [
        candidate("C:\\Program Files\\Acme"),
        candidate("D:\\Unrelated\\Folder"),
        candidate("C:\\Windows\\System32"),
    ]
    found = footprints_of(program, candidates)
    assert [f.path for f in found] == ["C:\\Program Files\\Acme"]


def test_footprints_of_preserves_the_given_order():
    program = make_program(install_location=None, name="Acme")
    candidates = [
        candidate("D:\\Acme"),
        candidate("C:\\ProgramData\\Acme"),
    ]
    found = footprints_of(program, candidates)
    assert [f.path for f in found] == ["D:\\Acme", "C:\\ProgramData\\Acme"]


def test_might_be_related_matches_an_exact_name():
    program = make_program(install_location=None, name="Acme App")
    assert might_be_related("C:\\Users\\x\\AppData\\Roaming\\Acme App", program)


def test_might_be_related_matches_an_exact_publisher():
    program = make_program(install_location=None, publisher="Acme")
    assert might_be_related("C:\\ProgramData\\Acme", program)


def test_might_be_related_matches_a_partial_name():
    program = make_program(
        install_location=None, name="Acme App Suite", publisher="Acme Corp"
    )
    assert might_be_related("D:\\Random\\Acme", program)


def test_might_be_related_rejects_an_unrelated_name():
    program = make_program(install_location=None, name="Zzz", publisher="Zzz")
    assert not might_be_related("D:\\Unrelated\\Folder", program)


def test_might_be_related_rejects_a_generic_name():
    program = make_program(install_location=None, name="Google", publisher="Google")
    assert not might_be_related("C:\\Users\\x\\AppData\\Local\\Google", program)


def test_might_be_related_rejects_a_protected_zone_even_with_a_name_match():
    program = make_program(install_location=None, name="System32")
    assert not might_be_related("C:\\Windows\\System32", program)


def test_might_be_related_never_needs_size_or_date_to_match():
    program = make_program(install_location=None, name="Acme")
    assert might_be_related("D:\\Acme", program)
    found = classify_footprint(program, candidate("D:\\Acme"))
    assert found is not None
    assert found.reason == "name_match"


def test_a_footprint_keeps_the_size_and_last_modified_of_its_candidate():
    program = make_program()
    moment = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    found = classify_footprint(
        program,
        candidate("C:\\Program Files\\Acme", size_bytes=4096, last_modified=moment),
    )
    assert found.size_bytes == 4096
    assert found.last_modified == moment
