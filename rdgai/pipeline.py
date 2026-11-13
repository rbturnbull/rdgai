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

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

DEFAULT_MODEL = "anthropic:claude-opus-5-5"
AGENT_TOOLS = ("get_unit", "search_similar", "list_category")
MAX_PAIRS_PER_REQUEST = 40


# ---------------------------------------------------------------------------
# structured output
# ---------------------------------------------------------------------------

class PairDecision(BaseModel):
    active: str = Field(description="Id of the first (active) reading, exactly as listed.")
    passive: str = Field(description="Id of the second (passive) reading, exactly as listed.")
    category: str = Field(description="The category for the change from the first reading to the second.")
    justification: str = Field(description="One or two sentences: why this category, citing the definitions or precedents.")
    confidence: Literal["high", "medium", "low"]


class UnitDecisions(BaseModel):
    decisions: list[PairDecision] = Field(description="One decision for every pair listed under 'Pairs to classify'.")


@dataclass
class DraftDeps:
    requested: list[tuple[str, str]]
    categories: set[str]


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

def build_instructions(store:AnnotationStore) -> str:
    """ The instructions for every request. They depend only on the definitions, so the prompt cache is reused. """
    definitions = store.definitions()
    lines = [
        f"You classify transcriptional changes between variant readings in a critical apparatus of a text in {definitions.language}.",
        "",
        "For each pair of readings you are given, choose the category that describes the change from the FIRST (active) reading "
        "to the SECOND (passive) reading. " + DIRECTION_CONVENTION,
        "",
        "Categories (name — inverse — definition):",
    ]
    for category in definitions.categories:
        lines.append(f"- {category.name} — inverse {category.inverse} — {category.description}")
    if definitions.guidance:
        lines += ["", "Guidance from the editors:", definitions.guidance]
    lines += [
        "",
        "Tools: use search_similar to find labelled pairs with a similar change and read how they were classified; "
        "list_category shows the pairs labelled with a category; get_unit shows a unit in full. "
        "Human labels are certain. Pipeline labels are tentative: count them for much less, and never treat a tentative "
        "label as settling a question on its own. Classify from the readings, the given verse and the definitions; "
        "use precedents to apply the definitions consistently.",
        "",
        "Answer with one decision for every pair listed under 'Pairs to classify', in the direction given "
        "(first reading → second reading), using exactly one of the category names above. Give a short justification "
        "(one or two sentences) and your confidence: high, medium or low.",
    ]
    return "\n".join(lines)


def unit_prompt(store:AnnotationStore, app, pairs) -> str:
    """ The request for one unit: its verse, readings, labels already made, and the pairs to classify. """
    unit_id = str(app)
    lines = [f"Variation unit {unit_id} ({verse_label(unit_id)})", f"Verse: {app.text_in_context()}", "", "Readings:"]
    for reading in app.readings:
        if store.is_excluded(reading):
            continue
        note = f" [notes: {'; '.join(reading.notes)}]" if reading.notes else ""
        lines.append(f"- {reading.n}: {reading} ({len(reading.witnesses)} witnesses){note}")

    labelled = []
    for pair in store.ordered_pairs(app):
        if pair.relation_element() is None or store.hides(app):
            continue
        source = store.label_source(pair)
        status = {"human": "human, certain", "pipeline": "pipeline, tentative", "rule": "rule"}.get(source, source)
        labelled.append(f"- {pair.active.n} → {pair.passive.n}: {store.current_category(pair)} ({status})")
    if labelled:
        lines += ["", "Labels already in this unit:", *labelled]

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


def draft_agent(store:AnnotationStore, model, toolsets, effort:str|None=None):
    from pydantic_ai import Agent, ModelRetry, RunContext

    agent = Agent(
        model,
        output_type=UnitDecisions,
        instructions=build_instructions(store),
        deps_type=DraftDeps,
        toolsets=toolsets,
        model_settings=model_settings_for(model, effort),
        retries=3,
    )

    @agent.output_validator
    def check(context:RunContext[DraftDeps], output:UnitDecisions) -> UnitDecisions:
        requested = context.deps.requested
        given = {(decision.active, decision.passive) for decision in output.decisions}
        problems = []
        missing = [pair for pair in requested if pair not in given]
        if missing:
            problems.append("Missing pairs: " + ", ".join(f"{a} → {b}" for a, b in missing) + ".")
        extra = sorted(given - set(requested))
        if extra:
            problems.append("Not asked about (check the direction): " + ", ".join(f"{a} → {b}" for a, b in extra) + ".")
        unknown = sorted({decision.category for decision in output.decisions} - context.deps.categories)
        if unknown:
            problems.append("Unknown categories: " + ", ".join(unknown) + ". Use exactly one of: " + ", ".join(sorted(context.deps.categories)) + ".")
        if problems:
            raise ModelRetry(" ".join(problems) + " Answer again with one decision for every pair listed.")
        return output

    return agent


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
) -> DraftReport:
    toolsets = default_toolsets(store) if toolsets is None else toolsets
    pending = pending_units(store, unit_ids)
    if limit_units is not None:
        pending = pending[:limit_units]
    run_id = store.start_run(model=model_name(model), description="Draft pass.")
    report = DraftReport(run_id=run_id, model=model_name(model))
    if not pending:
        return report

    agent = draft_agent(store, model, toolsets, effort)
    categories = {category.name for category in store.definitions().categories}
    limiter = anyio.Semaphore(max(concurrency, 1))

    async def classify(app, pairs):
        async with limiter:
            unit_done = False
            for start in range(0, len(pairs), max(max_pairs_per_request, 1)):
                chunk = pairs[start:start + max_pairs_per_request]
                deps = DraftDeps(requested=[(pair.active.n, pair.passive.n) for pair in chunk], categories=categories)
                with store.lock:  # other units may be writing to the document in worker threads
                    prompt = unit_prompt(store, app, chunk)
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
                        result = await agent.run(prompt, deps=ReviewDeps(category=category, members=keys, categories=set(all_categories)))
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
