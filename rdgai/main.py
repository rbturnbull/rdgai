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
):
    """
    Classifies relations in TEI documents.
    """
    doc = Doc(doc)
    output = get_output_path(doc, output, inplace)

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
    doc = Doc(doc)
    output = get_output_path(doc, output, inplace)
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
    )


@app.command()
def clean(
    doc:Path=typer.Argument(..., help="The path to the TEI XML document to clean."),
    output:Path=typer.Argument(None, help="The path to the output TEI XML file."),
    inplace: bool = typer.Option(False, "--inplace", "-i", help="Overwrite the input file."),
):
    """ Cleans a TEI XML file for common errors. """
    doc = Doc(doc)
    output = get_output_path(doc, output, inplace)
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
):
    """ Prints the prompt preamble for a TEI document for a given number of examples. """
    doc = Doc(doc)
    template = build_preamble(doc, examples)
    print(template)
