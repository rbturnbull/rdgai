"""
Similarity search over labelled pairs: BM25 over terms describing each pair's change.

A pair's terms are what the second reading adds (``+word``) and omits (``-word``), the kind of change
(``kind:addition`` …) and the words just before and after the variation unit (``ctx:word``). Words are normalized
(accents, breathings and case removed, final ς → σ), so accented and unaccented texts match.
"""
import math
import re
import unicodedata
from collections import Counter

from .diff import describe_change

WORD = re.compile(r"\w+")
UNIT_ID = re.compile(r"^(?P<book>[^.]+)\.(?P<chapter>\d+)\.(?P<verse>\d+)\.")
CONTEXT_WORDS = 2


def normalize_greek(text:str) -> str:
    """ Removes accents, breathings and other diacritics, lowercases, and writes final sigma as σ. """
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
    return unicodedata.normalize("NFC", stripped).lower().replace("ς", "σ")


def tokens(text:str) -> list[str]:
    return WORD.findall(normalize_greek(text))


def pair_terms(active_text:str, passive_text:str, before:str="", after:str="") -> list[str]:
    """ The search terms for the change from the active reading to the passive reading. """
    change = describe_change(" ".join(tokens(active_text)), " ".join(tokens(passive_text)))
    terms = [f"kind:{change.kind}"]
    # the difference is what matters most, so its terms count twice
    terms += [f"+{word}" for word in change.added] * 2
    terms += [f"-{word}" for word in change.removed] * 2
    terms += [f"ctx:{word}" for word in tokens(before)[-CONTEXT_WORDS:]]
    terms += [f"ctx:{word}" for word in tokens(after)[:CONTEXT_WORDS]]
    return terms


def verse_label(unit_id:str) -> str:
    """ 'Mark.2.27.20' -> 'Mark 2:27' (other ids are returned unchanged). """
    match = UNIT_ID.match(unit_id)
    return f"{match['book']} {match['chapter']}:{match['verse']}" if match else unit_id


class BM25:
    """ Okapi BM25 over pre-tokenized documents. """
    def __init__(self, documents:list[list[str]], k1:float=1.5, b:float=0.75):
        self.k1, self.b = k1, b
        self.frequencies = [Counter(document) for document in documents]
        self.lengths = [len(document) for document in documents]
        self.average_length = (sum(self.lengths) / len(documents)) if documents else 0.0
        document_frequency = Counter(term for frequencies in self.frequencies for term in frequencies)
        count = len(documents)
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def scores(self, query:list[str]) -> list[float]:
        results = []
        for frequencies, length in zip(self.frequencies, self.lengths):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * length / self.average_length) if self.average_length else self.k1
            for term in set(query):
                frequency = frequencies.get(term, 0)
                if frequency:
                    score += self.idf[term] * frequency * (self.k1 + 1) / (frequency + norm)
            results.append(score)
        return results
