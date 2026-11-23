from pathlib import Path
import typer
from rich.console import Console
import pandas as pd

from .apparatus import Doc
from .export import export_variants_to_excel, import_classifications_from_dataframe

console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer(pretty_exceptions_enable=False)
    


def get_output_path(doc:Path, output:Path, inplace:bool) -> Path:
    """ Checks if the output path should be replaced with the input doc. """
    if output and inplace:
       raise typer.BadParameter("You cannot use both an output path and --inplace/-i at the same time.")
    if not output and not inplace:
        raise typer.BadParameter("You must provide either an output path or use --inplace/-i.")
    
    if inplace:
        output = doc

    if isinstance(output, Doc):
        output = output.path

    assert isinstance(output, Path), f"Expected Path, got {type(output)}"
    
    return output


@app.command()
def classified_pairs(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document with the classifications."),
):
    """ Print classified pairs in a document. """
    doc = Doc(doc)
    doc.print_classified_pairs(console)


@app.command()
def evaluate(
    doc:Path=typer.Argument(..., help="The TEI XML document with human labels and a sample of example and test units. It is not modified."),
    model:str=typer.Option("anthropic:claude-opus-5-5", help="The model, as a pydantic-ai model name."),
    output_dir:Path=typer.Option(..., help="Where to write the working copy, the revision log and report.html / report.json."),
    rounds:int=typer.Option(3, help="Rounds of category review after the draft (0: draft only)."),
    concurrency:int=typer.Option(4, help="Units drafted at the same time."),
    effort:str=typer.Option(None, help="Effort level for Anthropic models."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable)."),
    fold:int=typer.Option(None, help="Cross-validation: evaluate this fold (1-based) of the example units instead of the test units. The test units are hidden entirely."),
    folds:int=typer.Option(5, help="Cross-validation: number of folds."),
    seed:int=typer.Option(0, help="Cross-validation: seed for splitting the example units into folds."),
    folds_file:Path=typer.Option(None, help="Cross-validation: where the folds are saved and reused (default: <output-dir>/../folds.yaml)."),
    expert_model:str=typer.Option(None, help="A stronger model for the pairs the draft model is unsure about (e.g. anthropic:claude-opus-5-5)."),
    batch:bool=typer.Option(False, help="Draft through Anthropic Message Batches (half price; results within hours)."),
    poll_interval:float=typer.Option(60.0, help="With --batch: seconds between checks on the batch."),
    expert_mode:str=typer.Option("after", help="'after': escalate the uncertain pairs after the draft; 'tool': let the draft model ask the expert while it works."),
    fold_pool:str=typer.Option("example", help="Cross-validation: 'example' (folds over the example units; the test units are hidden) or 'all' (folds over every sample unit, stratified by size)."),
):
    """
    Evaluates the pipeline on the sample's test units: runs it on a copy with the test units' labels removed, then
    compares the draft and the reviewed predictions with the human labels (per-category precision, recall, F1 and
    the confusion matrix).
    """
    from .annotation import AnnotationError
    from .evaluate import evaluate as evaluate_fn
    try:
        report = evaluate_fn(doc, model, output_dir, rounds=rounds, concurrency=concurrency, effort=effort, ignore_types=ignore_types,
                             fold=fold, folds=folds, seed=seed, folds_path=folds_file, pool=fold_pool, expert=expert_model, expert_mode=expert_mode, batch=batch, poll_interval=poll_interval)
    except AnnotationError as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)
    if report.fold is not None:
        console.print(f"Fold {report.fold} of {report.folds} (seed {report.seed}): {report.test_units} held-out units, {len(report.hidden_units)} hidden test units.")
    if report.cost is not None:
        console.print(f"Cost: ${report.cost:.2f}")
    if report.escalation:
        escalation = report.escalation
        console.print(f"Expert ({escalation['model']}, {escalation['mode']}): {escalation.get('flagged', 0)} uncertain pairs, "
                      f"{escalation.get('changed', escalation.get('calls', 0))} {'changed' if escalation['mode'] == 'after' else 'questions'}, "
                      f"${(escalation.get('cost') or 0):.2f}")
    console.print(f"Draft: {report.draft.correct}/{report.draft.n} correct ({report.draft.accuracy:.1%}; per unit {report.draft.unit_accuracy:.1%}), macro F1 {report.draft.macro_f1:.1%}")
    if report.final:
        stage = "After escalation" if report.escalation.get("mode") == "after" else "After review"
        console.print(f"{stage}: {report.final.correct}/{report.final.n} correct ({report.final.accuracy:.1%}), macro F1 {report.final.macro_f1:.1%}")
    for note in report.notes:
        console.print(note)
    console.print(f"Report: {Path(output_dir) / 'report.html'}")


@app.command()
def clean(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to clean."),
    output:Path=typer.Argument(None, help="The path to the output TEI XML file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
):
    """ Cleans a TEI XML file for common errors. """
    output = get_output_path(doc, output, inplace)
    doc = Doc(doc)
    doc.clean(output=output)


@app.command()
def export(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to export."),
    output:Path=typer.Argument(..., help="The path to the output Excel file."),
):
    """ Exports pairs of readings with classifications from a TEI document to an Excel spreadsheet. """
    doc = Doc(doc)
    export_variants_to_excel(doc, output)


@app.command()
def import_classifications(
    doc:Path=typer.Argument(..., help="The path to the base TEI XML document to use for importing the classifications from Excel."),
    spreadsheet:Path=typer.Argument(..., help="The path to the Excel file to import."),
    output:Path=typer.Argument(None, help="The path to the output TEI XML file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
    responsible:str=typer.Option("", help="The responsible party for the classifications. By default it is the name of the spreadsheet."),
):
    """ Imports classifications from a spreadsheet into a TEI document. """
    doc = Doc(doc)
    output = get_output_path(doc, output, inplace)

    if spreadsheet.suffix == ".xlsx":
        variants_df = pd.read_excel(spreadsheet, sheet_name="Variants", keep_default_na=False)
    elif spreadsheet.suffix == ".csv":
        variants_df = pd.read_csv(spreadsheet, keep_default_na=False)

    # TODO add responsible to TEI header
    responsible = responsible or spreadsheet.stem
    responsible = responsible.replace(" ", "_")
    if not responsible.startswith("#"):
        responsible = "#" + responsible
    
    import_classifications_from_dataframe(doc, variants_df, output, responsible=responsible)


@app.command()
def serve_mcp(
    doc:Path=typer.Argument(..., help="The TEI XML document. It is modified in place when classifications are set."),
    transport:str=typer.Option("stdio", help="Transport: 'stdio' or 'http' (streamable HTTP)."),
    host:str=typer.Option("127.0.0.1", help="Host for the HTTP transport."),
    port:int=typer.Option(8000, help="Port for the HTTP transport."),
    annotator:str=typer.Option(None, help="xml:id of the human annotator responsible for classifications set through this server, e.g. 'RT'."),
    annotator_name:str=typer.Option(None, help="Full name of the annotator (used in the <respStmt> added to the TEI header)."),
    read_only:bool=typer.Option(False, help="Disable all tools that change the file."),
    lock_definitions:bool=typer.Option(False, help="Disable the tools that edit the category definitions."),
    evaluation:bool=typer.Option(False, help="Evaluation mode: no tool reveals the labels or suggestions of the sample's test units, test units cannot be changed, and the definitions are locked."),
    reading_notes:bool=typer.Option(True, help="Treat text in parentheses in readings (e.g. ECM's '(cf. 24b)') as notes, not reading text."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample of units (default: <doc>.sample.yaml)."),
    gui_port:int=typer.Option(None, help="Also serve the annotation GUI on this port, in the same process (e.g. 8765)."),
    gui_host:str=typer.Option("127.0.0.1", help="Host for the annotation GUI."),
    gui_retry_seconds:float=typer.Option(15.0, help="If the GUI port is taken (e.g. by a second process for the same file), try it again this often, so the GUI survives either process exiting (0: never)."),
    group_by_prefix:str=typer.Option(None, help="Lay out the GUI's category buttons in rows by the part of the name before this separator, e.g. '_'."),
    compact_threshold:int=typer.Option(10, help="In the GUI, show units with more pairs than this as compact rows."),
):
    """ Serves an MCP server for querying and classifying the reading pairs in a TEI document. """
    from .annotation import AnnotationStore, AnnotationError
    from .mcp_server import build_server

    if not read_only and not annotator:
        raise typer.BadParameter("Provide --annotator (the xml:id of the person responsible) or use --read-only.")

    try:
        store = AnnotationStore(
            doc, annotator=annotator, annotator_name=annotator_name, read_only=read_only, lock_definitions=lock_definitions,
            ignore_types=ignore_types, sample_path=sample_file, reading_notes=reading_notes, evaluation=evaluation,
        )
    except AnnotationError as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)
    if transport not in ("stdio", "http"):
        raise typer.BadParameter("transport must be 'stdio' or 'http'.")
    server = build_server(store)
    if gui_port:
        import anyio
        from .gui import create_app
        from .gui.serve import serve_mcp_with_gui
        gui_app = create_app(store, group_by_prefix=group_by_prefix, compact_threshold=compact_threshold)
        anyio.run(lambda: serve_mcp_with_gui(
            server, gui_app, gui_host, gui_port, transport=transport, host=host, port=port, retry_seconds=gui_retry_seconds,
        ))
    elif transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)


@app.command()
def apply_rules(
    doc:Path=typer.Argument(..., help="The TEI XML document."),
    rule:list[str]=typer.Option(None, "--rule", help="Reading type and category, e.g. orthographic=Orthography (repeatable). Replaces the rules stored in the file."),
    suffix:list[str]=typer.Option(None, "--suffix", help="Reading type and id suffix, e.g. orthographic=o (repeatable). Defaults: orthographic o, defective f."),
    dry_run:bool=typer.Option(False, "--dry-run", help="Report what would change without saving."),
    inplace:bool=typer.Option(False, "--inplace", "-i", help="Modify the document in place."),
    output:Path=typer.Option(None, "--output", "-o", help="Write the result to this file instead."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    reading_notes:bool=typer.Option(True, help="Treat text in parentheses in readings as notes, not reading text."),
):
    """
    Labels the pairs formed by typed subreadings (e.g. ECM orthographic readings 'ao', 'ao1' of reading 'a') by rule:
    a subreading and its parent, and two subreadings of the same parent, get the mapped category. Other pairs with a
    subreading (e.g. ao-b) are ordinary pairs, for an annotator or the pipeline; rule labels left on them (from the
    former rule 3) are removed. Human labels are never changed. Running it again changes nothing.
    """
    import shutil
    from .annotation import AnnotationStore, AnnotationError
    from .rules import build_rules, parse_assignments

    if not dry_run:
        if inplace and output:
            raise typer.BadParameter("Use either --inplace or --output, not both.")
        if not inplace and not output:
            raise typer.BadParameter("Give --inplace, --output FILE or --dry-run.")
    target = doc
    if output and not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(doc, output)
        target = output
    try:
        store = AnnotationStore(target, ignore_types=ignore_types, reading_notes=reading_notes, check_sample=False)
        rules = build_rules(store.rules, parse_assignments(rule, "--rule"), parse_assignments(suffix, "--suffix"))
        report = store.apply_rules(rules, dry_run=dry_run)
    except (AnnotationError, ValueError) as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)

    names = {1: "subreading ↔ parent", 2: "sibling subreadings"}
    if dry_run:
        typer.echo("Dry run: nothing was saved.")
    typer.echo("Rules: " + ", ".join(f"{item.reading_type} → {item.category} (suffix '{item.suffix}')" for item in report.rules))
    for count in report.counts:
        line = f"Rule {count.rule} ({names[count.rule]}): {count.covered} pairs: {count.written} written, {count.unchanged} unchanged"
        if count.human:
            line += f", {count.human} kept (labelled by a human)"
        typer.echo(line)
    if report.removed:
        typer.echo(f"Leftover rule labels removed (pairs no rule covers): {report.removed}")
    if report.descriptions_updated:
        typer.echo("Rule descriptions updated: " + ", ".join(report.descriptions_updated))
    if report.unresolved:
        typer.echo("Unresolved parents: " + "; ".join(f"{item.unit_id} {item.reading}" for item in report.unresolved))
    if report.nested:
        typer.echo("Nested subreadings: " + "; ".join(f"{item.unit_id} {item.reading} (of {item.parent})" for item in report.nested))
    if report.conflicts:
        typer.echo(f"Conflicts (human labels kept): {len(report.conflicts)}")
        for conflict in report.conflicts:
            typer.echo(f"  {conflict.unit_id} {conflict.active} → {conflict.passive}: {conflict.label} (rule {conflict.rule} gives {conflict.rule_label})")
    if not dry_run:
        typer.echo(f"Saved to {target}")


@app.command()
def annotate(
    doc:Path=typer.Argument(..., help="The TEI XML document. It is modified in place when classifications are saved."),
    port:int=typer.Option(8765, help="Port for the GUI."),
    host:str=typer.Option("127.0.0.1", help="Host for the GUI."),
    annotator:str=typer.Option(None, help="xml:id of the human annotator responsible for the labels saved, e.g. 'RT'."),
    annotator_name:str=typer.Option(None, help="Full name of the annotator (used in the <respStmt> added to the TEI header)."),
    read_only:bool=typer.Option(False, help="View only: no labels can be saved."),
    evaluation:bool=typer.Option(False, help="Evaluation mode: the labels and suggestions of the sample's test units are not shown, and test units cannot be changed."),
    reading_notes:bool=typer.Option(True, help="Treat text in parentheses in readings (e.g. ECM's '(cf. 24b)') as notes, not reading text."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample of units (default: <doc>.sample.yaml)."),
    group_by_prefix:str=typer.Option(None, help="Lay out the category buttons in rows by the part of the name before this separator, e.g. '_'."),
    compact_threshold:int=typer.Option(10, help="Show units with more pairs than this as compact rows."),
    open_browser:bool=typer.Option(False, "--open", help="Open the GUI in a web browser."),
):
    """ Runs the annotation GUI on its own (use `serve-mcp --gui-port` to run it inside the MCP server instead). """
    from .annotation import AnnotationStore, AnnotationError
    from .gui import create_app
    from .gui.serve import bind, gui_server

    if not read_only and not annotator:
        raise typer.BadParameter("Provide --annotator (the xml:id of the person responsible) or use --read-only.")
    try:
        store = AnnotationStore(
            doc, annotator=annotator, annotator_name=annotator_name, read_only=read_only,
            ignore_types=ignore_types, sample_path=sample_file, reading_notes=reading_notes, evaluation=evaluation,
        )
    except AnnotationError as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)

    sock = bind(host, port)
    if sock is None:
        raise typer.Exit(code=1)
    url = f"http://{host}:{port}/"
    console.print(f"Annotation GUI for {doc} at {url}")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    import anyio
    anyio.run(lambda: gui_server(create_app(store, group_by_prefix=group_by_prefix, compact_threshold=compact_threshold), host, port).serve([sock]))


@app.command()
def sample(
    doc:Path=typer.Argument(..., help="The TEI XML document."),
    size:int=typer.Option(..., "--size", "-n", help="Number of units to sample."),
    seed:int=typer.Option(..., help="Random seed. The same seed and settings give the same sample."),
    test_fraction:float=typer.Option(0.5, help="Fraction of the sampled units held out as test units."),
    max_pairs:int=typer.Option(None, help="Exclude units with more pairs than this."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file to write (default: <doc>.sample.yaml)."),
    overwrite:bool=typer.Option(False, help="Replace an existing sample file."),
):
    """ Draws a reproducible random sample of variation units, split into example and test units. """
    from .annotation import AnnotationStore, AnnotationError

    try:
        store = AnnotationStore(doc, ignore_types=ignore_types, sample_path=sample_file, check_sample=not overwrite)
        info = store.draw_sample(size=size, seed=seed, test_fraction=test_fraction, max_pairs=max_pairs, overwrite=overwrite)
    except AnnotationError as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)

    console.print(f"Sampled {info.size} of {info.eligible_count} eligible units (seed {info.seed}) -> {info.path}")
    for progress in info.progress:
        console.print(f"  {progress.role}: {progress.units} units, {progress.pairs} pairs")


def open_store(doc:Path, ignore_types:list[str], sample_file:Path|None, reading_notes:bool=True, **kwargs):
    from .annotation import AnnotationStore, AnnotationError
    try:
        return AnnotationStore(doc, ignore_types=ignore_types, sample_path=sample_file, reading_notes=reading_notes, **kwargs)
    except AnnotationError as error:
        error_console.print(str(error))
        raise typer.Exit(code=1)


@app.command()
def estimate(
    doc:Path=typer.Argument(..., help="The TEI XML document."),
    model:str=typer.Option("anthropic:claude-opus-5-5", help="The model, as a pydantic-ai model name (e.g. anthropic:claude-opus-5-5, openai:gpt-5)."),
    unit:list[str]=typer.Option(None, "--unit", help="Only these units (repeatable)."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable)."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample (default: <doc>.sample.yaml)."),
):
    """ Estimates the tokens and cost of drafting the pairs still to classify, without calling any model. """
    from .pipeline import estimate as estimate_fn
    store = open_store(doc, ignore_types, sample_file, read_only=True)
    result = estimate_fn(store, model, unit_ids=unit or None)
    console.print(f"{result.pairs} pairs to classify in {result.units} units ({result.requests} requests) with {result.model}")
    console.print(f"About {result.input_tokens:,} input tokens ({result.cache_read_tokens:,} from the prompt cache) and {result.output_tokens:,} output tokens")
    if result.cost is None:
        console.print("Cost: unknown for this model")
    else:
        console.print(f"Estimated cost: ${result.cost:,.2f} (rough: calibrate with a small run, e.g. rdgai draft --limit-units 20)")
        if model.startswith("anthropic:"):
            console.print(f"With --batch (Message Batches, half price): about ${result.cost / 2:,.2f}")


@app.command()
def draft(
    doc:Path=typer.Argument(..., help="The TEI XML document. Labels are written to it as the pipeline's (resp='#rdgai')."),
    model:str=typer.Option("anthropic:claude-opus-5-5", help="The model, as a pydantic-ai model name (e.g. anthropic:claude-opus-5-5, openai:gpt-5)."),
    unit:list[str]=typer.Option(None, "--unit", help="Only these units (repeatable)."),
    limit_units:int=typer.Option(None, help="Classify at most this many units (e.g. to calibrate the cost estimate)."),
    concurrency:int=typer.Option(4, help="Units classified at the same time."),
    effort:str=typer.Option(None, help="Effort level for Anthropic models (low, medium, high, xhigh, max)."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable)."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample (default: <doc>.sample.yaml)."),
    batch:bool=typer.Option(False, help="Send the requests as Anthropic Message Batches: half the price; results within hours (Anthropic models only)."),
    poll_interval:float=typer.Option(60.0, help="With --batch: seconds between checks on the batch."),
    state_file:Path=typer.Option(None, help="With --batch: where the batch state is kept, so an interrupted run resumes without paying again (default: <doc>.batch-state.json)."),
):
    """
    Draft pass: classifies the pairs still to classify (not those covered by subreading rules), unit by unit.
    Agents read through the rdgai MCP server and give a checked decision per pair. Resumable: run it again to continue.
    """
    from .pipeline import draft as draft_fn
    store = open_store(doc, ignore_types, sample_file)
    if batch:
        from .batch import draft_batch
        report = draft_batch(store, model, unit_ids=unit or None, limit_units=limit_units, poll_interval=poll_interval,
                             state_path=state_file or doc.with_name(f"{doc.stem}.batch-state.json"),
                             progress=lambda message: console.print(f"  {message}"))
    else:
        report = draft_fn(
            store, model, unit_ids=unit or None, limit_units=limit_units, concurrency=concurrency, effort=effort,
            progress=lambda app: console.print(f"  {app}"),
        )
    console.print(f"Run {report.run_id}: {report.pairs_written} pairs written in {report.units_done} units; {report.pairs_skipped} skipped (labelled meanwhile)")
    usage = report.usage
    console.print(f"{usage['requests']} requests, {usage['input_tokens']:,} input tokens ({usage['cache_read_tokens']:,} cached), {usage['output_tokens']:,} output tokens"
                  + (f", ${report.cost:,.2f}" if report.cost is not None else ""))
    for error in report.errors:
        error_console.print(error)


def print_usage(report):
    usage = report.usage
    console.print(f"{usage['requests']} requests, {usage['input_tokens']:,} input tokens ({usage['cache_read_tokens']:,} cached), {usage['output_tokens']:,} output tokens"
                  + (f", ${report.cost:,.2f}" if report.cost is not None else ""))
    for error in report.errors:
        error_console.print(error)


@app.command()
def review(
    doc:Path=typer.Argument(..., help="The TEI XML document."),
    model:str=typer.Option("anthropic:claude-opus-5-5", help="The model, as a pydantic-ai model name."),
    rounds:int=typer.Option(3, help="At most this many rounds of review (stops early when a round changes nothing)."),
    category:list[str]=typer.Option(None, "--category", help="Only review these categories (repeatable)."),
    unit:list[str]=typer.Option(None, "--unit", help="Only review labels in these units (repeatable)."),
    effort:str=typer.Option(None, help="Effort level for Anthropic models."),
    log:Path=typer.Option(None, help="Revision log (JSON lines; default <doc>.revisions.jsonl)."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable)."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample (default: <doc>.sample.yaml)."),
):
    """ Reviews the pipeline's labels one category at a time and applies the revisions (human labels are never changed). """
    from .pipeline import review as review_fn
    store = open_store(doc, ignore_types, sample_file)
    report = review_fn(store, model, categories=category or None, rounds=rounds, unit_ids=unit or None, effort=effort, log_path=log,
                       progress=lambda round_number, name: console.print(f"  round {round_number}: {name}"))
    applied = sum(entry.status == "applied" for entry in report.revisions)
    console.print(f"Run {report.run_id}: {report.rounds_run} rounds, {applied} revisions applied, {len(report.revisions) - applied} skipped")
    for note in report.notes:
        console.print(f"Note: {note}")
    print_usage(report)


@app.command()
def run(
    doc:Path=typer.Argument(..., help="The TEI XML document."),
    model:str=typer.Option("anthropic:claude-opus-5-5", help="The model, as a pydantic-ai model name."),
    rounds:int=typer.Option(3, help="Rounds of category review after the draft (0: draft only)."),
    limit_units:int=typer.Option(None, help="Draft at most this many units."),
    concurrency:int=typer.Option(4, help="Units drafted at the same time."),
    effort:str=typer.Option(None, help="Effort level for Anthropic models."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable)."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample (default: <doc>.sample.yaml)."),
):
    """ The full pipeline: subreading rules (if configured), the draft pass, then category review. Resumable. """
    from .pipeline import draft as draft_fn, review as review_fn
    store = open_store(doc, ignore_types, sample_file)
    if store.rules:
        store.apply_rules()
    report = draft_fn(store, model, limit_units=limit_units, concurrency=concurrency, effort=effort,
                      progress=lambda app: console.print(f"  {app}"))
    console.print(f"Draft {report.run_id}: {report.pairs_written} pairs written in {report.units_done} units")
    print_usage(report)
    if rounds > 0:
        reviewed = review_fn(store, model, rounds=rounds, effort=effort,
                             progress=lambda round_number, name: console.print(f"  round {round_number}: {name}"))
        applied = sum(entry.status == "applied" for entry in reviewed.revisions)
        console.print(f"Review {reviewed.run_id}: {reviewed.rounds_run} rounds, {applied} revisions applied")
        print_usage(reviewed)
    if store.rules:
        store.apply_rules()
