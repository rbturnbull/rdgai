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
    

@app.command()
def classify(
    doc:Path,
    output:Path,
    verbose:bool=False,
    api_key:str=typer.Option(""),
    llm:str=DEFAULT_MODEL_ID,
    temperature:float=typer.Option(0.1, help="Temperature for sampling from the language model."),
    prompt_only:bool=False,
    examples:int=10,
):
    """
    Classifies relations in TEI documents.
    """
    doc = Doc(doc)
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
    )


@app.command()
def classified_pairs(
    doc:Path,
):
    """ Print classified pairs in a document. """
    doc = Doc(doc)
    doc.print_classified_pairs(console)


@app.command()
def html(
    doc:Path,
    output:Path,
):    
    doc = Doc(doc)
    doc.render_html(output)


@app.command()
def serve(
    doc:Path,
    output:Path=typer.Argument(None, help="The path to the output file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
    debug:bool=True,
    use_reloader:bool=False,
    all_apps:bool=False,
):
    if output and inplace:
        raise typer.BadParameter("You cannot use both an output path and --inplace/-i at the same time.")
    if not output and not inplace:
        raise typer.BadParameter("You must provide either an output path or use --inplace/-i.")
    
    if inplace:
        output = doc

    doc = Doc(doc)
    flask_app = doc.flask_app(output, all_apps=all_apps)
    flask_app.run(debug=debug, use_reloader=use_reloader)


@app.command()
def evaluate(
    predicted:Path,
    ground_truth:Path,
    confusion_matrix:Path=None,
    confusion_matrix_plot:Path=None,
    report:Path=None,
):
    predicted = Doc(predicted)
    ground_truth = Doc(ground_truth)
    
    evaluate_docs(predicted, ground_truth, confusion_matrix=confusion_matrix, confusion_matrix_plot=confusion_matrix_plot, report=report)


@app.command()
def validate(
    ground_truth:Path,
    output:Path,    
    proportion:float=typer.Option(0.5, help="Proportion of classified pairs to use for validation."),
    api_key:str=typer.Option(""),
    llm:str=DEFAULT_MODEL_ID,
    temperature:float=typer.Option(0.1, help="Temperature for sampling from the language model."),
    examples:int=10,
    confusion_matrix:Path=None,
    confusion_matrix_plot:Path=None,
    seed:int=typer.Option(42, help="Seed for random sampling of validation pairs."),
    report:Path=None,
):
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
    )


@app.command()
def clean(
    doc:Path,
    output:Path,
):
    """ Cleans a TEI XML file for common errors. """
    doc = Doc(doc)
    doc.clean(output=output)


@app.command()
def export(
    doc:Path,
    output:Path,
):
    doc = Doc(doc)
    export_variants_to_excel(doc, output)


@app.command()
def import_classifications(
    doc:Path,
    spreadsheet:Path,
    output:Path,
    responsible:str="",
):
    doc = Doc(doc)
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
    doc:Path,
    examples:int=10,
):
    doc = Doc(doc)
    template = build_preamble(doc, examples)
    print(template)
