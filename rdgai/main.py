from pathlib import Path
import typer
from langchain.schema.output_parser import StrOutputParser
from rich.console import Console
from rich.progress import track
from dataclasses import dataclass
import pandas as pd

from .tei import read_tei, find_elements, get_language, write_tei, find_parent, find_element
from .relations import get_relation_categories, get_relation_categories_dict, get_classified_relations, get_apparatus_unclassified_relations, make_readings_list
from .prompts import build_template
from .parsers import read_output
from .apparatus import read_doc, RelationType, Pair, App
from .mapper import Mapper
from .llms import get_llm
from .export import export_variants_to_excel, import_classifications_from_dataframe

console = Console()
error_console = Console(stderr=True, style="bold red")


app = typer.Typer(pretty_exceptions_enable=False)
    
DEFAULT_MODEL_ID = "gpt-4o"


@app.command()
def classify(
    doc:Path,
    output:Path,
    ignore:list[str]=typer.Option(None, help="Categories to ignore"),
    verbose:bool=False,
    hf_auth:str=typer.Option("", envvar=["HF_AUTH"]),
    openai_api_key:str=typer.Option("", envvar=["OPENAI_API_KEY"]),
    model_id:str=DEFAULT_MODEL_ID,
    prompt_only:bool=False,
    examples:int=10,
):
    """
    Classifies relations in TEI documents.
    """
    doc_path = doc
    doc = read_tei(doc_path)
    relation_category_dict = get_relation_categories_dict(doc, categories_to_ignore=ignore)
    get_classified_relations(doc, relation_category_dict.values())
    language = get_language(doc)
    llm = get_llm(hf_auth=hf_auth, openai_api_key=openai_api_key, model_id=model_id)

    for apparatus in track(find_elements(doc, ".//app")):
        app = App(apparatus)
        print(f"Analyzing apparatus at {app}")
        unclassified_relations = get_apparatus_unclassified_relations(apparatus)
        if not unclassified_relations:
            continue

        readings = make_readings_list(apparatus)

        template = build_template(relation_category_dict.values(), app, readings, language, examples=examples)
        if verbose or prompt_only:
            template.pretty_print()
            if prompt_only:
                return

        chain = template | llm.bind(stop=["----"]) | StrOutputParser() | read_output

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
                print('remove', relation_type)
                pair.remove_type(relation_type)
            elif data['operation'] == 'add':
                print('add', relation_type)
                pair.add_type(relation_type)
            
            print('write', output)
            doc.write(output)
            return "Success", 200           
        except Exception as e:  
            print(str(e))
            return str(e), 400

        return "Failed", 400

    app.run(debug=True, use_reloader=True)


@app.command()
def evaluate(
    doc:Path,
    ground_truth:Path,
    confusion_matrix:Path=None,
    confusion_matrix_plot:Path=None,
    report:Path=None,
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
    correct_items = []
    incorrect_items = []

    @dataclass
    class EvalItem:
        app_id:str
        app:App
        active:str
        passive:str
        text_in_context:str
        reading_transition_str:str
        ground_truth:str
        predicted:str
        description:str = ""

    relation_category_dict = get_relation_categories_dict(doc.tree)
    predicted_classified_relations = get_classified_relations(doc.tree, relation_category_dict.values())
    ground_truth_classified_relations = get_classified_relations(ground_truth.tree, relation_category_dict.values())

    predicted_classified_relations_dict = {relation.relation_element:relation for relation in predicted_classified_relations}        
    ground_truth_classified_relations_dict = {relation.relation_element:relation for relation in ground_truth_classified_relations}

    for rdgai_relation in rdgai_relations:
        # find app
        app = find_parent(rdgai_relation, "app")
        app_id = app.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
        app_object = App(app)
        active = rdgai_relation.attrib['active']
        passive = rdgai_relation.attrib['passive']

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

        desc = find_element(rdgai_relation, ".//desc")
        description = desc.text if desc is not None else ""

        ground_truth_relation_object = ground_truth_classified_relations_dict[ground_truth_relation]
        eval_item = EvalItem(
            app_id=app_id,
            app=app_object,
            text_in_context=app_object.text_in_context(),
            active=ground_truth_relation_object.active,
            passive=ground_truth_relation_object.passive,
            reading_transition_str=ground_truth_relation_object.reading_transition_str(),
            ground_truth=ground_truth_ana,
            predicted=rdgai_ana,
            description=description,
        )
        if ground_truth_ana == rdgai_ana:
            correct_items.append(eval_item)
        else:
            incorrect_items.append(eval_item)

        predicted.append(rdgai_ana)
        gold.append(ground_truth_ana)
        print(eval_item)

    print(len(predicted), len(gold))
    assert len(predicted) == len(gold), f"Predicted and gold lengths do not match: {len(predicted)} != {len(gold)}"

    from sklearn.metrics import precision_score, recall_score, f1_score, classification_report, accuracy_score
    print(classification_report(gold, predicted))

    precision = precision_score(gold, predicted, average='macro')*100.0
    print("precision", precision)
    recall = recall_score(gold, predicted, average='macro')*100.0
    print("recall", recall)
    f1 = f1_score(gold, predicted, average='macro')*100.0
    print("f1", f1)
    accuracy = accuracy_score(gold, predicted)*100.0
    print("accuracy", accuracy)

    # create confusion matrix
    if confusion_matrix or confusion_matrix_plot or report:
        from sklearn.metrics import confusion_matrix as sk_confusion_matrix
        import pandas as pd
        labels = list(relation_category_dict.keys())
        cm = sk_confusion_matrix(gold, predicted, labels=labels)
        confusion_df = pd.DataFrame(cm, index=labels, columns=labels)
        if confusion_matrix:
            confusion_matrix = Path(confusion_matrix)
            confusion_matrix.parent.mkdir(parents=True, exist_ok=True)
            confusion_df.to_csv(confusion_matrix)

        if confusion_matrix_plot or report:
            import plotly.graph_objects as go

            cm_normalized = cm / cm.sum(axis=1, keepdims=True)

            text_annotations = [[str(cm[i][j]) for j in range(len(labels))] for i in range(len(labels))]

            # Plot the normalized confusion matrix
            fig = go.Figure(data=go.Heatmap(
                z=cm_normalized,
                x=labels,
                y=labels,
                colorscale='Viridis',
                text=text_annotations,      # Add only the raw counts to each cell
                colorbar=dict(title="Proportion of True Values")  # Updated legend title
            ))
            annotations = []
            for i in range(len(labels)):
                for j in range(len(labels)):
                    count = cm[i][j]  # Raw count
                    proportion = cm_normalized[i][j]  # Normalized proportion
                    annotations.append(
                        go.layout.Annotation(
                            x=j, y=i,
                            text=f"{count}",  # Showing both raw count and normalized proportion
                            showarrow=False,
                            font=dict(size=10, color="white" if proportion < 0.5 else "black")
                        )
                    )
            fig.update_layout(annotations=annotations)


            fig.update_layout(
                xaxis_title='Predicted',
                yaxis_title='Actual',
                xaxis=dict(tickmode='array', tickvals=list(range(len(labels))), ticktext=labels, side="top"),
                yaxis=dict(tickmode='array', tickvals=list(range(len(labels))), ticktext=labels, autorange="reversed"),
            )

            # Save the plot as HTML
            if confusion_matrix_plot:
                confusion_matrix_plot = Path(confusion_matrix_plot)
                confusion_matrix_plot.parent.mkdir(parents=True, exist_ok=True)
                fig.write_html(confusion_matrix_plot)

            if report:
                from flask import Flask, render_template
                report = Path(report)
                report.parent.mkdir(parents=True, exist_ok=True)
                app = Flask(__name__)

                import plotly.io as pio
                confusion_matrix_html = pio.to_html(fig, full_html=True, include_plotlyjs='inline')

                with app.app_context():
                    text = render_template(
                        'report.html', 
                        correct_items=correct_items, 
                        incorrect_items=incorrect_items, 
                        confusion_matrix=confusion_matrix_html,
                        accuracy=accuracy,
                        precision=precision,
                        recall=recall,
                        f1=f1,
                        correct_count=len(correct_items),
                        incorrect_count=len(incorrect_items),
                    )
                report.write_text(text)


@app.command()
def clean(
    doc:Path,
    output:Path,
):
    """ Cleans a TEI XML file for common errors. """
    doc = read_doc(doc)

    # find all listRelation elements
    list_relations = find_elements(doc.tree, ".//listRelation")
    for list_relation in list_relations:
        relations_so_far = set()
        for relation in find_elements(list_relation, ".//relation"):
            # make sure that relation elements have a # at the start of the ana attribute
            if not relation.attrib['ana'].startswith("#"):
                relation.attrib['ana'] = f"#{relation.attrib['ana']}"
            
            relations_so_far.add( (relation.attrib['active'], relation.attrib['passive']) )
        
        # consolidate duplicate relations
        for active, passive in relations_so_far:
            relations = find_elements(list_relation, f".//relation[@active='{active}'][@passive='{passive}']")
            if len(relations) > 1:
                analytic_set = set(relation.attrib['ana'] for relation in relations)
                for relation in relations[1:]:
                    list_relation.remove(relation)
                relations[0].attrib['ana'] = " ".join(analytic_set)
    
    print("writing to", output)
    doc.write(output)


@app.command()
def export(
    doc:Path,
    output:Path,
):
    doc = read_doc(doc)
    export_variants_to_excel(doc, output)


@app.command()
def import_classifications(
    doc:Path,
    spreadsheet:Path,
    output:Path,
):
    doc = read_doc(doc)
    if spreadsheet.suffix == ".xlsx":
        variants_df = pd.read_excel(spreadsheet, sheet_name="Variants", keep_default_na=False)
    elif spreadsheet.suffix == ".csv":
        variants_df = pd.read_csv(spreadsheet, keep_default_na=False)

    import_classifications_from_dataframe(doc, variants_df, output)