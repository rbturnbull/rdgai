from pathlib import Path
import typer
from langchain_openai import ChatOpenAI
from langchain.schema.output_parser import StrOutputParser
from rich.console import Console
from rich.progress import track

from .tei import read_tei, find_elements, get_language, write_tei, find_parent, find_element
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
    write_tei(doc, output)
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
            write_tei(doc, output)
            return "Success", 200           
        except Exception as e:  
            return str(e), 400

        return "Failed", 400

    app.run(debug=True, use_reloader=True)


@app.command()
def evaluate(
    doc:Path,
    ground_truth:Path,
    confusion_matrix:Path=None,
    confusion_matrix_plot:Path=None,
):
    doc = read_doc(doc)
    ground_truth = read_doc(ground_truth)
    
    # get dictionary of ground truth apps
    ground_truth_apps = {str(app):app for app in ground_truth.apps}

    # find all classified relations in the doc that have been classified with rdgai
    rdgai_relations = find_elements(doc.tree, ".//relation[@resp='#rdgai']")

    # find all classified relations in the ground truth that correspond to the classified relations in the doc
    predicted = []
    gold = []
    for rdgai_relation in rdgai_relations:
        # find app
        app = find_parent(rdgai_relation, "app")
        app_id = app.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
        active = rdgai_relation.attrib['active']
        passive = rdgai_relation.attrib['passive']

        print(app_id, active, passive)

        ground_truth_app = ground_truth_apps.get(app_id, None)
        if ground_truth_app is None:
            continue
        
        ground_truth_relations = [element for element in find_elements(ground_truth_app.element, f".//relation[@active='{active}']") if element.attrib['passive'] == passive]
        if not ground_truth_relations:
            continue
        ground_truth_relation = ground_truth_relations[0]
        
        # exclude any classified with rdgai
        if ground_truth_relation.attrib.get('resp', '') == '#rdgai':
            continue

        ground_truth_ana = ground_truth_relation.attrib['ana'].replace("#", "")
        rdgai_ana = rdgai_relation.attrib['ana'].replace("#", "")

        predicted.append(rdgai_ana)
        gold.append(ground_truth_ana)

    print(len(predicted), len(gold))
    assert len(predicted) == len(gold), f"Predicted and gold lengths do not match: {len(predicted)} != {len(gold)}"

    print("importing metrics")
    # calculate precision, recall, f1
    from sklearn.metrics import precision_score, recall_score, f1_score, classification_report
    print("imported metrics")
    print(classification_report(gold, predicted))

    precision = precision_score(gold, predicted, average='macro')
    print(precision)

    # create confusion matrix
    if confusion_matrix or confusion_matrix_plot:
        from sklearn.metrics import confusion_matrix as sk_confusion_matrix
        import numpy as np
        import pandas as pd
        labels = np.unique(gold + predicted)
        cm = sk_confusion_matrix(gold, predicted, labels=labels)
        confusion_df = pd.DataFrame(cm, index=labels, columns=labels)
        print(confusion_df)
        if confusion_matrix:
            confusion_matrix = Path(confusion_matrix)
            confusion_matrix.parent.mkdir(parents=True, exist_ok=True)
            confusion_df.to_csv(confusion_matrix)

        if confusion_matrix_plot:
            import plotly.graph_objects as go

            fig = go.Figure(data=go.Heatmap(
                z=cm,
                x=labels,
                y=labels,
                colorscale='Viridis'))
            fig.update_layout(
                title='Confusion Matrix',
                xaxis_title='Predicted',
                yaxis_title='Actual',
                xaxis=dict(tickmode='array', tickvals=list(range(len(labels))), ticktext=labels),
                yaxis=dict(tickmode='array', tickvals=list(range(len(labels))), ticktext=labels),
            )
            confusion_matrix_plot = Path(confusion_matrix_plot)
            confusion_matrix_plot.parent.mkdir(parents=True, exist_ok=True)
            fig.write_html(confusion_matrix_plot)


