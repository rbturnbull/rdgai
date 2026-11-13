"""
Evaluating the pipeline on the sample's test units, on a stripped working copy: the test units' labels are removed
before the pipeline runs (so nothing can leak), predictions after the draft and after the review rounds are compared
with the human gold labels, and an HTML report (in the style of the old rdgai report) and JSON are written.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai import sampling
from rdgai.annotation import AnnotationStore
from rdgai.evaluate import strip_test_units, gold_labels, metrics, evaluate
from rdgai.main import app as cli

from .util import TEST_DATA_DIR
from .test_pipeline import requested_pairs

TEST_UNITS = ["Mark.1.1.10", "Mark.1.1.12-16", "Mark.1.2.8-14"]
EXAMPLE_UNITS = ["Mark.1.2.2"]

# the scripted pipeline's draft answers (display direction); everything else gets Major_Substitution
DRAFT = {
    ("Mark.1.1.10", "a", "b"): "Major_Omission",          # gold Major_Omission: right
    ("Mark.1.1.12-16", "a", "b"): "Major_Substitution",   # gold Minor_Omission: wrong (fixed by the review)
    ("Mark.1.1.12-16", "a", "f"): "Major_Omission",       # gold Major_Omission: right
    ("Mark.1.1.12-16", "a", "c"): "Major_Substitution",   # gold Major_Substitution: right
    ("Mark.1.2.8-14", "b", "a"): "Minor_Addition",        # gold Minor_Addition: right
}


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "corpus" / "mark_sample.xml"
    path.parent.mkdir()
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    store = AnnotationStore(path, annotator="RT", check_sample=False)
    order = [unit.id for unit in store.list_units(limit=500).units]
    units = [sampling.SampleUnit(id=unit_id, role="test") for unit_id in TEST_UNITS]
    units += [sampling.SampleUnit(id=unit_id, role="example") for unit_id in EXAMPLE_UNITS]
    units.sort(key=lambda unit: order.index(unit.id))
    sampling.save_sample(sampling.Sample(
        seed=0, size=4, test_fraction=0.75, ignore_types=["defective"], eligible_count=10,
        created="2026-11-13T00:00:00Z", source=path.name, units=units,
    ), store.sample_path)
    # gold labels on test units (their reasons contain a marker that must never reach the model)
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="GOLD-1 omits χριστου")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="GOLD-2 the article")
    store.set_classification("Mark.1.1.12-16", "a", "f", "Major_Omission", justification="GOLD-3")
    store.set_classification("Mark.1.1.12-16", "a", "c", "Major_Substitution", justification="GOLD-4")
    store.set_classification("Mark.1.2.8-14", "b", "a", "Minor_Addition", justification="GOLD-5")
    store.suggest_classification("Mark.1.2.8-14", "e", "b", "Major_Substitution", justification="GOLD-6 suggestion", source="claude", allow_test_unit=True)
    # an example unit, which the pipeline may use as a precedent
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution", justification="EXAMPLE")
    return path


def scripted(seen:list, revise:bool=True):
    """ Answers drafts from DRAFT and, in the review of Major_Substitution, fixes Mark.1.1.12-16 a>b. """
    def respond(messages, info: AgentInfo):
        seen.append(messages)
        prompt = messages[0].parts[-1].content
        if "Category under review" in prompt:
            category = prompt.split("Category under review: ")[1].split("\n")[0]
            revisions = []
            if revise and category == "Major_Substitution" and "Mark.1.1.12-16:a>b:" in prompt:
                revisions = [{"unit_id": "Mark.1.1.12-16", "active": "a", "passive": "b", "category": "Minor_Omission",
                              "reason": "only the article is omitted", "evidence": [], "confidence": "medium"}]
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"revisions": revisions, "notes": ""})])
        unit_id = prompt.split("Variation unit ")[1].split(" ")[0]
        decisions = [
            {"active": active, "passive": passive, "category": DRAFT.get((unit_id, active, passive), "Major_Substitution"),
             "justification": "scripted", "confidence": "medium"}
            for active, passive in requested_pairs(messages)
        ]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])
    return FunctionModel(respond)


def everything_seen(seen) -> str:
    return "\n".join(str(part) for messages in seen for message in messages for part in message.parts)


# ---------------------------------------------------------------------------
# pieces
# ---------------------------------------------------------------------------

def test_strip_test_units(path, tmp_path):
    working = strip_test_units(path, tmp_path / "work" / "working.xml")
    store = AnnotationStore(working, read_only=True)
    for unit_id in TEST_UNITS:
        unit = store.get_unit(unit_id)
        assert all(pair.category is None for pair in unit.pairs)
    assert store.get_suggestions() == []
    assert store.get_unit("Mark.1.2.2").pairs[0].category == "Minor_Substitution"  # examples kept
    assert "GOLD" not in working.read_text()
    assert store.load_sample() is not None  # the sample travels with the copy


def test_gold_labels_in_display_direction(path):
    gold = gold_labels(AnnotationStore(path, read_only=True), TEST_UNITS)
    assert gold == {
        ("Mark.1.1.10", "a", "b"): "Major_Omission",
        ("Mark.1.1.12-16", "a", "b"): "Minor_Omission",
        ("Mark.1.1.12-16", "a", "f"): "Major_Omission",
        ("Mark.1.1.12-16", "a", "c"): "Major_Substitution",
        ("Mark.1.2.8-14", "b", "a"): "Minor_Addition",
    }


def test_metrics():
    gold = {1: "A", 2: "A", 3: "B", 4: "C"}
    predicted = {1: "A", 2: "B", 3: "B", 4: "C"}
    result = metrics(gold, predicted, ["A", "B", "C", "D"])
    assert result.n == 4 and result.correct == 3
    assert result.accuracy == pytest.approx(0.75)
    rows = {row.category: row for row in result.per_category}
    assert rows["A"].precision == pytest.approx(1.0) and rows["A"].recall == pytest.approx(0.5)
    assert rows["B"].precision == pytest.approx(0.5) and rows["B"].recall == pytest.approx(1.0)
    assert rows["A"].support == 2
    assert rows["A"].indicative is True  # fewer than 10 test pairs
    assert "D" not in rows               # no support and never predicted
    assert result.confusion.labels == ["A", "B", "C"]
    assert result.confusion.matrix == [[1, 1, 0], [0, 1, 0], [0, 0, 1]]
    assert result.macro_f1 == pytest.approx((2 / 3 + 2 / 3 + 1) / 3)


def test_metrics_counts_missing_predictions():
    result = metrics({1: "A", 2: "A"}, {1: "A"}, ["A"])
    assert result.n == 2 and result.missing == 1 and result.correct == 1


# ---------------------------------------------------------------------------
# the evaluation
# ---------------------------------------------------------------------------

def test_evaluate(path, tmp_path):
    seen = []
    before = path.read_bytes()
    report = evaluate(path, scripted(seen), output_dir=tmp_path / "evaluation", rounds=2, toolsets=[])

    assert path.read_bytes() == before                    # the annotation file is never touched
    assert report.gold_pairs == 5
    assert report.draft.correct == 4 and report.draft.accuracy == pytest.approx(0.8)
    assert report.final.correct == 5 and report.final.accuracy == pytest.approx(1.0)
    assert [(entry.unit_id, entry.old, entry.new, entry.status) for entry in report.revisions] == [
        ("Mark.1.1.12-16", "Major_Substitution", "Minor_Omission", "applied"),
    ]
    assert report.unlabelled_test_pairs == 22 - 5          # test pairs without a gold label are left out
    assert len(report.definitions_hash) == 64
    assert any("5 test pairs" in note for note in report.notes)
    assert all(row.indicative for row in report.final.per_category)

    # nothing about the gold labels ever reached the model
    assert "GOLD" not in everything_seen(seen)
    # only test units were asked about
    asked = {message.parts[-1].content.split("Variation unit ")[1].split(" ")[0]
             for messages in seen for message in messages[:1] if "Variation unit " in str(message.parts[-1].content)}
    assert asked == set(TEST_UNITS)

    output = tmp_path / "evaluation"
    data = json.loads((output / "report.json").read_text())
    assert data["draft"]["accuracy"] == pytest.approx(0.8)
    html = (output / "report.html").read_text()
    for text in ("Overview", "Confusion matrix", "Categories", "Revisions", "Mark.1.1.12-16", "indicative only", "rdgAI", "#940000"):
        assert text in html, text


def test_evaluate_draft_only(path, tmp_path):
    report = evaluate(path, scripted([], revise=False), output_dir=tmp_path / "evaluation", rounds=0, toolsets=[])
    assert report.final is None
    assert report.draft.correct == 4


def test_evaluate_needs_a_sample(tmp_path):
    path = tmp_path / "mark_sample.xml"
    shutil.copy(TEST_DATA_DIR / "mark_sample.xml", path)
    with pytest.raises(Exception, match="sample"):
        evaluate(path, scripted([]), output_dir=tmp_path / "evaluation", toolsets=[])


@pytest.mark.skipif(not Path(sys.executable).with_name("rdgai").exists(), reason="rdgai entry point not installed")
def test_no_leak_through_the_mcp_tools(path, tmp_path):
    """ With the real MCP server, searching from a test pair finds the example unit but nothing from test units. """
    seen = []

    def respond(messages, info: AgentInfo):
        seen.append(messages)
        prompt = messages[0].parts[-1].content
        if "Category under review" in prompt:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"revisions": [], "notes": ""})])
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            unit_id = prompt.split("Variation unit ")[1].split(" ")[0]
            active, passive = requested_pairs(messages)[0]
            return ModelResponse(parts=[
                ToolCallPart("search_similar", {"unit_id": unit_id, "active": active, "passive": passive, "limit": 50}),
                ToolCallPart("list_category", {"category": "Minor_Omission"}),
            ])
        decisions = [{"active": a, "passive": b, "category": "Major_Substitution", "justification": "x", "confidence": "low"}
                     for a, b in requested_pairs(messages)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])

    evaluate(path, FunctionModel(respond), output_dir=tmp_path / "evaluation", rounds=1, concurrency=1)
    seen_text = everything_seen(seen)
    assert "GOLD" not in seen_text
    assert "EXAMPLE" in seen_text or "Minor_Substitution" in seen_text  # the example unit is a precedent


def test_cli_evaluate_options():
    output = CliRunner().invoke(cli, ["evaluate", "--help"]).output
    for option in ("--model", "--rounds", "--output-dir", "--concurrency"):
        assert option in output
