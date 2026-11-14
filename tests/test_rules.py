"""
Rule-based labels for typed subreadings (e.g. ECM orthographic readings 'ao', 'ao1' of reading 'a').

Rule 1: subreading <-> parent gets the mapped category.
Rule 2: two subreadings of the same parent and type get the mapped category.
Any other pair with a subreading (e.g. ao-b) is an ordinary pair, for an annotator or the pipeline.
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
    return {count.rule: (count.covered, count.written, count.unchanged) for count in report.counts}


OTHER_SUBREADING_PAIRS = [("ao1", "b"), ("ao1", "bo"), ("a", "bo"), ("ao2", "c"), ("bo", "c")]


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
# the two rules
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


def test_other_subreading_pairs_are_not_covered(ruled):
    """ A subreading only determines its pair with its parent (and its siblings): ao-b is an ordinary pair. """
    app = ruled.get_app("Mark.1.1.2")
    for active, passive in OTHER_SUBREADING_PAIRS:
        assert ruled.coverage_for(ruled.get_pair(app, active, passive)) is None, (active, passive)
        assert category(ruled, "Mark.1.1.2", active, passive) is None


def test_labelling_the_parents_pair_writes_nothing_on_subreading_pairs(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    for active, passive in OTHER_SUBREADING_PAIRS:
        assert category(ruled, "Mark.1.1.2", active, passive) is None, (active, passive)
    assert category(ruled, "Mark.1.1.2", "a", "ao1") == "Orthography"   # rule labels are untouched


def test_rule_labels_are_never_tentative(ruled):
    run = ruled.start_run(model="test-model")
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution", resp="rdgai", cert="medium", change=run)
    app = ruled.get_app("Mark.1.1.2")
    assert not ruled.is_tentative(ruled.get_pair(app, "a", "ao1"))
    assert ruled.is_tentative(ruled.get_pair(app, "a", "b"))


def test_leftover_rule_3_labels_are_removed(ruled, path):
    """ Labels written by the old rule 3 (resp #rule-..., on a pair no rule covers) are removed and counted. """
    ruled.set_classification("Mark.1.1.2", "ao1", "b", "Minor_Substitution", resp="rule-orthographic")
    ruled.set_classification("Mark.1.1.2", "ao1", "bo", "Minor_Substitution", resp="rule-orthographic")
    ruled.set_classification("Mark.1.1.2", "ao2", "c", "Major_Omission")          # human: kept
    ruled.set_classification("Mark.1.1.2", "a", "bo", "Minor_Substitution", resp="rdgai", cert="low")  # pipeline: kept

    dry = ruled.apply_rules(dry_run=True)
    assert dry.removed == 2
    assert category(ruled, "Mark.1.1.2", "ao1", "b") == "Minor_Substitution"   # a dry run removes nothing

    report = ruled.apply_rules()
    assert report.removed == 2
    for active, passive in [("ao1", "b"), ("b", "ao1"), ("ao1", "bo"), ("bo", "ao1")]:
        assert category(ruled, "Mark.1.1.2", active, passive) is None
    assert label(ruled, "Mark.1.1.2", "ao2", "c")[:2] == ("Major_Omission", "#RT")
    assert label(ruled, "Mark.1.1.2", "a", "bo")[:2] == ("Minor_Substitution", "#rdgai")
    assert ruled.apply_rules().removed == 0


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
        1: (4, 4, 0),  # a-ao1, a-ao2, b-bo, and a-ao in Mark.1.1.8
        2: (1, 1, 0),  # ao1-ao2
    }
    assert report.removed == 0
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
    assert counts(report) == {1: (4, 0, 4), 2: (1, 0, 1)}


def test_dry_run_changes_nothing(store, path):
    before = path.read_bytes()
    report = store.apply_rules(rules=[ORTHOGRAPHIC], dry_run=True)
    assert report.dry_run is True
    assert counts(report)[1] == (4, 4, 0)
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


def test_rule_description_has_no_rule_3():
    text = ORTHOGRAPHIC.description()
    assert "corresponding pair of parents" not in text
    assert "are labelled Orthography" in text


def test_outdated_rule_description_is_updated(ruled, path):
    """ Files written before rule 3 was removed describe it; apply_rules brings the description up to date. """
    old = ORTHOGRAPHIC.description() + " Any other pair with a subreading has the label of the corresponding pair of parents."
    text = path.read_text(encoding="utf-8").replace(ORTHOGRAPHIC.description(), old)
    path.write_text(text, encoding="utf-8")
    store = AnnotationStore(path, annotator="RT")

    report = store.apply_rules(dry_run=True)
    assert report.descriptions_updated == ["rule-orthographic"]
    assert "corresponding pair of parents" in path.read_text(encoding="utf-8")

    report = store.apply_rules()
    assert report.descriptions_updated == ["rule-orthographic"]
    assert "corresponding pair of parents" not in path.read_text(encoding="utf-8")
    assert store.apply_rules().descriptions_updated == []


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
    assert category(store, "Mark.1.1.10", "a", "bf") is None   # an ordinary pair now


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
    assert (units["Mark.1.1.2"].pair_count, units["Mark.1.1.2"].rule_pair_count) == (11, 4)
    assert (units["Mark.1.1.8"].pair_count, units["Mark.1.1.8"].rule_pair_count) == (5, 1)  # only a-ao is covered
    assert (units["Mark.1.1.6"].pair_count, units["Mark.1.1.6"].rule_pair_count) == (3, 0)  # ax is not covered

    work = [(pair.active.n, pair.passive.n) for pair in ruled.work_pairs(ruled.get_app("Mark.1.1.2"))]
    assert len(work) == 11
    ruled.set_classifications("Mark.1.1.2", work[:3], "Major_Substitution")
    assert ruled.get_unit("Mark.1.1.2").status == "partial"
    ruled.set_classifications("Mark.1.1.2", work[3:], "Major_Substitution")
    assert ruled.get_unit("Mark.1.1.2").status == "classified"
    assert "Mark.1.1.2" not in [unit.id for unit in ruled.list_units(status="unclassified").units]


def test_get_unit_reports_label_sources(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    pairs = {(pair.active, pair.passive): pair for pair in ruled.get_unit("Mark.1.1.2").pairs}
    assert pairs[("a", "b")].source == "human"
    assert pairs[("a", "ao1")].source == "rule"
    assert pairs[("a", "ao1")].rule == 1
    assert pairs[("ao1", "b")].rule is None
    assert pairs[("ao1", "b")].source is None
    assert pairs[("a", "c")].source is None
    assert pairs[("a", "c")].rule is None


def test_sample_progress_excludes_rule_pairs(ruled):
    before = ruled.eligible_units()
    info = ruled.draw_sample(size=5, seed=0)
    progress = {item.role: item for item in info.progress}
    assert progress["all"].pairs == 11 + 5 + 3 + 1 + 1  # work pairs only (bf is defective, so excluded)
    assert progress["all"].rule_pairs == 4 + 1
    # sampling eligibility does not depend on the rules, so existing samples stay reproducible
    assert [str(app) for app in ruled.eligible_units()] == [str(app) for app in before]


def test_category_counts_have_a_rule_source(ruled):
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    counts_ = {count.category: count for count in ruled.category_counts().categories}
    assert counts_["Orthography"].rule == 10  # 5 unordered pairs, both directions
    assert counts_["Orthography"].human == 0
    assert counts_["Minor_Substitution"].human == 2
    assert counts_["Minor_Substitution"].rule == 0
    totals = ruled.category_counts()
    assert totals.rule_pairs == 5
    assert totals.pairs_to_classify == 11 + 5 + 3 + 1 + 1


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def test_evaluation_excludes_rule_labelled_pairs(ruled, path):
    """ Rule-covered pairs are not decisions: they are left out of the gold set and the scores. """
    from rdgai.evaluate import gold_labels, metrics
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    ruled.set_classification("Mark.1.1.2", "ao1", "b", "Minor_Substitution")   # a former rule-3 pair: now a decision
    gold = gold_labels(ruled, ["Mark.1.1.2"])
    assert gold == {("Mark.1.1.2", "a", "b"): "Minor_Substitution", ("Mark.1.1.2", "ao1", "b"): "Minor_Substitution"}
    assert len(ruled.rule_pairs(ruled.get_app("Mark.1.1.2"))) == 4
    assert metrics(gold, gold, ["Minor_Substitution"]).n == 2


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def test_gui_separates_rule_pairs(ruled):
    client = TestClient(create_app(ruled))
    ruled.set_classification("Mark.1.1.2", "a", "b", "Minor_Substitution")
    data = client.get("/api/unit/Mark.1.1.2").json()
    shown = {frozenset((pair["first"], pair["second"])) for pair in data["pairs"]}
    assert len(shown) == 11
    assert frozenset(("ao1", "b")) in shown and frozenset(("bo", "c")) in shown   # former rule-3 pairs are work
    assert len(data["rule_pairs"]) == 4
    assert data["progress"] == {"pairs_done": 1, "pairs_total": 11, "rule_pairs": 4}
    rule_pairs = {(pair["first"], pair["second"]): pair for pair in data["rule_pairs"]}
    assert rule_pairs[("a", "ao1")]["source"] == "rule"
    assert rule_pairs[("a", "ao1")]["category"] == "Orthography"
    assert rule_pairs[("a", "ao1")]["rule"] == {"rule": 1}
    assert rule_pairs[("ao1", "ao2")]["rule"] == {"rule": 2}
    former = next(pair for pair in data["pairs"] if frozenset((pair["first"], pair["second"])) == frozenset(("ao1", "b")))
    assert former["rule"] is None and former["change"]
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
    assert {count["rule"]: count["written"] for count in report["counts"]} == {1: 4, 2: 1}
    assert report["removed"] == 0
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
