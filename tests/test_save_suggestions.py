"""
The GUI's "Save all suggestions" and "Save all reasons" buttons, for the unit on screen (POST /api/save-suggestions).

- labels: every unlabelled pair whose suggestions agree on a category is labelled with it, with the suggestion's reason
  (recorded as copied from the suggester) and certainty. Labelled pairs and pairs with disagreeing suggestions are skipped.
- reasons: every pair the annotator labelled without a reason gets the reason of a suggestion for the same category.
  An existing reason is never replaced; a reason for another category is never copied.
Everything is written with one save.
"""
import pytest
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore
from rdgai.gui import create_app

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)

UNIT = "Mark.1.1.12-16"   # readings a-f; the GUI shows a → b, a → c, …


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def shown(client, unit_id=UNIT) -> dict:
    data = client.get(f"/api/unit/{unit_id}?sample=all").json()
    return {(pair["first"], pair["second"]): pair for pair in data["pairs"]}


def save(client, kind, unit_id=UNIT, pairs=None):
    pairs = pairs if pairs is not None else [[first, second] for first, second in shown(client, unit_id)]
    return client.post("/api/save-suggestions", json={"unit_id": unit_id, "kind": kind, "pairs": pairs})


def test_save_all_suggestions_as_labels(client, store):
    store.suggest_classification(UNIT, "a", "b", "Minor_Omission", justification="omits του", source="claude", cert="medium")
    store.suggest_classification(UNIT, "a", "c", "Minor_Substitution", justification="κυριου for θεου", source="claude")
    # made in the other direction: saved in the direction shown, with the inverse category and a note
    store.suggest_classification(UNIT, "c", "b", "Minor_Addition", justification="adds του", source="claude")
    # already labelled: not overwritten
    store.suggest_classification(UNIT, "a", "d", "Minor_Addition", justification="adds του", source="claude")
    store.set_classification(UNIT, "a", "d", "Major_Addition", justification="mine")
    # two sources that disagree: left for the annotator
    store.suggest_classification(UNIT, "a", "e", "Minor_Omission", source="claude")
    store.suggest_classification(UNIT, "a", "e", "Major_Omission", source="rdgai:model")

    response = save(client, "labels")
    assert response.status_code == 200, response.text
    result = response.json()
    assert sorted(map(tuple, result["written"])) == [("a", "b"), ("a", "c"), ("b", "c")]
    skipped = {tuple(item["pair"]): item["why"] for item in result["skipped"]}
    assert skipped[("a", "d")] == "labelled"
    assert skipped[("a", "e")] == "suggestions disagree"

    pairs = shown(client)
    assert pairs[("a", "b")]["category"] == "Minor_Omission"
    assert pairs[("a", "b")]["reason"] == "omits του"
    assert pairs[("a", "b")]["provenance"]["justification_source"] == "claude"
    assert pairs[("a", "b")]["provenance"]["cert"] == "medium"
    assert pairs[("a", "b")]["provenance"]["resp"] == "#RT"   # the annotator accepted it
    assert pairs[("a", "c")]["provenance"]["cert"] == "high"   # no certainty suggested: the GUI's default
    assert pairs[("b", "c")]["category"] == "Minor_Omission"   # the inverse of c → b's Minor_Addition
    assert pairs[("b", "c")]["reason"] == "Written for c → b: adds του"
    assert (pairs[("a", "d")]["category"], pairs[("a", "d")]["reason"]) == ("Major_Addition", "mine")
    assert pairs[("a", "e")]["category"] is None
    # the inverse labels are set too
    fresh = AnnotationStore(store.path, read_only=True)
    assert fresh.pair_info(fresh.get_pair(fresh.get_app(UNIT), "b", "a")).category == "Minor_Addition"


def test_save_all_reasons(client, store):
    store.suggest_classification(UNIT, "a", "b", "Minor_Omission", justification="omits του", source="claude")
    store.set_classification(UNIT, "a", "b", "Minor_Omission", cert="low")
    # a different category: its reason argues for another label, so it isn't copied
    store.suggest_classification(UNIT, "a", "c", "Minor_Substitution", justification="κυριου for θεου", source="claude")
    store.set_classification(UNIT, "a", "c", "Major_Substitution")
    # the annotator's own reason: never replaced
    store.suggest_classification(UNIT, "a", "d", "Minor_Addition", justification="adds του", source="claude")
    store.set_classification(UNIT, "a", "d", "Minor_Addition", justification="mine")
    # not labelled: nothing to attach a reason to
    store.suggest_classification(UNIT, "a", "e", "Minor_Omission", justification="omits υιου", source="claude")

    result = save(client, "reasons").json()
    assert [tuple(pair) for pair in result["written"]] == [("a", "b")]
    skipped = {tuple(item["pair"]): item["why"] for item in result["skipped"]}
    assert skipped[("a", "c")] == "different category"
    assert skipped[("a", "d")] == "has a reason"
    assert skipped[("a", "e")] == "not labelled"

    pairs = shown(client)
    assert (pairs[("a", "b")]["category"], pairs[("a", "b")]["reason"]) == ("Minor_Omission", "omits του")
    assert pairs[("a", "b")]["provenance"]["cert"] == "low"   # the label's certainty is kept
    assert pairs[("a", "b")]["provenance"]["justification_source"] == "claude"
    assert pairs[("a", "c")]["reason"] == ""
    assert pairs[("a", "d")]["reason"] == "mine"
    assert pairs[("a", "e")]["category"] is None


def test_one_save_for_all_the_pairs(client, store, monkeypatch):
    for second in "bcd":
        store.suggest_classification(UNIT, "a", second, "Minor_Omission", justification=f"a→{second}", source="claude")
    saves = []
    real_save = store.save
    monkeypatch.setattr(store, "save", lambda: (saves.append(1), real_save()))
    assert len(save(client, "labels").json()["written"]) == 3
    assert len(saves) == 1


def test_nothing_to_save(client, store, monkeypatch):
    saves = []
    monkeypatch.setattr(store, "save", lambda: saves.append(1))
    result = save(client, "labels").json()
    assert result["written"] == []
    assert saves == []


def test_suggestions_on_test_units_are_never_saved(client, store):
    """ Test units hide their suggestions in the GUI; saving them would leak them into the labels. """
    info = store.draw_sample(size=10, seed=3)
    test_unit = next(unit.id for unit in info.units if unit.role == "test")
    first = store.reading_ordered_pairs(store.get_app(test_unit))[0]
    store.suggest_classification(test_unit, first.active.n, first.passive.n, "Minor_Omission", justification="x", source="claude", allow_test_unit=True)
    result = save(client, "labels", unit_id=test_unit, pairs=[[first.active.n, first.passive.n]]).json()
    assert result["written"] == []


def test_bad_requests(client):
    assert save(client, "everything").status_code == 400
    assert client.post("/api/save-suggestions", json={"unit_id": "nope", "kind": "labels", "pairs": []}).status_code in (400, 404)


def test_read_only(mark_path):
    client = TestClient(create_app(AnnotationStore(mark_path, read_only=True)))
    assert save(client, "labels", pairs=[["a", "b"]]).status_code == 403
