"""
The views of the GUI for developing rdgai: queue filters, pairs by category and agreement between the human labels
and suggestions. Callers hold ``store.lock`` and have refreshed the store.

Suggestions are never shown on test units (unless the agreement view is explicitly asked to include them), so the
filters that look at suggestions skip test units too.
"""
from collections import Counter

from ..annotation import AnnotationError, AnnotationStore, CERTAINTY_VALUES
from ..evaluate import metrics

LABEL_SOURCES = ("human", "pipeline", "rule")
QUEUE_SOURCES = LABEL_SOURCES + ("suggestion",)
AGREEMENT_SCOPES = ("example", "sampled", "test", "all")
TRUE_VALUES = ("1", "true", "yes", "on")


def check_queue_filters(store:AnnotationStore, filters:dict):
    if filters.get("category"):
        store.get_relation_type(filters["category"])
    if filters.get("cert") and filters["cert"] not in CERTAINTY_VALUES:
        raise AnnotationError(f"cert must be one of {', '.join(CERTAINTY_VALUES)}.")
    if filters.get("source") and filters["source"] not in QUEUE_SOURCES:
        raise AnnotationError(f"source must be one of {', '.join(QUEUE_SOURCES)}.")


def labelled_relations(pair) -> list:
    """ The label elements of a pair in either direction. """
    return [relation for relation in (pair.relation_element(), pair.get_inverse().relation_element()) if relation is not None]


def disagrees(store:AnnotationStore, app, pair) -> bool:
    """ True if the pair is labelled and a suggestion for it (in the same direction) gives another category. """
    category = store.current_category(pair)
    if category is None:
        return False
    return any(suggestion.category != category for suggestion in store.suggestions_for(app, pair))


def unit_matches(store:AnnotationStore, app, filters:dict, roles:dict) -> bool:
    """ Whether a unit passes the category, certainty, source and disagreement filters. """
    category, cert, source = filters.get("category"), filters.get("cert"), filters.get("source")
    disagreement = str(filters.get("disagreement") or "").lower() in TRUE_VALUES
    if not (category or cert or source or disagreement):
        return True
    if store.hides(app):
        return False
    shows_suggestions = roles.get(str(app)) != "test"
    if (source == "suggestion" or disagreement) and not shows_suggestions:
        return False
    pairs = store.reading_ordered_pairs(app)
    if category:
        name = store.get_relation_type(category).name
        if not any(name in pair.relation_type_names() or name in pair.get_inverse().relation_type_names() for pair in pairs):
            return False
    if cert and not any(relation.get("cert") == cert for pair in pairs for relation in labelled_relations(pair)):
        return False
    if source == "suggestion":
        if not store.suggestion_relations(app):
            return False
    elif source and not any(store.label_source(directed) == source for pair in pairs for directed in (pair, pair.get_inverse())):
        return False
    if disagreement and not any(disagrees(store, app, pair) for pair in pairs):
        return False
    return True


def filter_queue(store:AnnotationStore, filters:dict) -> list:
    check_queue_filters(store, filters)
    apps = store.filter_units(status=filters["status"], verse=filters["verse"], sample=filters["sample"])
    roles = store.sample_roles()
    return [app for app in apps if unit_matches(store, app, filters, roles)]


def category_pairs(store:AnnotationStore, category:str, source:str="all") -> list:
    """ The directed pairs labelled with a category, in document order (not in hidden test units). """
    relation_type = store.get_relation_type(category)
    if source not in ("all",) + LABEL_SOURCES:
        raise AnnotationError(f"source must be one of all, {', '.join(LABEL_SOURCES)}.")
    pairs = [pair for pair in store.directed_pairs() if relation_type in pair.types and not store.hides(pair.app)]
    if source != "all":
        pairs = [pair for pair in pairs if store.label_source(pair) == source]
    # a category that is its own inverse labels both directions of a pair: list it once, in display direction
    chosen = {}
    for pair in pairs:
        key = (id(pair.app), frozenset((pair.active.n, pair.passive.n)))
        if key not in chosen:
            chosen[key] = pair
        elif display_direction(store, pair):
            chosen[key] = pair
    return list(chosen.values())


def display_direction(store:AnnotationStore, pair) -> bool:
    """ Whether the directed pair is in the order the GUI shows it (reading order: the earlier reading first). """
    return any((ordered.active.n, ordered.passive.n) == (pair.active.n, pair.passive.n) for ordered in store.reading_ordered_pairs(pair.app))


def default_scope(store:AnnotationStore) -> str:
    return "example" if store.load_sample() is not None else "all"


def agreement(store:AnnotationStore, scope:str|None=None, source:str|None=None, display_justification=lambda text: text) -> dict:
    """
    Compares the human labels with the suggestions of one source, pair by pair in display direction.
    By default only the sample's example units are compared; test units only when the scope asks for them.
    """
    scope = scope or default_scope(store)
    if scope not in AGREEMENT_SCOPES:
        raise AnnotationError(f"scope must be one of {', '.join(AGREEMENT_SCOPES)}.")
    apps = store.filter_units(sample=scope)
    categories = [category.name for category in store.categories()]

    counts = Counter()
    compared_pairs = []   # (app, pair, human category, {source: suggestion})
    for app in apps:
        if store.hides(app):
            continue
        for pair in store.reading_ordered_pairs(app):
            if store.coverage_for(pair) is not None:
                continue
            by_source = {}
            for suggestion in store.suggestions_for(app, pair):
                by_source.setdefault(suggestion.source, suggestion)
            counts.update(by_source.keys())
            human = store.current_category(pair) if store.label_source(pair) == "human" else None
            if human and by_source:
                compared_pairs.append((app, pair, human, by_source))

    sources = [dict(source=name, count=count) for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    if source is None:
        source = sources[0]["source"] if sources else None

    gold, predicted, disagreements = {}, {}, []
    for app, pair, human, by_source in compared_pairs:
        suggestion = by_source.get(source)
        if suggestion is None:
            continue
        key = (str(app), pair.active.n, pair.passive.n)
        gold[key] = human
        predicted[key] = suggestion.category
        if suggestion.category != human:
            disagreements.append(dict(
                unit_id=str(app), first=pair.active.n, second=pair.passive.n,
                first_text=str(pair.active), second_text=str(pair.passive),
                human=human, suggested=suggestion.category, source=suggestion.source,
                justification=display_justification(suggestion.justification or ""),
            ))
    scores = metrics(gold, predicted, categories)
    return dict(
        scope=scope,
        sources=sources,
        source=source,
        compared=len(gold),
        agreed=scores.correct,
        metrics=scores.model_dump(),
        disagreements=disagreements,
    )
