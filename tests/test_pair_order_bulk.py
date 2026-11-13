"""
Display order of pairs (better-attested reading first), bulk labelling, certainty-only changes, and refusing
writes that are based on a stale view of a pair (so another writer's label is never silently overwritten).
"""
import pytest

from rdgai.annotation import AnnotationStore, AnnotationError, ConflictError

from .test_mcp import call, mark_path, store  # noqa: F401 (fixtures)
from rdgai.mcp_server import build_server


def directions(pairs):
    return [(pair.active.n, pair.passive.n) for pair in pairs]


# ---------------------------------------------------------------------------
# display order
# ---------------------------------------------------------------------------

def test_ordered_pairs_better_attested_first(store):
    # witnesses: a 151, b 6, c 1, d 2, e 5, f 5  ->  a, b, e, f, d, c (ties in document order)
    app = store.get_app("Mark.1.1.12-16")
    assert directions(store.ordered_pairs(app)) == [
        ("a", "b"), ("a", "e"), ("a", "f"), ("a", "d"), ("a", "c"),
        ("b", "e"), ("b", "f"), ("b", "d"), ("b", "c"),
        ("e", "f"), ("e", "d"), ("e", "c"),
        ("f", "d"), ("f", "c"),
        ("d", "c"),
    ]


def test_ordered_pairs_when_base_text_is_less_attested(store):
    # witnesses: a 11, b 29, c 3, e 126  ->  e, b, a, c
    app = store.get_app("Mark.1.2.8-14")
    assert directions(store.ordered_pairs(app)) == [
        ("e", "b"), ("e", "a"), ("e", "c"), ("b", "a"), ("b", "c"), ("a", "c"),
    ]


def test_ordered_pairs_skip_excluded_readings(mark_path):
    store = AnnotationStore(mark_path, ignore_types=["orthographic"])
    app = store.get_app("Mark.1.5.20-22")
    # a 99, b 28, c 1 (ao and bo are excluded)
    assert directions(store.ordered_pairs(app)) == [("a", "b"), ("a", "c"), ("b", "c")]


def test_get_unit_in_attestation_order(store):
    store.set_classification("Mark.1.2.8-14", "a", "e", "Major_Substitution")
    store.set_classification("Mark.1.2.8-14", "b", "a", "Minor_Omission")
    unit = store.get_unit("Mark.1.2.8-14", pair_order="attestation")
    pairs = {(pair.active, pair.passive): pair for pair in unit.pairs}
    assert list(pairs) == [("e", "b"), ("e", "a"), ("e", "c"), ("b", "a"), ("b", "c"), ("a", "c")]
    # categories are reported for the displayed direction
    assert pairs[("e", "a")].category == "Major_Substitution"
    assert pairs[("b", "a")].category == "Minor_Omission"
    assert pairs[("b", "a")].inverse_category == "Minor_Addition"

    # document order is still the default
    assert [(pair.active, pair.passive) for pair in store.get_unit("Mark.1.2.8-14").pairs][0] == ("a", "b")


def test_get_unit_pair_order_through_mcp(store):
    data = call(build_server(store), "get_unit", unit_id="Mark.1.2.8-14", pair_order="attestation").structured_content
    assert (data["pairs"][0]["active"], data["pairs"][0]["passive"]) == ("e", "b")


def test_saving_in_display_direction_sets_inverse(store):
    app = store.get_app("Mark.1.2.8-14")
    first = store.ordered_pairs(app)[0]
    result = store.set_classification("Mark.1.2.8-14", first.active.n, first.passive.n, "Minor_Addition")
    assert (result.pair.active, result.pair.passive) == ("e", "b")
    assert result.inverse.category == "Minor_Omission"


# ---------------------------------------------------------------------------
# bulk labelling
# ---------------------------------------------------------------------------

def test_set_classifications_bulk(store, mark_path, monkeypatch):
    saves = []
    original_save = store.save
    monkeypatch.setattr(store, "save", lambda: (saves.append(1), original_save()))

    store.set_classification("Mark.1.1.12-16", "a", "c", "Minor_Substitution")
    saves.clear()
    results = store.set_classifications(
        "Mark.1.1.12-16", [("a", "c"), ("b", "c"), ("d", "c")], "Major_Substitution", justification="different noun", cert="medium",
    )
    assert len(saves) == 1  # one save for the whole batch
    assert [(result.pair.active, result.pair.passive, result.pair.category) for result in results] == [
        ("a", "c", "Major_Substitution"), ("b", "c", "Major_Substitution"), ("d", "c", "Major_Substitution"),
    ]
    assert all(result.inverse.category == "Major_Substitution" for result in results)
    assert all(result.pair.provenance.cert == "medium" for result in results)

    reloaded = AnnotationStore(mark_path, read_only=True)
    categories = {(pair.active, pair.passive): pair.category for pair in reloaded.get_unit("Mark.1.1.12-16").pairs}
    assert categories[("a", "c")] == "Major_Substitution"  # replaced, not added
    assert categories[("c", "d")] == "Major_Substitution"


def test_bulk_is_all_or_nothing(store, mark_path):
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match="has no reading 'z'"):
        store.set_classifications("Mark.1.1.12-16", [("a", "c"), ("b", "z")], "Major_Substitution")
    assert mark_path.read_bytes() == before
    assert store.get_unit("Mark.1.1.12-16").status == "unclassified"


def test_bulk_needs_pairs(store):
    with pytest.raises(AnnotationError, match="No pairs"):
        store.set_classifications("Mark.1.1.12-16", [], "Major_Substitution")


def test_set_certainty_bulk(store, mark_path):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits", cert="high")
    store.set_classification("Mark.1.1.12-16", "a", "c", "Major_Substitution", cert="high")
    results = store.set_certainty("Mark.1.1.12-16", [("a", "b"), ("c", "a")], cert="low")
    assert [result.pair.provenance.cert for result in results] == ["low", "low"]
    assert [result.inverse.provenance.cert for result in results] == ["low", "low"]

    pairs = {(pair.active, pair.passive): pair for pair in AnnotationStore(mark_path, read_only=True).get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "b")].category == "Minor_Omission"
    assert pairs[("a", "b")].provenance.cert == "low"
    assert pairs[("a", "b")].provenance.justification == "omits"
    assert pairs[("a", "c")].provenance.cert == "low"


def test_set_certainty_requires_labels(store, mark_path):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match="not classified"):
        store.set_certainty("Mark.1.1.12-16", [("a", "b"), ("a", "c")], cert="low")
    assert mark_path.read_bytes() == before
    with pytest.raises(AnnotationError, match="cert"):
        store.set_certainty("Mark.1.1.12-16", [("a", "b")], cert="sure")


# ---------------------------------------------------------------------------
# refusing stale writes
# ---------------------------------------------------------------------------

def test_expected_category_matches(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", expected_category=None)
    store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission", expected_category="Major_Omission")
    # the expectation is for the direction given
    store.set_classification("Mark.1.1.10", "b", "a", "Major_Addition", expected_category="Minor_Addition")


def test_expected_category_conflict(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    before = mark_path.read_bytes()
    with pytest.raises(ConflictError, match="Major_Omission") as error:
        store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission", expected_category=None)
    assert error.value.current == {("a", "b"): "Major_Omission"}
    with pytest.raises(ConflictError):
        store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission", expected_category="Minor_Omission")
    with pytest.raises(ConflictError):
        store.clear_classification("Mark.1.1.10", "a", "b", expected_category=None)
    assert mark_path.read_bytes() == before
    assert issubclass(ConflictError, AnnotationError)


def test_no_expectation_means_no_check(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.set_classification("Mark.1.1.10", "a", "b", "Minor_Omission")
    store.clear_classification("Mark.1.1.10", "a", "b")


def test_bulk_expected_categories(store, mark_path):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    before = mark_path.read_bytes()
    with pytest.raises(ConflictError) as error:
        store.set_classifications(
            "Mark.1.1.12-16", [("a", "b"), ("a", "c")], "Major_Substitution",
            expected={("a", "b"): None, ("a", "c"): None},
        )
    assert error.value.current == {("a", "b"): "Minor_Omission"}
    assert mark_path.read_bytes() == before

    store.set_classifications(
        "Mark.1.1.12-16", [("a", "b"), ("a", "c")], "Major_Substitution",
        expected={("a", "b"): "Minor_Omission", ("a", "c"): None},
    )


# ---------------------------------------------------------------------------
# writing many different decisions with one save (used by the pipeline)
# ---------------------------------------------------------------------------

def test_write_decisions(store, monkeypatch):
    saves = []
    original_save = store.save
    monkeypatch.setattr(store, "save", lambda: (saves.append(1), original_save()))
    run = store.start_run(model="test-model")
    saves.clear()
    store.set_classification("Mark.1.1.12-16", "a", "c", "Minor_Substitution")  # a human label made meanwhile
    saves.clear()
    result = store.write_decisions(
        "Mark.1.1.12-16",
        [
            ("a", "b", "Minor_Omission", "omits του", "high"),
            ("a", "c", "Major_Substitution", "x", "low"),   # labelled meanwhile: skipped
            ("e", "a", "Minor_Addition", "adds υιου", "medium"),
        ],
        resp="rdgai", change=run,
    )
    assert len(saves) == 1
    assert result.written == [("a", "b"), ("e", "a")]
    assert result.skipped == [("a", "c")]
    pairs = {(pair.active, pair.passive): pair for pair in store.get_unit("Mark.1.1.12-16").pairs}
    assert (pairs[("a", "b")].category, pairs[("a", "b")].provenance.cert, pairs[("a", "b")].source) == ("Minor_Omission", "high", "pipeline")
    assert pairs[("a", "c")].category == "Minor_Substitution"
    assert pairs[("a", "e")].category == "Minor_Omission"  # inverse of e → a Minor_Addition
    assert pairs[("a", "b")].provenance.change.split()[0] == f"#{run}"


def test_write_decisions_validates_everything_first(store, mark_path):
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match="Unknown category"):
        store.write_decisions("Mark.1.1.12-16", [("a", "b", "Minor_Omission", "", "high"), ("a", "c", "Nope", "", "high")], resp="rdgai")
    with pytest.raises(AnnotationError, match="cert"):
        store.write_decisions("Mark.1.1.12-16", [("a", "b", "Minor_Omission", "", "certain")], resp="rdgai")
    assert mark_path.read_bytes() == before
