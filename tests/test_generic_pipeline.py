"""
rdgai's pipeline knows nothing about one file's categories or language. Everything specific to a category scheme lives
in the TEI file: the definitions and guidance, the classifier instructions (an editable text for the pipeline) and
optional constraints that categories declare on the computed change (e.g. "adds-only"). Language-specific spelling
rules live in rdgai/languages.py and apply only when the file's language matches.
"""
import re
import shutil
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, AgentInfo

import rdgai
from rdgai.annotation import AnnotationStore, AnnotationError
from rdgai.mcp_server import build_server
from rdgai.pipeline import build_instructions, unit_prompt, draft, constraint_problem, CONSTRAINTS, UnitDecisions
from rdgai.languages import trivially_respelled

from .util import TEST_DATA_DIR
from .test_mcp import call, mark_path, store  # noqa: F401 (fixtures)
from .test_pipeline import requested_pairs, decide

PIPELINE_SOURCE = Path(rdgai.__file__).parent / "pipeline.py"
SCHEME_WORDS = ("Addition", "Omission", "Substitution", "Inflection", "Transposition", "Orthography", "Major", "Minor")


# ---------------------------------------------------------------------------
# nothing about one file in the code
# ---------------------------------------------------------------------------

def test_pipeline_code_names_no_categories_and_has_no_greek():
    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    for word in SCHEME_WORDS:
        assert not re.search(rf"\b{word}", source), f"pipeline.py mentions {word}"
    assert not re.search(r"[Ͱ-Ͽἀ-῿]", source), "pipeline.py contains Greek"


def test_analysis_description_is_generic():
    description = UnitDecisions.model_fields["analysis"].description
    assert not any(word in description for word in SCHEME_WORDS)


# ---------------------------------------------------------------------------
# classifier instructions: stored in the TEI, edited like the guidance
# ---------------------------------------------------------------------------

def test_classifier_instructions_are_empty_by_default(store):
    definitions = store.definitions()
    assert definitions.classifier_instructions == "" and definitions.constraints == {}


def test_classifier_instructions_are_stored_in_the_file(store, mark_path):
    before = store.definitions_hash()
    change = store.update_classifier_instructions("Check the whole verse.\nDecide each change once.")
    assert change.definitions_hash != before
    reloaded = AnnotationStore(mark_path, annotator="RT")
    assert reloaded.definitions().classifier_instructions == "Check the whole verse.\nDecide each change once."
    assert "Decide each change once." in mark_path.read_text(encoding="utf-8")
    reloaded.update_classifier_instructions("")
    assert AnnotationStore(mark_path).definitions_hash() == before     # empty again: the old hash


def test_categories_declare_constraints(store, mark_path):
    name = store.categories()[0].name
    store.set_category_constraints(name, ["adds-only"])
    assert AnnotationStore(mark_path).definitions().constraints == {name: ["adds-only"]}
    store.set_category_constraints(name, [])
    assert AnnotationStore(mark_path).definitions().constraints == {}


def test_unknown_constraints_and_categories_are_refused(store):
    with pytest.raises(AnnotationError):
        store.set_category_constraints(store.categories()[0].name, ["no-such-constraint"])
    with pytest.raises(AnnotationError):
        store.set_category_constraints("NoSuchCategory", ["adds-only"])


def test_classifier_instructions_and_constraints_through_mcp(store):
    server = build_server(store)
    name = store.categories()[0].name
    call(server, "update_classifier_instructions", instructions="Be careful.")
    call(server, "set_category_constraints", category=name, constraints=["omits-only"])
    data = call(server, "get_definitions").structured_content
    assert data["classifier_instructions"] == "Be careful."
    assert data["constraints"] == {name: ["omits-only"]}


def test_instructions_include_the_files_classifier_instructions(store):
    store.update_classifier_instructions("SPECIAL RULE for this file.")
    assert "SPECIAL RULE for this file." in build_instructions(store)


# ---------------------------------------------------------------------------
# subreadings: the apparatus's reading types, stated generically
# ---------------------------------------------------------------------------

def subreading_store(tmp_path):
    from rdgai.rules import SubreadingRule
    path = tmp_path / "subreadings.xml"
    shutil.copy(TEST_DATA_DIR / "subreadings.xml", path)
    store = AnnotationStore(path, annotator="RT")
    store.apply_rules(rules=[SubreadingRule("orthographic", "Orthography", "o")])
    return AnnotationStore(path, annotator="RT")


def test_unit_prompt_states_each_subreadings_parent_and_type(tmp_path):
    store = subreading_store(tmp_path)
    app = store.get_app("Mark.1.1.2")
    prompt = unit_prompt(store, app, store.work_pairs(app))
    readings = prompt.split("Readings:")[1].split("\n\n")[0]
    line = next(line for line in readings.splitlines() if line.startswith("- bo:"))
    assert "subreading of b" in line and "orthographic" in line
    assert "subreading" not in next(line for line in readings.splitlines() if line.startswith("- b:"))


def test_typed_reading_without_a_rule_still_shows_its_type(tmp_path):
    path = tmp_path / "subreadings.xml"
    shutil.copy(TEST_DATA_DIR / "subreadings.xml", path)
    store = AnnotationStore(path, annotator="RT")          # no rules: no parent known
    app = store.get_app("Mark.1.1.2")
    readings = unit_prompt(store, app, store.work_pairs(app)).split("Readings:")[1].split("\n\n")[0]
    assert "type orthographic" in next(line for line in readings.splitlines() if line.startswith("- bo:"))


# ---------------------------------------------------------------------------
# constraints declared by categories, checked against the computed change
# ---------------------------------------------------------------------------

def test_constraint_vocabulary():
    assert set(CONSTRAINTS) == {"adds-only", "omits-only", "keeps-word-count", "not-only-adds-or-omits", "rules-only"}


def test_rules_only_is_not_a_check_on_the_change():
    assert constraint_problem("rules-only", "και", "και ειπεν") is None


def test_constraint_problem():
    assert constraint_problem("adds-only", "εν φυλακη", "και εν φυλακη") is None
    assert "εν" in constraint_problem("adds-only", "εν φυλακη", "και εβαλεν εις φυλακην")
    assert constraint_problem("omits-only", "υιου του θεου", "υιου θεου") is None
    assert "κυριου" in constraint_problem("omits-only", "υιου του θεου", "υιου του κυριου")
    assert constraint_problem("keeps-word-count", "το θερος εστιν", "εστιν το θερος") is None
    assert constraint_problem("keeps-word-count", "και λεγειν", "λεγοντες") is not None
    assert constraint_problem("not-only-adds-or-omits", "το θερος", "εστιν το θερος") is not None
    assert constraint_problem("not-only-adds-or-omits", "και λεγειν", "λεγοντες") is None


def test_a_similar_shared_word_is_queried_not_refused():
    # a shared word that differs a little may be spelling: the model is asked, with the definition as the judge
    problem = constraint_problem("omits-only", "πρωτη σαββατου", "σαββατων")
    assert problem and "σαββατου → σαββατων" in problem
    assert constraint_problem("omits-only", "πρωτη σαββατου", "σαββατου") is None


def test_language_rules_allow_trivial_respellings_only_for_their_language():
    assert trivially_respelled("ειπεν", "ειπε", "grc")
    assert not trivially_respelled("ειπεν", "ειπε", "en")
    assert trivially_respelled("same", "same", "en")
    assert constraint_problem("adds-only", "ειπεν", "και ειπε", language="grc") is None
    assert constraint_problem("adds-only", "ειπεν", "και ειπε", language="en") is not None


def respond_with(category, attempts):
    def respond(messages, info: AgentInfo):
        attempts.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decide(requested_pairs(messages), category))])
    return respond


def test_draft_checks_nothing_when_no_constraints_are_declared(store):
    attempts = []
    draft(store, FunctionModel(respond_with("Major_Omission", attempts)), toolsets=[], unit_ids=["Mark.1.1.12-16"])
    assert len(attempts) == 1


def test_draft_asks_once_when_a_declared_constraint_is_broken(store):
    store.set_category_constraints("Major_Omission", ["omits-only"])
    attempts = []
    draft(store, FunctionModel(respond_with("Major_Omission", attempts)), toolsets=[], unit_ids=["Mark.1.1.12-16"])
    assert len(attempts) == 2
    feedback = [str(part.content) for message in attempts[1] for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
    assert any("Major_Omission" in text and "omits-only" in text for text in feedback)
