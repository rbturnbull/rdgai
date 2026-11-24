"""
Cross-validation over the sample's example units.

For fold k of n, the fold's example units are held out (labels and suggestions removed, predicted and scored), the
sample's real test units are hidden entirely (labels and suggestions removed, never predicted, never visible as
precedents), and the other example units are the only human labels the pipeline can see. The folds are fixed by a
seed and saved, so every run uses identical splits.
"""
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai import sampling
from rdgai.annotation import AnnotationStore, AnnotationError
from rdgai.evaluate import make_folds, load_or_create_folds, strip_units, evaluate
from rdgai.main import app as cli

from .util import TEST_DATA_DIR
from .test_pipeline import requested_pairs

TEST_UNITS = ["Mark.1.1.10", "Mark.1.2.43"]
EXAMPLE_UNITS = ["Mark.1.1.12-16", "Mark.1.2.2", "Mark.1.2.8-14", "Mark.1.2.18",
                 "Mark.1.2.20", "Mark.1.2.32", "Mark.1.2.34", "Mark.1.5.20-22"]


def first_pair(store, unit_id):
    pair = store.get_unit(unit_id).pairs[0]
    return pair.active, pair.passive


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
        seed=0, size=10, test_fraction=0.2, ignore_types=["defective"], eligible_count=10,
        created="2026-11-13T00:00:00Z", source=path.name, units=units,
    ), store.sample_path)
    # every unit gets a human label (with a marker in its reason) and a suggestion
    for unit_id in TEST_UNITS + EXAMPLE_UNITS:
        active, passive = first_pair(store, unit_id)
        marker = "TESTGOLD" if unit_id in TEST_UNITS else "EXGOLD"
        store.set_classification(unit_id, active, passive, "Major_Substitution", justification=f"{marker} {unit_id}")
        store.suggest_classification(unit_id, active, passive, "Major_Substitution", justification=f"SUGGESTED {unit_id}",
                                     source="claude", allow_test_unit=True)
    return path


def sample_of(path):
    return AnnotationStore(path, read_only=True).load_sample()


# ---------------------------------------------------------------------------
# folds
# ---------------------------------------------------------------------------

def test_make_folds_partitions_the_examples():
    ids = [f"u{i}" for i in range(100)]
    folds = make_folds(ids, folds=5, seed=1)
    assert len(folds) == 5
    assert all(len(fold) == 20 for fold in folds)
    assert sorted(unit for fold in folds for unit in fold) == sorted(ids)


def test_make_folds_is_reproducible_and_order_independent():
    ids = [f"u{i}" for i in range(23)]
    assert make_folds(ids, 5, seed=3) == make_folds(list(reversed(ids)), 5, seed=3)
    assert make_folds(ids, 5, seed=3) != make_folds(ids, 5, seed=4)
    assert sorted(len(fold) for fold in make_folds(ids, 5, seed=3)) == [4, 4, 5, 5, 5]


def test_make_folds_keeps_document_order_within_a_fold():
    ids = [f"u{i:02d}" for i in range(20)]
    for fold in make_folds(ids, 4, seed=0, document_order=ids):
        assert fold == sorted(fold)


def test_folds_file_is_created_and_reused(path, tmp_path):
    folds_path = tmp_path / "cv" / "folds.yaml"
    sample = sample_of(path)
    plan = load_or_create_folds(folds_path, sample, folds=4, seed=7)
    assert folds_path.exists()
    data = yaml.safe_load(folds_path.read_text())
    assert data["seed"] == 7 and data["folds"] == 4 and len(data["units"]) == 4
    assert sorted(unit for fold in plan.units for unit in fold) == sorted(EXAMPLE_UNITS)
    assert load_or_create_folds(folds_path, sample, folds=4, seed=7) == plan


def test_folds_file_must_match(path, tmp_path):
    folds_path = tmp_path / "folds.yaml"
    sample = sample_of(path)
    load_or_create_folds(folds_path, sample, folds=4, seed=7)
    with pytest.raises(AnnotationError, match="seed"):
        load_or_create_folds(folds_path, sample, folds=4, seed=8)
    with pytest.raises(AnnotationError, match="folds"):
        load_or_create_folds(folds_path, sample, folds=2, seed=7)


# ---------------------------------------------------------------------------
# the stripped copy for a fold
# ---------------------------------------------------------------------------

def test_strip_units_for_a_fold(path, tmp_path):
    held_out = EXAMPLE_UNITS[:2]
    working = strip_units(path, tmp_path / "work" / "working.xml", held_out=held_out, hidden=TEST_UNITS)
    store = AnnotationStore(working, read_only=True)
    for unit_id in held_out + TEST_UNITS:
        assert all(pair.category is None for pair in store.get_unit(unit_id).pairs), unit_id
    for unit_id in EXAMPLE_UNITS[2:]:
        assert store.get_unit(unit_id).pairs[0].category == "Major_Substitution", unit_id
    text = working.read_text()
    assert "TESTGOLD" not in text
    for unit_id in held_out + TEST_UNITS:
        assert f"EXGOLD {unit_id}<" not in text and f"SUGGESTED {unit_id}<" not in text
    suggested = {suggestion.unit_id for suggestion in store.get_suggestions()}
    assert suggested == set(EXAMPLE_UNITS[2:])
    # in the copy's sample the held-out and the hidden units are both test units; the rest are examples
    sample = store.load_sample()
    assert set(sample.ids("test")) == set(held_out + TEST_UNITS)
    assert set(sample.ids("example")) == set(EXAMPLE_UNITS[2:])


# ---------------------------------------------------------------------------
# evaluating a fold
# ---------------------------------------------------------------------------

def recording_model(seen):
    def respond(messages, info: AgentInfo):
        seen.append(messages)
        prompt = messages[0].parts[-1].content
        if "Category under review" in prompt:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"revisions": [], "notes": ""})])
        decisions = [{"active": a, "passive": b, "category": "Major_Substitution", "justification": "x", "confidence": "low"}
                     for a, b in requested_pairs(messages)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])
    return FunctionModel(respond)


def everything_seen(seen) -> str:
    return "\n".join(str(part) for messages in seen for message in messages for part in message.parts)


def asked_units(seen) -> set:
    return {message.parts[-1].content.split("Variation unit ")[1].split(" ")[0]
            for messages in seen for message in messages[:1] if "Variation unit " in str(message.parts[-1].content)}


def test_evaluate_fold(path, tmp_path):
    seen = []
    before = path.read_bytes()
    folds_path = tmp_path / "cv" / "folds.yaml"
    report = evaluate(path, recording_model(seen), output_dir=tmp_path / "cv" / "fold2", rounds=1, toolsets=[],
                      fold=2, folds=4, seed=5, folds_path=folds_path)
    plan = load_or_create_folds(folds_path, sample_of(path), folds=4, seed=5)
    held_out = plan.units[1]

    assert path.read_bytes() == before
    assert report.fold == 2 and report.folds == 4 and report.seed == 5
    assert report.test_units == len(held_out)
    assert sorted(report.held_out_units) == sorted(held_out)
    assert sorted(report.hidden_units) == sorted(TEST_UNITS)
    assert report.gold_pairs == len(held_out)            # one human label per unit
    assert report.draft.accuracy == pytest.approx(1.0)
    assert asked_units(seen) == set(held_out)            # only the held-out units are predicted
    seen_text = everything_seen(seen)
    assert "TESTGOLD" not in seen_text
    for unit_id in held_out:
        assert f"EXGOLD {unit_id}" not in seen_text
        assert f"SUGGESTED {unit_id}" not in seen_text


def test_evaluate_fold_out_of_range(path, tmp_path):
    with pytest.raises(AnnotationError, match="fold"):
        evaluate(path, recording_model([]), output_dir=tmp_path / "out", rounds=0, toolsets=[], fold=5, folds=4, seed=0,
                 folds_path=tmp_path / "folds.yaml")


def test_evaluate_without_fold_is_unchanged(path, tmp_path):
    seen = []
    report = evaluate(path, recording_model(seen), output_dir=tmp_path / "out", rounds=0, toolsets=[])
    assert report.fold is None
    assert asked_units(seen) == set(TEST_UNITS)


@pytest.mark.skipif(not Path(sys.executable).with_name("rdgai").exists(), reason="rdgai entry point not installed")
def test_fold_no_leak_through_the_mcp_tools(path, tmp_path):
    """ With the real MCP server, the tools show the visible examples but nothing of held-out or test units. """
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
            calls = [ToolCallPart("search_similar", {"unit_id": unit_id, "active": active, "passive": passive, "limit": 50}),
                     ToolCallPart("list_category", {"category": "Major_Substitution"})]
            calls += [ToolCallPart("get_unit", {"unit_id": other}) for other in TEST_UNITS + EXAMPLE_UNITS]
            return ModelResponse(parts=calls)
        decisions = [{"active": a, "passive": b, "category": "Major_Substitution", "justification": "x", "confidence": "low"}
                     for a, b in requested_pairs(messages)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])

    folds_path = tmp_path / "folds.yaml"
    report = evaluate(path, FunctionModel(respond), output_dir=tmp_path / "fold1", rounds=1, concurrency=1,
                      fold=1, folds=4, seed=0, folds_path=folds_path)
    seen_text = everything_seen(seen)
    assert "TESTGOLD" not in seen_text
    for unit_id in report.held_out_units:
        assert f"EXGOLD {unit_id}" not in seen_text and f"SUGGESTED {unit_id}" not in seen_text
    visible = [unit_id for unit_id in EXAMPLE_UNITS if unit_id not in report.held_out_units]
    assert any(f"EXGOLD {unit_id}" in seen_text for unit_id in visible)


def test_cli_evaluate_fold_options():
    output = CliRunner().invoke(cli, ["evaluate", "--help"]).output
    for option in ("--fold", "--folds", "--seed", "--folds-file"):
        assert option in output


# ---------------------------------------------------------------------------
# folds over every labelled unit of the sample (examples and test), stratified by size
# ---------------------------------------------------------------------------

def test_make_folds_spreads_the_largest_units():
    ids = [f"u{i:02d}" for i in range(40)]
    sizes = {unit: (100 - i if i < 5 else 1) for i, unit in enumerate(ids)}   # five big units
    folds = make_folds(ids, 5, seed=2, sizes=sizes)
    assert sorted(len(fold) for fold in folds) == [8] * 5
    for fold in folds:
        assert sum(sizes[unit] > 1 for unit in fold) == 1                     # one big unit per fold
    assert make_folds(ids, 5, seed=2, sizes=sizes) == folds


def test_folds_over_every_sample_unit(path, tmp_path):
    folds_path = tmp_path / "all" / "folds.yaml"
    plan = load_or_create_folds(folds_path, sample_of(path), folds=5, seed=1, pool="all")
    assert sorted(unit for fold in plan.units for unit in fold) == sorted(EXAMPLE_UNITS + TEST_UNITS)
    assert plan.pool == "all"
    with pytest.raises(AnnotationError, match="pool|units"):
        load_or_create_folds(folds_path, sample_of(path), folds=5, seed=1, pool="example")


def test_evaluate_fold_over_every_unit_hides_nothing(path, tmp_path):
    seen = []
    folds_path = tmp_path / "all" / "folds.yaml"
    report = evaluate(path, recording_model(seen), output_dir=tmp_path / "all" / "fold1", rounds=0, toolsets=[],
                      fold=1, folds=5, seed=1, folds_path=folds_path, pool="all")
    assert report.hidden_units == []
    held_out = set(report.held_out_units)
    assert asked_units(seen) == held_out
    visible = [unit for unit in EXAMPLE_UNITS + TEST_UNITS if unit not in held_out]
    seen_text = everything_seen(seen)
    assert all(f"GOLD {unit}" not in seen_text for unit in held_out)
    assert report.pool == "all"


def test_report_gives_unit_weighted_and_gold_certainty_accuracy(path, tmp_path):
    report = evaluate(path, recording_model([]), output_dir=tmp_path / "out", rounds=0, toolsets=[])
    assert report.draft.unit_accuracy == pytest.approx(1.0)
    assert all(pair.gold_cert in ("high", "medium", "low", None) for pair in report.pairs)
    assert all(pair.draft_cert == "low" for pair in report.pairs)
    assert set(report.draft.accuracy_by_gold_cert) <= {"high", "medium", "low", "none"}


def test_unit_accuracy_averages_per_unit():
    from rdgai.evaluate import metrics
    gold = {("u1", "a", "b"): "X", ("u1", "a", "c"): "X", ("u1", "b", "c"): "X", ("u2", "a", "b"): "Y"}
    predicted = {("u1", "a", "b"): "X", ("u1", "a", "c"): "X", ("u1", "b", "c"): "X", ("u2", "a", "b"): "X"}
    result = metrics(gold, predicted, ["X", "Y"])
    assert result.accuracy == pytest.approx(0.75)
    assert result.unit_accuracy == pytest.approx(0.5)


def test_cli_evaluate_fold_pool_option():
    assert "--fold-pool" in CliRunner().invoke(cli, ["evaluate", "--help"]).output
