"""
Similarity search over labelled pairs: BM25 over terms describing each pair's change.

A pair's terms are what the second reading adds (``+word``) and omits (``-word``), the kind of change
(``kind:addition`` …), character n-grams of the added and omitted words (``+g:κρα``, ``-g:#κρ``, with ``#`` marking
the word's edges, so that other forms of a word still match: κραζον / κραυγαζον, πρωτη / πρωτου) and the words just
before and after the variation unit (``ctx:word``). Words are normalized (combining marks removed,
case-folded), so accented and unaccented texts match. N-gram terms are weighted by
``NGRAM_WEIGHT``; a whole-word match shares the word term and all its n-grams, so it still ranks first.
"""
import math
import os
import re
import unicodedata
from collections import Counter

from .diff import describe_change

WORD = re.compile(r"\w+")
UNIT_ID = re.compile(r"^(?P<book>[^.]+)\.(?P<chapter>\d+)\.(?P<verse>\d+)\.")
CONTEXT_WORDS = 2
DEFAULT_NGRAM_SIZES = (2,)        # chosen by measurement (results/mark-cv/retrieval.py, retrieval.txt)
DEFAULT_NGRAM_WEIGHT = 1.0        # per n-gram term, as for a whole-word term (the word term counts twice)
NGRAM_PREFIXES = ("+g:", "-g:")


def settings_from_env(environ=os.environ) -> tuple[tuple[int, ...], float]:
    """ N-gram sizes and weight, overridable for experiments by RDGAI_NGRAMS ("3,4"; empty for none) and RDGAI_NGRAM_WEIGHT. """
    sizes = environ.get("RDGAI_NGRAMS")
    sizes = DEFAULT_NGRAM_SIZES if sizes is None else tuple(int(size) for size in sizes.split(",") if size.strip())
    return sizes, float(environ.get("RDGAI_NGRAM_WEIGHT", DEFAULT_NGRAM_WEIGHT))


NGRAM_SIZES, NGRAM_WEIGHT = settings_from_env()


def normalize(text:str) -> str:
    """
    Removes accents, breathings and other combining marks and case-folds (any script; case-folding also writes Greek
    final sigma as σ), so accented and unaccented texts match.
    """
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return unicodedata.normalize("NFC", stripped).casefold()


normalize_greek = normalize   # the older name


def tokens(text:str) -> list[str]:
    return WORD.findall(normalize(text))


def character_ngrams(word:str, sizes) -> list[str]:
    """ The character n-grams of a word with ``#`` marking its start and end (a short word is one n-gram). """
    marked = f"#{word}#"
    grams = []
    for size in sizes:
        if len(marked) <= size:
            grams.append(marked)
        else:
            grams += [marked[index:index + size] for index in range(len(marked) - size + 1)]
    return list(dict.fromkeys(grams))


def ngram_weights() -> dict[str, float]:
    return {prefix: NGRAM_WEIGHT for prefix in NGRAM_PREFIXES}


def pair_terms(active_text:str, passive_text:str, before:str="", after:str="", ngrams=None) -> list[str]:
    """
    The search terms for the change from the active reading to the passive reading. `ngrams` gives the n-gram sizes
    (default ``NGRAM_SIZES``; empty for none).
    """
    sizes = NGRAM_SIZES if ngrams is None else ngrams
    change = describe_change(" ".join(tokens(active_text)), " ".join(tokens(passive_text)))
    terms = [f"kind:{change.kind}"]
    if change.reordered and change.kind != "reordering":
        terms.append("kind:reordering")   # moved as well as changed
    # the difference is what matters most, so its terms count twice
    terms += [f"+{word}" for word in change.added] * 2
    terms += [f"-{word}" for word in change.removed] * 2
    if sizes:
        terms += [f"+g:{gram}" for word in change.added for gram in character_ngrams(word, sizes)]
        terms += [f"-g:{gram}" for word in change.removed for gram in character_ngrams(word, sizes)]
    terms += [f"ctx:{word}" for word in tokens(before)[-CONTEXT_WORDS:]]
    terms += [f"ctx:{word}" for word in tokens(after)[:CONTEXT_WORDS]]
    return terms


def verse_label(unit_id:str) -> str:
    """ 'Mark.2.27.20' -> 'Mark 2:27' (other ids are returned unchanged). """
    match = UNIT_ID.match(unit_id)
    return f"{match['book']} {match['chapter']}:{match['verse']}" if match else unit_id


class BM25:
    """
    Okapi BM25 over pre-tokenized documents. `weights` scales the score of query terms by prefix (default: n-gram
    terms count ``NGRAM_WEIGHT``).
    """
    def __init__(self, documents:list[list[str]], k1:float=1.5, b:float=0.75, weights:dict[str, float]|None=None):
        self.k1, self.b = k1, b
        self.weights = ngram_weights() if weights is None else weights
        self.frequencies = [Counter(document) for document in documents]
        self.lengths = [len(document) for document in documents]
        self.average_length = (sum(self.lengths) / len(documents)) if documents else 0.0
        document_frequency = Counter(term for frequencies in self.frequencies for term in frequencies)
        count = len(documents)
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def weight(self, term:str) -> float:
        for prefix, weight in self.weights.items():
            if term.startswith(prefix):
                return weight
        return 1.0

    def scores(self, query:list[str]) -> list[float]:
        weights = {term: self.weight(term) for term in set(query)}
        results = []
        for frequencies, length in zip(self.frequencies, self.lengths):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * length / self.average_length) if self.average_length else self.k1
            for term in set(query):
                frequency = frequencies.get(term, 0)
                if frequency:
                    score += weights[term] * self.idf[term] * frequency * (self.k1 + 1) / (frequency + norm)
            results.append(score)
        return results
