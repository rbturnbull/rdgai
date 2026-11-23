"""
What the draft prompt gives the model besides the definitions: the whole verse with each reading in place, the most
similar human-labelled pairs from other units (precedents, with their reasons), and an analysis field the model fills
before its decisions, so that each distinct change in a unit is judged once and consistently.
"""
from rdgai.pipeline import build_instructions, unit_prompt, UnitDecisions, precedents_for

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


def test_unit_prompt_shows_the_verse_with_each_reading_in_place(store):
    app = store.get_app("Mark.1.1.12-16")
    prompt = unit_prompt(store, app, store.ordered_pairs(app)[:2])
    section = prompt.split("Verse with each reading in place:")[1].split("\n\n")[0]
    assert "- a: αρχη του ευαγγελιου ιησου χριστου ⸂υιου του θεου⸃" in section
    assert "- b: αρχη του ευαγγελιου ιησου χριστου ⸂υιου θεου⸃" in section
    assert "- f: αρχη του ευαγγελιου ιησου χριστου ⸆" in section


def test_unit_prompt_gives_human_precedents_from_other_units(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="PRECEDENT omits a title")
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution", justification="OTHER")
    app = store.get_app("Mark.1.1.12-16")
    pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) == ("a", "f")]
    prompt = unit_prompt(store, app, pairs, precedents=5)
    section = prompt.split("Precedents")[1]
    assert "Mark.1.1.10" in section and "Major_Omission" in section and "PRECEDENT omits a title" in section


def test_precedents_are_human_only_and_never_the_unit_itself(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="HUMAN")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="SAME UNIT")
    store.write_decisions("Mark.1.2.2", [("a", "b", "Minor_Substitution", "PIPELINE", "low")], resp="rdgai", change="run")
    app = store.get_app("Mark.1.1.12-16")
    found = precedents_for(store, app, store.ordered_pairs(app), per_pair=10, limit=50)
    units = {item.unit_id for item in found}
    assert units == {"Mark.1.1.10"}
    assert all(item.source == "human" for item in found)


def test_precedents_are_deduplicated_and_capped(store):
    for unit_id in ("Mark.1.1.10", "Mark.1.2.2"):
        pair = store.get_unit(unit_id).pairs[0]
        store.set_classification(unit_id, pair.active, pair.passive, "Major_Substitution", justification="x")
    app = store.get_app("Mark.1.1.12-16")
    found = precedents_for(store, app, store.ordered_pairs(app), per_pair=10, limit=1)
    assert len(found) == 1
    found = precedents_for(store, app, store.ordered_pairs(app), per_pair=10, limit=50)
    keys = [(item.unit_id, frozenset((item.active, item.passive))) for item in found]
    assert len(keys) == len(set(keys))


def test_no_precedents_section_when_turned_off(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="PRECEDENT")
    app = store.get_app("Mark.1.1.12-16")
    assert "PRECEDENT" not in unit_prompt(store, app, store.ordered_pairs(app), precedents=0)


def test_decisions_start_with_an_analysis_of_the_changes():
    fields = list(UnitDecisions.model_fields)
    assert fields[0] == "analysis" and "decisions" in fields
    assert UnitDecisions(decisions=[]).analysis == ""


def test_instructions_ask_to_check_the_whole_verse_and_decide_each_change_once(store):
    instructions = build_instructions(store)
    assert "outside the variation unit" in instructions
    assert "analysis" in instructions


def test_precedents_from_one_unit_are_capped(store):
    app = store.get_app("Mark.1.1.12-16")
    for pair in store.get_unit("Mark.1.2.8-14").pairs:
        store.set_classification("Mark.1.2.8-14", pair.active, pair.passive, "Major_Substitution", justification="x")
    found = precedents_for(store, app, store.ordered_pairs(app), per_pair=10, limit=50, per_unit=2)
    assert 0 < len(found) <= 2


def test_instructions_say_the_computed_change_is_reliable(store):
    assert "computed from the words" in build_instructions(store)


def test_instructions_say_asked_pairs_are_separate_readings(store):
    assert "listed separately" in build_instructions(store)


# ---------------------------------------------------------------------------
# categories only the subreading rules assign
# ---------------------------------------------------------------------------

import shutil

from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

from rdgai.annotation import AnnotationStore
from rdgai.pipeline import draft, reserved_categories
from rdgai.rules import SubreadingRule

from .util import TEST_DATA_DIR
from .test_pipeline import requested_pairs, decide


def rule_store(tmp_path, declare:bool=True):
    """ A file with a subreading rule for Orthography; with `declare`, the file also declares Orthography rules-only. """
    path = tmp_path / "subreadings.xml"
    shutil.copy(TEST_DATA_DIR / "subreadings.xml", path)
    store = AnnotationStore(path, annotator="RT")
    store.apply_rules(rules=[SubreadingRule("orthographic", "Orthography", "o")])
    if declare:
        store.set_category_constraints("Orthography", ["rules-only"])
    store = AnnotationStore(path, annotator="RT")
    assert store.rules
    return store


def test_categories_declared_rules_only_are_reserved(tmp_path, store):
    assert reserved_categories(rule_store(tmp_path)) == {"Orthography"}
    assert reserved_categories(store) == set()          # nothing declared, nothing reserved


def test_a_rule_category_is_not_reserved_unless_declared(tmp_path):
    # a file may let annotators (and the pipeline) use a rule's category for other pairs too
    store = rule_store(tmp_path, declare=False)
    assert reserved_categories(store) == set()
    line = next(line for line in build_instructions(store).splitlines() if line.startswith("- Orthography"))
    assert "subreading rules" not in line


def test_reserved_categories_are_marked_in_the_instructions(tmp_path):
    instructions = build_instructions(rule_store(tmp_path))
    line = next(line for line in instructions.splitlines() if line.startswith("- Orthography"))
    assert "subreading rules" in line


def test_draft_retries_a_reserved_category(tmp_path):
    store = rule_store(tmp_path)
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        category = "Orthography" if len(attempts) == 1 else "Minor_Substitution"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), category))])

    report = draft(store, FunctionModel(respond), toolsets=[], unit_ids=["Mark.1.1.2"], structure_checks=False)
    assert len(attempts) == 2
    feedback = [str(part.content) for message in attempts[1] for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
    assert any("Orthography" in text and "subreading rules" in text for text in feedback)
    assert report.pairs_written > 0


def test_reserving_can_be_turned_off(tmp_path):
    store = rule_store(tmp_path)
    attempts = []

    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), "Orthography"))])

    draft(store, FunctionModel(respond), toolsets=[], unit_ids=["Mark.1.1.2"], reserve_rule_categories=False)
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# contrasting precedents: similar pairs labelled differently, so the model sees the boundary
# ---------------------------------------------------------------------------

def _label_omissions(store):
    """ Several units whose change resembles Mark.1.1.12-16 a → f (an omission), mostly one category. """
    labelled = []
    for unit_id in ("Mark.1.1.10", "Mark.1.2.2", "Mark.1.2.8-14", "Mark.1.3.2", "Mark.1.4.2"):
        try:
            unit = store.get_unit(unit_id)
        except Exception:
            continue
        for pair in unit.pairs[:2]:
            labelled.append((unit_id, pair.active, pair.passive))
    for unit_id, active, passive in labelled:
        store.set_classification(unit_id, active, passive, "Major_Omission", justification="MAJORITY")
    return labelled


def test_precedents_include_a_contrasting_category_when_one_exists(store):
    labelled = _label_omissions(store)
    unit_id, active, passive = labelled[-1]
    store.set_classification(unit_id, active, passive, "Minor_Omission", justification="CONTRAST")
    app = store.get_app("Mark.1.1.12-16")
    pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) == ("a", "f")]
    plain = precedents_for(store, app, pairs, per_pair=2, contrast=False)
    contrasted = precedents_for(store, app, pairs, per_pair=2, contrast=True, contrast_ratio=0.0)
    assert len(contrasted) == len(plain) == 2
    assert len({item.category for item in contrasted}) == 2
    assert contrasted[0].unit_id == plain[0].unit_id                  # the best match still comes first


def test_contrast_needs_a_similar_enough_pair(store):
    labelled = _label_omissions(store)
    unit_id, active, passive = labelled[-1]
    store.set_classification(unit_id, active, passive, "Minor_Omission", justification="CONTRAST")
    app = store.get_app("Mark.1.1.12-16")
    pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) == ("a", "f")]
    strict = precedents_for(store, app, pairs, per_pair=2, contrast=True, contrast_ratio=1e9)
    plain = precedents_for(store, app, pairs, per_pair=2, contrast=False)
    assert [item.unit_id for item in strict] == [item.unit_id for item in plain]


def test_contrast_keeps_the_overall_cap(store):
    _label_omissions(store)
    app = store.get_app("Mark.1.1.12-16")
    assert len(precedents_for(store, app, store.ordered_pairs(app), per_pair=3, limit=4, contrast=True, contrast_ratio=0.0)) <= 4


def test_unit_prompt_can_turn_contrast_on(store):
    labelled = _label_omissions(store)
    unit_id, active, passive = labelled[-1]
    store.set_classification(unit_id, active, passive, "Minor_Omission", justification="CONTRAST")
    app = store.get_app("Mark.1.1.12-16")
    pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) == ("a", "f")]
    prompt = unit_prompt(store, app, pairs, precedents=2, contrast=True)
    assert "labelled differently" in prompt


def test_contrast_setting_from_the_environment():
    from rdgai.pipeline import contrast_from_env
    assert contrast_from_env({"RDGAI_CONTRAST": "1"}) is True
    assert contrast_from_env({"RDGAI_CONTRAST": "0"}) is False
