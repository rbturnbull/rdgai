import os
import shutil
import time
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from mcp.client.client import Client

from rdgai.apparatus import Doc
from rdgai.annotation import AnnotationStore, AnnotationError, ReadOnlyError, verse_of, matches_verse
from rdgai.mcp_server import build_server
from rdgai.main import app

from .util import TEST_DATA_DIR

MARK_PATH = Path("/Users/rturnbull/MDAP/DECRA/MarkVariantClassification/mark_collation_with_context-variant_classification.xml")


@pytest.fixture
def mark_path(tmp_path) -> Path:
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    return path


@pytest.fixture
def store(mark_path) -> AnnotationStore:
    return AnnotationStore(mark_path, annotator="RT", annotator_name="Test Annotator")


def call(server, tool:str, **arguments):
    """ Calls a tool through an in-memory MCP client and returns the result. """
    async def run():
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)
    return anyio.run(run)


def list_tool_names(server) -> list[str]:
    async def run():
        async with Client(server) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]
    return anyio.run(run)


def relations_in_file(path:Path, unit_id:str) -> list[dict]:
    doc = Doc(path)
    result = []
    for pair in doc[unit_id].pairs:
        for relation in pair.relation_elements():
            result.append(dict(relation.attrib))
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_verse_of():
    assert verse_of("Mark.1.1.12-16") == "Mark.1.1"
    assert verse_of("app") == "app"


def test_matches_verse():
    assert matches_verse("Mark.1.1.12-16", "Mark.1.1")
    assert matches_verse("Mark.1.1.12-16", "Mark.1")
    assert matches_verse("Mark.1.1.12-16", "Mark.1.1.")
    assert not matches_verse("Mark.1.10.2", "Mark.1.1")


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------

def test_get_definitions(store):
    result = call(build_server(store), "get_definitions")
    assert not result.is_error
    data = result.structured_content
    assert data["language"] == "Ancient Greek" or data["language"]
    assert data["guidance"].startswith("Transcriptional change categories for ECM Mark.")
    assert "Decision order" in data["guidance"]
    assert "ACTIVE" in data["direction_convention"]
    categories = {category["name"]: category for category in data["categories"]}
    assert len(categories) == 10
    assert categories["Minor_Addition"]["inverse"] == "Minor_Omission"
    assert categories["Major_Omission"]["inverse"] == "Major_Addition"
    assert categories["Transposition"]["inverse"] == "Transposition"
    assert "spelling" in categories["Orthography"]["description"]


def test_definitions_without_comment(tmp_path):
    path = tmp_path / "minimal.xml"
    shutil.copy(TEST_DATA_DIR / "minimal.xml", path)
    definitions = AnnotationStore(path, read_only=True).definitions()
    assert definitions.guidance == ""
    assert [category.name for category in definitions.categories] == ["category1", "category2", "category3"]


# ---------------------------------------------------------------------------
# navigation
# ---------------------------------------------------------------------------

def test_list_units(store):
    result = call(build_server(store), "list_units")
    data = result.structured_content
    assert data["total"] == 10
    first = data["units"][0]
    assert first == {
        "id": "Mark.1.1.10",
        "verse": "Mark.1.1",
        "lemma": "χριστου",
        "reading_count": 2,
        "pair_count": 1,
        "classified_count": 0,
        "status": "unclassified",
    }


def test_list_units_excludes_orthographic_pairs(store):
    units = {unit.id: unit for unit in store.list_units(limit=500).units}
    # readings: a, ao (orthographic), b, bo (orthographic), c -> 3 substantive readings -> 3 pairs
    assert units["Mark.1.5.20-22"].reading_count == 5
    assert units["Mark.1.5.20-22"].pair_count == 3
    assert units["Mark.1.1.12-16"].pair_count == 15


def test_list_units_by_verse_and_pagination(store):
    server = build_server(store)
    data = call(server, "list_units", verse="Mark.1.2").structured_content
    assert data["total"] == 7
    assert all(unit["verse"] == "Mark.1.2" for unit in data["units"])

    page = call(server, "list_units", verse="Mark.1.2", offset=5, limit=5).structured_content
    assert page["total"] == 7
    assert page["offset"] == 5
    assert [unit["id"] for unit in page["units"]] == ["Mark.1.2.34", "Mark.1.2.43"]


def test_list_units_by_status(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")

    assert [unit.id for unit in store.list_units(status="classified").units] == ["Mark.1.1.10"]
    assert [unit.id for unit in store.list_units(status="partial").units] == ["Mark.1.1.12-16"]
    unclassified = store.list_units(status="unclassified")
    assert unclassified.total == 8
    assert "Mark.1.1.10" not in [unit.id for unit in unclassified.units]

    partial = store.list_units(status="partial").units[0]
    assert partial.classified_count == 1
    assert partial.pair_count == 15


def test_get_unit(store):
    result = call(build_server(store), "get_unit", unit_id="Mark.1.1.12-16")
    assert not result.is_error
    data = result.structured_content
    assert data["id"] == "Mark.1.1.12-16"
    assert data["verse"] == "Mark.1.1"
    assert data["context"] == "αρχη του ευαγγελιου ιησου χριστου ⸂υιου του θεου⸃"
    assert data["context_before"] == "αρχη του ευαγγελιου ιησου χριστου"
    assert data["context_after"] == ""
    assert [reading["id"] for reading in data["readings"]] == ["a", "b", "c", "d", "e", "f"]
    assert data["readings"][5]["text"] == "OMIT"
    assert data["readings"][1]["witnesses"] == ["03", "05", "019", "032", "732", "2148"]
    assert data["readings"][1]["witness_count"] == 6
    assert "P45" in data["lacunose_witnesses"]
    assert len(data["pairs"]) == 15
    assert data["pairs"][0] == {
        "active": "a",
        "passive": "b",
        "active_text": "υιου του θεου",
        "passive_text": "υιου θεου",
        "category": None,
        "inverse_category": None,
        "provenance": None,
    }
    assert data["status"] == "unclassified"


def test_get_unit_context_after(store):
    unit = store.get_unit("Mark.1.1.10")
    assert unit.context == "αρχη του ευαγγελιου ιησου ⸂χριστου⸃ υιου του θεου"


def test_get_unit_marks_excluded_readings(store):
    unit = store.get_unit("Mark.1.5.20-22")
    excluded = {reading.id: reading.excluded for reading in unit.readings}
    assert excluded == {"a": False, "ao": True, "b": False, "bo": True, "c": False}
    assert [(pair.active, pair.passive) for pair in unit.pairs] == [("a", "b"), ("a", "c"), ("b", "c")]


def test_get_unit_unknown(store):
    result = call(build_server(store), "get_unit", unit_id="Mark.99.1.1")
    assert result.is_error
    assert "No variation unit with id 'Mark.99.1.1'" in result.content[0].text


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def test_set_classification_writes_inverse_and_provenance(store, mark_path):
    result = call(
        build_server(store), "set_classification",
        unit_id="Mark.1.1.12-16", active="a", passive="b", category="Minor_Omission",
        justification="Omits the article του.", cert="medium",
    )
    assert not result.is_error
    data = result.structured_content
    assert data["pair"]["category"] == "Minor_Omission"
    assert data["pair"]["inverse_category"] == "Minor_Addition"
    assert data["inverse"]["active"] == "b"
    assert data["inverse"]["category"] == "Minor_Addition"
    assert data["pair"]["provenance"]["resp"] == "#RT"
    assert data["pair"]["provenance"]["cert"] == "medium"
    assert data["pair"]["provenance"]["justification"] == "Omits the article του."
    assert data["inverse"]["provenance"]["justification"] == "Inverse of a → b: Omits the article του."

    # The file on disk has been updated
    relations = relations_in_file(mark_path, "Mark.1.1.12-16")
    assert {"active": "a", "passive": "b", "ana": "#Minor_Omission", "resp": "#RT", "cert": "medium"} in relations
    assert {"active": "b", "passive": "a", "ana": "#Minor_Addition", "resp": "#RT", "cert": "medium"} in relations
    assert len(relations) == 2

    # The annotator is declared in the header so @resp resolves
    text = mark_path.read_text()
    assert '<respStmt xml:id="RT">' in text
    assert "<name>Test Annotator</name>" in text
    # The guidance comment survives saving
    assert "Decision order" in text


def test_set_classification_replaces(store, mark_path):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="first")
    store.set_classification("Mark.1.1.12-16", "b", "a", "Major_Addition", justification="second", cert="low")

    relations = relations_in_file(mark_path, "Mark.1.1.12-16")
    assert len(relations) == 2
    by_direction = {(relation["active"], relation["passive"]): relation for relation in relations}
    assert by_direction[("b", "a")]["ana"] == "#Major_Addition"
    assert by_direction[("a", "b")]["ana"] == "#Major_Omission"

    pair = store.get_unit("Mark.1.1.12-16").pairs[0]
    assert pair.category == "Major_Omission"
    assert pair.provenance.cert == "low"
    assert pair.provenance.justification == "Inverse of b → a: second"

    # The in-memory model matches the file
    reloaded = AnnotationStore(mark_path, read_only=True)
    assert reloaded.get_unit("Mark.1.1.12-16").pairs[0].category == "Major_Omission"
    assert reloaded.doc.relation_types["Minor_Omission"].pairs == set()


def test_set_classification_self_inverse(store):
    result = store.set_classification("Mark.1.2.8-14", "a", "e", "Major_Substitution")
    assert result.pair.category == "Major_Substitution"
    assert result.inverse.category == "Major_Substitution"


def test_set_classification_errors(store):
    server = build_server(store)
    result = call(server, "set_classification", unit_id="Mark.1.1.10", active="a", passive="b", category="Nonsense")
    assert result.is_error
    assert "Unknown category 'Nonsense'" in result.content[0].text

    result = call(server, "set_classification", unit_id="Mark.1.1.10", active="a", passive="z", category="Major_Omission")
    assert result.is_error
    assert "has no reading 'z'" in result.content[0].text

    result = call(server, "set_classification", unit_id="Mark.1.1.10", active="a", passive="a", category="Major_Omission")
    assert result.is_error

    result = call(server, "set_classification", unit_id="Mark.1.5.20-22", active="a", passive="ao", category="Orthography")
    assert result.is_error
    assert "is not classified" in result.content[0].text

    result = call(server, "set_classification", unit_id="Mark.1.1.10", active="a", passive="b", category="Major_Omission", cert="certain")
    assert result.is_error

    # Nothing was written
    assert store.list_units(status="unclassified").total == 10


def test_set_classification_requires_annotator(mark_path):
    store = AnnotationStore(mark_path)
    with pytest.raises(AnnotationError, match="annotator"):
        store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")


def test_clear_classification(store, mark_path):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    store.set_classification("Mark.1.1.12-16", "a", "c", "Major_Substitution")
    result = call(build_server(store), "clear_classification", unit_id="Mark.1.1.12-16", active="b", passive="a")
    assert not result.is_error
    assert result.structured_content["pair"]["category"] is None
    assert result.structured_content["inverse"]["category"] is None

    relations = relations_in_file(mark_path, "Mark.1.1.12-16")
    assert {(relation["active"], relation["passive"]) for relation in relations} == {("a", "c"), ("c", "a")}


def test_read_only(mark_path):
    store = AnnotationStore(mark_path, annotator="RT", read_only=True)
    server = build_server(store)
    assert list_tool_names(server) == ["get_definitions", "list_units", "get_unit", "category_counts", "list_category"]
    with pytest.raises(ReadOnlyError):
        store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    with pytest.raises(ReadOnlyError):
        store.clear_classification("Mark.1.1.10", "a", "b")


def test_tool_list(store):
    assert list_tool_names(build_server(store)) == [
        "get_definitions", "list_units", "get_unit", "category_counts", "list_category",
        "set_classification", "clear_classification",
    ]


def test_reloads_after_external_change(store, mark_path):
    other = AnnotationStore(mark_path, annotator="XY")
    time.sleep(0.01)
    other.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    # ensure the modification time differs even on coarse filesystems
    stat = mark_path.stat()
    os.utime(mark_path, (stat.st_atime, stat.st_mtime + 1))

    pair = store.get_unit("Mark.1.1.10").pairs[0]
    assert pair.category == "Major_Omission"
    assert pair.provenance.resp == "#XY"

    # A write from this store keeps the other store's work
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    assert relations_in_file(mark_path, "Mark.1.1.10")


def test_start_run_records_change(store, mark_path):
    change_id = store.start_run(model="test-model")
    assert change_id.startswith("rdgai-run-")
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", resp="rdgai", cert="medium", change=change_id)

    provenance = store.get_unit("Mark.1.1.10").pairs[0].provenance
    assert provenance.resp == "#rdgai"
    assert provenance.change == f"#{change_id}"
    assert provenance.model == "test-model"
    assert provenance.when.endswith("Z")

    text = mark_path.read_text()
    assert '<respStmt xml:id="rdgai">' in text
    assert '<name type="model">test-model</name>' in text
    assert store.start_run(model="test-model") != change_id


def test_legacy_relations_are_read(tmp_path):
    """ Relations written by older versions of rdgai (untyped <desc>, no @cert) are reported. """
    path = tmp_path / "minimal_output.xml"
    shutil.copy(TEST_DATA_DIR / "minimal_output.xml", path)
    store = AnnotationStore(path, annotator="RT")
    pairs = store.get_unit("app").pairs
    classified = [pair for pair in pairs if pair.category]
    assert classified
    for pair in classified:
        assert pair.provenance is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_serve_mcp_requires_annotator(mark_path):
    result = CliRunner().invoke(app, ["serve-mcp", str(mark_path)])
    assert result.exit_code != 0
    assert "--annotator" in result.output


# ---------------------------------------------------------------------------
# The full Mark file (skipped if not present)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not MARK_PATH.exists(), reason="Mark collation not available")
def test_mark_file(tmp_path):
    path = tmp_path / "mark.xml"
    shutil.copy(MARK_PATH, path)
    store = AnnotationStore(path, annotator="RT")
    server = build_server(store)

    assert store.list_units().total == 5626
    assert sum(len(store.substantive_pairs(app)) for app in store.doc.apps) == 39746

    definitions = call(server, "get_definitions").structured_content
    assert len(definitions["categories"]) == 10
    assert "Decision order" in definitions["guidance"]

    unit = call(server, "get_unit", unit_id="Mark.1.1.12-16").structured_content
    assert len(unit["pairs"]) == 15

    result = call(server, "set_classification", unit_id="Mark.1.1.12-16", active="a", passive="f", category="Major_Omission")
    assert result.structured_content["inverse"]["category"] == "Major_Addition"

    reloaded = AnnotationStore(path, read_only=True)
    pairs = {(pair.active, pair.passive): pair for pair in reloaded.get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "f")].category == "Major_Omission"
    assert pairs[("a", "f")].inverse_category == "Major_Addition"
    assert reloaded.list_units(status="partial").total == 1


# ---------------------------------------------------------------------------
# teiphy compatibility (skipped if teiphy is not available)
# ---------------------------------------------------------------------------

TEIPHY = shutil.which("teiphy") or "/Users/rturnbull/Documents/Joey/teiphy/.venv/bin/teiphy"


def test_relations_are_written_in_a_note(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    doc = Doc(mark_path)
    relation = doc["Mark.1.1.10"].pairs[0].relation_element()
    list_relation = relation.getparent()
    assert list_relation.tag.endswith("listRelation")
    assert list_relation.getparent().tag.endswith("note")
    assert list_relation.getparent().getparent() is doc["Mark.1.1.10"].element


@pytest.mark.skipif(not Path(TEIPHY).exists(), reason="teiphy not available")
def test_teiphy_reads_classifications(store, mark_path, tmp_path):
    import subprocess

    change_id = store.start_run(model="test-model")
    for index, app_ in enumerate(store.doc.apps):
        for pair in store.substantive_pairs(app_):
            if not pair.passive.text:
                category = "Major_Omission"
            elif not pair.active.text:
                category = "Major_Addition"
            else:
                category = "Minor_Substitution"
            if index % 2:
                store.set_classification(str(app_), pair.active.n, pair.passive.n, category, resp="rdgai", cert="medium", change=change_id)
            else:
                store.set_classification(str(app_), pair.active.n, pair.passive.n, category, justification="test")

    output = tmp_path / "beast.xml"
    result = subprocess.run(
        [TEIPHY, "-t", "orthographic", "-t", "defective", "--format", "beast", str(mark_path), str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    beast = output.read_text()
    # Categories that were used appear in the transition matrices, so more often than an unused
    # category (Minor_Omission), which only appears in its parameter declaration and prior
    unused = beast.count("Minor_Omission_rate")
    assert unused > 0
    assert beast.count("Major_Omission_rate") > unused
    assert beast.count("Major_Addition_rate") > unused
    assert beast.count("Minor_Substitution_rate") > unused


# ---------------------------------------------------------------------------
# by category
# ---------------------------------------------------------------------------

def test_category_counts(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    change_id = store.start_run(model="test-model")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", resp="rdgai", cert="medium", change=change_id)
    store.set_classification("Mark.1.2.8-14", "a", "e", "Major_Substitution")

    data = call(build_server(store), "category_counts").structured_content
    counts = {count["category"]: count for count in data["categories"]}
    assert data["classified"] == 3
    assert data["pairs_to_classify"] == sum(unit.pair_count for unit in store.list_units(limit=500).units)
    assert counts["Major_Omission"] == {"category": "Major_Omission", "total": 1, "human": 1, "pipeline": 0}
    assert counts["Major_Addition"]["total"] == 1
    assert counts["Minor_Omission"] == {"category": "Minor_Omission", "total": 1, "human": 0, "pipeline": 1}
    assert counts["Major_Substitution"]["total"] == 2  # self-inverse: both directions
    assert counts["Transposition"]["total"] == 0


def test_list_category(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="omits χριστου")
    change_id = store.start_run(model="test-model")
    store.set_classification("Mark.1.1.12-16", "f", "a", "Major_Addition", resp="rdgai", cert="low", change=change_id)

    server = build_server(store)
    data = call(server, "list_category", category="Major_Omission").structured_content
    assert data["total"] == 2
    first = data["pairs"][0]
    assert first["unit_id"] == "Mark.1.1.10"
    assert first["context"] == "αρχη του ευαγγελιου ιησου ⸂χριστου⸃ υιου του θεου"
    assert first["pair"]["active"] == "a"
    assert first["pair"]["provenance"]["justification"] == "omits χριστου"
    second = data["pairs"][1]
    assert (second["pair"]["active"], second["pair"]["passive"]) == ("a", "f")
    assert second["pair"]["provenance"]["model"] == "test-model"

    human = call(server, "list_category", category="Major_Omission", source="human").structured_content
    assert [pair["unit_id"] for pair in human["pairs"]] == ["Mark.1.1.10"]
    pipeline = call(server, "list_category", category="Major_Omission", source="pipeline").structured_content
    assert [pair["unit_id"] for pair in pipeline["pairs"]] == ["Mark.1.1.12-16"]

    result = call(server, "list_category", category="Nonsense")
    assert result.is_error
