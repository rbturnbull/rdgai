"""
Tests for reading and editing the category definitions through the annotation store and the MCP server.
"""
import shutil

import pytest

from rdgai.apparatus import Doc
from rdgai.annotation import AnnotationStore, AnnotationError, ReadOnlyError, DefinitionsLockedError
from rdgai.mcp_server import build_server

from .util import TEST_DATA_DIR
from .test_mcp import call, list_tool_names, relations_in_file, mark_path, store  # noqa: F401 (fixtures)

EDIT_TOOLS = ["update_category", "add_category", "remove_category", "update_guidance"]


def categories(store) -> dict:
    return {category.name: category for category in store.definitions().categories}


# ---------------------------------------------------------------------------
# hash
# ---------------------------------------------------------------------------

def test_definitions_hash(store, mark_path):
    definitions = store.definitions()
    assert len(definitions.definitions_hash) == 64
    int(definitions.definitions_hash, 16)

    # stable across a reload and across unrelated writes
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    assert AnnotationStore(mark_path, read_only=True).definitions().definitions_hash == definitions.definitions_hash

    # changes when a definition changes
    store.update_category("Transposition", description="Changed.")
    assert store.definitions().definitions_hash != definitions.definitions_hash


def test_definitions_hash_changes_with_guidance(store):
    before = store.definitions().definitions_hash
    store.update_guidance("New guidance.")
    assert store.definitions().definitions_hash != before


# ---------------------------------------------------------------------------
# update_category
# ---------------------------------------------------------------------------

def test_update_description(store, mark_path):
    before = categories(store)
    result = store.update_category("Transposition", description="  Same words, different order.  ")
    assert result.relations_updated == 0
    # the result is compact: only the changed category and the new hash
    assert [category.name for category in result.changed] == ["Transposition"]
    assert result.changed[0].description == "Same words, different order."
    assert result.removed == []
    assert result.definitions_hash == store.definitions().definitions_hash
    assert "definitions" not in result.model_dump()
    after = categories(store)
    assert after["Transposition"].description == "Same words, different order."
    assert {name: category for name, category in after.items() if name != "Transposition"} == \
        {name: category for name, category in before.items() if name != "Transposition"}

    # saved to the file
    assert categories(AnnotationStore(mark_path, read_only=True))["Transposition"].description == "Same words, different order."


def test_rename_category_updates_corresp_and_labels(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="kept")
    store.set_classification("Mark.1.1.12-16", "f", "a", "Major_Addition")

    result = store.update_category("Major_Addition", new_name="Major_Plus")
    assert result.relations_updated == 2
    # the renamed category and its inverse (whose corresp changed) are reported
    assert {category.name for category in result.changed} == {"Major_Plus", "Major_Omission"}
    assert result.removed == ["Major_Addition"]

    after = categories(store)
    assert "Major_Addition" not in after
    assert after["Major_Plus"].inverse == "Major_Omission"
    assert after["Major_Omission"].inverse == "Major_Plus"
    # order of categories is kept
    assert [category.name for category in store.definitions().categories].index("Major_Plus") == 7

    pair = store.get_unit("Mark.1.1.10").pairs[0]
    assert pair.category == "Major_Omission"
    assert pair.inverse_category == "Major_Plus"
    assert pair.provenance.justification == "kept"

    text = mark_path.read_text()
    assert 'corresp="#Major_Plus"' in text
    assert "#Major_Addition" not in text
    reloaded = AnnotationStore(mark_path, read_only=True)
    pairs = {(pair.active, pair.passive): pair for pair in reloaded.get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "f")].inverse_category == "Major_Plus"


def test_rename_and_describe_together(store):
    store.update_category("Transposition", new_name="Reordering", description="Order changes.")
    assert categories(store)["Reordering"].description == "Order changes."
    assert categories(store)["Reordering"].inverse == "Reordering"


@pytest.mark.parametrize("new_name, message", [
    ("Major_Omission", "already exists"),
    ("Mark.1.1.10", "already used"),
    ("1bad", "not a valid"),
    ("has space", "not a valid"),
    ("", "not a valid"),
])
def test_rename_errors(store, mark_path, new_name, message):
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match=message):
        store.update_category("Transposition", new_name=new_name)
    assert mark_path.read_bytes() == before


def test_update_unknown_category(store):
    with pytest.raises(AnnotationError, match="Unknown category"):
        store.update_category("Nonsense", description="x")


def test_update_needs_a_change(store):
    with pytest.raises(AnnotationError, match="Nothing to change"):
        store.update_category("Transposition")


# ---------------------------------------------------------------------------
# add_category
# ---------------------------------------------------------------------------

def test_add_self_inverse_category(store, mark_path):
    result = store.add_category("Harmonization", description="Assimilated to a parallel passage.")
    assert [category.name for category in result.changed] == ["Harmonization"]
    after = categories(store)
    assert after["Harmonization"].inverse == "Harmonization"
    assert after["Harmonization"].description == "Assimilated to a parallel passage."
    assert [category.name for category in store.definitions().categories][-1] == "Harmonization"

    write = store.set_classification("Mark.1.2.8-14", "a", "e", "Harmonization")
    assert write.inverse.category == "Harmonization"
    assert "Harmonization" in categories(AnnotationStore(mark_path, read_only=True))


def test_add_category_pair(store, mark_path):
    result = store.add_category("Gloss_Addition", description="Adds a gloss.", inverse_name="Gloss_Omission", inverse_description="Omits a gloss.")
    assert [category.name for category in result.changed] == ["Gloss_Addition", "Gloss_Omission"]
    after = categories(store)
    assert after["Gloss_Addition"].inverse == "Gloss_Omission"
    assert after["Gloss_Omission"].inverse == "Gloss_Addition"
    assert after["Gloss_Omission"].description == "Omits a gloss."

    write = store.set_classification("Mark.1.1.12-16", "a", "d", "Gloss_Addition")
    assert write.inverse.category == "Gloss_Omission"

    text = mark_path.read_text()
    assert '<interp xml:id="Gloss_Addition" corresp="#Gloss_Omission">Adds a gloss.</interp>' in text
    assert '<interp xml:id="Gloss_Omission" corresp="#Gloss_Addition">Omits a gloss.</interp>' in text


@pytest.mark.parametrize("kwargs, message", [
    (dict(name="Transposition", description="x"), "already exists"),
    (dict(name="New", description="x", inverse_name="Major_Omission"), "already exists"),
    (dict(name="New", description="x", inverse_name="New"), "must be different"),
    (dict(name="bad name", description="x"), "not a valid"),
    (dict(name="Mark.1.1.10", description="x"), "already used"),
    (dict(name="New", description=""), "description"),
])
def test_add_category_errors(store, mark_path, kwargs, message):
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match=message):
        store.add_category(**kwargs)
    assert mark_path.read_bytes() == before


# ---------------------------------------------------------------------------
# remove_category
# ---------------------------------------------------------------------------

def test_remove_unused_category(store, mark_path):
    result = store.remove_category("Transposition")
    assert result.relations_updated == 0
    assert result.removed == ["Transposition"]
    assert result.changed == []
    assert "Transposition" not in categories(store)
    assert 'xml:id="Transposition"' not in mark_path.read_text()


def test_remove_pair_removes_both(store, mark_path):
    result = store.remove_category("Major_Addition")
    assert sorted(result.removed) == ["Major_Addition", "Major_Omission"]
    after = categories(store)
    assert "Major_Addition" not in after
    assert "Major_Omission" not in after
    assert len(after) == 8


def test_remove_category_in_use(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission")
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match="2 pairs"):
        store.remove_category("Minor_Omission")
    assert mark_path.read_bytes() == before


def test_remove_category_reassigning_pair(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission", justification="kept", cert="low")
    result = store.remove_category("Minor_Addition", reassign_to="Major_Addition")
    assert result.relations_updated == 2
    assert "Minor_Omission" not in categories(store)

    pair = store.get_unit("Mark.1.1.10").pairs[0]
    assert pair.category == "Major_Omission"
    assert pair.inverse_category == "Major_Addition"
    assert pair.provenance.justification == "kept"
    assert pair.provenance.cert == "low"
    assert "Minor_Omission" not in mark_path.read_text()


def test_remove_category_reassigning_self_inverse(store):
    store.set_classification("Mark.1.2.8-14", "a", "e", "Transposition")
    store.remove_category("Transposition", reassign_to="Major_Substitution")
    pair = [pair for pair in store.get_unit("Mark.1.2.8-14").pairs if (pair.active, pair.passive) == ("a", "e")][0]
    assert pair.category == "Major_Substitution"
    assert pair.inverse_category == "Major_Substitution"


@pytest.mark.parametrize("name, reassign_to, message", [
    ("Transposition", "Nonsense", "Unknown category"),
    ("Major_Addition", "Major_Omission", "is being removed"),
    ("Transposition", "Major_Addition", "inverse"),
    ("Major_Addition", "Transposition", "inverse"),
])
def test_remove_category_reassign_errors(store, mark_path, name, reassign_to, message):
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match=message):
        store.remove_category(name, reassign_to=reassign_to)
    assert mark_path.read_bytes() == before


# ---------------------------------------------------------------------------
# guidance
# ---------------------------------------------------------------------------

def test_update_guidance(store, mark_path):
    result = store.update_guidance("Decision order:\n  1. Spelling only -> Orthography\n  2. Anything else -> Substitution")
    assert result.guidance == "Decision order:\n  1. Spelling only -> Orthography\n  2. Anything else -> Substitution"
    assert result.changed == []
    text = mark_path.read_text()
    assert text.count("<!--") == 1
    assert "Spelling only -> Orthography" in text
    assert "Transcriptional change categories for ECM Mark." not in text
    assert AnnotationStore(mark_path, read_only=True).definitions().guidance == result.guidance


def test_update_guidance_without_existing_comment(tmp_path):
    path = tmp_path / "minimal.xml"
    shutil.copy(TEST_DATA_DIR / "minimal.xml", path)
    store = AnnotationStore(path, annotator="RT")
    store.update_guidance("Use category1 first.")
    assert AnnotationStore(path, read_only=True).definitions().guidance == "Use category1 first."


def test_update_guidance_rejects_double_hyphen(store):
    with pytest.raises(AnnotationError, match="--"):
        store.update_guidance("a -- b")


# ---------------------------------------------------------------------------
# locking
# ---------------------------------------------------------------------------

def test_locked_definitions(mark_path):
    store = AnnotationStore(mark_path, annotator="RT", lock_definitions=True)
    before = mark_path.read_bytes()
    with pytest.raises(DefinitionsLockedError):
        store.update_category("Transposition", description="x")
    with pytest.raises(DefinitionsLockedError):
        store.add_category("New", description="x")
    with pytest.raises(DefinitionsLockedError):
        store.remove_category("Transposition")
    with pytest.raises(DefinitionsLockedError):
        store.update_guidance("x")
    assert mark_path.read_bytes() == before

    # classifications can still be set
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")


def test_read_only_definitions(mark_path):
    store = AnnotationStore(mark_path, read_only=True)
    with pytest.raises(ReadOnlyError):
        store.update_category("Transposition", description="x")


def test_edit_tools_listed(store):
    names = list_tool_names(build_server(store))
    assert all(name in names for name in EDIT_TOOLS)


def test_edit_tools_hidden_when_locked_or_read_only(mark_path):
    locked = list_tool_names(build_server(AnnotationStore(mark_path, annotator="RT", lock_definitions=True)))
    assert not any(name in locked for name in EDIT_TOOLS)
    assert "set_classification" in locked

    read_only = list_tool_names(build_server(AnnotationStore(mark_path, read_only=True)))
    assert not any(name in read_only for name in EDIT_TOOLS)


# ---------------------------------------------------------------------------
# through MCP
# ---------------------------------------------------------------------------

def test_tools_through_mcp(store):
    server = build_server(store)
    result = call(server, "add_category", name="Harmonization", description="Assimilated to a parallel.")
    assert not result.is_error
    assert [category["name"] for category in result.structured_content["changed"]] == ["Harmonization"]
    assert "definitions" not in result.structured_content
    assert len(result.structured_content["definitions_hash"]) == 64

    result = call(server, "update_category", name="Harmonization", description="Assimilated to a parallel passage.")
    assert not result.is_error

    result = call(server, "update_guidance", guidance="Prefer Minor when in doubt.")
    assert result.structured_content["guidance"] == "Prefer Minor when in doubt."

    result = call(server, "remove_category", name="Harmonization")
    assert not result.is_error

    result = call(server, "remove_category", name="Nonsense")
    assert result.is_error
    assert "Unknown category" in result.content[0].text

    definitions = call(server, "get_definitions").structured_content
    assert len(definitions["definitions_hash"]) == 64


def test_cli_lock_definitions_option():
    from typer.testing import CliRunner
    from rdgai.main import app
    result = CliRunner().invoke(app, ["serve-mcp", "--help"])
    assert "--lock-definitions" in result.output
