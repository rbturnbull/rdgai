"""
Targeted escalation: the draft model names a second category only when it is unsure, and only those pairs go to a
stronger model, which is asked a narrow question ("for these pairs, A or B?") with the guidance, the definitions of
just those two categories, the verse and the closest human precedents labelled A or B. Two ways:

- after the draft (``escalate``): the pipeline groups the uncertain pairs of a unit by their two options and asks the
  expert once per group, then applies its answers;
- as a tool (``ask_expert``) the draft model may call while it works, when it is unsure.

Easy pairs never reach the expert. Nothing here names a category of any particular file.
"""
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, RetryPromptPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai.pipeline import (
    draft, escalate, PairDecision, uncertain_groups, expert_prompt, Uncertain, build_expert_instructions,
)

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)
from .test_pipeline import requested_pairs

UNIT = "Mark.1.1.12-16"


def prompt_of(messages) -> str:
    return str(messages[0].parts[-1].content)


def draft_answer(pairs, alternative_for=None, category="Major_Omission", alternative="Minor_Omission"):
    """ A draft that is unsure (names an alternative) only for the pairs in `alternative_for`. """
    alternative_for = set(alternative_for or [])
    return {"analysis": "", "decisions": [
        {"active": a, "passive": b, "category": category, "justification": f"{a}>{b}", "confidence": "medium",
         **({"alternative": alternative} if (a, b) in alternative_for else {})}
        for a, b in pairs]}


def expert_pairs(messages):
    section = prompt_of(messages).split("Pairs to decide")[1]
    found = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and " → " in line:
            a, b = [part.strip() for part in line[2:].split(":")[0].split("→")]
            found.append((a, b))
    return found


def expert_model(choice, seen):
    def respond(messages, info: AgentInfo):
        seen.append(messages)
        decisions = [{"active": a, "passive": b, "category": choice, "justification": "expert", "confidence": "high"}
                     for a, b in expert_pairs(messages)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])
    return FunctionModel(respond)


# ---------------------------------------------------------------------------
# the second choice
# ---------------------------------------------------------------------------

def test_a_decision_may_name_an_alternative():
    assert "alternative" in PairDecision.model_fields
    assert PairDecision(active="a", passive="b", category="X", justification="", confidence="high").alternative is None


def test_an_unknown_or_identical_alternative_is_retried(store):
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        pairs = requested_pairs(messages)
        alternative = "NoSuchCategory" if len(attempts) == 1 else ("Major_Omission" if len(attempts) == 2 else "Minor_Omission")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(pairs, pairs[:1], alternative=alternative))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT])
    assert len(attempts) == 3
    feedback = " ".join(str(part.content) for message in attempts[1] + attempts[2] for part in message.parts
                        if isinstance(part, (RetryPromptPart, ToolReturnPart)))
    assert "NoSuchCategory" in feedback and "alternative" in feedback
    assert len(report.uncertain) == 1


def test_the_draft_reports_only_the_uncertain_pairs(store):
    def respond(messages, info: AgentInfo):
        pairs = requested_pairs(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(pairs, pairs[:2]))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT])
    assert [(item.unit_id, item.category, item.alternative) for item in report.uncertain] == [(UNIT, "Major_Omission", "Minor_Omission")] * 2


# ---------------------------------------------------------------------------
# escalation after the draft
# ---------------------------------------------------------------------------

def uncertain(pairs, category="Major_Omission", alternative="Minor_Omission", unit=UNIT):
    return [Uncertain(unit_id=unit, active=a, passive=b, category=category, alternative=alternative, confidence="medium",
                      justification="draft") for a, b in pairs]


def test_uncertain_pairs_are_grouped_by_unit_and_by_their_two_options():
    items = uncertain([("a", "b"), ("a", "c")]) + uncertain([("b", "c")], "Minor_Omission", "Major_Omission") \
        + uncertain([("a", "d")], "Major_Substitution", "Minor_Substitution")
    groups = uncertain_groups(items)
    assert sorted(len(group) for group in groups) == [1, 3]


def test_the_expert_sees_only_the_two_categories_and_the_guidance(store):
    store.update_guidance("GUIDANCE TEXT")
    store.update_classifier_instructions("FILE INSTRUCTIONS")
    instructions = build_expert_instructions(store)
    assert "GUIDANCE TEXT" in instructions and "FILE INSTRUCTIONS" in instructions
    app = store.get_app(UNIT)
    prompt = expert_prompt(store, app, uncertain([("a", "b")]), ("Major_Omission", "Minor_Omission"))
    definitions = {category.name: category.description for category in store.categories()}
    assert definitions["Major_Omission"] in prompt and definitions["Minor_Omission"] in prompt
    others = [name for name in definitions if name not in ("Major_Omission", "Minor_Omission")]
    assert not any(definitions[name] in prompt for name in others if definitions[name])
    assert "Verse with each reading in place" in prompt and "a → b" in prompt


def test_expert_precedents_are_human_labels_of_the_two_categories(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="PRECEDENT A")
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution", justification="OTHER CATEGORY")
    app = store.get_app(UNIT)
    pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) == ("a", "f")]
    prompt = expert_prompt(store, app, uncertain([("a", "f")]), ("Major_Omission", "Minor_Omission"))
    assert "PRECEDENT A" in prompt and "OTHER CATEGORY" not in prompt


def test_escalation_asks_only_about_uncertain_pairs_and_applies_the_answer(store):
    def respond(messages, info: AgentInfo):
        pairs = requested_pairs(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(pairs, pairs[:2]))])

    drafted = draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT])
    seen = []
    report = escalate(store, expert_model("Minor_Omission", seen), drafted.uncertain)
    assert len(seen) == 1                                        # one group, one question
    assert sorted(expert_pairs(seen[0])) == sorted((item.active, item.passive) for item in drafted.uncertain)
    assert report.pairs == 2 and report.groups == 1 and report.changed == 2
    unit = store.get_unit(UNIT)
    labels = {(pair.active, pair.passive): pair.category for pair in unit.pairs}
    for item in drafted.uncertain:
        assert labels[(item.active, item.passive)] == "Minor_Omission"
    flagged = {frozenset((item.active, item.passive)) for item in drafted.uncertain}
    untouched = [category for (a, b), category in labels.items() if frozenset((a, b)) not in flagged]
    assert untouched and set(untouched) <= {"Major_Omission", "Major_Addition"}     # the sure ones keep the draft's label


def test_the_expert_must_choose_one_of_the_two(store):
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        choice = "Major_Substitution" if len(attempts) == 1 else "Minor_Omission"
        decisions = [{"active": a, "passive": b, "category": choice, "justification": "x", "confidence": "high"}
                     for a, b in expert_pairs(messages)]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"decisions": decisions})])

    store.write_decisions(UNIT, [("a", "b", "Major_Omission", "draft", "medium")], resp="rdgai", change="run")
    escalate(store, FunctionModel(respond), uncertain([("a", "b")]))
    assert len(attempts) == 2


def test_nothing_uncertain_means_no_expert_call(store):
    seen = []
    report = escalate(store, expert_model("Minor_Omission", seen), [])
    assert seen == [] and report.pairs == 0 and (report.cost or 0) == 0


def test_escalation_never_overwrites_a_human_label(store):
    store.set_classification(UNIT, "a", "b", "Major_Substitution", justification="human")
    report = escalate(store, expert_model("Minor_Omission", []), uncertain([("a", "b")]))
    pair = next(pair for pair in store.get_unit(UNIT).pairs if (pair.active, pair.passive) == ("a", "b"))
    assert pair.category == "Major_Substitution" and report.changed == 0


# ---------------------------------------------------------------------------
# the expert as a tool the draft model can call
# ---------------------------------------------------------------------------

def test_the_draft_model_can_ask_the_expert(store):
    expert_seen = []
    draft_turns = []

    def respond(messages, info: AgentInfo):
        draft_turns.append(messages)
        pairs = requested_pairs(messages)
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            assert "ask_expert" in [tool.name for tool in info.function_tools]
            return ModelResponse(parts=[ToolCallPart("ask_expert", {
                "pairs": [f"{a}>{b}" for a, b in pairs[:2]], "options": ["Major_Omission", "Minor_Omission"],
                "question": "Is the omitted word already implied?"})])
        assert "Minor_Omission" in str(returns[-1].content)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(pairs))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT],
                   expert=expert_model("Minor_Omission", expert_seen), expert_mode="tool")
    assert len(expert_seen) == 1
    assert "Is the omitted word already implied?" in prompt_of(expert_seen[0])
    assert report.expert_calls == 1 and report.expert_usage["requests"] >= 1


def test_the_expert_tool_is_limited_per_request(store):
    expert_seen = []

    def respond(messages, info: AgentInfo):
        pairs = requested_pairs(messages)
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if len(returns) < 4:
            return ModelResponse(parts=[ToolCallPart("ask_expert", {
                "pairs": [f"{a}>{b}" for a, b in pairs[:1]], "options": ["Major_Omission", "Minor_Omission"], "question": "?"})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(pairs))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT],
                   expert=expert_model("Minor_Omission", expert_seen), expert_mode="tool")
    assert len(expert_seen) == 2 and report.expert_calls == 2


def test_no_expert_tool_without_an_expert(store):
    def respond(messages, info: AgentInfo):
        assert "ask_expert" not in [tool.name for tool in info.function_tools]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, draft_answer(requested_pairs(messages)))])

    draft(store, FunctionModel(respond), toolsets=[], unit_ids=[UNIT])


# ---------------------------------------------------------------------------
# evaluation and the command line
# ---------------------------------------------------------------------------

from .test_cross_validation import path, EXAMPLE_UNITS, TEST_UNITS  # noqa: E402,F401 (fixture)


def unsure_draft():
    def respond(messages, info: AgentInfo):
        pairs = requested_pairs(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                 draft_answer(pairs, pairs, category="Minor_Substitution", alternative="Major_Substitution"))])
    return FunctionModel(respond)


def test_evaluate_with_escalation_after_the_draft(path, tmp_path):
    from rdgai.evaluate import evaluate
    report = evaluate(path, unsure_draft(), output_dir=tmp_path / "out", rounds=0, toolsets=[],
                      expert=expert_model("Major_Substitution", []), expert_mode="after")
    assert report.draft.accuracy == pytest.approx(0.0)       # the gold labels are Major_Substitution
    assert report.final.accuracy == pytest.approx(1.0)       # the expert fixed every uncertain pair
    assert report.escalation["mode"] == "after" and report.escalation["pairs"] >= report.gold_pairs   # every uncertain pair, labelled or not
    assert report.escalation["changed"] == report.escalation["pairs"]
    assert all(pair.alternative == "Major_Substitution" and pair.expert == "Major_Substitution" for pair in report.pairs)


def test_cli_expert_options():
    from typer.testing import CliRunner
    from rdgai.main import app as cli
    output = CliRunner().invoke(cli, ["evaluate", "--help"]).output
    assert "--expert-model" in output and "--expert-mode" in output
