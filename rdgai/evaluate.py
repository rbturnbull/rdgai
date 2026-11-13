"""
Evaluating the pipeline against the human labels of the sample's test units.

The evaluation runs on a stripped working copy: the test units' labels and suggestions (and labels copied from them
by subreading rules) are removed before the pipeline runs, so nothing about them can reach the model. The pipeline
drafts the test units, its predictions are recorded, the review rounds run and the predictions are recorded again,
and both are compared with the gold labels in the original file, which is never modified.
"""
import json
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from .annotation import AnnotationStore, AnnotationError
from .search import verse_label
from .tei import find_elements
from .apparatus import unique, prune_empty_list

SMALL_SUPPORT = 10


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

class CategoryScore(BaseModel):
    category: str
    support: int = Field(description="Gold test pairs with this category.")
    predicted: int
    precision: float
    recall: float
    f1: float
    indicative: bool = Field(description="Fewer than 10 test pairs: indicative only.")


class Confusion(BaseModel):
    labels: list[str]
    matrix: list[list[int]] = Field(description="Rows: gold; columns: predicted.")


class Metrics(BaseModel):
    n: int
    correct: int
    missing: int = Field(description="Gold pairs the pipeline did not label.")
    accuracy: float
    macro_precision: float
    macro_recall: float
    macro_f1: float
    per_category: list[CategoryScore]
    confusion: Confusion


def metrics(gold:dict, predicted:dict, categories:list[str]) -> Metrics:
    """ Accuracy, macro and per-category precision/recall/F1 and the confusion matrix over the gold pairs. """
    keys = list(gold)
    correct = sum(predicted.get(key) == gold[key] for key in keys)
    used = [category for category in categories if any(gold[key] == category or predicted.get(key) == category for key in keys)]
    rows = []
    for category in used:
        support = sum(gold[key] == category for key in keys)
        guessed = sum(predicted.get(key) == category for key in keys)
        hits = sum(gold[key] == category and predicted.get(key) == category for key in keys)
        precision = hits / guessed if guessed else 0.0
        recall = hits / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append(CategoryScore(category=category, support=support, predicted=guessed, precision=precision, recall=recall,
                                  f1=f1, indicative=support < SMALL_SUPPORT))
    scored = [row for row in rows if row.support]
    labels = used
    matrix = [[sum(gold[key] == actual and predicted.get(key) == guess for key in keys) for guess in labels] for actual in labels]
    count = len(scored) or 1
    return Metrics(
        n=len(keys), correct=correct, missing=sum(key not in predicted for key in keys),
        accuracy=correct / len(keys) if keys else 0.0,
        macro_precision=sum(row.precision for row in scored) / count,
        macro_recall=sum(row.recall for row in scored) / count,
        macro_f1=sum(row.f1 for row in scored) / count,
        per_category=rows, confusion=Confusion(labels=labels, matrix=matrix),
    )


# ---------------------------------------------------------------------------
# gold labels, predictions and the stripped copy
# ---------------------------------------------------------------------------

def labels_in_display_direction(store:AnnotationStore, unit_ids:list[str], sources:tuple[str, ...]) -> dict:
    """ {(unit_id, first, second): category} for the work pairs labelled by these sources, in display direction. """
    result = {}
    for unit_id in unit_ids:
        app = store.get_app(unit_id)
        work = {frozenset((pair.active.n, pair.passive.n)) for pair in store.work_pairs(app)}
        for pair in store.ordered_pairs(app):
            if frozenset((pair.active.n, pair.passive.n)) not in work:
                continue
            for directed, inverse in ((pair, False), (pair.get_inverse(), True)):
                if directed.relation_element() is not None and store.label_source(directed) in sources:
                    category = store.current_category(directed)
                    if inverse:
                        category = store.get_relation_type(category).get_inverse().name
                    result[(unit_id, pair.active.n, pair.passive.n)] = category
                    break
    return result


def gold_labels(store:AnnotationStore, unit_ids:list[str]) -> dict:
    return labels_in_display_direction(store, unit_ids, ("human",))


def predictions(store:AnnotationStore, unit_ids:list[str]) -> dict:
    return labels_in_display_direction(store, unit_ids, ("pipeline",))


def strip_test_units(source:Path, working:Path) -> Path:
    """
    Copies the file (and its sample) and removes every label and suggestion in the sample's test units, so the
    pipeline cannot see them. Returns the path of the copy.
    """
    source, working = Path(source), Path(working)
    original = AnnotationStore(source, read_only=True)
    sample = original.load_sample()
    if sample is None:
        raise AnnotationError(f"Evaluation needs a sample of example and test units ({original.sample_path.name} not found).")
    working.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, working)
    shutil.copy(original.sample_path, working.with_name(f"{working.stem}.sample.yaml"))
    store = AnnotationStore(working, annotator="evaluation", ignore_types=sorted(original.ignore_types))
    for unit_id in sample.ids("test"):
        app = store.get_app(unit_id)
        for list_type in ("transcriptional", "suggestion"):
            for list_relation in unique(find_elements(app.element, f".//listRelation[@type='{list_type}']")):
                for relation in list(list_relation):
                    list_relation.remove(relation)
                prune_empty_list(list_relation)
    store.save()
    return working


# ---------------------------------------------------------------------------
# the evaluation and its report
# ---------------------------------------------------------------------------

class EvaluatedPair(BaseModel):
    unit_id: str
    verse: str
    before: str
    lemma: str
    after: str
    first: str
    second: str
    first_text: str
    second_text: str
    change: str
    gold: str
    draft: str | None
    final: str | None
    justification: str = Field(description="The pipeline's final justification.")


class EvaluationReport(BaseModel):
    model: str
    definitions_hash: str
    references: list[str] = Field(default_factory=list)
    test_units: int
    gold_pairs: int
    excluded_rule_pairs: int
    unlabelled_test_pairs: int
    draft: Metrics
    final: Metrics | None = None
    rounds: int
    revisions: list = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    pairs: list[EvaluatedPair] = Field(default_factory=list)
    draft_run: str = ""
    review_run: str = ""
    usage: dict = Field(default_factory=dict)
    cost: float | None = None


def evaluate(
    path:Path,
    model,
    output_dir:Path,
    rounds:int=3,
    concurrency:int=4,
    toolsets:list|None=None,
    effort:str|None=None,
    ignore_types:list[str]|None=None,
) -> EvaluationReport:
    """ Runs the pipeline on a stripped copy and compares it with the test units' gold labels; writes the report. """
    from .pipeline import draft, review, model_name, price

    path, output_dir = Path(path), Path(output_dir)
    original = AnnotationStore(path, read_only=True, **({"ignore_types": ignore_types} if ignore_types else {}))
    sample = original.load_sample()
    if sample is None:
        raise AnnotationError(f"Evaluation needs a sample of example and test units ({original.sample_path.name} not found).")
    test_units = sample.ids("test")
    gold = gold_labels(original, test_units)

    working = strip_test_units(path, output_dir / f"{path.stem}.working.xml")
    store = AnnotationStore(working, ignore_types=sorted(original.ignore_types))
    if store.rules:
        store.apply_rules()
    definitions_hash = store.definitions().definitions_hash
    categories = [category.name for category in store.definitions().categories]

    drafted = draft(store, model, toolsets=toolsets, unit_ids=test_units, concurrency=concurrency, effort=effort)
    draft_predictions = predictions(store, test_units)
    draft_metrics = metrics(gold, draft_predictions, categories)

    final_metrics, reviewed = None, None
    final_predictions = draft_predictions
    if rounds > 0:
        reviewed = review(store, model, toolsets=toolsets, rounds=rounds, unit_ids=test_units, effort=effort,
                          log_path=output_dir / "revisions.jsonl")
        final_predictions = predictions(store, test_units)
        final_metrics = metrics(gold, final_predictions, categories)

    work_pairs = sum(len(store.work_pairs(store.get_app(unit_id))) for unit_id in test_units)
    rule_pairs = sum(len(store.rule_pairs(store.get_app(unit_id))) for unit_id in test_units)
    notes = [f"The gold set has {len(gold)} test pairs in {len(test_units)} test units."]
    small = [row.category for row in (final_metrics or draft_metrics).per_category if row.indicative and row.support]
    if small:
        notes.append(f"Fewer than {SMALL_SUPPORT} test pairs (indicative only): {', '.join(small)}.")
    if rule_pairs:
        notes.append(f"{rule_pairs} pairs labelled by subreading rules are left out of the scores.")

    pairs = []
    for (unit_id, first, second), category in gold.items():
        app = store.get_app(unit_id)
        pair = store.get_pair(app, first, second)
        relation = pair.relation_element()
        justification = store.provenance_from_relation(relation).justification if relation is not None and store.label_source(pair) == "pipeline" else ""
        from .diff import describe_change
        pairs.append(EvaluatedPair(
            unit_id=unit_id, verse=verse_label(unit_id), before=app.text_before(), lemma=app.text(), after=app.text_after(),
            first=first, second=second, first_text=str(pair.active), second_text=str(pair.passive),
            change=describe_change(pair.active.text, pair.passive.text).text, gold=category,
            draft=draft_predictions.get((unit_id, first, second)), final=final_predictions.get((unit_id, first, second)) if reviewed else None,
            justification=justification,
        ))

    usage = dict(drafted.usage)
    if reviewed:
        for key, value in reviewed.usage.items():
            usage[key] = usage.get(key, 0) + value
    report = EvaluationReport(
        model=model_name(model), definitions_hash=definitions_hash, test_units=len(test_units), gold_pairs=len(gold),
        excluded_rule_pairs=rule_pairs, unlabelled_test_pairs=work_pairs - len(gold),
        draft=draft_metrics, final=final_metrics, rounds=rounds,
        revisions=list(reviewed.revisions) if reviewed else [], notes=notes, pairs=pairs,
        draft_run=drafted.run_id, review_run=reviewed.run_id if reviewed else "",
        usage=usage, cost=price(model, usage),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    (output_dir / "report.html").write_text(render_report(report))
    return report


def render_report(report:EvaluationReport) -> str:
    """ The HTML report, in the style of the original rdgai report (same frame, logo, icons and stylesheet). """
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    import base64
    templates = Path(__file__).parent / "templates"
    environment = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape(["html"]))
    favicon = base64.b64encode((templates / "assets" / "img" / "favicon.svg").read_bytes()).decode()
    return environment.get_template("evaluation.html").render(report=report, favicon=favicon)
