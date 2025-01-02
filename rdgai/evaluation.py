import numpy as np
from pathlib import Path
from dataclasses import dataclass
from langchain_core.language_models.llms import LLM
from langchain.schema.output_parser import StrOutputParser

from .tei import find_elements
from .apparatus import App, Doc, Pair
from .prompts import build_preamble, build_review_prompt

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
    ground_truth_description:str = ""


def llm_review_results(
    doc:Doc,
    correct_items:list[EvalItem],
    incorrect_items:list[EvalItem],
    llm:LLM|None=None,
    examples:int=10,        
):
    template = build_review_prompt(doc, correct_items, incorrect_items, examples=examples)
    result = ""
    if llm:
        chain = template | llm | StrOutputParser()
        result = chain.invoke({})
        
    return template, result


def evaluate_docs(
    doc:Doc, 
    ground_truth:Doc,
    pairs:list[Pair]|None=None,
    confusion_matrix:Path|None=None,
    confusion_matrix_plot:Path|None=None,
    report:Path|None=None,
    llm:LLM|None=None,
    examples:int=10,
):
    # get dictionary of ground truth apps
    ground_truth_apps = {str(app):app for app in ground_truth.apps}

    # find all classified relations in the ground truth that correspond to the classified relations in the doc
    predicted = []
    gold = []
    correct_items = []
    incorrect_items = []

    # Create a dictionary of ground truth relation elements to their corresponding pairs
    ground_truth_pair_dict = dict()
    for pair in ground_truth.get_classified_pairs():
        for relation_element in pair.relation_elements():
            ground_truth_pair_dict[relation_element] = pair


    # find all classified relations in the doc that have been classified with rdgai
    pairs = pairs or [pair for pair in doc.get_classified_pairs() if pair.rdgai_responsible()]

    if len(pairs) == 0:
        print("No rdgai relations found in predicted document.")
        return

    for pair in pairs:
        # find app
        app = pair.app
        app_id = str(app)
        
        active = pair.active.n
        passive = pair.passive.n

        ground_truth_app = ground_truth_apps.get(app_id, None)
        if ground_truth_app is None:
            continue
        
        ground_truth_relations = [element for element in find_elements(ground_truth_app.element, f".//relation[@active='{active}']") if element.attrib['passive'] == passive]
        if not ground_truth_relations:
            continue
        ground_truth_relation = ground_truth_relations[0]
        ground_truth_pair = ground_truth_pair_dict[ground_truth_relation]
        
        # exclude any classified with rdgai
        if ground_truth_pair.rdgai_responsible():
            continue

        ground_truth_types = ground_truth_pair.relation_type_names()
        predicted_types = pair.relation_type_names()

        description = pair.get_description()

        eval_item = EvalItem(
            app_id=app_id,
            app=app,
            text_in_context=app.text_in_context(),
            active=ground_truth_pair.active,
            passive=ground_truth_pair.passive,
            reading_transition_str=ground_truth_pair.reading_transition_str(),
            ground_truth=ground_truth_types,
            predicted=predicted_types,
            description=description,
            ground_truth_description=ground_truth_pair.get_description(),
        )
        if ground_truth_types == predicted_types:
            correct_items.append(eval_item)
        else:
            incorrect_items.append(eval_item)

        predicted.append(" ".join(sorted(predicted_types)))
        gold.append(" ".join(sorted(ground_truth_types)))

    print(len(predicted), len(gold))
    assert len(predicted) == len(gold), f"Predicted and gold lengths do not match: {len(predicted)} != {len(gold)}"

    if len(gold) == 0:
        print("No relations found in ground truth.")
        return

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

        labels = list(ground_truth.relation_types.keys())
        cm = sk_confusion_matrix(gold, predicted, labels=labels)
        confusion_df = pd.DataFrame(cm, index=labels, columns=labels)
        if confusion_matrix:
            confusion_matrix = Path(confusion_matrix)
            confusion_matrix.parent.mkdir(parents=True, exist_ok=True)
            confusion_df.to_csv(confusion_matrix)

        if confusion_matrix_plot or report:
            import plotly.graph_objects as go

            sums = cm.sum(axis=1, keepdims=True)
            cm_normalized = cm / np.maximum(sums, 1)

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

                review_template, review_result = llm_review_results(
                    doc, 
                    correct_items=correct_items, 
                    incorrect_items=incorrect_items, 
                    examples=examples, 
                    llm=llm,
                )

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
                        prompt=build_preamble(doc, examples=examples),
                        review_template=review_template,
                        review_result=review_result,
                    )
                
                print(f"Writing HTML report to {report}")
                report.write_text(text)


