import random
from pathlib import Path
from rich.console import Console
import llmloader

from .apparatus import Doc, Pair
from .classification import classify, DEFAULT_MODEL_ID
from .evaluation import evaluate_docs


def validate(
    ground_truth:Doc,
    output:Path,
    validation_pairs:list[Pair]|None=None,
    verbose:bool=False,
    proportion:float=0.5,
    seed:int=42,
    api_key:str="",
    llm:str=DEFAULT_MODEL_ID,
    temperature:float=0.1,
    examples:int=10,
    console:Console|None=None,
    confusion_matrix:Path|None=None,
    confusion_matrix_plot:Path|None=None,
    report:Path|None=None,
):
    """
    Partitions the classified pairs in the document and uses a proportion for examples and the remainder for classification.
    Then it evaluates the classifications and writes a report.
    """
    ground_truth.write(output)
    doc = Doc(output)

    llm = llmloader.load(model=llm, api_key=api_key, temperature=temperature)

    # Find pairs to classify
    if not validation_pairs:
        random.seed(seed)
        classified_pairs = doc.get_classified_pairs(redundant=False)
        validation_pairs = random.sample(classified_pairs, int(len(classified_pairs) * proportion))
        validation_pairs = sorted(validation_pairs, key=lambda pair: (str(pair.active.app), pair.active.n, pair.passive.n))

    # Remove classifications from validation pairs
    for pair in validation_pairs:
        pair.remove_all_types()

    # Classify pairs
    classify(
        doc, 
        output,
        pairs=validation_pairs,
        verbose=verbose,
        llm=llm,
        examples=examples,
        console=console,
    )

    # Evaluate classifications
    evaluate_docs(
        doc,
        ground_truth,
        pairs=validation_pairs,
        confusion_matrix=confusion_matrix,  
        confusion_matrix_plot=confusion_matrix_plot,
        report=report,
        examples=examples,
        llm=llm,
    )

