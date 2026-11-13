"""
Similarity search: find labelled pairs whose change resembles a given pair's, so that annotators and the pipeline
can check precedents. BM25 over normalized words (accents, breathings, case and final sigma removed), indexing what
each pair adds or removes plus the words around the variation unit.
"""
import pytest

from rdgai.annotation import AnnotationStore
from rdgai.mcp_server import build_server
from rdgai.search import normalize_greek, tokens, pair_terms, verse_label, BM25

from .test_mcp import call, mark_path, store  # noqa: F401 (fixtures)


# ---------------------------------------------------------------------------
# normalization and terms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, normalized", [
    ("Ἰησοῦς", "ιησουσ"),
    ("λόγος", "λογοσ"),
    ("Ἠσαΐᾳ", "ησαια"),
    ("ΘΕΟΥ", "θεου"),
    ("υιου του θεου", "υιου του θεου"),
    ("Mark", "mark"),
])
def test_normalize_greek(text, normalized):
    assert normalize_greek(text) == normalized


def test_tokens_drop_punctuation():
    assert tokens("ὁ λόγος, καὶ θεός·") == ["ο", "λογοσ", "και", "θεοσ"]


def test_pair_terms_describe_the_difference():
    terms = pair_terms("υιου του θεου", "υιου θεου", before="ιησου χριστου", after="")
    assert "-του" in terms           # what the second reading omits
    assert "kind:omission" in terms
    assert "ctx:χριστου" in terms    # a word just before the unit
    assert "+του" not in terms


def test_pair_terms_ignore_accents():
    assert pair_terms("υἱοῦ τοῦ θεοῦ", "υἱοῦ θεοῦ", "", "") == pair_terms("υιου του θεου", "υιου θεου", "", "")


def test_bm25_ranks_shared_rare_terms_higher():
    index = BM25([["+του", "kind:addition"], ["+και", "kind:addition"], ["-χριστου", "kind:omission"]])
    scores = index.scores(["+του", "kind:addition"])
    assert scores[0] > scores[1] > scores[2]
    assert scores[2] == 0


@pytest.mark.parametrize("unit_id, label", [
    ("Mark.1.1.12-16", "Mark 1:1"),
    ("Mark.2.27.20", "Mark 2:27"),
    ("app", "app"),
])
def test_verse_label(unit_id, label):
    assert verse_label(unit_id) == label


# ---------------------------------------------------------------------------
# searching a store
# ---------------------------------------------------------------------------

@pytest.fixture
def labelled(store):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits the article")  # υιου του θεου → υιου θεου
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="omits χριστου")
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution")                                      # καθως → ως
    return store


def test_most_similar_change_ranks_first(labelled):
    # Mark.1.1.12-16 a → d adds του: the stored inverse b → a (adds του) is the closest precedent
    results = labelled.search_similar("Mark.1.1.12-16", "a", "d")
    top = results[0]
    assert (top.unit_id, top.active, top.passive) == ("Mark.1.1.12-16", "b", "a")
    assert top.category == "Minor_Addition"
    assert top.change == "second reading adds: του"
    assert top.source == "human"
    assert top.corpus == "target"
    assert top.verse == "Mark 1:1"
    assert top.score > 0


def test_result_fields(labelled):
    result = next(result for result in labelled.search_similar("Mark.1.1.12-16", "a", "f") if result.unit_id == "Mark.1.1.10")
    assert (result.active_text, result.passive_text) == ("χριστου", "OMIT")
    assert result.justification in ("omits χριστου", "")
    assert result.cert == "high"
    assert result.tentative is False


def test_the_pair_itself_and_its_inverse_are_never_results(labelled):
    results = labelled.search_similar("Mark.1.1.12-16", "a", "b")
    assert not any(result.unit_id == "Mark.1.1.12-16" and {result.active, result.passive} == {"a", "b"} for result in results)


def test_unlabelled_pairs_are_not_results(labelled):
    results = labelled.search_similar("Mark.1.1.12-16", "a", "d", limit=100)
    assert all(result.category for result in results)
    assert {result.unit_id for result in results} <= {"Mark.1.1.12-16", "Mark.1.1.10", "Mark.1.2.2"}


def test_limit(labelled):
    assert len(labelled.search_similar("Mark.1.1.12-16", "a", "d", limit=1)) == 1


def test_human_labels_outrank_tentative_ones(store):
    # two precedents with the same change (adds του): one human, one from the pipeline
    store.set_classification("Mark.1.1.12-16", "b", "d", "Minor_Addition")  # υιου θεου → του υιου του θεου: adds του … του
    run = store.start_run(model="test-model")
    store.set_classification("Mark.1.1.12-16", "e", "d", "Minor_Addition", resp="rdgai", cert="medium", change=run)
    results = store.search_similar("Mark.1.1.12-16", "a", "d", limit=10)
    sources = [(result.active, result.passive, result.source, result.tentative) for result in results]
    human = next(index for index, item in enumerate(sources) if item[2] == "human")
    pipeline = next(index for index, item in enumerate(sources) if item[2] == "pipeline")
    assert human < pipeline
    assert results[pipeline].tentative is True
    # pipeline labels can be left out
    assert all(result.source == "human" for result in store.search_similar("Mark.1.1.12-16", "a", "d", sources=("human",)))


def test_rule_labels_are_not_results(mark_path):
    from rdgai.rules import SubreadingRule
    store = AnnotationStore(mark_path, annotator="RT")
    store.apply_rules(rules=[SubreadingRule("orthographic", "Orthography", "o")])
    store.set_classification("Mark.1.5.20-22", "a", "b", "Minor_Omission")
    results = store.search_similar("Mark.1.5.20-22", "a", "c", limit=100)
    assert results and all(result.source != "rule" for result in results)


def test_search_hides_test_units_in_evaluation_mode(labelled, mark_path):
    from rdgai import sampling
    units = [sampling.SampleUnit(id="Mark.1.1.12-16", role="test"), sampling.SampleUnit(id="Mark.1.2.2", role="example")]
    sampling.save_sample(sampling.Sample(
        seed=0, size=2, test_fraction=0.5, ignore_types=["defective"], eligible_count=10,
        created="2026-11-13T00:00:00Z", source=mark_path.name, units=units,
    ), labelled.sample_path)
    evaluation = AnnotationStore(mark_path, annotator="RT", evaluation=True)
    results = evaluation.search_similar("Mark.1.1.10", "a", "b", limit=100)
    assert all(result.unit_id != "Mark.1.1.12-16" for result in results)


def test_search_similar_through_mcp(labelled):
    data = call(build_server(labelled), "search_similar", unit_id="Mark.1.1.12-16", active="a", passive="d").structured_content
    top = data["results"][0]
    assert (top["unit_id"], top["active"], top["passive"], top["category"]) == ("Mark.1.1.12-16", "b", "a", "Minor_Addition")
    assert data["query"]["change"] == "second reading adds: του"


def test_search_unknown_pair(labelled):
    result = call(build_server(labelled), "search_similar", unit_id="Mark.1.1.12-16", active="a", passive="z")
    assert result.is_error
