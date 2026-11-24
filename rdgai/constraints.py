"""
Constraints a category can declare on the computed change between two readings, checked by generic code.

A file's categories may declare constraints in the TEI (``AnnotationStore.set_category_constraints``), e.g. a category
for added words declares ``adds-only``. When the pipeline assigns a category whose declared constraint the computed
change breaks, it asks the model once to reconsider; the model may keep its answer (the check cannot tell every
difference of spelling from one of form). rdgai itself knows nothing about any category scheme: without declared
constraints nothing is checked.

Words are compared after normalization (``search.normalize``). A removed and an added word count as the same word in
another spelling or form if they are similar enough (``similar_words``), or are trivially respelled by the rules for
the file's language (``languages.trivially_respelled``).
"""
from difflib import SequenceMatcher

from .diff import describe_change
from .search import normalize
from .languages import trivially_respelled

CONSTRAINTS = {
    "adds-only": "the second reading only adds words (any word of the first reading kept, at most in another spelling)",
    "omits-only": "the second reading only omits words (any word it keeps, at most in another spelling)",
    "keeps-word-count": "both readings have the same number of words (words change form or order, none added or omitted)",
    "not-only-adds-or-omits": "the change is not a bare addition or omission: something is replaced or reordered",
    "rules-only": "assigned only by the subreading rules, never to a pair the pipeline is asked about",
}
CATEGORY_DECLARATIONS = ("rules-only",)   # declarations about the category, not checks on the change

SIMILARITY = 0.6
SHORT_WORD_SIMILARITY = 0.8   # a word of 3 letters or fewer must match more closely
SHORT_WORD = 3


def similar_words(first:str, second:str) -> bool:
    """ Whether two words look like spellings or forms of one word (normalized similarity, stricter for short words). """
    first, second = normalize(first), normalize(second)
    threshold = SHORT_WORD_SIMILARITY if min(len(first), len(second)) <= SHORT_WORD else SIMILARITY
    return SequenceMatcher(None, first, second).ratio() >= threshold


def constraint_problem(constraint:str, active_text:str, passive_text:str, category:str="", language:str="") -> str|None:
    """ How the change from the active to the passive reading breaks the constraint, as a message (None if it fits). """
    if constraint in CATEGORY_DECLARATIONS:
        return None
    change = describe_change(active_text, passive_text)
    label = f"{category} is declared '{constraint}' ({CONSTRAINTS[constraint]})" if category else f"'{constraint}' ({CONSTRAINTS[constraint]})"
    if constraint in ("adds-only", "omits-only"):
        adds = constraint == "adds-only"
        stray, counterpart = (change.removed, change.added) if adds else (change.added, change.removed)
        if change.reordered and change.kind != "reordering" and not stray:
            return f"{label}, but the words are also reordered"
        extra = [word for word in stray if not any(similar_words(word, other) for other in counterpart)]
        if extra:
            return (f"{label}, but the {'first' if adds else 'second'} reading also has {', '.join(extra)}, which is not "
                    f"another spelling of any word {'added' if adds else 'omitted'}: reconsider which definition fits")
        differing = [(word, other) if adds else (other, word) for word in stray for other in counterpart
                     if similar_words(word, other) and not trivially_respelled(word, other, language)]
        if differing:
            changed = ", ".join(f"{first} → {second}" for first, second in differing)
            return (f"{label}; besides the {'added' if adds else 'omitted'} words, {changed} differs: keep it only if the "
                    f"definitions count that difference as spelling (or the apparatus marks it as a subreading of that type)")
        return None
    if constraint == "keeps-word-count":
        if len(change.removed) == len(change.added):
            return None
        return (f"{label}, but the readings have a different number of words "
                f"({', '.join(change.removed) or 'nothing'} → {', '.join(change.added) or 'nothing'}): reconsider which definition fits")
    if constraint == "not-only-adds-or-omits":
        if change.reordered or change.kind not in ("addition", "omission"):
            return None
        verb = "only adds" if change.kind == "addition" else "only omits"
        return (f"{label}, but the computed change shows that the second reading {verb} words "
                f"({', '.join(change.added or change.removed)}), with nothing replaced or reordered: reconsider which definition fits")
    raise ValueError(f"Unknown constraint '{constraint}'")
