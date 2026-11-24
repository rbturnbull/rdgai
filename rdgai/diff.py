"""
A description of the change from one reading to another, from a word-level diff.

It is a reading aid (e.g. "second reading adds: και"), never a label. ``label_warning`` flags a label that clearly
contradicts it, judged from the words in the category's name (e.g. an "Omission" when the second reading only adds
words); categories named otherwise are never flagged.
"""
import re
from dataclasses import dataclass, field
from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher

ADDITION_WORDS = {"addition", "additions", "add", "adds", "insertion", "insertions", "insert", "plus"}
OMISSION_WORDS = {"omission", "omissions", "omit", "omits", "deletion", "deletions", "delete", "minus"}


@dataclass
class ChangeDescription:
    kind: str          # identical, addition, omission, reordering or changed (never named after a category)
    text: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reordered: bool = False   # words the readings share (or forms of them) come in a different order


# two words count as forms of the same word (αυτον/αυτων, αυτον/αυτω) when at least this alike, and this long
SIMILAR_WORD = 0.6
SIMILAR_WORD_LENGTH = 3


def _similar(first:str, second:str) -> bool:
    if min(len(first), len(second)) < SIMILAR_WORD_LENGTH:
        return False
    return SequenceMatcher(None, first, second, autojunk=False).ratio() >= SIMILAR_WORD


def _alignment(a:list[str], b:list[str]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """
    Which words of a correspond to which of b: (exact, similar) lists of (index in a, index in b).

    The diff's in-order matches come first; words left over that occur in both readings are words that moved; then
    left-over words that are forms of each other (αυτον → αυτων) are paired, most alike first.
    """
    exact = []
    for block in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        exact += [(block.a + offset, block.b + offset) for offset in range(block.size)]
    matched_a, matched_b = {i for i, _ in exact}, {j for _, j in exact}
    left_in_b = defaultdict(deque)
    for j, word in enumerate(b):
        if j not in matched_b:
            left_in_b[word].append(j)
    for i, word in enumerate(a):
        if i not in matched_a and left_in_b[word]:
            j = left_in_b[word].popleft()
            exact.append((i, j))
            matched_a.add(i)
            matched_b.add(j)

    candidates = sorted(
        ((SequenceMatcher(None, a[i], b[j], autojunk=False).ratio(), i, j)
         for i in range(len(a)) if i not in matched_a
         for j in range(len(b)) if j not in matched_b and _similar(a[i], b[j])),
        reverse=True)
    similar = []
    for _, i, j in candidates:
        if i not in matched_a and j not in matched_b:
            similar.append((i, j))
            matched_a.add(i)
            matched_b.add(j)
    return exact, similar


def _runs(tokens:list[str], indexes:list[int]) -> str:
    """ The words at these indexes, consecutive ones together, separate runs joined by ' … '. """
    runs = []
    for index in sorted(indexes):
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return " … ".join(" ".join(tokens[index] for index in run) for run in runs)


def _describe_reordered(a:list[str], b:list[str], exact:list[tuple[int, int]]) -> ChangeDescription:
    """ Words moved, and others added, omitted or changed: the words that differ, saying that the order changed too. """
    in_a, in_b = {i for i, _ in exact}, {j for _, j in exact}
    removed_indexes = [i for i in range(len(a)) if i not in in_a]
    added_indexes = [j for j in range(len(b)) if j not in in_b]
    removed = [a[i] for i in removed_indexes]
    added = [b[j] for j in added_indexes]
    if added and removed:
        text = f"reordered and changed: {_runs(a, removed_indexes)} → {_runs(b, added_indexes)}"
        return ChangeDescription("changed", text, added, removed, reordered=True)
    if added:
        return ChangeDescription("addition", f"second reading reorders and adds: {_runs(b, added_indexes)}", added, removed, reordered=True)
    return ChangeDescription("omission", f"second reading reorders and omits: {_runs(a, removed_indexes)}", added, removed, reordered=True)


def _segments(tokens:list[str], spans:list[tuple[int, int]]) -> str:
    return " … ".join(" ".join(tokens[start:end]) for start, end in spans)


def describe_change(first:str, second:str) -> ChangeDescription:
    """ Describes the change from the first reading's text to the second's ('' for an omission). """
    a, b = (first or "").split(), (second or "").split()
    if a == b:
        return ChangeDescription("identical", "readings are identical")
    if Counter(a) == Counter(b):
        return ChangeDescription("reordering", "same words, reordered", reordered=True)

    # words (or forms of them) the readings share, in a different order: a move as well as a change
    exact, similar = _alignment(a, b)
    in_order_of_a = [j for _, j in sorted(exact + similar)]
    if any(later < earlier for earlier, later in zip(in_order_of_a, in_order_of_a[1:])):
        return _describe_reordered(a, b, exact)

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
