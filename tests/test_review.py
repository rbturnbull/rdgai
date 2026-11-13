"""
Category review and the revision loop: an agent reviews all the pipeline's labels in one category together (with
human examples of it) and proposes revisions. Only pipeline labels are revised; a label is never evidence for itself
or its inverse; revisions that would undo an earlier one are skipped; every revision is logged.
"""
import json

import pytest
from typer.testing import CliRunner

from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai.pipeline import review, review_prompt, category_members
from rdgai.main import app as cli

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


@pytest.fixture
def drafted(store):
    """ Pipeline labels on Mark.1.1.12-16 (all Major_Substitution) and a human label elsewhere. """
    run = store.start_run(model="test-model")
    decisions = [(pair.active.n, pair.passive.n, "Major_Substitution", "draft", "medium") for pair in store.ordered_pairs(store.get_app("Mark.1.1.12-16"))]
    store.write_decisions("Mark.1.1.12-16", decisions, resp="rdgai", change=run)
    store.set_classification("Mark.1.2.8-14", "b", "a", "Minor_Addition", justification="adds the article τω")  # human
    store.set_classification("Mark.1.2.2", "a", "b", "Major_Substitution", justification="human example")       # human
    return store


def members_in(messages):
    prompt = messages[0].parts[-1].content
    return [line[2:].split(": ")[0].strip() for line in prompt.split("Labels to review")[1].splitlines() if line.startswith("- ")]


def category_in(messages):
    prompt = messages[0].parts[-1].content
    return prompt.split("Category under review: ")[1].split("\n")[0].strip()


def answer(revisions, notes=""):
    def respond(messages, info: AgentInfo):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"revisions": revisions(messages) if callable(revisions) else revisions, "notes": notes})])
    return FunctionModel(respond)


def revision(key, category, reason="fits the definition better", evidence=()):
    unit_id, pair = key.split(":")
    active, passive = pair.split(">")
    return {"unit_id": unit_id, "active": active, "passive": passive, "category": category, "reason": reason,
            "evidence": list(evidence), "confidence": "medium"}


# ---------------------------------------------------------------------------
# what is reviewed
# ---------------------------------------------------------------------------

def test_members_are_the_pipeline_labels_of_the_category(drafted):
    members = category_members(drafted, "Major_Substitution")
    keys = [f"{unit_id}:{pair.active.n}>{pair.passive.n}" for unit_id, pair in members]
    assert len(keys) == 15                                         # each unordered pair once
    assert all(key.startswith("Mark.1.1.12-16:") for key in keys)  # the human label in Mark.1.2.2 is not a member


def test_review_prompt(drafted):
    members = category_members(drafted, "Major_Substitution")
    prompt = review_prompt(drafted, "Major_Substitution", members[:3])
    assert "Major_Substitution" in prompt
    assert "Labels to review" in prompt
    assert "- Mark.1.1.12-16:a>b: υιου του θεου → υιου θεου (second reading omits: του)" in prompt
    # human examples of the category, marked as certain
    assert "- Mark.1.2.2:" in prompt.split("Human examples")[1].split("Labels to review")[0]


# ---------------------------------------------------------------------------
# revisions
# ---------------------------------------------------------------------------

def test_revision_is_applied_and_logged(drafted, tmp_path):
    log = tmp_path / "revisions.jsonl"
    model = answer(lambda messages: [revision("Mark.1.1.12-16:a>b", "Minor_Omission", "only the article", ["Mark.1.2.8-14:b>a"])]
                   if "Mark.1.1.12-16:a>b" in members_in(messages) else [])
    report = review(drafted, model, toolsets=[], categories=["Major_Substitution"], rounds=1, log_path=log)
    pairs = {(pair.active, pair.passive): pair for pair in drafted.get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "b")].category == "Minor_Omission"
    assert pairs[("a", "b")].inverse_category == "Minor_Addition"
    assert pairs[("a", "b")].source == "pipeline"
    assert [entry.status for entry in report.revisions] == ["applied"]

    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries[0]["old"] == "Major_Substitution" and entries[0]["new"] == "Minor_Omission"
    assert entries[0]["reason"] == "only the article"
    assert entries[0]["evidence"] == ["Mark.1.2.8-14:b>a"]
    assert entries[0]["status"] == "applied"
    assert entries[0]["run_id"] == report.run_id


def test_only_listed_pipeline_labels_can_be_revised(drafted):
    attempts = []

    def revisions(messages):
        attempts.append(messages)
        if len(attempts) == 1:
            return [revision("Mark.1.2.2:a>b", "Minor_Substitution")]  # a human label: not a member
        return []

    review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution"], rounds=1)
    assert len(attempts) == 2
    feedback = [str(part.content) for message in attempts[1] for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
    assert any("not one of the labels to review" in text for text in feedback)
    assert drafted.get_unit("Mark.1.2.2").pairs[0].category == "Major_Substitution"


def test_a_label_is_never_evidence_for_itself_or_its_inverse(drafted):
    attempts = []

    def revisions(messages):
        attempts.append(messages)
        evidence = ["Mark.1.1.12-16:b>a"] if len(attempts) == 1 else ["Mark.1.2.8-14:b>a"]  # its own inverse, then a real precedent
        return [revision("Mark.1.1.12-16:a>b", "Minor_Omission", evidence=evidence)] if "Mark.1.1.12-16:a>b" in members_in(messages) else []

    report = review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution"], rounds=1)
    feedback = [str(part.content) for message in attempts[1] for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
    assert any("cannot be evidence for itself" in text for text in feedback)
    assert [entry.evidence for entry in report.revisions] == [["Mark.1.2.8-14:b>a"]]


def test_loop_stops_when_nothing_changes(drafted):
    report = review(drafted, answer([]), toolsets=[], categories=["Major_Substitution"], rounds=5)
    assert report.rounds_run == 1
    assert report.revisions == []


def test_revisions_that_undo_an_earlier_one_are_skipped(drafted):
    def revisions(messages):
        if "Mark.1.1.12-16:a>b" not in members_in(messages):
            return []
        if category_in(messages) == "Major_Substitution":
            return [revision("Mark.1.1.12-16:a>b", "Minor_Omission")]       # a>b becomes Minor_Omission
        return [revision("Mark.1.1.12-16:a>b", "Major_Substitution")]       # then an attempt to send it back

    report = review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution", "Minor_Omission"], rounds=3)
    statuses = [(entry.new, entry.status) for entry in report.revisions]
    assert ("Minor_Omission", "applied") in statuses
    assert ("Major_Substitution", "skipped: would undo an earlier revision") in statuses
    pairs = {(pair.active, pair.passive): pair for pair in drafted.get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "b")].category == "Minor_Omission"


def test_label_changed_meanwhile_is_not_revised(drafted):
    def revisions(messages):
        if "Mark.1.1.12-16:a>c" in members_in(messages):
            drafted.set_classification("Mark.1.1.12-16", "a", "c", "Major_Inflection")  # an annotator steps in
            return [revision("Mark.1.1.12-16:a>c", "Minor_Substitution")]
        return []

    report = review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution"], rounds=1)
    assert [entry.status for entry in report.revisions] == ["skipped: changed meanwhile"]
    pairs = {(pair.active, pair.passive): pair for pair in drafted.get_unit("Mark.1.1.12-16").pairs}
    assert (pairs[("a", "c")].category, pairs[("a", "c")].source) == ("Major_Inflection", "human")


def test_review_scope(drafted):
    run = drafted.start_run(model="test-model")
    drafted.write_decisions("Mark.1.1.10", [("a", "b", "Major_Substitution", "draft", "low")], resp="rdgai", change=run)
    seen = []

    def revisions(messages):
        seen.extend(members_in(messages))
        return []

    review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution"], rounds=1, unit_ids=["Mark.1.1.10"])
    assert seen == ["Mark.1.1.10:a>b"]


def test_large_categories_are_reviewed_in_parts(drafted):
    sizes = []

    def revisions(messages):
        sizes.append(len(members_in(messages)))
        return []

    review(drafted, answer(revisions), toolsets=[], categories=["Major_Substitution"], rounds=1, max_pairs_per_request=6)
    assert sizes == [6, 6, 3]


def test_cli_review_and_run_options():
    runner = CliRunner()
    review_help = runner.invoke(cli, ["review", "--help"]).output
    for option in ("--model", "--rounds", "--category", "--unit"):
        assert option in review_help
    run_help = runner.invoke(cli, ["run", "--help"]).output
    for option in ("--model", "--rounds", "--limit-units"):
        assert option in run_help
