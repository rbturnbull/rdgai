from pathlib import Path
import typer
from langchain_text_splitters import LatexTextSplitter
from langchain_openai import ChatOpenAI
from langchain.schema.output_parser import StrOutputParser
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element

from rich.console import Console
from vorlagellm.tei import read_tei

from .relations import get_relation_categories, get_classified_relations


console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer()
    


@app.command()
def classify(
    doc:Path,
    ignore:list[str]=typer.Option(None, help="Categories to ignore"),
    name:str="",
    description:str="",
):
    """
    Classifies relations in TEI documents.
    """
    doc_path = doc
    doc = read_tei(doc_path)
    relation_categories = get_relation_categories(doc, ignore)
    relations = get_classified_relations(doc, relation_categories)
    breakpoint()

    llm = ChatOpenAI()
    

@app.command()
def show(
    doc:Path,
    ignore:list[str]=typer.Option(None, help="Categories to ignore"),
):
    doc_path = doc
    doc = read_tei(doc_path)
    relation_categories = get_relation_categories(doc, ignore)
    relations = get_classified_relations(doc, relation_categories)
    for relation in relations:
        active = relation.active or "OMITTED"
        passive = relation.passive or "OMITTED"
        # console.print(f"[purple]{relation.location}\t[bold red]{relation.category}[/bold red]: [green]{active}[bold red] -> [/bold red][green]{passive}")
        console.print(f"[bold red]{relation.category}[/bold red]: [green]{active}[bold red] ➞ [/bold red][green]{passive}")