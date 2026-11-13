"""
Rule-based labels for typed subreadings (e.g. ECM orthographic readings 'ao', 'ao1' of reading 'a').

Rule 1: subreading <-> parent gets the mapped category.
Rule 2: two subreadings of the same parent and type get the mapped category.
Rule 3: subreading <-> anything else copies the label of the corresponding pair of parents.
"""
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from rdgai.apparatus import Doc
from rdgai.annotation import AnnotationStore, AnnotationError
from rdgai.rules import SubreadingRule, find_parents
from rdgai.gui import create_app
from rdgai.main import app as cli
from rdgai.mcp_server import build_server

from .util import TEST_DATA_DIR
from .test_mcp import call, list_tool_names

ORTHOGRAPHIC = SubreadingRule(reading_type="orthographic", category="Orthography", suffix="o")
TEI = "{http://www.tei-c.org/ns/1.0}"
TEIPHY = shutil.which("teiphy") or "/Users/rturnbull/Documents/Joey/teiphy/.venv/bin/teiphy"


@pytest.fixture
def path(tmp_path) -> Path:
    path = tmp_path / "subreadings.xml"
    shutil.copy(TEST_DATA_DIR / "subreadings.xml", path)
    return path


@pytest.fixture
def store(path) -> AnnotationStore:
    return AnnotationStore(path, annotator="RT")


@pytest.fixture
def ruled(store) -> AnnotationStore:
    store.apply_rules(rules=[ORTHOGRAPHIC])
    return store


def label(store, unit_id, active, passive):
    """ (category, resp, cert, justification) of a directed pair, or None. """
    app = store.get_app(unit_id)
    pair = store.get_pair(app, active, passive)
    relation = pair.relation_element()
    if relation is None:
        return None
    provenance = store.provenance_from_relation(relation)
    return store.current_category(pair), provenance.resp, provenance.cert, provenance.justification


def category(store, unit_id, active, passive):
    result = label(store, unit_id, active, passive)
    return result[0] if result else None


def counts(report):
    return {count.rule: (count.covered, count.written, count.unchanged, count.waiting) for count in report.counts}


# ---------------------------------------------------------------------------
# finding parents
# ---------------------------------------------------------------------------

def test_find_parents(store):
    parents = find_parents(store.get_app("Mark.1.1.2"), [ORTHOGRAPHIC])
    assert parents.parents == {"ao1": "a", "ao2": "a", "bo": "b"}
    assert parents.unresolved == []
    assert parents.nested == []


def test_unresolved_parent_is_reported_not_guessed(store):
    parents = find_parents(store.get_app("Mark.1.1.6"), [ORTHOGRAPHIC])
    assert parents.parents == {}
    assert parents.unresolved == ["ax"]


def test_subreading_of_a_subreading_is_reported(store):
    parents = find_parents(store.get_app("Mark.1.1.8"), [ORTHOGRAPHIC])
    assert parents.parents == {"ao": "a"}
    assert parents.nested == [("aoo1", "ao")]


def test_suffix_is_configurable(store):
    defective = SubreadingRule(reading_type="defective", category="Orthography", suffix="f")
    parents = find_parents(store.get_app("Mark.1.1.10"), [ORTHOGRAPHIC, defective])
    assert parents.parents == {"bf": "b"}


# ---------------------------------------------------------------------------
# the three rules
# ---------------------------------------------------------------------------

def test_rule_1_subreading_and_parent(ruled):
    assert label(ruled, "Mark.1.1.2", "a", "ao1") == (
        "Orthography", "#rule-orthographic", "high", "ao1 is an orthographic subreading of a.",
    )
    assert category(ruled, "Mark.1.1.2", "ao1", "a") == "Orthography"
    assert category(ruled, "Mark.1.1.2", "b", "bo") == "Orthography"
    assert category(ruled, "Mark.1.1.8", "a", "ao") == "Orthography"


def test_rule_2_sibling_subreadings(ruled):
    assert label(ruled, "Mark.1.1.2", "ao1", "ao2") == (
        "Orthography", "#rule-orthographic", "high", "ao1 and ao2 are both orthographic subreadings of a.",
    )
    assert category(ruled, "Mark.1.1.2", "ao2", "ao1") == "Orthography"


def test_rule_3_waits_for_the_parent_pair(ruled):
    for active, passive in [("ao1", "b"), ("ao1", "bo"), ("a", "bo"), ("ao2", "c"), ("bo", "c")]:
        assert category(ruled, "Mark.1.1.2", active, passive) is None


def test_rule_3_copies_the_parent_pair(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution", justification="different verb")
    # the store re-applies the rules to the unit after every save
    for active, passive in [("ao1", "b"), ("ao2", "b"), ("ao1", "bo"), ("ao2", "bo"), ("a", "bo")]:
        assert category(ruled, "Mark.1.1.2", active, passive) == "Minor_Substitution"
    assert label(ruled, "Mark.1.1.2", "ao1", "bo") == (
        "Minor_Substitution", "#rule-orthographic", "high", "Same as a → b; spelling difference ignored.",
    )


def test_rule_3_sets_the_inverse(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "c", "Major_Omission")
    assert category(ruled, "Mark.1.1.2", "ao1", "c") == "Major_Omission"
    assert category(ruled, "Mark.1.1.2", "c", "ao1") == "Major_Addition"
    ruled.set_classification("Mark.1.1.2", "c", "b", "Major_Addition")
    assert category(ruled, "Mark.1.1.2", "bo", "c") == "Major_Omission"
    assert category(ruled, "Mark.1.1.2", "c", "bo") == "Major_Addition"


def test_rule_3_follows_parent_changes(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    ruled.set_classification("Mark.1.1.2", "a", "b", "Major_Substitution")
    assert category(ruled, "Mark.1.1.2", "ao1", "b") == "Major_Substitution"
    ruled.clear_classification("Mark.1.1.2", "a", "b")
    assert category(ruled, "Mark.1.1.2", "ao1", "b") is None


def test_rule_3_follows_bulk_and_certainty_changes(ruled):
    ruled.set_classifications("Mark.1.1.2", [("a", "b"), ("a", "c")], "Major_Substitution", cert="medium")
    assert label(ruled, "Mark.1.1.2", "ao1", "c")[2] == "medium"
    ruled.set_certainty("Mark.1.1.2", [("a", "c")], cert="low")
    assert label(ruled, "Mark.1.1.2", "ao1", "c")[2] == "low"


def test_rule_3_from_a_tentative_parent_is_tentative(ruled):
    run = ruled.start_run(model="test-model")
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution", resp="rdgai", cert="medium", change=run)
    app = ruled.get_app("Mark.1.1.2")
    derived = ruled.get_pair(app, "ao1", "b")
    assert ruled.label_source(derived) == "rule"
    assert ruled.is_tentative(derived)
    provenance = ruled.provenance_from_relation(derived.relation_element())
    assert provenance.cert == "medium"
    assert provenance.model == "test-model"

    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    assert not ruled.is_tentative(ruled.get_pair(ruled.get_app("Mark.1.1.2"), "ao1", "b"))


# ---------------------------------------------------------------------------
# precedence
# ---------------------------------------------------------------------------

def test_human_label_is_never_overwritten_and_conflict_is_reported(store):
    store.set_classification("Mark.1.1.2", "ao1", "ao2", "Major_Substitution")
    store.set_classification("Mark.1.1.2", "a", "ao1", "Orthography")  # agrees with the rule
    report = store.apply_rules(rules=[ORTHOGRAPHIC])
    assert label(store, "Mark.1.1.2", "ao1", "ao2")[:2] == ("Major_Substitution", "#RT")
    assert label(store, "Mark.1.1.2", "a", "ao1")[:2] == ("Orthography", "#RT")
    assert [(c.unit_id, c.active, c.passive, c.label, c.rule_label, c.rule) for c in report.conflicts] == [
        ("Mark.1.1.2", "ao1", "ao2", "Major_Substitution", "Orthography", 2),
    ]


def test_human_override_of_a_derived_label_survives_parent_changes(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    ruled.set_classification("Mark.1.1.2", "ao1", "b", "Major_Substitution")  # annotator overrides the rule
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Omission")
    assert label(ruled, "Mark.1.1.2", "ao1", "b")[:2] == ("Major_Substitution", "#RT")
    assert category(ruled, "Mark.1.1.2", "ao2", "b") == "Minor_Omission"
    report = ruled.apply_rules()
    assert ("ao1", "b") in [(c.active, c.passive) for c in report.conflicts]


def test_clearing_a_human_override_restores_the_rule_label(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "ao1", "Major_Substitution")
    assert label(ruled, "Mark.1.1.2", "a", "ao1")[1] == "#RT"
    ruled.clear_classification("Mark.1.1.2", "a", "ao1")
    assert label(ruled, "Mark.1.1.2", "a", "ao1")[:2] == ("Orthography", "#rule-orthographic")


def test_pipeline_labels_on_rule_pairs_are_overwritten(store):
    store.set_classification("Mark.1.1.2", "a", "ao1", "Major_Substitution", resp="rdgai", cert="medium")
    store.apply_rules(rules=[ORTHOGRAPHIC])
    assert label(store, "Mark.1.1.2", "a", "ao1")[:3] == ("Orthography", "#rule-orthographic", "high")


# ---------------------------------------------------------------------------
# reports, idempotence and configuration
# ---------------------------------------------------------------------------

def test_report(store):
    report = store.apply_rules(rules=[ORTHOGRAPHIC])
    assert counts(report) == {
        1: (4, 4, 0, 0),  # a-ao1, a-ao2, b-bo, and a-ao in Mark.1.1.8
        2: (1, 1, 0, 0),  # ao1-ao2
        3: (9, 0, 0, 9),  # all waiting for their parent pair
    }
    assert [(item.unit_id, item.reading) for item in report.unresolved] == [("Mark.1.1.6", "ax")]
    assert [(item.unit_id, item.reading, item.parent) for item in report.nested] == [("Mark.1.1.8", "aoo1", "ao")]
    assert report.conflicts == []
    assert report.dry_run is False


def test_second_run_changes_nothing(store, path):
    store.apply_rules(rules=[ORTHOGRAPHIC])
    store.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    before = path.read_bytes()
    report = store.apply_rules()
    assert path.read_bytes() == before
    assert counts(report)[1] == (4, 0, 4, 0)
    assert counts(report)[3] == (9, 0, 5, 4)


def test_dry_run_changes_nothing(store, path):
    before = path.read_bytes()
    report = store.apply_rules(rules=[ORTHOGRAPHIC], dry_run=True)
    assert report.dry_run is True
    assert counts(report)[1] == (4, 4, 0, 0)
    assert path.read_bytes() == before


def test_rules_are_saved_in_the_file(ruled, path):
    doc = Doc(path)
    groups = [group for group in doc.tree.iter(f"{TEI}interpGrp") if group.get("type") == "subreading-rules"]
    assert len(groups) == 1
    rule = groups[0].find(f"{TEI}interp")
    assert rule.get("{http://www.w3.org/XML/1998/namespace}id") == "rule-orthographic"
    assert (rule.get("type"), rule.get("ana"), rule.get("n")) == ("orthographic", "#Orthography", "o")
    # placed right after the category definitions
    assert groups[0].getprevious().get("type") == "transcriptional"

    reloaded = AnnotationStore(path, annotator="RT")
    assert reloaded.rules == [ORTHOGRAPHIC]


def test_no_rules_configured(store):
    with pytest.raises(AnnotationError, match="No subreading rules"):
        store.apply_rules()


def test_rule_with_unknown_category(store):
    with pytest.raises(AnnotationError, match="Unknown category"):
        store.apply_rules(rules=[SubreadingRule(reading_type="orthographic", category="Spelling", suffix="o")])


def test_rules_have_no_effect_until_configured(store):
    store.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    assert category(store, "Mark.1.1.2", "a", "ao1") is None
    assert category(store, "Mark.1.1.2", "ao1", "b") is None


def test_defective_rule_with_its_own_suffix(path):
    store = AnnotationStore(path, annotator="RT", ignore_types=[])
    defective = SubreadingRule(reading_type="defective", category="Orthography", suffix="f")
    store.apply_rules(rules=[ORTHOGRAPHIC, defective])
    assert label(store, "Mark.1.1.10", "b", "bf")[:2] == ("Orthography", "#rule-defective")
    store.set_classification("Mark.1.1.10", "a", "b", "Minor_Substitution")
    assert category(store, "Mark.1.1.10", "a", "bf") == "Minor_Substitution"


def test_renaming_a_category_updates_the_rules(ruled):
    ruled.update_category("Orthography", new_name="Spelling")
    assert ruled.rules[0].category == "Spelling"
    assert category(ruled, "Mark.1.1.2", "a", "ao1") == "Spelling"


def test_removing_a_category_used_by_a_rule(ruled):
    with pytest.raises(AnnotationError, match="rule"):
        ruled.remove_category("Orthography")


# ---------------------------------------------------------------------------
# effects on annotation: queue, progress, counts
# ---------------------------------------------------------------------------

def test_rule_pairs_are_not_work(ruled):
    units = {unit.id: unit for unit in ruled.list_units(limit=500).units}
    assert (units["Mark.1.1.2"].pair_count, units["Mark.1.1.2"].rule_pair_count) == (3, 12)
    assert (units["Mark.1.1.8"].pair_count, units["Mark.1.1.8"].rule_pair_count) == (4, 2)  # aoo1 is not covered
    assert (units["Mark.1.1.6"].pair_count, units["Mark.1.1.6"].rule_pair_count) == (3, 0)  # ax is not covered

    ruled.set_classifications("Mark.1.1.2", [("a", "b"), ("a", "c"), ("b", "c")], "Major_Substitution")
    assert ruled.get_unit("Mark.1.1.2").status == "classified"
    assert "Mark.1.1.2" not in [unit.id for unit in ruled.list_units(status="unclassified").units]


def test_get_unit_reports_label_sources(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    pairs = {(pair.active, pair.passive): pair for pair in ruled.get_unit("Mark.1.1.2").pairs}
    assert pairs[("a", "b")].source == "human"
    assert pairs[("a", "ao1")].source == "rule"
    assert pairs[("a", "ao1")].rule == 1
    assert pairs[("ao1", "b")].rule == 3
    assert pairs[("ao1", "b")].rule_parent == ["a", "b"]
    assert pairs[("a", "c")].source is None
    assert pairs[("a", "c")].rule is None


def test_sample_progress_excludes_rule_pairs(ruled):
    before = ruled.eligible_units()
    info = ruled.draw_sample(size=5, seed=0)
    progress = {item.role: item for item in info.progress}
    assert progress["all"].pairs == 3 + 4 + 3 + 1 + 1  # work pairs only (bf is defective, so excluded)
    assert progress["all"].rule_pairs == 12 + 2
    # sampling eligibility does not depend on the rules, so existing samples stay reproducible
    assert [str(app) for app in ruled.eligible_units()] == [str(app) for app in before]


def test_category_counts_have_a_rule_source(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    counts_ = {count.category: count for count in ruled.category_counts().categories}
    assert counts_["Orthography"].rule == 10  # 5 unordered pairs, both directions
    assert counts_["Orthography"].human == 0
    assert counts_["Minor_Substitution"].human == 2
    assert counts_["Minor_Substitution"].rule == 10
    totals = ruled.category_counts()
    assert totals.rule_pairs == 14
    assert totals.pairs_to_classify == 3 + 4 + 3 + 1 + 1


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def test_evaluation_excludes_rule_labelled_pairs(ruled, path):
    """ Rule-covered pairs are not decisions: they are left out of the gold set and the scores. """
    from rdgai.evaluate import gold_labels, metrics
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")  # human; rule 3 copies it to ao1-b, ...
    gold = gold_labels(ruled, ["Mark.1.1.2"])
    assert gold == {("Mark.1.1.2", "a", "b"): "Minor_Substitution"}
    assert len(ruled.rule_pairs(ruled.get_app("Mark.1.1.2"))) == 12
    assert metrics(gold, gold, ["Minor_Substitution"]).n == 1


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def test_gui_separates_rule_pairs(ruled):
    client = TestClient(create_app(ruled))
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    data = client.get("/api/unit/Mark.1.1.2").json()
    assert [(pair["first"], pair["second"]) for pair in data["pairs"]] == [("a", "b"), ("a", "c"), ("b", "c")]
    assert len(data["rule_pairs"]) == 12
    assert data["progress"] == {"pairs_done": 1, "pairs_total": 3, "rule_pairs": 12}
    rule_pairs = {(pair["first"], pair["second"]): pair for pair in data["rule_pairs"]}
    assert rule_pairs[("a", "ao1")]["source"] == "rule"
    assert rule_pairs[("a", "ao1")]["category"] == "Orthography"
    assert rule_pairs[("ao1", "b")]["rule"] == {"rule": 3, "parent": ["a", "b"], "waiting": False}
    assert rule_pairs[("ao1", "c")]["rule"] == {"rule": 3, "parent": ["a", "c"], "waiting": True}
    assert data["pairs"][0]["source"] == "human"


def test_gui_override_of_a_rule_pair(ruled):
    client = TestClient(create_app(ruled))
    response = client.post("/api/label", json={
        "unit_id": "Mark.1.1.2", "pairs": [["a", "ao1"]], "category": "Major_Substitution", "expected": {"a|ao1": "Orthography"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["pairs"][0]["source"] == "human"
    response = client.post("/api/label", json={"unit_id": "Mark.1.1.2", "pairs": [["a", "ao1"]], "category": None})
    assert response.json()["pairs"][0]["source"] == "rule"
    assert response.json()["pairs"][0]["category"] == "Orthography"


# ---------------------------------------------------------------------------
# MCP and CLI
# ---------------------------------------------------------------------------

def test_mcp_apply_rules(store, path):
    server = build_server(store)
    result = call(server, "apply_rules", rules={"orthographic": "Orthography"}, suffixes={"orthographic": "o"}, dry_run=True)
    assert not result.is_error, result.content
    assert result.structured_content["dry_run"] is True
    result = call(server, "apply_rules", rules={"orthographic": "Orthography"})
    report = result.structured_content
    assert {count["rule"]: count["written"] for count in report["counts"]} == {1: 4, 2: 1, 3: 0}
    assert call(server, "apply_rules").structured_content["counts"][0]["unchanged"] == 4

    unit = call(server, "get_unit", unit_id="Mark.1.1.2").structured_content
    assert {pair["source"] for pair in unit["pairs"]} == {"rule", None}

    assert "apply_rules" not in list_tool_names(build_server(AnnotationStore(path, read_only=True)))


def test_cli_apply_rules(path, tmp_path):
    runner = CliRunner()
    before = path.read_bytes()
    result = runner.invoke(cli, ["apply-rules", str(path), "--rule", "orthographic=Orthography", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Rule 1" in result.output and "4 pairs" in result.output
    assert path.read_bytes() == before

    result = runner.invoke(cli, ["apply-rules", str(path), "--rule", "orthographic=Orthography"])
    assert result.exit_code != 0  # needs --inplace or --output

    output = tmp_path / "out.xml"
    result = runner.invoke(cli, ["apply-rules", str(path), "--rule", "orthographic=Orthography", "--suffix", "orthographic=o", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert category(AnnotationStore(output, read_only=True), "Mark.1.1.2", "a", "ao1") == "Orthography"
    assert "Unresolved parents: Mark.1.1.6 ax" in result.output
    assert "Nested subreadings: Mark.1.1.8 aoo1 (of ao)" in result.output

    # idempotent, and the rules come from the file the second time
    again = output.read_bytes()
    result = runner.invoke(cli, ["apply-rules", str(output), "--inplace"])
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == again


# ---------------------------------------------------------------------------
# teiphy
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path(TEIPHY).exists(), reason="teiphy not available")
def test_teiphy_reads_rule_labels(ruled, path, tmp_path):
    for unit in ruled.list_units(limit=500).units:
        pairs = [(pair.active, pair.passive) for pair in ruled.get_unit(unit.id).pairs if pair.rule is None and pair.category is None]
        if pairs:
            ruled.set_classifications(unit.id, pairs, "Major_Substitution")
    output = tmp_path / "beast.xml"
    result = subprocess.run([TEIPHY, "-t", "defective", "--format", "beast", str(path), str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    beast = output.read_text()
    unused = beast.count("Minor_Omission_rate")
    assert beast.count("Orthography_rate") > unused
