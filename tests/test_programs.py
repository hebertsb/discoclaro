"""Pruebas de la diferencia entre inventarios (T2B.3): puras, con datos sintéticos."""

from discoclaro.domain.models import Program
from discoclaro.domain.programs import InventoryDiff, ProgramUpdate, diff_inventories


def make_program(**changes):
    fields = {
        "name": "Acme App",
        "version": "1.0",
        "publisher": "Acme",
        "install_location": None,
        "install_date": None,
        "estimated_size_bytes": None,
        "uninstall_string": None,
    }
    return Program(**{**fields, **changes})


def test_two_empty_inventories_have_no_differences():
    assert diff_inventories([], []) == InventoryDiff((), (), ())


def test_the_same_inventory_twice_has_no_differences():
    programs = [make_program(name="A"), make_program(name="B")]
    assert diff_inventories(programs, programs) == InventoryDiff((), (), ())


def test_a_new_program_is_reported_as_installed():
    before = [make_program(name="A")]
    after = [make_program(name="A"), make_program(name="B")]
    diff = diff_inventories(before, after)
    assert diff.installed == (make_program(name="B"),)
    assert diff.uninstalled == ()
    assert diff.updated == ()


def test_a_missing_program_is_reported_as_uninstalled():
    before = [make_program(name="A"), make_program(name="B")]
    after = [make_program(name="A")]
    diff = diff_inventories(before, after)
    assert diff.uninstalled == (make_program(name="B"),)
    assert diff.installed == ()
    assert diff.updated == ()


def test_a_different_version_of_the_same_name_is_an_update():
    before = [make_program(name="A", version="1.0")]
    after = [make_program(name="A", version="2.0")]
    diff = diff_inventories(before, after)
    assert diff.updated == (ProgramUpdate("A", "1.0", "2.0"),)
    assert diff.installed == ()
    assert diff.uninstalled == ()


def test_the_same_version_of_the_same_name_is_not_an_update():
    before = [make_program(name="A", version="1.0")]
    after = [make_program(name="A", version="1.0")]
    assert diff_inventories(before, after).updated == ()


def test_everything_is_installed_when_before_is_empty():
    after = [make_program(name="A"), make_program(name="B")]
    diff = diff_inventories([], after)
    assert diff.installed == tuple(after)
    assert diff.uninstalled == ()


def test_everything_is_uninstalled_when_after_is_empty():
    before = [make_program(name="A"), make_program(name="B")]
    diff = diff_inventories(before, [])
    assert diff.uninstalled == tuple(before)
    assert diff.installed == ()


def test_a_version_that_appears_where_there_was_none_is_an_update():
    before = [make_program(name="A", version=None)]
    after = [make_program(name="A", version="1.0")]
    diff = diff_inventories(before, after)
    assert diff.updated == (ProgramUpdate("A", None, "1.0"),)


def test_a_version_that_disappears_is_also_an_update():
    before = [make_program(name="A", version="1.0")]
    after = [make_program(name="A", version=None)]
    diff = diff_inventories(before, after)
    assert diff.updated == (ProgramUpdate("A", "1.0", None),)


def test_installed_uninstalled_and_updated_can_happen_at_once():
    before = [
        make_program(name="Gone", version="1.0"),
        make_program(name="Stable", version="1.0"),
        make_program(name="Changing", version="1.0"),
    ]
    after = [
        make_program(name="Stable", version="1.0"),
        make_program(name="Changing", version="2.0"),
        make_program(name="New", version="1.0"),
    ]
    diff = diff_inventories(before, after)
    assert [p.name for p in diff.installed] == ["New"]
    assert [p.name for p in diff.uninstalled] == ["Gone"]
    assert diff.updated == (ProgramUpdate("Changing", "1.0", "2.0"),)


def test_duplicate_names_in_one_inventory_are_treated_as_one():
    before = [make_program(name="A", version="1.0")]
    after = [
        make_program(name="A", version="1.0"),
        make_program(name="A", version="2.0"),
    ]
    diff = diff_inventories(before, after)
    assert diff.updated == (ProgramUpdate("A", "1.0", "2.0"),)
    assert diff.installed == ()


def test_installed_and_uninstalled_preserve_the_original_order():
    before = [make_program(name="Z"), make_program(name="A")]
    after = [make_program(name="New1"), make_program(name="New2")]
    diff = diff_inventories(before, after)
    assert [p.name for p in diff.uninstalled] == ["Z", "A"]
    assert [p.name for p in diff.installed] == ["New1", "New2"]
