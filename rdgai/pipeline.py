"""
The classification pipeline, built on pydantic-ai.

Agents read through the rdgai MCP server (a read-only ``rdgai serve-mcp`` subprocess: definitions, units, similar
pairs, category listings) and answer with a structured decision for every pair they are asked about. The pipeline
checks each answer (every pair present, known categories) and writes it with ``resp="#rdgai"``, the agent's
confidence as ``@cert`` and a ``<change>`` recording the run and model. The TEI file is the state, so runs are
resumable: only unlabelled pairs are ever asked about, and a pair labelled by someone else meanwhile is never
overwritten.
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Literal

import anyio
from pydantic import BaseModel, Field

from .annotation import AnnotationStore, AnnotationError, DIRECTION_CONVENTION
from .diff import describe_change
from .search import verse_label
from .constraints import CONSTRAINTS, constraint_problem
from .tei import get_language_code

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

DEFAULT_MODEL = "anthropic:claude-opus-5-5"
AGENT_TOOLS = ("get_unit", "search_similar", "list_category")
MAX_PAIRS_PER_REQUEST = 40
MAX_EXPERT_CALLS = 2        # consultations of the expert per draft request (tool mode)
EXPERT_PRECEDENTS_PER_PAIR = 3
MAX_EXPERT_PRECEDENTS = 12
PRECEDENTS_PER_PAIR = 3     # similar human-labelled pairs given in the prompt for each pair to classify
MAX_PRECEDENTS = 30         # at most this many per request
MAX_PRECEDENTS_PER_UNIT = 4 # and at most this many from any one unit, for variety
PRECEDENT_REASON_CHARACTERS = 300


# ---------------------------------------------------------------------------
# structured output
# ---------------------------------------------------------------------------

class PairDecision(BaseModel):
    active: str = Field(description="Id of the first (active) reading, exactly as listed.")
    passive: str = Field(description="Id of the second (passive) reading, exactly as listed.")
    category: str = Field(description="The category for the change from the first reading to the second.")
    justification: str = Field(description="One or two sentences: why this category, citing the definitions or precedents.")
    confidence: Literal["high", "medium", "low"]
    alternative: str | None = Field(default=None, description=(
        "Only if you are unsure: the other category a careful annotator might choose for this pair. Leave it empty when "
        "the definitions decide the case."))


class UnitDecisions(BaseModel):
    analysis: str = Field(default="", description=(
        "Written before the decisions: the distinct changes among the readings (each added, omitted, replaced, reordered "
        "or re-formed word or phrase), and how the definitions and instructions judge each one, after checking the whole "
        "verse. Where several words change, say how they are related; where a subreading is involved, say what its type "
        "means for the comparison. Brief."))
    decisions: list[PairDecision] = Field(description="One decision for every pair listed under 'Pairs to classify'.")


@dataclass
class DraftDeps:
    requested: list[tuple[str, str]]
    categories: set[str]
    reserved: set[str] = field(default_factory=set)
    texts: dict = field(default_factory=dict)        # (active, passive) -> (active text, passive text), for the constraint check
    constraints: dict = field(default_factory=dict)  # category -> constraint names declared in the file
    language: str = ""                               # the file's language code, for language-specific spelling rules
    reconsidered: bool = False                       # the constraint check asks only once
    unit_id: str = ""
    expert: object = None                            # the expert agent, when the draft model may consult it (tool mode)
    expert_name: str = ""
    expert_calls: int = 0                            # consultations in this request
    report: object = None                            # the DraftReport, for the expert's usage


CONTRAST_DEFAULT = False   # include a contrasting precedent for each pair (RDGAI_CONTRAST overrides)
CONTRAST_RATIO = 0.5       # ... if it scores at least this fraction of the best precedent


def constraint_problems(category:str, active_text:str, passive_text:str, constraints:dict, language:str="") -> list[str]:
    """ How the change breaks the constraints the file declares for the category (none declared: nothing to check). """
    problems = []
    for constraint in constraints.get(category, []):
        if constraint in CONSTRAINTS:
            problem = constraint_problem(constraint, active_text, passive_text, category=category, language=language)
            if problem:
                problems.append(problem)
    return problems


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

def reserved_categories(store:AnnotationStore) -> set[str]:
    """
    Categories the file declares 'rules-only': assigned only by the subreading rules, so never to a pair the pipeline
    is asked about. Nothing is reserved unless declared (a file may also use a rule's category for other pairs).
    """
    return {name for name, declared in store.category_constraints().items() if "rules-only" in declared}


def build_instructions(store:AnnotationStore, reserve:bool=True, expert_tool:bool=False) -> str:
    """ The instructions for every request. They depend only on the definitions, so the prompt cache is reused. """
    definitions = store.definitions()
    reserved = reserved_categories(store) if reserve else set()
    lines = [
        f"You classify transcriptional changes between variant readings in a critical apparatus of a text in {definitions.language}.",
        "",
        "For each pair of readings you are given, choose the category that describes the change from the FIRST (active) reading "
        "to the SECOND (passive) reading. " + DIRECTION_CONVENTION,
        "",
        "Categories (name — inverse — definition):",
    ]
    for category in definitions.categories:
        note = (" [Assigned only by subreading rules, to readings the apparatus tags as variants of another; never use it "
                "for a pair you are asked about.]") if category.name in reserved else ""
        lines.append(f"- {category.name} — inverse {category.inverse} — {category.description}{note}")
    if definitions.guidance:
        lines += ["", "Guidance from the editors:", definitions.guidance]
    if definitions.classifier_instructions:
        lines += ["", "Instructions for classifying this file (from the editors):", definitions.classifier_instructions]
    lines += [
        "",
        "Before deciding:",
        "- Read the whole verse with each reading in place. Words outside the variation unit are the same in every reading: "
        "never count them as added, omitted or changed.",
        "- The change given in parentheses for each pair (what the second reading adds, omits, reorders or changes) is "
        "computed from the words and is reliable: a pair described as only adding or omitting words has no reordering, "
        "even if the words stand elsewhere in other readings. 'Changed: X → Y' lists words removed and words added.",
        "- Readings the apparatus types as subreadings are marked in the list of readings with their type and, where known, "
        "the reading they belong to (e.g. 'subreading of b, type orthographic'). A subreading differs from that reading only "
        "in the way its type describes; where the definitions or guidance refer to the apparatus's reading types, apply them "
        "to those differences.",
        "- Every pair you are asked about consists of readings the apparatus has listed separately: pairs covered by subreading "
        "rules are labelled automatically and never asked about.",
        "- In the analysis field, list each distinct change in the unit once and decide how the definitions judge it; then "
        "classify every pair from the changes it contains, so that the same change gets the same judgement in every pair.",
        "",
        "Precedents: each request lists human-labelled pairs from other units with a similar change, with the annotator's "
        "reasons. Human labels are certain: where a precedent has the same kind of change, follow it. "
        "Tools: search_similar finds more labelled pairs with a similar change; list_category shows the pairs labelled with "
        "a category; get_unit shows a unit in full. Pipeline labels are tentative: count them for much less, and never "
        "treat a tentative label as settling a question on its own. Classify from the readings, the given verse and the "
        "definitions; use precedents to apply the definitions consistently.",
        "",
        "Answer with one decision for every pair listed under 'Pairs to classify', in the direction given "
        "(first reading → second reading), using exactly one of the category names above. Give a short justification "
        "(one or two sentences) and your confidence: high, medium or low. Only where you are unsure between two "
        "categories, also give the other one as the alternative; leave it empty when the definitions decide the case, "
        "as they should for most pairs.",
    ]
    if expert_tool:
        lines += [
            "",
            f"Expert: the tool ask_expert asks a stronger model to choose between two categories for some pairs. Use it only "
            f"when, after applying the definitions, you remain unsure between two categories; most pairs do not need it. Ask "
            f"about all the pairs of this request that share the same two options at once (at most {MAX_EXPERT_CALLS} "
            f"questions per request), and follow its answer unless you find a clear error in it.",
        ]
    return "\n".join(lines)


def contrast_from_env(environ=os.environ) -> bool:
    """ Whether precedents include a contrasting category, overridable for experiments by RDGAI_CONTRAST (1 / 0). """
    value = environ.get("RDGAI_CONTRAST")
    return CONTRAST_DEFAULT if value is None else value.strip() not in ("", "0", "false", "no")


def contrasted(store:AnnotationStore, app, pair, found:list, ratio:float) -> list:
    """
    `found` (best first) with the closest pair of another category moved to second place, if one scores at least
    `ratio` × the best: the model then sees where the boundary between the two categories lies.
    """
    if not found:
        return found
    top = found[0]
    alternative = next((item for item in found[1:] if item.category != top.category), None)
    if alternative is None:
        others = [category.name for category in store.categories() if category.name != top.category]
        more = store.search_similar(str(app), pair.active.n, pair.passive.n, limit=5, sources=("human",), categories=others)
        alternative = next((item for item in more if item.unit_id != str(app)), None)
    if alternative is None or alternative.score < ratio * top.score:
        return found
    rest = [item for item in found[1:] if item is not alternative and
            (item.unit_id, item.active, item.passive) != (alternative.unit_id, alternative.active, alternative.passive)]
    return [top, alternative, *rest]


def precedents_for(store:AnnotationStore, app, pairs, per_pair:int=PRECEDENTS_PER_PAIR, limit:int=MAX_PRECEDENTS,
                   per_unit:int=MAX_PRECEDENTS_PER_UNIT, contrast:bool|None=None, contrast_ratio:float=CONTRAST_RATIO) -> list:
    """
    Human-labelled pairs from other units whose change resembles the pairs to classify: the best `per_pair` for each
    pair, taken in turn (so every pair is represented), without duplicates, at most `per_unit` from any one unit and
    `limit` in all. With `contrast`, each pair's second precedent is the closest of a different category (if similar
    enough; see `contrasted`).
    """
    if per_pair <= 0 or limit <= 0:
        return []
    from collections import Counter
    contrast = contrast_from_env() if contrast is None else contrast
    unit_id = str(app)
    candidates = []
    for pair in pairs:
        found = store.search_similar(unit_id, pair.active.n, pair.passive.n, limit=per_pair + 20, sources=("human",))
        found = [item for item in found if item.unit_id != unit_id]
        candidates.append(contrasted(store, app, pair, found, contrast_ratio) if contrast and per_pair > 1 else found)
    chosen, seen, per_source = [], set(), Counter()
    for _ in range(per_pair):
        for found in candidates:
            # the next precedent for this pair not already chosen and not from a unit already used up
            while found:
                item = found.pop(0)
                key = (item.unit_id, frozenset((item.active, item.passive)))
                if key not in seen and per_source[item.unit_id] < per_unit:
                    break
            else:
                continue
            seen.add(key)
            per_source[item.unit_id] += 1
            chosen.append(item)
            if len(chosen) >= limit:
                return chosen
    return chosen


def subreading_note(reading, parents) -> str:
    """ ' [subreading of b, type orthographic]' for a typed reading whose parent is known, ' [type …]' otherwise. """
    if not reading.type:
        return ""
    parent = parents.parents.get(reading.n) if parents else None
    return f" [subreading of {parent}, type {reading.type}]" if parent else f" [type {reading.type}]"


def unit_prompt(store:AnnotationStore, app, pairs, precedents:int=PRECEDENTS_PER_PAIR, contrast:bool|None=None) -> str:
    """
    The request for one unit: its verse, readings (and the verse with each in place), labels already made, similar
    human-labelled pairs from other units, and the pairs to classify.
    """
    unit_id = str(app)
    lines = [f"Variation unit {unit_id} ({verse_label(unit_id)})", f"Verse: {app.text_in_context()}", "", "Readings:"]
    readings = [reading for reading in app.readings if not store.is_excluded(reading)]
    parents = store.rule_parents(app) if store.rules else None
    for reading in readings:
        note = f" [notes: {'; '.join(reading.notes)}]" if reading.notes else ""
        lines.append(f"- {reading.n}: {reading} ({len(reading.witnesses)} witnesses){subreading_note(reading, parents)}{note}")
    lines += ["", "Verse with each reading in place:"]
    lines += [f"- {reading.n}: {reading.text_in_context()}" for reading in readings]

    labelled = []
    for pair in store.ordered_pairs(app):
        if pair.relation_element() is None or store.hides(app):
            continue
        source = store.label_source(pair)
        status = {"human": "human, certain", "pipeline": "pipeline, tentative", "rule": "rule"}.get(source, source)
        labelled.append(f"- {pair.active.n} → {pair.passive.n}: {store.current_category(pair)} ({status})")
    if labelled:
        lines += ["", "Labels already in this unit:", *labelled]

    contrast = contrast_from_env() if contrast is None else contrast
    found = precedents_for(store, app, pairs, per_pair=precedents, contrast=contrast) if precedents else []
    if found:
        note = "; where similar changes were labelled differently, both are shown: note what separates them" if contrast else ""
        lines += ["", f"Precedents (human labels from other units with a similar change; certain{note}):"]
        for item in found:
            reason = " ".join((item.justification or "").split())
            if len(reason) > PRECEDENT_REASON_CHARACTERS:
                reason = reason[:PRECEDENT_REASON_CHARACTERS].rsplit(" ", 1)[0] + " …"
            lines.append(
                f"- {item.verse} ({item.unit_id}) {item.active} → {item.passive}: {item.active_text} → {item.passive_text} "
                f"({item.change}): {item.category}" + (f" — reason: {reason}" if reason else "")
            )

    lines += ["", "Pairs to classify (category for the change from the first reading to the second):"]
    for pair in pairs:
        change = describe_change(pair.active.text, pair.passive.text).text
        lines.append(f"- {pair.active.n} → {pair.passive.n}: {pair.active} → {pair.passive} ({change})")
    return "\n".join(lines)


def model_settings_for(model, effort:str|None=None) -> dict:
    """ Settings for the model: for Anthropic models, cache the instructions and tool definitions. """
    name = model if isinstance(model, str) else getattr(model, "model_name", "")
    settings = {"max_tokens": 16000}
    if isinstance(model, str) and model.startswith("anthropic:"):
        settings["anthropic_cache_instructions"] = True
        settings["anthropic_cache_tool_definitions"] = True
        if effort:
            settings["anthropic_effort"] = effort
    return settings


def default_toolsets(store:AnnotationStore) -> list:
    """ The rdgai MCP server for this file, read-only, as a subprocess, limited to the tools agents use. """
    from fastmcp.client import Client
    from fastmcp.client.transports import StdioTransport
    from pydantic_ai.mcp import MCPToolset

    args = ["-c", "from rdgai.main import app; app()", "serve-mcp", str(store.path), "--read-only", "--sample-file", str(store.sample_path)]
    for reading_type in sorted(store.ignore_types):
        args += ["--ignore-type", reading_type]
    if not store.reading_notes:
        args.append("--no-reading-notes")
    if store.evaluation:
        args.append("--evaluation")
    toolset = MCPToolset(Client(StdioTransport(command=sys.executable, args=args, env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1"})))
    return [toolset.filtered(lambda context, tool: tool.name in AGENT_TOOLS)]


def model_name(model) -> str:
    return model if isinstance(model, str) else getattr(model, "model_name", type(model).__name__)


def price(model, usage) -> float|None:
    """ The cost in USD of the usage on this model, if its price is known. """
    from genai_prices import Usage, calc_price
    name = model_name(model)
    provider, _, ref = name.partition(":") if ":" in name else ("", "", name)
    try:
        return float(calc_price(
            Usage(
                input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_tokens", 0), cache_write_tokens=usage.get("cache_write_tokens", 0),
            ),
            model_ref=ref, provider_id=provider or None,
        ).total_price)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# the draft pass
# ---------------------------------------------------------------------------

class DraftReport(BaseModel):
    run_id: str
    model: str
    units_done: int = 0
    pairs_written: int = 0
    pairs_skipped: int = Field(default=0, description="Pairs labelled by someone else while the agent worked (kept).")
    errors: list[str] = Field(default_factory=list)
    usage: dict = Field(default_factory=lambda: dict(requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0))
    cost: float | None = None
    uncertain: list["Uncertain"] = Field(default_factory=list, description="Pairs written with an alternative category (the draft was unsure).")
    batch: bool = Field(default=False, description="Sent as Message Batches (half price).")
    batch_ids: list[str] = Field(default_factory=list)
    expert_model: str = ""
    expert_calls: int = 0
    expert_usage: dict = Field(default_factory=lambda: dict(requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0))
    expert_cost: float | None = None


def add_usage(total:dict, usage):
    for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        total[key] = total.get(key, 0) + (getattr(usage, key, 0) or 0)


def review_answer(deps:DraftDeps, output:UnitDecisions) -> str|None:
    """
    Checks a draft answer against the request: every pair present and none extra, known categories, a valid alternative,
    the categories' declared constraints (asked once) and categories reserved for the subreading rules. Returns the
    message asking the model to answer again, or None if the answer stands. Shared by the agent and the batch mode.
    """
    requested = deps.requested
    given = {(decision.active, decision.passive) for decision in output.decisions}
    problems = []
    missing = [pair for pair in requested if pair not in given]
    if missing:
        problems.append("Missing pairs: " + ", ".join(f"{a} → {b}" for a, b in missing) + ".")
    extra = sorted(given - set(requested))
    if extra:
        problems.append("Not asked about (check the direction): " + ", ".join(f"{a} → {b}" for a, b in extra) + ".")
    unknown = sorted({decision.category for decision in output.decisions} - deps.categories)
    if unknown:
        problems.append("Unknown categories: " + ", ".join(unknown) + ". Use exactly one of: " + ", ".join(sorted(deps.categories)) + ".")
    for decision in output.decisions:
        if decision.alternative is None or not decision.alternative.strip():
            decision.alternative = None
        elif decision.alternative not in deps.categories:
            problems.append(f"{decision.active} → {decision.passive}: the alternative {decision.alternative} is not a category; "
                            f"give one of the category names, or leave the alternative empty.")
        elif decision.alternative == decision.category:
            problems.append(f"{decision.active} → {decision.passive}: the alternative must differ from the category; "
                            f"leave it empty if you are not unsure.")
    if not problems and not deps.reconsidered and deps.texts and deps.constraints:
        broken = []
        for decision in output.decisions:
            texts = deps.texts.get((decision.active, decision.passive))
            for problem in (constraint_problems(decision.category, *texts, deps.constraints, deps.language) if texts else []):
                broken.append(f"{decision.active} → {decision.passive}: {problem}.")
        if broken:
            deps.reconsidered = True   # asked once; the next answer stands
            return (" ".join(broken) + " Reconsider these pairs (keep an answer if the definitions support it) "
                    "and answer again with one decision for every pair listed.")
    reserved = sorted({decision.category for decision in output.decisions} & deps.reserved)
    if reserved:
        pairs = [f"{d.active} → {d.passive}" for d in output.decisions if d.category in deps.reserved]
        problems.append(
            f"{', '.join(reserved)} is assigned only by subreading rules, and these readings are listed separately in the "
            f"apparatus, so it does not apply to {', '.join(pairs)}: classify them by the change they make.")
    if problems:
        return " ".join(problems) + " Answer again with one decision for every pair listed."
    return None


def draft_agent(store:AnnotationStore, model, toolsets, effort:str|None=None, reserve:bool=True, expert_tool:bool=False):
    from pydantic_ai import Agent, ModelRetry, RunContext

    agent = Agent(
        model,
        output_type=UnitDecisions,
        instructions=build_instructions(store, reserve=reserve, expert_tool=expert_tool),
        deps_type=DraftDeps,
        toolsets=toolsets,
        model_settings=model_settings_for(model, effort),
        retries=3,
    )

    @agent.output_validator
    def check(context:RunContext[DraftDeps], output:UnitDecisions) -> UnitDecisions:
        problem = review_answer(context.deps, output)
        if problem:
            raise ModelRetry(problem)
        return output

    if expert_tool:
        @agent.tool
        async def ask_expert(context:RunContext[DraftDeps], pairs:list[str], options:list[str], question:str) -> str:
            """
            Asks a stronger model to choose between two categories for some of the pairs you are classifying.
            pairs: the pairs, as 'first>second' (e.g. 'a>b'), in the direction asked; options: the two categories you are
            unsure between; question: what you are unsure about. Returns the expert's choice and reason for each pair.
            """
            deps = context.deps
            if deps.expert_calls >= MAX_EXPERT_CALLS:
                return "No more expert questions for this request: decide the remaining pairs yourself."
            requested = set(deps.requested)
            asked = []
            for item in pairs:
                active, _, passive = item.partition(">")
                if (active.strip(), passive.strip()) not in requested:
                    return f"{item} is not one of the pairs to classify (give them as 'first>second', in the direction asked)."
                asked.append((active.strip(), passive.strip()))
            if len(options) != 2 or len(set(options)) != 2 or not set(options) <= deps.categories:
                return "Give exactly two different category names as the options."
            deps.expert_calls += 1
            app = store.get_app(deps.unit_id)
            items = [Uncertain(unit_id=deps.unit_id, active=a, passive=b, category=options[0], alternative=options[1],
                               confidence="low", justification="") for a, b in asked]
            with store.lock:
                prompt = expert_prompt(store, app, items, (options[0], options[1]), question=question)
            result = await deps.expert.run(prompt, deps=ExpertDeps(requested=asked, options=(options[0], options[1])))
            if deps.report is not None:
                deps.report.expert_calls += 1
                add_usage(deps.report.expert_usage, result.usage)
            return "\n".join(f"{d.active} → {d.passive}: {d.category} ({d.confidence}) — {d.justification}" for d in result.output.decisions)

    return agent


# ---------------------------------------------------------------------------
# the expert: a stronger model choosing between two categories
# ---------------------------------------------------------------------------

class Uncertain(BaseModel):
    """ A pair the draft model was unsure about: its category and the alternative it named. """
    unit_id: str
    active: str
    passive: str
    category: str
    alternative: str
    confidence: str = ""
    justification: str = ""


DraftReport.model_rebuild()


class ExpertDecision(BaseModel):
    active: str = Field(description="Id of the first (active) reading, exactly as listed.")
    passive: str = Field(description="Id of the second (passive) reading, exactly as listed.")
    category: str = Field(description="One of the two categories offered.")
    justification: str = Field(description="One or two sentences: why, citing the definitions or precedents.")
    confidence: Literal["high", "medium", "low"]


class ExpertDecisions(BaseModel):
    decisions: list[ExpertDecision] = Field(description="One decision for every pair listed under 'Pairs to decide'.")


@dataclass
class ExpertDeps:
    requested: list[tuple[str, str]]
    options: tuple[str, str]


def build_expert_instructions(store:AnnotationStore) -> str:
    """ The expert's instructions: the task, the editors' guidance and classifier instructions (cached across requests). """
    definitions = store.definitions()
    lines = [
        f"You settle hard cases in the classification of transcriptional changes between variant readings in a critical "
        f"apparatus of a text in {definitions.language}. Another model classified the pairs you are given but was unsure "
        f"between two categories. For each pair, decide which of the two categories offered describes the change from the "
        f"FIRST (active) reading to the SECOND (passive) reading. " + DIRECTION_CONVENTION,
    ]
    if definitions.guidance:
        lines += ["", "Guidance from the editors:", definitions.guidance]
    if definitions.classifier_instructions:
        lines += ["", "Instructions for classifying this file (from the editors):", definitions.classifier_instructions]
    lines += [
        "",
        "Read the whole verse with each reading in place: words outside the variation unit are the same in every reading. "
        "The change given for each pair is computed from the words and is reliable. Readings the apparatus types as "
        "subreadings are marked with their type and, where known, the reading they belong to. Human precedents are "
        "certain: where a precedent has the same kind of change, follow it.",
        "",
        "Answer with one decision for every pair listed under 'Pairs to decide', using exactly one of the two categories "
        "offered, with a short justification and your confidence: high, medium or low.",
    ]
    return "\n".join(lines)


def expert_precedents(store:AnnotationStore, app, pairs:list[tuple[str, str]], options:tuple[str, str],
                      per_pair:int=EXPERT_PRECEDENTS_PER_PAIR, limit:int=MAX_EXPERT_PRECEDENTS) -> list:
    """ The closest human-labelled pairs from other units labelled with either option, a few per pair, without duplicates. """
    chosen, seen = [], set()
    for active, passive in pairs:
        found = store.search_similar(str(app), active, passive, limit=per_pair + 10, sources=("human",), categories=list(options))
        taken = 0
        for item in found:
            key = (item.unit_id, frozenset((item.active, item.passive)))
            if item.unit_id == str(app) or key in seen:
                continue
            seen.add(key)
            chosen.append(item)
            taken += 1
            if taken >= per_pair or len(chosen) >= limit:
                break
        if len(chosen) >= limit:
            break
    return chosen


def expert_prompt(store:AnnotationStore, app, items:list[Uncertain], options:tuple[str, str], question:str="") -> str:
    """ The narrow question for the expert: these pairs, A or B, with the verse, the two definitions and precedents. """
    unit_id = str(app)
    definitions = {category.name: category for category in store.definitions().categories}
    lines = [f"Variation unit {unit_id} ({verse_label(unit_id)})", f"Verse: {app.text_in_context()}", "", "Readings:"]
    readings = [reading for reading in app.readings if not store.is_excluded(reading)]
    parents = store.rule_parents(app) if store.rules else None
    for reading in readings:
        lines.append(f"- {reading.n}: {reading} ({len(reading.witnesses)} witnesses){subreading_note(reading, parents)}")
    lines += ["", "Verse with each reading in place:"]
    lines += [f"- {reading.n}: {reading.text_in_context()}" for reading in readings]
    lines += ["", "The two categories to choose between:"]
    for name in options:
        lines.append(f"- {name} — inverse {definitions[name].inverse} — {definitions[name].description}")
    if question:
        lines += ["", f"The other model's question: {question}"]
    found = expert_precedents(store, app, [(item.active, item.passive) for item in items], options)
    if found:
        lines += ["", "Precedents (human labels of these two categories from other units with a similar change; certain):"]
        for item in found:
            reason = " ".join((item.justification or "").split())
            if len(reason) > PRECEDENT_REASON_CHARACTERS:
                reason = reason[:PRECEDENT_REASON_CHARACTERS].rsplit(" ", 1)[0] + " …"
            lines.append(f"- {item.verse} ({item.unit_id}) {item.active} → {item.passive}: {item.active_text} → {item.passive_text} "
                         f"({item.change}): {item.category}" + (f" — reason: {reason}" if reason else ""))
    lines += ["", f"Pairs to decide ({options[0]} or {options[1]}, for the change from the first reading to the second):"]
    for item in items:
        pair = store.get_pair(app, item.active, item.passive)
        lines.append(f"- {item.active} → {item.passive}: {pair.active} → {pair.passive} ({describe_change(pair.active.text, pair.passive.text).text})")
    return "\n".join(lines)


def expert_agent(store:AnnotationStore, model, effort:str|None=None):
    from pydantic_ai import Agent, ModelRetry, RunContext

    agent = Agent(
        model,
        output_type=ExpertDecisions,
        instructions=build_expert_instructions(store),
        deps_type=ExpertDeps,
        model_settings=model_settings_for(model, effort),
        retries=3,
    )

    @agent.output_validator
    def check(context:RunContext[ExpertDeps], output:ExpertDecisions) -> ExpertDecisions:
        given = {(decision.active, decision.passive) for decision in output.decisions}
        problems = []
        missing = [pair for pair in context.deps.requested if pair not in given]
        if missing:
            problems.append("Missing pairs: " + ", ".join(f"{a} → {b}" for a, b in missing) + ".")
        wrong = sorted({decision.category for decision in output.decisions} - set(context.deps.options))
        if wrong:
            problems.append(f"Choose only {context.deps.options[0]} or {context.deps.options[1]} (not {', '.join(wrong)}).")
        if problems:
            raise ModelRetry(" ".join(problems) + " Answer again with one decision for every pair listed.")
        return output

    return agent


def uncertain_groups(items:list[Uncertain]) -> list[list[Uncertain]]:
    """ The uncertain pairs grouped by unit and by their two options (either order), in the order first seen. """
    groups = {}
    for item in items:
        groups.setdefault((item.unit_id, frozenset((item.category, item.alternative))), []).append(item)
    return list(groups.values())


class EscalationReport(BaseModel):
    run_id: str
    model: str
    pairs: int = 0
    groups: int = 0
    changed: int = 0
    kept: int = 0
    skipped: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    decisions: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=lambda: dict(requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0))
    cost: float | None = None


async def escalate_async(store:AnnotationStore, model, items:list[Uncertain], concurrency:int=4, effort:str|None=None) -> EscalationReport:
    """
    Asks the expert about the pairs the draft was unsure of, one question per unit and pair of options, and replaces the
    draft's label where the expert chooses the other option. Only the pipeline's own labels are changed (a label changed
    meanwhile, or a human label, is kept).
    """
    from .annotation import ConflictError

    run_id = store.start_run(model=model_name(model), description="Escalation of uncertain pairs.") if items else ""
    report = EscalationReport(run_id=run_id, model=model_name(model), pairs=len(items))
    groups = uncertain_groups(items)
    report.groups = len(groups)
    if not groups:
        report.cost = 0.0
        return report
    agent = expert_agent(store, model, effort)
    limiter = anyio.Semaphore(max(concurrency, 1))

    async def ask(group:list[Uncertain]):
        async with limiter:
            first = group[0]
            options = (first.category, first.alternative)
            app = store.get_app(first.unit_id)
            with store.lock:
                prompt = expert_prompt(store, app, group, options)
            try:
                result = await agent.run(prompt, deps=ExpertDeps(requested=[(item.active, item.passive) for item in group], options=options))
            except Exception as error:
                report.errors.append(f"{first.unit_id}: {type(error).__name__}: {error}")
                return
            add_usage(report.usage, result.usage)
            by_pair = {(item.active, item.passive): item for item in group}
            for decision in result.output.decisions:
                item = by_pair.get((decision.active, decision.passive))
                if item is None:
                    continue
                report.decisions.append(dict(unit_id=item.unit_id, active=item.active, passive=item.passive, draft=item.category,
                                             alternative=item.alternative, expert=decision.category, confidence=decision.confidence,
                                             justification=decision.justification))
                if decision.category == item.category:
                    report.kept += 1
                    continue
                try:
                    await anyio.to_thread.run_sync(lambda: store.set_classification(
                        item.unit_id, item.active, item.passive, decision.category,
                        justification=f"Expert ({model_name(model)}): {decision.justification}", cert=decision.confidence,
                        resp="rdgai", change=run_id, expected_category=item.category,
                    ))
                    report.changed += 1
                except (ConflictError, AnnotationError) as error:
                    report.skipped.append(f"{item.unit_id} {item.active}>{item.passive}: {error}")

    async with anyio.create_task_group() as task_group:
        for group in groups:
            task_group.start_soon(ask, group)
    report.cost = price(model, report.usage)
    return report


def escalate(store:AnnotationStore, model, items:list[Uncertain], **kwargs) -> EscalationReport:
    """ See escalate_async. """
    return anyio.run(lambda: escalate_async(store, model, items, **kwargs))


def pending_units(store:AnnotationStore, unit_ids:list[str]|None=None) -> list:
    """ Units (in document order) with pairs still to classify, and the pairs, in display order. """
    store.refresh()
    apps = [store.get_app(unit_id) for unit_id in unit_ids] if unit_ids else store.doc.apps
    pending = []
    for app in apps:
        if store.hides(app):
            continue
        work = {(pair.active.n, pair.passive.n) for pair in store.work_pairs(app) if not store.is_classified(pair)}
        pairs = [pair for pair in store.ordered_pairs(app) if (pair.active.n, pair.passive.n) in work or (pair.passive.n, pair.active.n) in work]
        if pairs:
            pending.append((app, pairs))
    return pending


async def draft_async(
    store:AnnotationStore,
    model=DEFAULT_MODEL,
    toolsets:list|None=None,
    unit_ids:list[str]|None=None,
    limit_units:int|None=None,
    concurrency:int=4,
    max_pairs_per_request:int=MAX_PAIRS_PER_REQUEST,
    effort:str|None=None,
    progress=None,
    precedents:int=PRECEDENTS_PER_PAIR,
    reserve_rule_categories:bool=True,
    structure_checks:bool=True,
    expert=None,
    expert_mode:str="tool",
) -> DraftReport:
    """
    Drafts the pending pairs. With `expert` and expert_mode 'tool', the draft model may consult the expert through the
    ask_expert tool. (For escalation after the draft, call escalate() with report.uncertain.)
    """
    toolsets = default_toolsets(store) if toolsets is None else toolsets
    pending = pending_units(store, unit_ids)
    if limit_units is not None:
        pending = pending[:limit_units]
    run_id = store.start_run(model=model_name(model), description="Draft pass.")
    report = DraftReport(run_id=run_id, model=model_name(model))
    if not pending:
        return report

    expert_tool = expert is not None and expert_mode == "tool"
    agent = draft_agent(store, model, toolsets, effort, reserve=reserve_rule_categories, expert_tool=expert_tool)
    expert_runner = expert_agent(store, expert, effort) if expert_tool else None
    if expert_tool:
        report.expert_model = model_name(expert)
    definitions = store.definitions()
    categories = {category.name for category in definitions.categories}
    constraints = definitions.constraints if structure_checks else {}
    language = get_language_code(store.doc.tree)
    reserved = reserved_categories(store) if reserve_rule_categories else set()
    limiter = anyio.Semaphore(max(concurrency, 1))

    async def classify(app, pairs):
        async with limiter:
            unit_done = False
            for start in range(0, len(pairs), max(max_pairs_per_request, 1)):
                chunk = pairs[start:start + max_pairs_per_request]
                deps = DraftDeps(requested=[(pair.active.n, pair.passive.n) for pair in chunk], categories=categories, reserved=reserved,
                                 texts={(pair.active.n, pair.passive.n): (pair.active.text, pair.passive.text) for pair in chunk} if structure_checks else {},
                                 constraints=constraints, language=language, unit_id=str(app), expert=expert_runner,
                                 expert_name=report.expert_model, report=report)
                with store.lock:  # other units may be writing to the document in worker threads
                    prompt = unit_prompt(store, app, chunk, precedents=precedents)
                try:
                    result = await agent.run(prompt, deps=deps)
                except Exception as error:  # a failed unit is reported; the rest of the run continues
                    report.errors.append(f"{app}: {type(error).__name__}: {error}")
                    continue
                usage = result.usage
                for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                    report.usage[key] += getattr(usage, key, 0) or 0
                decisions = [
                    (decision.active, decision.passive, decision.category, decision.justification, decision.confidence)
                    for decision in result.output.decisions
                ]
                try:
                    written = await anyio.to_thread.run_sync(
                        lambda: store.write_decisions(str(app), decisions, resp="rdgai", change=run_id)
                    )
                except AnnotationError as error:
                    report.errors.append(f"{app}: {error}")
                    continue
                report.pairs_written += len(written.written)
                kept = set(written.written)
                report.uncertain += [
                    Uncertain(unit_id=str(app), active=decision.active, passive=decision.passive, category=decision.category,
                              alternative=decision.alternative, confidence=decision.confidence, justification=decision.justification)
                    for decision in result.output.decisions if decision.alternative and (decision.active, decision.passive) in kept
                ]
                report.pairs_skipped += len(written.skipped)
                unit_done = unit_done or bool(written.written)
            if unit_done:
                report.units_done += 1
            if progress:
                progress(app)

    async with agent:
        async with anyio.create_task_group() as group:
            for app, pairs in pending:
                group.start_soon(classify, app, pairs)
    report.cost = price(model, report.usage)
    if expert_tool:
        report.expert_cost = price(expert, report.expert_usage)
    return report


def draft(store:AnnotationStore, model=DEFAULT_MODEL, **kwargs) -> DraftReport:
    """ Classifies the unlabelled pairs (excluding pairs covered by subreading rules); see draft_async. """
    return anyio.run(lambda: draft_async(store, model, **kwargs))


# ---------------------------------------------------------------------------
# cost estimate
# ---------------------------------------------------------------------------

class Estimate(BaseModel):
    model: str
    units: int
    pairs: int
    requests: int
    input_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost: float | None = Field(description="Estimated USD; a rough guide. Calibrate with a small run (--limit-units) first.")


CHARACTERS_PER_TOKEN = 2.5     # Greek and other non-Latin scripts tokenize densely
TOOL_TOKENS_PER_REQUEST = 3000  # e.g. one search_similar call and its results, plus the extra turn
OUTPUT_TOKENS_PER_PAIR = 90
OUTPUT_TOKENS_PER_REQUEST = 150


def estimate(store:AnnotationStore, model=DEFAULT_MODEL, unit_ids:list[str]|None=None,
             max_pairs_per_request:int=MAX_PAIRS_PER_REQUEST) -> Estimate:
    """ A rough token and cost estimate for drafting the pending pairs, before spending anything. """
    pending = pending_units(store, unit_ids)
    instructions = len(build_instructions(store)) / CHARACTERS_PER_TOKEN
    requests = input_tokens = cache_read = output_tokens = pairs_total = 0
    for app, pairs in pending:
        pairs_total += len(pairs)
        for start in range(0, len(pairs), max_pairs_per_request):
            chunk = pairs[start:start + max_pairs_per_request]
            prompt = len(unit_prompt(store, app, chunk)) / CHARACTERS_PER_TOKEN
            # two model turns per request (a tool call, then the answer); the instructions come from the cache
            requests += 1
            input_tokens += int(2 * (instructions + prompt) + TOOL_TOKENS_PER_REQUEST)
            cache_read += int(2 * instructions)
            output_tokens += OUTPUT_TOKENS_PER_REQUEST + OUTPUT_TOKENS_PER_PAIR * len(chunk)
    usage = dict(input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cache_read, cache_write_tokens=0)
    return Estimate(
        model=model_name(model), units=len(pending), pairs=pairs_total, requests=requests,
        input_tokens=input_tokens, cache_read_tokens=cache_read, output_tokens=output_tokens,
        cost=price(model, usage) if pending else 0.0,
    )


# ---------------------------------------------------------------------------
# category review and the revision loop
# ---------------------------------------------------------------------------

class Revision(BaseModel):
    unit_id: str
    active: str = Field(description="As in the key unit:active>passive of a listed label.")
    passive: str
    category: str = Field(description="The category the label should have instead.")
    reason: str = Field(description="Why, citing the definitions.")
    evidence: list[str] = Field(default_factory=list, description="Keys (unit:active>passive) of precedents supporting the revision. Never the pair itself or its inverse.")
    confidence: Literal["high", "medium", "low"] = "medium"


class CategoryReview(BaseModel):
    revisions: list[Revision] = Field(default_factory=list, description="Only the labels that should change. Leave out labels that are right.")
    notes: str = Field(default="", description="Borderline cases or inconsistencies worth an annotator's attention.")


class RevisionLogEntry(BaseModel):
    round: int
    run_id: str
    model: str
    unit_id: str
    active: str
    passive: str
    old: str
    new: str
    reason: str
    evidence: list[str]
    status: str
    when: str


class ReviewReport(BaseModel):
    run_id: str
    model: str
    rounds_run: int = 0
    revisions: list[RevisionLogEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    usage: dict = Field(default_factory=lambda: dict(requests=0, input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0))
    cost: float | None = None


@dataclass
class ReviewDeps:
    category: str
    members: dict[str, tuple[str, str, str]]  # key -> (unit_id, active, passive)
    categories: set[str]


def pair_key(unit_id:str, active:str, passive:str) -> str:
    return f"{unit_id}:{active}>{passive}"


def category_members(store:AnnotationStore, category:str, unit_ids:list[str]|None=None) -> list:
    """
    The pipeline's labels with this category, as (unit_id, pair) in the direction labelled with it, each unordered
    pair once (in display order for categories that are their own inverse). Human and rule labels are never members.
    """
    store.refresh()
    apps = [store.get_app(unit_id) for unit_id in unit_ids] if unit_ids else store.doc.apps
    members = []
    for app in apps:
        if store.hides(app):
            continue
        seen = set()
        for pair in store.ordered_pairs(app):
            for directed in (pair, pair.get_inverse()):
                key = frozenset((directed.active.n, directed.passive.n))
                if key in seen or store.label_source(directed) != "pipeline" or store.current_category(directed) != category:
                    continue
                seen.add(key)
                members.append((str(app), directed))
    return members


def human_examples(store:AnnotationStore, category:str, limit:int=12) -> list:
    examples = []
    for app in store.doc.apps:
        if store.hides(app):
            continue
        for pair in store.ordered_pairs(app):
            for directed in (pair, pair.get_inverse()):
                if store.label_source(directed) == "human" and store.current_category(directed) == category:
                    examples.append((str(app), directed))
                    break
            if len(examples) >= limit:
                return examples
    return examples


def describe_pair(store:AnnotationStore, unit_id:str, pair) -> str:
    change = describe_change(pair.active.text, pair.passive.text).text
    return f"{pair_key(unit_id, pair.active.n, pair.passive.n)}: {pair.active} → {pair.passive} ({change})"


def review_prompt(store:AnnotationStore, category:str, members:list) -> str:
    definitions = {item.name: item for item in store.definitions().categories}
    lines = [
        f"Category under review: {category}",
        f"Definition: {definitions[category].description}",
        f"Inverse: {definitions[category].inverse}",
        "",
        "Review these labels made by the pipeline (tentative) together, for consistency with the definitions and with each "
        "other. Propose a revision only for a label that is wrong, giving the right category, the reason and the keys of "
        "precedents that support it (search_similar finds them). A label is never evidence for itself or its inverse.",
        "",
        "Human examples of this category (certain):",
    ]
    examples = human_examples(store, category)
    lines += [f"- {describe_pair(store, unit_id, pair)}" for unit_id, pair in examples] or ["(none yet)"]
    lines += ["", "Labels to review (unit:first>second: first reading → second reading):"]
    for unit_id, pair in members:
        app = store.get_app(unit_id)
        justification = store.provenance_from_relation(pair.relation_element()).justification if pair.relation_element() is not None else ""
        lines.append(f"- {describe_pair(store, unit_id, pair)} — verse: {app.text_in_context()} — reason given: {justification or '(none)'}")
    return "\n".join(lines)


def review_agent(store:AnnotationStore, model, toolsets, effort:str|None=None):
    from pydantic_ai import Agent, ModelRetry, RunContext

    agent = Agent(
        model,
        output_type=CategoryReview,
        instructions=build_instructions(store),
        deps_type=ReviewDeps,
        toolsets=toolsets,
        model_settings=model_settings_for(model, effort),
        retries=3,
    )

    @agent.output_validator
    def check(context:RunContext[ReviewDeps], output:CategoryReview) -> CategoryReview:
        problems = []
        for item in output.revisions:
            key = pair_key(item.unit_id, item.active, item.passive)
            if key not in context.deps.members:
                problems.append(f"{key} is not one of the labels to review (only the listed pipeline labels can be revised).")
                continue
            if item.category not in context.deps.categories:
                problems.append(f"{key}: unknown category {item.category}.")
            elif item.category == context.deps.category:
                problems.append(f"{key}: already {item.category}; leave out labels that are right.")
            own = {key, pair_key(item.unit_id, item.passive, item.active)}
            if own & set(item.evidence):
                problems.append(f"{key}: a label cannot be evidence for itself or its inverse; cite other precedents.")
        if problems:
            raise ModelRetry(" ".join(problems))
        return output

    return agent


async def review_async(
    store:AnnotationStore,
    model=DEFAULT_MODEL,
    toolsets:list|None=None,
    categories:list[str]|None=None,
    rounds:int=3,
    unit_ids:list[str]|None=None,
    max_pairs_per_request:int=MAX_PAIRS_PER_REQUEST,
    effort:str|None=None,
    log_path=None,
    progress=None,
) -> ReviewReport:
    """
    Reviews the pipeline's labels one category at a time and applies the revisions, for up to `rounds` rounds
    (stopping when a round changes nothing). Human labels are never revised; a revision that would undo an earlier
    one in this run is skipped; every revision is logged (JSON lines, default <file>.revisions.jsonl).
    """
    import json
    from pathlib import Path
    from .annotation import ConflictError

    toolsets = default_toolsets(store) if toolsets is None else toolsets
    log_path = Path(log_path) if log_path else store.path.with_name(f"{store.path.stem}.revisions.jsonl")
    run_id = store.start_run(model=model_name(model), description="Category review.")
    report = ReviewReport(run_id=run_id, model=model_name(model))
    all_categories = [item.name for item in store.definitions().categories]
    categories = categories or all_categories
    history = {}  # (unit_id, frozenset) -> categories this pair has had in this run (in its member direction)
    agent = review_agent(store, model, toolsets, effort)

    def log(entry:RevisionLogEntry):
        report.revisions.append(entry)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")

    async with agent:
        for round_number in range(1, max(rounds, 0) + 1):
            report.rounds_run = round_number
            applied = 0
            for category in categories:
                members = category_members(store, category, unit_ids)
                for start in range(0, len(members), max(max_pairs_per_request, 1)):
                    chunk = members[start:start + max_pairs_per_request]
                    keys = {pair_key(unit_id, pair.active.n, pair.passive.n): (unit_id, pair.active.n, pair.passive.n) for unit_id, pair in chunk}
                    with store.lock:
                        prompt = review_prompt(store, category, chunk)
                    try:
                        result = await agent.run(prompt, deps=ReviewDeps(category=category, members=keys,
                                                                         categories=set(all_categories) - reserved_categories(store)))
                    except Exception as error:
                        report.errors.append(f"{category}: {type(error).__name__}: {error}")
                        continue
                    usage = result.usage
                    for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
                        report.usage[key] += getattr(usage, key, 0) or 0
                    if result.output.notes:
                        report.notes.append(f"{category}: {result.output.notes}")
                    for item in result.output.revisions:
                        pair_id = (item.unit_id, frozenset((item.active, item.passive)))
                        earlier = history.setdefault(pair_id, [category])
                        status = "applied"
                        if item.category in earlier:
                            status = "skipped: would undo an earlier revision"
                        else:
                            try:
                                await anyio.to_thread.run_sync(lambda: store.set_classification(
                                    item.unit_id, item.active, item.passive, item.category, justification=item.reason,
                                    cert=item.confidence, resp="rdgai", change=run_id, expected_category=category,
                                ))
                                earlier.append(item.category)
                                applied += 1
                            except ConflictError:
                                status = "skipped: changed meanwhile"
                            except AnnotationError as error:
                                status = f"skipped: {error}"
                        log(RevisionLogEntry(
                            round=round_number, run_id=run_id, model=model_name(model), unit_id=item.unit_id,
                            active=item.active, passive=item.passive, old=category, new=item.category, reason=item.reason,
                            evidence=item.evidence, status=status, when=store.now(),
                        ))
                if progress:
                    progress(round_number, category)
            if applied == 0:
                break
    report.cost = price(model, report.usage)
    return report


def review(store:AnnotationStore, model=DEFAULT_MODEL, **kwargs) -> ReviewReport:
    return anyio.run(lambda: review_async(store, model, **kwargs))
