from pathlib import Path
import typer
from langchain_openai import ChatOpenAI
from langchain.schema.output_parser import StrOutputParser
from rich.console import Console

from .tei import read_tei, find_elements, get_language
from .relations import get_relation_categories, get_relation_categories_dict, get_classified_relations, get_apparatus_unclassified_relations, make_readings_list
from .prompts import build_template
from .parsers import read_output
from .apparatus import read_doc

console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer()
    


@app.command()
def classify(
    doc:Path,
    output:Path,
    ignore:list[str]=typer.Option(None, help="Categories to ignore"),
):
    """
    Classifies relations in TEI documents.
    """
    doc_path = doc
    doc = read_tei(doc_path)
    relation_category_dict = get_relation_categories_dict(doc, categories_to_ignore=ignore)
    classified_relations = get_classified_relations(doc, relation_category_dict.keys())
    language = get_language(doc)
    llm = ChatOpenAI()

    for apparatus in find_elements(doc, ".//app"):
        unclassified_relations = get_apparatus_unclassified_relations(apparatus)
        readings = make_readings_list(apparatus)

        template = build_template(relation_category_dict.keys(), readings, language)
        chain = template | llm.bind(stop="----") | StrOutputParser() | read_output

        results = chain.invoke({})

        for result in results:
            for relation in unclassified_relations:
                category = relation_category_dict.get(relation.category, None)
                if category is None:
                    continue
                if relation.active_name == result.reading_1_id and relation.passive_name == result.reading_2_id:
                    console.print(f"[bold red]{relation.location}[/bold red]: [green]{relation.active} ➞ {relation.passive}[/green] [bold red]{relation.category}[/bold red]")
                    relation.add_category(category)
                    relation.set_responsible("#rdgai")
                    relation.set_justification(result.justification)
                    if result.justification:
                        console.print(f"[green]Justification[/green]: {result.justification}")
                        relation.set_description(result.justification)

                    break
        output


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


@app.command()
def serve(
    doc:Path,
):
    from flask import Flask
    from flask import render_template

    doc = read_doc(doc)
    
    app = Flask(__name__)

    @app.route("/")
    def root():
        return render_template('server.html', doc=doc)

    app.run(debug=True, use_reloader=True)
