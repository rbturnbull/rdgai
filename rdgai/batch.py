"""
The draft pass through Anthropic's Message Batches API: the same prompts and checks as the interactive pass, at half
the price, with results within hours instead of seconds.

Every request (a unit, or a chunk of a large unit) goes into one batch. The model answers with a forced tool call
whose input is a ``UnitDecisions``. Each answer is checked with ``review_answer``, exactly as in the interactive pass.
A request that needs another try (a missing pair, an unknown category, a broken declared constraint) goes into the next
batch with the conversation so far, the problem given as the tool result. After ``max_rounds`` batches, requests still
without an accepted answer are reported as errors; an answer that only awaited reconsideration of a constraint stands,
as it would interactively. The agent's MCP tools are not available in a batch: precedents are already in the prompt.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .annotation import AnnotationStore, AnnotationError
from .pipeline import (
    DraftDeps, DraftReport, UnitDecisions, Uncertain, MAX_PAIRS_PER_REQUEST, PRECEDENTS_PER_PAIR,
    build_instructions, unit_prompt, review_answer, reserved_categories, pending_units, model_name, price,
)
from .tei import get_language_code

BATCH_DISCOUNT = 0.5        # Message Batches cost half the standard price
ANSWER_TOOL = "final_result"


class BatchError(AnnotationError):
    pass


def inline_schema(schema:dict) -> dict:
    """ A JSON schema with its $defs references written out in place (no $ref, no $defs). """
    definitions = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = definitions[node["$ref"].split("/")[-1]]
                merged = {**resolve(target), **{key: resolve(value) for key, value in node.items() if key != "$ref"}}
                return merged
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def anthropic_model(model) -> str:
    name = model_name(model)
    if not name.startswith("anthropic:"):
        raise BatchError(f"Batches need an Anthropic model (anthropic:…), not {name}.")
    return name.split(":", 1)[1]


ANSWER_REMINDER = f"Answer by calling the tool {ANSWER_TOOL}, with one decision for every pair listed under 'Pairs to classify'."


def can_force_tool(name:str) -> bool:
    """ Whether the model accepts a forced tool choice (Sonnet and Opus 5.5 do not); from pydantic-ai's model profile. """
    try:
        from pydantic_ai.profiles.anthropic import anthropic_model_profile
        profile = anthropic_model_profile(name)
    except Exception:
        return True
    if profile is None:
        return True
    value = profile.get("supports_forced_tool_choice", True) if isinstance(profile, dict) else getattr(profile, "supports_forced_tool_choice", True)
    return bool(value)


@dataclass
class Job:
    app: object
    chunk: list
    deps: DraftDeps
    messages: list
    error: str = ""
    fallback: UnitDecisions | None = None      # an answer that only awaited reconsideration of a constraint
    done: bool = False


def error_message(result) -> str:
    error = getattr(result, "error", None)
    inner = getattr(error, "error", None)
    return getattr(inner, "message", None) or getattr(error, "message", None) or getattr(result, "type", "failed")


def add_message_usage(usage:dict, message):
    counts = getattr(message, "usage", None)
    read = getattr(counts, "cache_read_input_tokens", 0) or 0
    written = getattr(counts, "cache_creation_input_tokens", 0) or 0
    usage["requests"] += 1
    # Anthropic reports cached input separately; pydantic-ai (and the CV reports) count it in input_tokens
    usage["input_tokens"] += (getattr(counts, "input_tokens", 0) or 0) + read + written
    usage["output_tokens"] += getattr(counts, "output_tokens", 0) or 0
    usage["cache_read_tokens"] += read
    usage["cache_write_tokens"] += written


RETRYABLE_NAMES = {"APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError", "OverloadedError",
                   "ServiceUnavailableError"}


def retryable(error:Exception) -> bool:
    """ A dropped connection, a timeout, an overloaded or failing server: worth trying again. """
    status = getattr(error, "status_code", None)
    return (isinstance(error, (ConnectionError, TimeoutError)) or type(error).__name__ in RETRYABLE_NAMES
            or (isinstance(status, int) and (status == 429 or status >= 500)))


def with_retries(call, retry_wait:float, max_retries:int, progress=None):
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception as error:
            if not retryable(error) or attempt == max_retries:
                raise
            if progress:
                progress(f"  {type(error).__name__}: {error}; trying again in {retry_wait:.0f}s")
            time.sleep(retry_wait * min(2 ** attempt, 10))


def save_state(path, state:dict):
    """ Writes the state atomically (a temporary file, then a rename), so an interruption never leaves half a file. """
    import json
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def draft_batch(
    store:AnnotationStore,
    model,
    unit_ids:list[str]|None=None,
    limit_units:int|None=None,
    max_pairs_per_request:int=MAX_PAIRS_PER_REQUEST,
    precedents:int=PRECEDENTS_PER_PAIR,
    reserve_rule_categories:bool=True,
    structure_checks:bool=True,
    client=None,
    poll_interval:float=60.0,
    max_rounds:int=3,
    max_tokens:int=16000,
    progress=None,
    state_path=None,
    retry_wait:float=30.0,
    max_retries:int=20,
) -> DraftReport:
    """
    The draft pass as Message Batches (see the module docstring). `client` defaults to anthropic.Anthropic().

    With `state_path`, the requests, the batch being waited on and the usage so far are saved there after every step. If
    the program stops (the computer turned off, the connection lost), running it again with the same state file
    collects the batch already submitted instead of paying for a new one; the file is removed when the pass ends.
    Network errors while submitting or waiting are retried (`retry_wait` seconds, doubling, at most `max_retries` times).
    """
    import json
    name = anthropic_model(model)
    state_path = Path(state_path) if state_path else None
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path and state_path.exists() else None
    if state and state.get("model") != model_name(model):
        raise BatchError(f"The batch state file {state_path} belongs to a run with {state.get('model')}, not {model_name(model)}: "
                         f"rerun with that model, or delete the state file to start afresh.")
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    retry = lambda call: with_retries(call, retry_wait, max_retries, progress)

    definitions = store.definitions()
    categories = {category.name for category in definitions.categories}
    constraints = definitions.constraints if structure_checks else {}
    language = get_language_code(store.doc.tree)
    reserved = reserved_categories(store) if reserve_rule_categories else set()
    system = [{"type": "text", "text": build_instructions(store, reserve=reserve_rule_categories), "cache_control": {"type": "ephemeral"}}]
    tool = {"name": ANSWER_TOOL, "description": "Your answer: the analysis, then one decision for every pair listed under 'Pairs to classify'.",
            "input_schema": inline_schema(UnitDecisions.model_json_schema())}
    forced = can_force_tool(name)
    tool_choice = {"type": "tool", "name": ANSWER_TOOL} if forced else {"type": "auto"}

    def new_deps(app, chunk) -> DraftDeps:
        return DraftDeps(requested=[(pair.active.n, pair.passive.n) for pair in chunk], categories=categories, reserved=reserved,
                         texts={(pair.active.n, pair.passive.n): (pair.active.text, pair.passive.text) for pair in chunk} if structure_checks else {},
                         constraints=constraints, language=language, unit_id=str(app))

    jobs = {}
    if state:
        run_id = state["run_id"]
        report = DraftReport(run_id=run_id, model=model_name(model), batch=True, usage=state["usage"], batch_ids=state["batch_ids"])
        for custom_id, saved in state["jobs"].items():
            app = store.get_app(saved["unit_id"])
            wanted = [tuple(pair) for pair in saved["requested"]]
            pairs = {(pair.active.n, pair.passive.n): pair for pair in store.ordered_pairs(app)}
            chunk = [pairs[key] if key in pairs else store.get_pair(app, *key) for key in wanted]
            deps = new_deps(app, chunk)
            deps.reconsidered = saved["reconsidered"]
            jobs[custom_id] = Job(app=app, chunk=chunk, deps=deps, messages=saved["messages"], error=saved.get("error", ""),
                                  fallback=UnitDecisions.model_validate(saved["fallback"]) if saved.get("fallback") else None)
        open_jobs, round_number, waiting = state["open"], state["round"], state.get("batch_id")
        if progress:
            progress(f"Resuming run {run_id}: round {round_number}, {len(open_jobs)} requests" + (f", batch {waiting}" if waiting else ""))
    else:
        pending = pending_units(store, unit_ids)
        if limit_units is not None:
            pending = pending[:limit_units]
        run_id = store.start_run(model=model_name(model), description="Draft pass (Message Batches).")
        report = DraftReport(run_id=run_id, model=model_name(model), batch=True)
        if not pending:
            report.cost = 0.0
            return report
        for app, pairs in pending:
            for start in range(0, len(pairs), max(max_pairs_per_request, 1)):
                chunk = pairs[start:start + max_pairs_per_request]
                prompt = unit_prompt(store, app, chunk, precedents=precedents)
                if not forced:
                    prompt += "\n\n" + ANSWER_REMINDER
                jobs[f"r{len(jobs) + 1:06d}"] = Job(app=app, chunk=chunk, deps=new_deps(app, chunk), messages=[{"role": "user", "content": prompt}])
        open_jobs, round_number, waiting = sorted(jobs), 1, None

    def remember(batch_id):
        if not state_path:
            return
        save_state(state_path, dict(
            model=model_name(model), run_id=run_id, round=round_number, batch_id=batch_id, open=open_jobs,
            usage=report.usage, batch_ids=report.batch_ids,
            jobs={custom_id: dict(unit_id=str(job.app), requested=[list(pair) for pair in job.deps.requested], messages=job.messages,
                                  reconsidered=job.deps.reconsidered, error=job.error,
                                  fallback=job.fallback.model_dump() if job.fallback else None)
                  for custom_id, job in jobs.items() if custom_id in open_jobs},
        ))

    units_written = set()

    def write(job:Job, output:UnitDecisions):
        decisions = [(d.active, d.passive, d.category, d.justification, d.confidence) for d in output.decisions]
        try:
            written = store.write_decisions(str(job.app), decisions, resp="rdgai", change=run_id)
        except AnnotationError as error:
            report.errors.append(f"{job.app}: {error}")
            return
        report.pairs_written += len(written.written)
        report.pairs_skipped += len(written.skipped)
        if written.written:
            units_written.add(str(job.app))
        kept = set(written.written)
        report.uncertain += [
            Uncertain(unit_id=str(job.app), active=d.active, passive=d.passive, category=d.category, alternative=d.alternative,
                      confidence=d.confidence, justification=d.justification)
            for d in output.decisions if d.alternative and (d.active, d.passive) in kept
        ]
        job.done = True

    remember(waiting)
    while open_jobs and round_number <= max(max_rounds, 1):
        if waiting:
            batch_id, waiting = waiting, None
        else:
            requests = [{"custom_id": custom_id, "params": {
                "model": name, "max_tokens": max_tokens, "system": system, "tools": [tool],
                "tool_choice": tool_choice, "messages": jobs[custom_id].messages,
            }} for custom_id in open_jobs]
            batch_id = retry(lambda: client.messages.batches.create(requests=requests)).id
            report.batch_ids.append(batch_id)
            remember(batch_id)
            if progress:
                progress(f"Batch {batch_id}: {len(requests)} requests (round {round_number})")
        while retry(lambda: client.messages.batches.retrieve(batch_id)).processing_status != "ended":
            time.sleep(poll_interval)
        results = {result.custom_id: result for result in retry(lambda: list(client.messages.batches.results(batch_id)))}
        still_open = []
        for custom_id in open_jobs:
            job = jobs[custom_id]
            result = results.get(custom_id)
            if result is None or result.result.type != "succeeded":
                job.error = error_message(result.result) if result is not None else "no result"
                still_open.append(custom_id)
                continue
            message = result.result.message
            add_message_usage(report.usage, message)
            call = next((block for block in message.content if getattr(block, "type", "") == "tool_use" and block.name == ANSWER_TOOL), None)
            if call is None:
                job.error = "the model did not answer with the tool"
                said = [{"type": "text", "text": block.text} for block in message.content if getattr(block, "type", "") == "text" and block.text]
                if said:
                    job.messages = job.messages + [{"role": "assistant", "content": said}, {"role": "user", "content": ANSWER_REMINDER}]
                still_open.append(custom_id)
                continue
            try:
                output = UnitDecisions.model_validate(call.input)
                was_reconsidered = job.deps.reconsidered
                problem = review_answer(job.deps, output)
                if problem and job.deps.reconsidered and not was_reconsidered:
                    job.fallback = output           # only a constraint to reconsider: this answer stands if no better one comes
            except ValidationError as error:
                problem = f"The answer does not match the required format: {error}. Answer again with one decision for every pair listed."
            if problem:
                job.error = problem
                job.messages = job.messages + [
                    {"role": "assistant", "content": [{"type": "tool_use", "id": call.id, "name": ANSWER_TOOL, "input": call.input}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "content": problem, "is_error": True}]},
                ]
                still_open.append(custom_id)
                continue
            write(job, output)
        open_jobs = still_open
        round_number += 1
        remember(None)

    for custom_id in open_jobs:
        job = jobs[custom_id]
        if job.fallback is not None:
            write(job, job.fallback)
        else:
            report.errors.append(f"{job.app}: {job.error}")
    if state_path and state_path.exists():
        state_path.unlink()
    report.units_done = len(units_written)
    full = price(model, report.usage)
    report.cost = full * BATCH_DISCOUNT if full is not None else None
    return report
