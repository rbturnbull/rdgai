"""
A description of the change from one reading to another, from a word-level diff.

It is a reading aid (e.g. "second reading adds: και"), never a label. ``label_warning`` flags a label that clearly
contradicts it, judged from the words in the category's name (e.g. an "Omission" when the second reading only adds
words); categories named otherwise are never flagged.
"""
import re
from dataclasses import dataclass, field
from collections import Counter
from difflib import SequenceMatcher

ADDITION_WORDS = {"addition", "additions", "add", "adds", "insertion", "insertions", "insert", "plus"}
OMISSION_WORDS = {"omission", "omissions", "omit", "omits", "deletion", "deletions", "delete", "minus"}


@dataclass
class ChangeDescription:
    kind: str          # identical, addition, omission, reordering or changed (never named after a category)
    text: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _segments(tokens:list[str], spans:list[tuple[int, int]]) -> str:
    return " … ".join(" ".join(tokens[start:end]) for start, end in spans)


def describe_change(first:str, second:str) -> ChangeDescription:
    """ Describes the change from the first reading's text to the second's ('' for an omission). """
    a, b = (first or "").split(), (second or "").split()
    if a == b:
        return ChangeDescription("identical", "readings are identical")
    if Counter(a) == Counter(b):
        return ChangeDescription("reordering", "same words, reordered")

    removed_spans, added_spans = [], []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            removed_spans.append((i1, i2))
        if tag in ("insert", "replace"):
            added_spans.append((j1, j2))
    removed = [token for start, end in removed_spans for token in a[start:end]]
    added = [token for start, end in added_spans for token in b[start:end]]

    if added and not removed:
        return ChangeDescription("addition", f"second reading adds: {_segments(b, added_spans)}", added, removed)
    if removed and not added:
        return ChangeDescription("omission", f"second reading omits: {_segments(a, removed_spans)}", added, removed)
    return ChangeDescription(
        # neutral wording: a different form of the same word (εξηλθεν → εξηλθον) is not a Substitution
        "changed", f"changed: {_segments(a, removed_spans)} → {_segments(b, added_spans)}", added, removed,
    )


def label_warning(category:str|None, change:ChangeDescription) -> str|None:
    """ A warning if the category's name says addition/omission but the readings show the opposite, else None. """
    if not category:
        return None
    words = set(re.split(r"[^a-z]+", category.lower()))
    if words & OMISSION_WORDS and change.kind == "addition":
        return f"{category} is for an omission, but the {change.text}."
    if words & ADDITION_WORDS and change.kind == "omission":
        return f"{category} is for an addition, but the {change.text}."
    return None
