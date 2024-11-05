from pathlib import Path
import typer
from langchain_openai import ChatOpenAI
from langchain.schema.output_parser import StrOutputParser
from rich.console import Console
from rich.progress import track

from .tei import read_tei, find_elements, get_language, write_tei
from .relations import get_relation_categories, get_relation_categories_dict, get_classified_relations, get_apparatus_unclassified_relations, make_readings_list
from .prompts import build_template
from .parsers import read_output
from .apparatus import read_doc, RelationType, Pair, App
from .mapper import Mapper

console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer()
    


@app.command()
def classify(
    doc:Path,
    output:Path,
    ignore:list[str]=typer.Option(None, help="Categories to ignore"),
    verbose:bool=False,
):
    """
    Classifies relations in TEI documents.
    """
    doc_path = doc
    doc = read_tei(doc_path)
    relation_category_dict = get_relation_categories_dict(doc, categories_to_ignore=ignore)
    get_classified_relations(doc, relation_category_dict.values())
    language = get_language(doc)
    llm = ChatOpenAI()

    for apparatus in track(find_elements(doc, ".//app")):
        app = App(apparatus)
        print(f"Analyzing apparatus at {app}")
        unclassified_relations = get_apparatus_unclassified_relations(apparatus)
        if not unclassified_relations:
            continue

        readings = make_readings_list(apparatus)

        template = build_template(relation_category_dict.values(), app, readings, language)
        if verbose:
            template.pretty_print()

        chain = template | llm.bind(stop="----") | StrOutputParser() | read_output

        print("Classifying reading relations ✨")
        results = chain.invoke({})

        for result in results:
            for relation in unclassified_relations:
                category = relation_category_dict.get(result.category, None)
                if category is None:
                    continue
                
                if relation.active_name == result.reading_1_id and relation.passive_name == result.reading_2_id:
                    console.print(f"[bold red]{relation.location}[/bold red]: [green]{relation.active} ➞ {relation.passive}[/green] [bold red]{category}[/bold red]")
                    relation.add_category(category)
                    relation.set_responsible("#rdgai")
                    if result.justification:
                        console.print(f"[green]Justification[/green]: {result.justification}")
                        relation.set_description(result.justification)
                elif relation.active_name == result.reading_2_id and relation.passive_name == result.reading_1_id:
                    category = category.inverse if category.inverse else category
                    console.print(f"[bold red]{relation.location}[/bold red]: [green]{relation.active} ➞ {relation.passive}[/green] [bold red]{category}[/bold red]")
                    relation.add_category(category)
                    relation.set_responsible("#rdgai")
                    relation.set_description(f"c.f. {relation.active} ➞ {relation.passive}")                    

                write_tei(doc, output)
            


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
def html(
    doc:Path,
    output:Path,
):
    from flask import Flask, request, render_template
    
    doc = read_doc(doc)
    mapper = Mapper()
    app = Flask(__name__)

    with app.app_context():
        text = render_template('server.html', doc=doc, mapper=mapper)
    output.write_text(text)


@app.command()
def serve(
    doc:Path,
    output:Path,
):
    from flask import Flask, request, render_template

    doc = read_doc(doc)
    doc.write(output)
    mapper = Mapper()
    
    app = Flask(__name__)

    @app.route("/")
    def root():
        return render_template('server.html', doc=doc, mapper=mapper)

    @app.route("/api/relation-type", methods=['POST'])
    def api_relation_type():
        data = request.get_json()
        relation_type = mapper.obj(data['relation_type'])
        pair = mapper.obj(data['pair'])
        assert isinstance(relation_type, RelationType), f"Expected RelationType, got {type(relation_type)}"
        assert isinstance(pair, Pair)

        try:
            if data['operation'] == 'remove':
                pair.remove_type(relation_type)
            elif data['operation'] == 'add':
                pair.add_type(relation_type)
            
            print('write', output)
            doc.write(output)
            return "Success", 200           
        except Exception as e:  
            return str(e), 400

        return "Failed", 400

    app.run(debug=True, use_reloader=True)
