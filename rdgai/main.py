from pathlib import Path
import typer
from langchain_text_splitters import LatexTextSplitter
from langchain_openai import ChatOpenAI
from langchain.schema.output_parser import StrOutputParser
from lxml.etree import _ElementTree as ElementTree
from lxml.etree import _Element as Element

from rich.console import Console
from vorlagellm.tei import read_tei

from .tei import get_relation_categories, get_relations


console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer()
    


@app.command()
def add(
    doc:Path,
    name:str="",
    description:str="",
):
    """
    Adds the index to the TeX file.
    """
    doc_path = doc
    doc = read_tei(doc_path)
    relation_categories = get_relation_categories(doc)
    relations = get_relations(doc)
    breakpoint()

    llm = ChatOpenAI()
    
