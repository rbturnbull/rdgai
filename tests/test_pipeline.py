"""
The draft pass of the pydantic-ai pipeline: agents read through the MCP tools and answer with a structured decision
per pair; the pipeline checks every answer and writes it (resp="#rdgai", the agent's confidence as @cert, the run's
<change>). Tested offline with scripted models (pydantic-ai's FunctionModel), so no API is called.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai.annotation import AnnotationStore
from rdgai.pipeline import (
    build_instructions, unit_prompt, draft, estimate, model_settings_for, AGENT_TOOLS, PairDecision,
)
from rdgai.main import app as cli

from .util import TEST_DATA_DIR
from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


def decide(pairs, category="Major_Substitution", confidence="medium"):
    """ A scripted answer covering the given (active, passive) pairs. """
    return {"decisions": [
        {"active": active, "passive": passive, "category": category, "justification": f"{active} to {passive}", "confidence": confidence}
        for active, passive in pairs
    ]}


def requested_pairs(messages):
    """ The pairs the prompt asks about, as (active, passive), from the 'Pairs to classify' section. """
    prompt = messages[0].parts[-1].content
    section = prompt.split("Pairs to classify")[1]
    pairs = []
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("- ") and " → " in line:
            ids = line[2:].split(":")[0]
            active, passive = [part.strip() for part in ids.split("→")]
            pairs.append((active, passive))
    return pairs


def answering(category="Major_Substitution", confidence="medium", log=None):
    """ A FunctionModel that answers every requested pair with the given category. """
    def respond(messages, info: AgentInfo):
        pairs = requested_pairs(messages)
        if log is not None:
            log.append(pairs)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(pairs, category, confidence))])
    return FunctionModel(respond)


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

def test_instructions_carry_definitions_guidance_and_direction(store):
    instructions = build_instructions(store)
    for category in store.definitions().categories:
        assert category.name in instructions
        assert category.description[:60] in instructions
    assert "Decision order" in instructions
    assert "ACTIVE" in instructions
    assert "tentative" in instructions
    assert "search_similar" in instructions
    # stable, so the prompt cache can be reused across units
    assert build_instructions(store) == instructions


def test_unit_prompt_lists_pairs_to_classify(store):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits the article")
    app = store.get_app("Mark.1.1.12-16")
    prompt = unit_prompt(store, app, store.ordered_pairs(app)[1:3])
    assert "Mark.1.1.12-16" in prompt
    assert "αρχη του ευαγγελιου ιησου χριστου ⸂υιου του θεου⸃" in prompt
    assert "- a → e: υιου του θεου → του θεου (second reading omits: υιου)" in prompt
    assert requested_pairs([type("M", (), {"parts": [type("P", (), {"content": prompt})()]})()]) == [("a", "e"), ("a", "f")]
    # human labels already in the unit are given as certain context
    assert "a → b: Minor_Omission (human, certain)" in prompt


def test_model_settings_for_anthropic_cache_the_prefix():
    settings = model_settings_for("anthropic:claude-opus-5-5")
    assert settings["anthropic_cache_instructions"] is True
    assert settings["anthropic_cache_tool_definitions"] is True
    assert "anthropic_cache_instructions" not in model_settings_for("openai:gpt-5")
    assert model_settings_for("anthropic:claude-opus-5-5", effort="high")["anthropic_effort"] == "high"


# ---------------------------------------------------------------------------
# the draft pass
# ---------------------------------------------------------------------------

def test_draft_writes_checked_decisions(store):
    report = draft(store, answering("Major_Substitution", "medium"), toolsets=[], unit_ids=["Mark.1.1.12-16"])
    assert report.pairs_written == 15
    assert report.units_done == 1
    unit = store.get_unit("Mark.1.1.12-16")
    assert all(pair.category == "Major_Substitution" for pair in unit.pairs)
    provenance = unit.pairs[0].provenance
    assert provenance.resp == "#rdgai"
    assert provenance.cert == "medium"
    assert provenance.change.split()[0] == f"#{report.run_id}"
    assert unit.pairs[0].source == "pipeline"


def test_draft_retries_an_incomplete_answer(store):
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        pairs = requested_pairs(messages)
        if len(attempts) == 1:
            pairs = pairs[:-1]  # forget one pair
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(pairs))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=["Mark.1.2.8-14"])
    assert len(attempts) == 2
    # the model is told exactly what was missing
    feedback = [str(part.content) for message in attempts[1] for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
    assert any("Missing pairs: a → c" in text for text in feedback)
    assert report.pairs_written == 6


def test_draft_retries_an_unknown_category(store):
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(1)
        category = "Spelling" if len(attempts) == 1 else "Minor_Substitution"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), category))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=["Mark.1.2.2"])
    assert len(attempts) == 2
    assert report.pairs_written == 1


def test_draft_is_resumable_and_skips_labelled_pairs(store):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")  # human
    asked = []
    report = draft(store, answering(log=asked), toolsets=[], unit_ids=["Mark.1.1.12-16"])
    assert report.pairs_written == 14
    assert ("a", "b") not in asked[0] and ("b", "a") not in asked[0]
    assert store.get_unit("Mark.1.1.12-16").pairs[0].category == "Minor_Omission"  # human label kept

    again = draft(store, answering(), toolsets=[], unit_ids=["Mark.1.1.12-16"])
    assert again.pairs_written == 0 and again.units_done == 0


def test_draft_limit_units_then_continue(store):
    first = draft(store, answering(), toolsets=[], limit_units=2)
    assert first.units_done == 2
    rest = draft(store, answering(), toolsets=[])
    assert rest.units_done == 8
    assert store.list_units(status="unclassified").total == 0


def test_draft_never_overwrites_a_label_made_meanwhile(store):
    def respond(messages, info: AgentInfo):
        # an annotator labels a pair while the model is thinking
        store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), "Minor_Omission"))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=["Mark.1.1.10"])
    assert report.pairs_written == 0
    assert report.pairs_skipped == 1
    pair = store.get_unit("Mark.1.1.10").pairs[0]
    assert (pair.category, pair.source) == ("Major_Omission", "human")


def test_draft_splits_large_units(store):
    asked = []
    report = draft(store, answering(log=asked), toolsets=[], unit_ids=["Mark.1.1.12-16"], max_pairs_per_request=6)
    assert [len(pairs) for pairs in asked] == [6, 6, 3]
    assert report.pairs_written == 15


def test_draft_concurrently(store):
    report = draft(store, answering(), toolsets=[], concurrency=4)
    assert report.units_done == 10
    assert store.list_units(status="unclassified").total == 0


def test_draft_skips_rule_pairs(tmp_path):
    from rdgai.rules import SubreadingRule
    path = tmp_path / "subreadings.xml"
    shutil.copy(TEST_DATA_DIR / "subreadings.xml", path)
    store = AnnotationStore(path, annotator="RT")
    store.apply_rules(rules=[SubreadingRule("orthographic", "Orthography", "o")])
    asked = []
    draft(store, answering(log=asked), toolsets=[], unit_ids=["Mark.1.1.2"])
    assert sorted(asked[0]) == [("a", "b"), ("a", "c"), ("b", "c")]
    # labels copied by rule 3 follow the drafted parents
    pairs = {(pair.active, pair.passive): pair for pair in store.get_unit("Mark.1.1.2").pairs}
    assert pairs[("ao1", "b")].category == "Major_Substitution"
    assert pairs[("ao1", "b")].source == "rule"


def test_draft_records_usage(store):
    report = draft(store, answering(), toolsets=[], unit_ids=["Mark.1.1.10"])
    assert report.usage["requests"] == 1
    assert report.usage["output_tokens"] > 0


def test_pair_decision_confidence_values():
    with pytest.raises(Exception):
        PairDecision(active="a", passive="b", category="X", justification="", confidence="sure")


# ---------------------------------------------------------------------------
# through the MCP server
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path(sys.executable).with_name("rdgai").exists(), reason="rdgai entry point not installed")
def test_draft_agent_uses_the_mcp_tools(store):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits the article")
    seen = {}

    def respond(messages, info: AgentInfo):
        seen.setdefault("tools", sorted(tool.name for tool in info.function_tools))
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("search_similar", {"unit_id": "Mark.1.1.12-16", "active": "a", "passive": "d"})])
        seen["search"] = returns[0].content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), "Minor_Addition"))])

    report = draft(store, FunctionModel(respond), unit_ids=["Mark.1.1.12-16"])  # default toolsets: the MCP server
    assert seen["tools"] == sorted(AGENT_TOOLS)
    assert "Minor_Addition" in json.dumps(seen["search"], ensure_ascii=False)  # the human precedent (b → a, adds του)
    assert report.pairs_written == 14


# ---------------------------------------------------------------------------
# cost estimate and CLI
# ---------------------------------------------------------------------------

def test_estimate(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    opus = estimate(store, "anthropic:claude-opus-5-5")
    assert opus.units == 9
    assert opus.pairs == sum(unit.pair_count - unit.classified_count for unit in store.list_units(limit=500).units)
    assert opus.input_tokens > 0 and opus.output_tokens > 0
    assert opus.cost > 0
    haiku = estimate(store, "anthropic:claude-haiku-5-5")
    assert haiku.cost < opus.cost


def test_cli_estimate(mark_path):
    result = CliRunner().invoke(cli, ["estimate", str(mark_path), "--model", "anthropic:claude-opus-5-5"])
    assert result.exit_code == 0, result.output
    assert "10 units" in result.output
    assert "$" in result.output


def test_cli_draft_options():
    output = CliRunner().invoke(cli, ["draft", "--help"]).output
    for option in ("--model", "--limit-units", "--concurrency", "--effort", "--unit"):
        assert option in output
