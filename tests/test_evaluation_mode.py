"""
Suggestions are refused on test units by default, and in evaluation mode no tool reveals the labels or suggestions
of test units: every read reports them as unlabelled, and writes to them are refused (whether labelled or not).
"""
import shutil

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from rdgai import sampling
from rdgai.annotation import AnnotationStore, AnnotationError, DefinitionsLockedError
from rdgai.gui import create_app
from rdgai.main import app as cli
from rdgai.mcp_server import build_server
from rdgai.rules import SubreadingRule

from .util import TEST_DATA_DIR
from .test_mcp import call, list_tool_names

TEST_UNITS = ["Mark.1.1.10", "Mark.1.1.12-16", "Mark.1.5.20-22"]
EXAMPLE_UNITS = ["Mark.1.2.2", "Mark.1.2.8-14"]


@pytest.fixture
def path(tmp_path):
    """ mark_sample.xml with a sample (3 test units, 2 example units), labels, suggestions and rule labels on both. """
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    store = AnnotationStore(path, annotator="RT", check_sample=False)
    units = [sampling.SampleUnit(id=unit_id, role="test") for unit_id in TEST_UNITS]
    units += [sampling.SampleUnit(id=unit_id, role="example") for unit_id in EXAMPLE_UNITS]
    order = [unit.id for unit in store.list_units(limit=500).units]
    units.sort(key=lambda unit: order.index(unit.id))
    sampling.save_sample(sampling.Sample(
        seed=0, size=5, test_fraction=0.6, ignore_types=["defective"], eligible_count=10,
        created="2026-11-13T00:00:00Z", source=path.name, units=units,
    ), store.sample_path)

    store.apply_rules(rules=[SubreadingRule("orthographic", "Orthography", "o")])  # a–ao etc. in Mark.1.5.20-22 (test)
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="gold")       # test unit
    store.set_classification("Mark.1.5.20-22", "a", "b", "Minor_Substitution")                      # test unit, parent of rule-3 pairs
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution", justification="example")  # example unit
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", source="claude", allow_test_unit=True)
    store.suggest_classification("Mark.1.2.8-14", "b", "a", "Minor_Addition", source="claude")
    return path


@pytest.fixture
def evaluation(path):
    return AnnotationStore(path, annotator="RT", evaluation=True)


def categories(unit):
    return {(pair.active, pair.passive): pair.category for pair in unit.pairs}


# ---------------------------------------------------------------------------
# suggestions on test units (outside evaluation mode)
# ---------------------------------------------------------------------------

def test_suggestion_on_a_test_unit_is_refused_by_default(path):
    store = AnnotationStore(path, annotator="RT")
    before = path.read_bytes()
    with pytest.raises(AnnotationError, match="test unit"):
        store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    assert path.read_bytes() == before
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude", allow_test_unit=True)
    store.suggest_classification("Mark.1.2.2", "a", "b", "Minor_Substitution", source="claude")  # example unit: fine


def test_mcp_suggestion_on_a_test_unit(path):
    server = build_server(AnnotationStore(path, annotator="RT"))
    result = call(server, "suggest_classification", unit_id="Mark.1.1.10", active="a", passive="b", category="Major_Omission")
    assert result.is_error
    assert "test unit" in result.content[0].text
    result = call(server, "suggest_classification", unit_id="Mark.1.1.10", active="a", passive="b", category="Major_Omission", allow_test_unit=True)
    assert not result.is_error


# ---------------------------------------------------------------------------
# evaluation mode: setting up
# ---------------------------------------------------------------------------

def test_evaluation_requires_a_sample(tmp_path):
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    with pytest.raises(AnnotationError, match="sample"):
        AnnotationStore(path, evaluation=True)


def test_evaluation_locks_the_definitions(evaluation):
    with pytest.raises(DefinitionsLockedError):
        evaluation.update_category("Transposition", description="x")
    assert len(evaluation.definitions().definitions_hash) == 64


def test_evaluation_tools(evaluation, path):
    names = list_tool_names(build_server(evaluation))
    for hidden in ("update_category", "add_category", "remove_category", "update_guidance", "apply_rules", "draw_sample"):
        assert hidden not in names
    for kept in ("get_unit", "list_units", "get_suggestions", "set_classification", "suggest_classification"):
        assert kept in names


# ---------------------------------------------------------------------------
# evaluation mode: nothing about test units is revealed by any read
# ---------------------------------------------------------------------------

def test_get_unit_hides_test_labels(evaluation):
    unit = evaluation.get_unit("Mark.1.1.10")
    pair = unit.pairs[0]
    assert (pair.category, pair.inverse_category, pair.provenance, pair.source, pair.warning) == (None, None, None, None, None)
    assert unit.status == "unclassified"

    rules = {(pair.active, pair.passive): pair for pair in evaluation.get_unit("Mark.1.5.20-22").pairs}
    assert rules[("a", "ao")].rule == 1           # which pairs a rule covers is structural, not a label
    assert rules[("a", "ao")].category is None    # but rule labels are hidden
    assert rules[("ao", "b")].category is None    # including those copied from a gold label (rule 3)

    # example units are unaffected
    assert categories(evaluation.get_unit("Mark.1.2.2"))[("a", "b")] == "Minor_Substitution"


def test_mcp_get_unit_hides_test_labels(evaluation):
    unit = call(build_server(evaluation), "get_unit", unit_id="Mark.1.1.10").structured_content
    assert unit["pairs"][0]["category"] is None
    assert unit["pairs"][0]["provenance"] is None


def test_list_units_hides_test_progress(evaluation):
    units = {unit.id: unit for unit in evaluation.list_units(limit=500).units}
    assert units["Mark.1.1.10"].classified_count == 0
    assert units["Mark.1.1.10"].status == "unclassified"
    assert units["Mark.1.2.2"].classified_count == 1
    classified = [unit.id for unit in evaluation.list_units(status="classified", limit=500).units]
    assert "Mark.1.1.10" not in classified
    assert "Mark.1.2.2" in classified


def test_category_listing_and_counts_hide_test_labels(evaluation):
    listed = evaluation.list_category("Major_Omission")
    assert listed.total == 0
    listed = evaluation.list_category("Minor_Substitution")
    assert {pair.unit_id for pair in listed.pairs} == {"Mark.1.2.2"}
    # rule labels are hidden on test units only (Mark.1.2.43 is not in the sample)
    assert {pair.unit_id for pair in evaluation.list_category("Orthography").pairs} == {"Mark.1.2.43"}

    counts = {count.category: count for count in evaluation.category_counts().categories}
    assert counts["Major_Omission"].total == 0
    assert counts["Orthography"].total == 2  # Mark.1.2.43 b–bo, both ways
    assert counts["Minor_Substitution"].total == 2  # Mark.1.2.2 both ways


def test_suggestions_hidden_on_test_units(evaluation):
    assert [suggestion.unit_id for suggestion in evaluation.get_suggestions()] == ["Mark.1.2.8-14"]
    assert evaluation.get_suggestions("Mark.1.1.12-16") == []
    data = call(build_server(evaluation), "get_suggestions", unit_id="Mark.1.1.12-16").structured_content
    assert data["suggestions"] == []


def test_sample_progress_hides_test_labels(evaluation):
    progress = {item.role: item for item in evaluation.sample_info().progress}
    assert progress["test"].pairs_classified == 0
    assert progress["example"].pairs_classified == 1


def test_gui_hides_test_labels(evaluation):
    client = TestClient(create_app(evaluation))
    assert client.get("/api/config").json()["evaluation"] is True
    data = client.get("/api/unit/Mark.1.5.20-22", params={"sample": "all"}).json()
    assert all(pair["category"] is None for pair in data["pairs"] + data["rule_pairs"])
    assert all(pair["reason"] == "" and pair["suggestions"] == [] for pair in data["pairs"])
    assert data["progress"]["pairs_done"] == 0
    queue = {unit["id"]: unit for unit in client.get("/api/queue", params={"sample": "test"}).json()["units"]}
    assert all(unit["classified_count"] == 0 for unit in queue.values())


def test_reads_do_not_change_the_file(evaluation, path):
    before = path.read_bytes()
    evaluation.get_unit("Mark.1.1.10")
    evaluation.list_units()
    evaluation.category_counts()
    evaluation.list_category("Major_Omission")
    evaluation.get_suggestions()
    evaluation.sample_info()
    assert path.read_bytes() == before
    # and the gold labels are still there for the evaluation
    plain = AnnotationStore(path, read_only=True)
    assert categories(plain.get_unit("Mark.1.1.10"))[("a", "b")] == "Major_Omission"


# ---------------------------------------------------------------------------
# evaluation mode: test units cannot be changed (the refusal is the same, labelled or not)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unit_id, active, passive", [("Mark.1.1.10", "a", "b"), ("Mark.1.1.12-16", "a", "c")])
def test_writes_to_test_units_are_refused(evaluation, path, unit_id, active, passive):
    before = path.read_bytes()
    messages = set()
    for write in (
        lambda: evaluation.set_classification(unit_id, active, passive, "Major_Substitution"),
        lambda: evaluation.set_classifications(unit_id, [(active, passive)], "Major_Substitution"),
        lambda: evaluation.clear_classification(unit_id, active, passive),
        lambda: evaluation.set_certainty(unit_id, [(active, passive)], cert="low"),
        lambda: evaluation.suggest_classification(unit_id, active, passive, "Major_Substitution", source="claude", allow_test_unit=True),
        lambda: evaluation.clear_suggestions(unit_id, active, passive),
    ):
        with pytest.raises(AnnotationError, match="evaluation mode") as error:
            write()
        messages.add(str(error.value))
    assert path.read_bytes() == before
    assert messages == {f"In evaluation mode, test unit {unit_id} cannot be changed."}


def test_writes_to_example_units_still_work(evaluation):
    evaluation.set_classification("Mark.1.2.8-14", "e", "b", "Major_Substitution")
    assert categories(evaluation.get_unit("Mark.1.2.8-14"))[("b", "e")] == "Major_Substitution"


def test_rules_and_sampling_refused_in_evaluation_mode(evaluation):
    with pytest.raises(AnnotationError, match="evaluation mode"):
        evaluation.apply_rules()
    with pytest.raises(AnnotationError, match="evaluation mode"):
        evaluation.draw_sample(size=3, seed=1, overwrite=True)


def test_cli_evaluation_option():
    for command in ("serve-mcp", "annotate"):
        assert "--evaluation" in CliRunner().invoke(cli, [command, "--help"]).output
