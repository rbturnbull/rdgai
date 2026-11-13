from pathlib import Path
import typer
from rich.console import Console
import pandas as pd

from .apparatus import Doc
from .export import export_variants_to_excel, import_classifications_from_dataframe
from .classification import classify as classify_fn
from .evaluation import evaluate_docs
from .classification import DEFAULT_MODEL_ID
from .validation import validate as validate_fn
from .prompts import build_preamble

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
def classify(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to classify."),
    output:Path=typer.Argument(None, help="The path to the output TEI XML file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
    verbose:bool=typer.Option(False, help="Print verbose output."),
    api_key:str=typer.Option("", help="API key for the LLM."),
    llm:str=typer.Option(DEFAULT_MODEL_ID, help="ID of the language model to use."),
    temperature:float=typer.Option(0.1, help="Temperature for sampling from the language model."),
    prompt_only:bool=typer.Option(False, help="Only print the prompt and not classify."),
    examples:int=typer.Option(10, help="Number of examples to include in the prompt."),
    examples_doc:Path=typer.Option(None, help="The path to a TEI XML document to use for examples."),
    justification_language:str=typer.Option("English", help="Language to use for classification justifications."),
):
    """
    Classifies relations in TEI documents.
    """
    doc = Doc(doc)
    output = get_output_path(doc, output, inplace)
    examples_doc = Doc(examples_doc) if examples_doc and Path(examples_doc).exists() else None

    return classify_fn(
        doc=doc, 
        output=output, 
        verbose=verbose, 
        api_key=api_key, 
        llm=llm, 
        temperature=temperature,
        prompt_only=prompt_only, 
        examples=examples, 
        console=console,
        examples_doc=examples_doc,
        justification_language=justification_language,
    )


@app.command()
def classified_pairs(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document with the classifications."),
):
    """ Print classified pairs in a document. """
    doc = Doc(doc)
    doc.print_classified_pairs(console)


@app.command()
def html(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to render as HTML."),
    output:Path=typer.Argument(..., help="The path to the output HTML file."),
    all_apps:bool=typer.Option(False, help="Whether or not to use all variation unit `app` elements. By default it shows only non-redundant pairs of readings."),
):    
    """ Renders the variation units of a TEI document as HTML. """
    doc = Doc(doc)
    doc.render_html(output, all_apps=all_apps)


@app.command()
def gui(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to classify."),
    output:Path=typer.Argument(None, help="The path to the output TEI XML file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
    debug:bool=True,
    use_reloader:bool=False,
    all_apps:bool=typer.Option(False, help="Whether or not to use all variation unit `app` elements. By default it shows only non-redundant pairs of readings."),
):
    """ Starts a Flask app to view and classify a TEI document. """
    output = get_output_path(doc, output, inplace)
    doc = Doc(doc)
    flask_app = doc.flask_app(output, all_apps=all_apps)
    flask_app.run(debug=debug, use_reloader=use_reloader)


@app.command()
def evaluate(
    predicted:Path=typer.Argument(..., help="The path to the TEI XML document with predictions from Rdgai to evaluate."),
    ground_truth:Path=typer.Argument(..., help="The path to the input TEI XML document to use as the ground truth for evaluation."),
    confusion_matrix:Path=typer.Option(None, help="Path to write the confusion matrix plot as a CSV file."),
    confusion_matrix_plot:Path=typer.Option(None, help="Path to write the confusion matrix plot as an HTML file."),
    report:Path=typer.Option(None, help="Path to write the report."),
):
    """ Evaluates the classifications in a predicted document against a ground truth document. """
    predicted = Doc(predicted)
    ground_truth = Doc(ground_truth)
    
    evaluate_docs(predicted, ground_truth, confusion_matrix=confusion_matrix, confusion_matrix_plot=confusion_matrix_plot, report=report)


@app.command()
def validate(
    ground_truth:Path=typer.Argument(..., help="The path to the input TEI XML document to use as the ground truth for evaluation."),
    output:Path=typer.Argument(..., help="The path to the output TEI XML file."),
    proportion:float=typer.Option(0.5, help="Proportion of classified pairs to use for validation."),
    api_key:str=typer.Option("", help="API key for the LLM."),
    llm:str=typer.Option(DEFAULT_MODEL_ID, help="ID of the language model to use."),
    temperature:float=typer.Option(0.1, help="Temperature for sampling from the language model."),
    examples:int=typer.Option(10, help="Number of examples to include in the prompt."),
    confusion_matrix:Path=typer.Option(None, help="Path to write the confusion matrix plot as a CSV file."),
    confusion_matrix_plot:Path=typer.Option(None, help="Path to write the confusion matrix plot as an HTML file."),
    seed:int=typer.Option(42, help="Seed for random sampling of validation pairs."),
    report:Path=typer.Option(None, help="Path to write the report."),
    justification_language:str=typer.Option("English", help="Language to use for classification justifications."),
):
    """ Takes a ground truth document, chooses a proportion of classified pairs to validate against and outputs a report. """
    ground_truth = Doc(ground_truth)
    
    validate_fn(
        ground_truth, 
        output,
        llm=llm,
        api_key=api_key,
        examples=examples,
        seed=seed,
        temperature=temperature,
        proportion=proportion,
        confusion_matrix=confusion_matrix, 
        confusion_matrix_plot=confusion_matrix_plot, 
        report=report,
        justification_language=justification_language,
    )


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
def prompt_preamble(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to classify."),
    examples:int=typer.Option(10, help="Number of examples to include in the prompt."),
    justification_language:str=typer.Option("English", help="Language to use for classification justifications."),
):
    """ Prints the prompt preamble for a TEI document for a given number of examples. """
    doc = Doc(doc)
    template = build_preamble(doc, examples, justification_language=justification_language)
    print(template)


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
    reading_notes:bool=typer.Option(True, help="Treat text in parentheses in readings (e.g. ECM's '(cf. 24b)') as notes, not reading text."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample of units (default: <doc>.sample.yaml)."),
    gui_port:int=typer.Option(None, help="Also serve the annotation GUI on this port, in the same process (e.g. 8765)."),
    gui_host:str=typer.Option("127.0.0.1", help="Host for the annotation GUI."),
    group_by_prefix:str=typer.Option(None, help="Lay out the GUI's category buttons in rows by the part of the name before this separator, e.g. '_'."),
):
    """ Serves an MCP server for querying and classifying the reading pairs in a TEI document. """
    from .annotation import AnnotationStore, AnnotationError
    from .mcp_server import build_server

    if not read_only and not annotator:
        raise typer.BadParameter("Provide --annotator (the xml:id of the person responsible) or use --read-only.")

    try:
        store = AnnotationStore(
            doc, annotator=annotator, annotator_name=annotator_name, read_only=read_only, lock_definitions=lock_definitions,
            ignore_types=ignore_types, sample_path=sample_file, reading_notes=reading_notes,
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
        gui_app = create_app(store, group_by_prefix=group_by_prefix)
        anyio.run(lambda: serve_mcp_with_gui(server, gui_app, gui_host, gui_port, transport=transport, host=host, port=port))
    elif transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)


@app.command()
def annotate(
    doc:Path=typer.Argument(..., help="The TEI XML document. It is modified in place when classifications are saved."),
    port:int=typer.Option(8765, help="Port for the GUI."),
    host:str=typer.Option("127.0.0.1", help="Host for the GUI."),
    annotator:str=typer.Option(None, help="xml:id of the human annotator responsible for the labels saved, e.g. 'RT'."),
    annotator_name:str=typer.Option(None, help="Full name of the annotator (used in the <respStmt> added to the TEI header)."),
    read_only:bool=typer.Option(False, help="View only: no labels can be saved."),
    reading_notes:bool=typer.Option(True, help="Treat text in parentheses in readings (e.g. ECM's '(cf. 24b)') as notes, not reading text."),
    ignore_types:list[str]=typer.Option(["defective"], "--ignore-type", help="Readings of this @type are not classified (repeatable). Defaults to defective."),
    sample_file:Path=typer.Option(None, help="Sidecar YAML file for the sample of units (default: <doc>.sample.yaml)."),
    group_by_prefix:str=typer.Option(None, help="Lay out the category buttons in rows by the part of the name before this separator, e.g. '_'."),
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
            ignore_types=ignore_types, sample_path=sample_file, reading_notes=reading_notes,
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
    anyio.run(lambda: gui_server(create_app(store, group_by_prefix=group_by_prefix), host, port).serve([sock]))


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
