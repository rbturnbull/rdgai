"""
Notes embedded in reading text (as in ECM Mark, e.g. 'θεου (cf. 24b)', 'om (cf. 27c)', '(κενον)') are parsed at
runtime into a separate notes field, so that they are never treated as reading text.
"""
import shutil

import pytest

from rdgai.apparatus import Doc
from rdgai.notes import parse_reading_notes, resolve_references
from rdgai.annotation import AnnotationStore
from rdgai.mcp_server import build_server

from .util import TEST_DATA_DIR
from .test_mcp import call

NOTES_PATH = TEST_DATA_DIR / "ecm_notes.xml"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, text, notes", [
    ("θεου (cf. 24b)", "θεου", ["cf. 24b"]),
    ("om (cf. 27c)", "", ["cf. 27c"]),
    ("om", "", []),
    ("τα περι (cf. 27/4) αυτης παντα", "τα περι αυτης παντα", ["cf. 27/4"]),
    ("αυτον (κενον)", "αυτον", ["κενον"]),
    ("λογος (Is 40:4-8)", "λογος", ["Is 40:4-8"]),
    ("εν ερημω (2-12e)", "εν ερημω", ["2-12e"]),
    ("a (x) b (y)", "a b", ["x", "y"]),
    ("υιου του θεου", "υιου του θεου", []),
    ("", "", []),
    ("ομως", "ομως", []),  # Greek, not the Latin 'om'
])
def test_parse_reading_notes(raw, text, notes):
    assert parse_reading_notes(raw) == (text, notes)


# ---------------------------------------------------------------------------
# resolving cross-references
# ---------------------------------------------------------------------------

UNIT_IDS = ["Mark.1.2.4", "Mark.1.2.8", "Mark.1.3.4-6", "Mark.1.3.10", "Mark.1.2.2-12"]


def refs(note, unit_id="Mark.1.2.4"):
    return [(ref.label, ref.unit_id, ref.reading) for ref in resolve_references(note, unit_id, UNIT_IDS)]


def test_resolve_same_verse_with_reading():
    assert refs("cf. 8b") == [("8b", "Mark.1.2.8", "b")]


def test_resolve_other_verse():
    assert refs("cf. 3/4") == [("3/4", "Mark.1.3.4-6", None)]


def test_resolve_position_inside_a_range():
    assert refs("cf. 3/5") == [("3/5", "Mark.1.3.4-6", None)]


def test_resolve_list_inherits_verse():
    assert refs("cf. 3/5, 10") == [("3/5", "Mark.1.3.4-6", None), ("10", "Mark.1.3.10", None)]


def test_resolve_list_of_readings():
    assert refs("cf. 8a, c") == [("8a", "Mark.1.2.8", "a"), ("c", "Mark.1.2.8", "c")]


def test_resolve_exact_range():
    assert refs("2-12e") == [("2-12e", "Mark.1.2.2-12", "e")]


def test_unresolved_reference():
    assert refs("cf. 99") == [("99", None, None)]


@pytest.mark.parametrize("note", ["κενον", "Is 40:4-8"])
def test_notes_that_are_not_references(note):
    assert refs(note) == []


def test_unit_ids_in_another_scheme_are_not_resolved():
    assert refs("cf. 8b", unit_id="app") == [("8b", None, "b")]


# ---------------------------------------------------------------------------
# readings in a Doc
# ---------------------------------------------------------------------------

def test_doc_readings_are_cleaned():
    doc = Doc(NOTES_PATH)
    readings = doc["Mark.1.2.4"].id_to_reading
    assert readings["a"].text == "θεου"
    assert readings["a"].raw_text == "θεου (cf. 8b)"
    assert readings["a"].notes == ["cf. 8b"]
    assert readings["b"].text == ""
    assert str(readings["b"]) == "OMIT"
    assert readings["c"].text == "τα περι αυτης παντα"
    assert doc["Mark.1.2.8"].id_to_reading["a"].notes == []


def test_lemma_notes_are_removed_from_context():
    doc = Doc(NOTES_PATH)
    assert doc["Mark.1.2.4"].text_in_context() == "και ⸂θεου⸃ λεγει"


def test_reading_notes_can_be_switched_off():
    doc = Doc(NOTES_PATH, reading_notes=False)
    reading = doc["Mark.1.2.4"].id_to_reading["a"]
    assert reading.text == "θεου (cf. 8b)"
    assert reading.notes == []


# ---------------------------------------------------------------------------
# through the store and MCP
# ---------------------------------------------------------------------------

@pytest.fixture
def notes_store(tmp_path):
    path = tmp_path / "ecm_notes.xml"
    shutil.copy(NOTES_PATH, path)
    return AnnotationStore(path, annotator="RT")


def test_get_unit_reports_notes(notes_store):
    data = call(build_server(notes_store), "get_unit", unit_id="Mark.1.2.4").structured_content
    readings = {reading["id"]: reading for reading in data["readings"]}
    assert readings["a"]["text"] == "θεου"
    assert readings["a"]["notes"] == [
        {"text": "cf. 8b", "references": [{"label": "8b", "unit_id": "Mark.1.2.8", "reading": "b"}]},
    ]
    assert readings["b"]["text"] == "OMIT"
    assert readings["d"]["notes"] == [{"text": "κενον", "references": []}]
    assert readings["e"]["notes"][0]["references"] == []
    assert data["context"] == "και ⸂θεου⸃ λεγει"

    pairs = {(pair["active"], pair["passive"]): pair for pair in data["pairs"]}
    assert pairs[("a", "b")]["active_text"] == "θεου"
    assert pairs[("a", "b")]["passive_text"] == "OMIT"


def test_store_without_reading_notes(tmp_path):
    path = tmp_path / "ecm_notes.xml"
    shutil.copy(NOTES_PATH, path)
    store = AnnotationStore(path, read_only=True, reading_notes=False)
    readings = {reading.id: reading for reading in store.get_unit("Mark.1.2.4").readings}
    assert readings["a"].text == "θεου (cf. 8b)"
    assert readings["a"].notes == []


def test_cli_reading_notes_option():
    from typer.testing import CliRunner
    from rdgai.main import app
    result = CliRunner().invoke(app, ["serve-mcp", "--help"])
    assert "--reading-notes" in result.output
    assert "--no-reading-notes" in result.output
