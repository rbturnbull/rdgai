from pathlib import Path
from dataclasses import dataclass

from .tei import find_elements, find_parent, find_element
from .apparatus import App, Doc

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


def evaluate_docs(
    doc:Doc, 
    ground_truth:Doc,
    confusion_matrix:Path|None=None,
    confusion_matrix_plot:Path|None=None,
    report:Path|None=None,
):
    # get dictionary of ground truth apps
    ground_truth_apps = {str(app):app for app in ground_truth.apps}

    # find all classified relations in the ground truth that correspond to the classified relations in the doc
    predicted = []
    gold = []
    correct_items = []
    incorrect_items = []

    # Create a dictionary of ground truth relation elements to their corresponding pairs
    predicted_pair_dict = dict()
    for pair in doc.get_classified_pairs():
        for relation_element in pair.relation_elements():
            predicted_pair_dict[relation_element] = pair

    ground_truth_pair_dict = dict()
    for pair in ground_truth.get_classified_pairs():
        for relation_element in pair.relation_elements():
            ground_truth_pair_dict[relation_element] = pair


    # find all classified relations in the doc that have been classified with rdgai
    rdgai_relations = find_elements(doc.tree, ".//relation[@resp='#rdgai']")

    if len(rdgai_relations) == 0:
        print("No rdgai relations found in predicted document.")
        return

    for rdgai_relation in rdgai_relations:
        # find app
        pair = predicted_pair_dict[rdgai_relation]
        app = pair.app
        app_id = str(app)
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

        ground_truth_pair = ground_truth_pair_dict[ground_truth_relation]
        eval_item = EvalItem(
            app_id=app_id,
            app=app,
            text_in_context=app.text_in_context(),
            active=ground_truth_pair.active,
            passive=ground_truth_pair.passive,
            reading_transition_str=ground_truth_pair.reading_transition_str(),
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

        labels = list(ground_truth.relation_types.keys())
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
