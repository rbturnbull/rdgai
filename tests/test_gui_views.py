"""
GUI phase 4: the views for developing rdgai — queue filters, pairs by category, agreement between human labels and
suggestions, and editing the definitions.
"""
import pytest
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore
from rdgai.gui import create_app

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def queue_ids(client, **params):
    response = client.get("/api/queue", params={"sample": "all", **params})
    assert response.status_code == 200, response.text
    return [unit["id"] for unit in response.json()["units"]]


def first_pair(store, unit_id):
    pair = store.ordered_pairs(store.get_app(unit_id))[0]
    return pair.active.n, pair.passive.n


# ---------------------------------------------------------------------------
# queue filters
# ---------------------------------------------------------------------------

def test_queue_filter_by_category(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    assert queue_ids(client, category="Major_Omission") == ["Mark.1.1.10"]
    assert queue_ids(client, category="Transposition") == []


def test_queue_filter_by_certainty(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", cert="low")
    store.set_classification("Mark.1.2.2", *first_pair(store, "Mark.1.2.2"), "Major_Substitution", cert="high")
    assert queue_ids(client, cert="low") == ["Mark.1.1.10"]
    assert queue_ids(client, cert="high") == ["Mark.1.2.2"]


def test_queue_filter_by_source(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    change_id = store.start_run(model="test-model")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", resp="rdgai", cert="low", change=change_id)
    store.suggest_classification("Mark.1.2.2", *first_pair(store, "Mark.1.2.2"), "Major_Substitution", source="claude")
    assert queue_ids(client, source="human") == ["Mark.1.1.10"]
    assert queue_ids(client, source="pipeline") == ["Mark.1.1.12-16"]
    assert queue_ids(client, source="suggestion") == ["Mark.1.2.2"]


def test_queue_filter_disagreement(client, store):
    # disagreement: the human label differs from a suggestion for the same pair
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="claude")
    # agreement, with the suggestion made in the other direction
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    store.suggest_classification("Mark.1.1.12-16", "b", "a", "Minor_Addition", source="claude")
    assert queue_ids(client, disagreement="1") == ["Mark.1.1.10"]


def test_suggestion_filters_skip_test_units(client, store):
    """ The GUI hides suggestions on test units, so the queue filters must not reveal them either. """
    info = store.draw_sample(size=10, seed=3)
    test_unit = next(unit.id for unit in info.units if unit.role == "test")
    active, passive = first_pair(store, test_unit)
    store.set_classification(test_unit, active, passive, "Major_Omission")
    store.suggest_classification(test_unit, active, passive, "Transposition", source="claude", allow_test_unit=True)
    assert queue_ids(client, source="suggestion") == []
    assert queue_ids(client, disagreement="1") == []


def test_unit_keeps_the_new_filters(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", cert="low")
    data = client.get("/api/unit/Mark.1.1.10", params={"sample": "all", "category": "Major_Omission", "cert": "low"}).json()
    assert data["filters"]["category"] == "Major_Omission"
    assert data["filters"]["cert"] == "low"
    assert data["position"] == {"index": 0, "total": 1}


def test_invalid_filters(client):
    assert client.get("/api/queue", params={"source": "nobody"}).status_code == 400
    assert client.get("/api/queue", params={"cert": "certain"}).status_code == 400
    assert client.get("/api/queue", params={"category": "Nonsense"}).status_code == 400


# ---------------------------------------------------------------------------
# by category
# ---------------------------------------------------------------------------

def test_category_view_lists_pairs_with_context(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="omits χριστου")
    data = client.get("/api/category/Major_Omission").json()
    assert data["category"] == "Major_Omission"
    assert data["total"] == 1   # the other direction (b → a) is Major_Addition
    pair = data["pairs"][0]
    assert pair["unit_id"] == "Mark.1.1.10"
    assert (pair["first"], pair["second"]) == ("a", "b")
    assert pair["category"] == "Major_Omission"
    assert pair["source"] == "human"
    assert pair["context"] == {"before": "αρχη του ευαγγελιου ιησου", "lemma": "χριστου", "after": "υιου του θεου"}
    assert pair["provenance"]["justification"] == "omits χριστου"
    assert pair["sample_role"] is None


def test_category_view_lists_a_symmetric_pair_once(client, store):
    """ A category that is its own inverse labels both directions: the pair is listed once, in display direction. """
    store.set_classification("Mark.1.1.12-16", "b", "a", "Major_Substitution")
    data = client.get("/api/category/Major_Substitution").json()
    assert data["total"] == 1
    assert (data["pairs"][0]["first"], data["pairs"][0]["second"]) == ("a", "b")


def test_category_view_counts_for_every_category(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    data = client.get("/api/categories").json()
    counts = {row["category"]: row for row in data["categories"]}
    assert counts["Major_Omission"]["total"] == 1
    assert counts["Major_Addition"]["total"] == 1
    assert counts["Transposition"]["total"] == 0
    assert data["classified"] == 1


def test_category_view_source_filter(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    change_id = store.start_run(model="test-model")
    store.set_classification("Mark.1.1.12-16", "f", "a", "Major_Addition", resp="rdgai", cert="low", change=change_id)
    units = lambda **params: [pair["unit_id"] for pair in client.get("/api/category/Major_Omission", params=params).json()["pairs"]]
    assert units() == ["Mark.1.1.10", "Mark.1.1.12-16"]
    assert units(source="human") == ["Mark.1.1.10"]
    assert units(source="pipeline") == ["Mark.1.1.12-16"]


def test_category_view_paging(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.set_classification("Mark.1.1.12-16", "a", "f", "Major_Omission")
    data = client.get("/api/category/Major_Omission", params={"offset": 1, "limit": 1}).json()
    assert data["total"] == 2
    assert data["offset"] == 1
    assert [pair["unit_id"] for pair in data["pairs"]] == ["Mark.1.1.12-16"]


def test_relabel_in_place_from_the_category_view(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    pair = client.get("/api/category/Major_Omission").json()["pairs"][0]
    response = client.post("/api/label", json={
        "unit_id": pair["unit_id"], "pairs": [[pair["first"], pair["second"]]], "category": "Minor_Omission",
        "expected": {f"{pair['first']}|{pair['second']}": pair["category"]},
    })
    assert response.status_code == 200
    assert client.get("/api/category/Major_Omission").json()["total"] == 0
    assert client.get("/api/category/Minor_Omission").json()["total"] == 1


def test_category_view_unknown_category(client):
    assert client.get("/api/category/Nonsense").status_code == 400


# ---------------------------------------------------------------------------
# agreement
# ---------------------------------------------------------------------------

def test_agreement_between_human_labels_and_suggestions(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Major_Omission", justification="drops the article", source="claude")
    # not compared: a suggestion without a human label, and a human label without a suggestion
    store.suggest_classification("Mark.1.2.2", *first_pair(store, "Mark.1.2.2"), "Transposition", source="claude")
    store.set_classification("Mark.1.1.12-16", "a", "e", "Major_Substitution")

    data = client.get("/api/agreement").json()
    assert data["scope"] == "all"
    assert data["sources"] == [{"source": "claude", "count": 3}]
    assert data["source"] == "claude"
    assert data["compared"] == 2
    assert data["agreed"] == 1
    assert data["metrics"]["accuracy"] == 0.5
    assert data["metrics"]["confusion"]["labels"] == ["Minor_Omission", "Major_Omission"]
    assert data["metrics"]["confusion"]["matrix"] == [[0, 1], [0, 1]]
    [disagreement] = data["disagreements"]
    assert disagreement["unit_id"] == "Mark.1.1.12-16"
    assert (disagreement["first"], disagreement["second"]) == ("a", "b")
    assert disagreement["human"] == "Minor_Omission"
    assert disagreement["suggested"] == "Major_Omission"
    assert disagreement["justification"] == "drops the article"
    assert disagreement["first_text"] == "υιου του θεου"


def test_agreement_in_display_direction(client, store):
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    store.suggest_classification("Mark.1.1.12-16", "b", "a", "Minor_Addition", source="claude")
    data = client.get("/api/agreement").json()
    assert (data["compared"], data["agreed"]) == (1, 1)


def test_agreement_chooses_a_source(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="rdgai:test-model")
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", source="rdgai:test-model")

    data = client.get("/api/agreement").json()
    assert [row["source"] for row in data["sources"]] == ["rdgai:test-model", "claude"]  # most suggestions first
    assert data["source"] == "rdgai:test-model"
    assert (data["compared"], data["agreed"]) == (1, 0)

    data = client.get("/api/agreement", params={"source": "claude"}).json()
    assert (data["compared"], data["agreed"]) == (1, 1)


def test_agreement_leaves_out_test_units_unless_asked(client, store):
    info = store.draw_sample(size=10, seed=3)
    test_unit = next(unit.id for unit in info.units if unit.role == "test")
    example_unit = next(unit.id for unit in info.units if unit.role == "example")
    for unit_id in (test_unit, example_unit):
        active, passive = first_pair(store, unit_id)
        store.set_classification(unit_id, active, passive, "Major_Substitution")
        store.suggest_classification(unit_id, active, passive, "Major_Substitution", source="claude", allow_test_unit=True)

    data = client.get("/api/agreement").json()
    assert data["scope"] == "example"
    assert data["compared"] == 1
    data = client.get("/api/agreement", params={"scope": "sampled"}).json()
    assert data["compared"] == 2


def test_agreement_without_suggestions(client):
    data = client.get("/api/agreement").json()
    assert data["sources"] == []
    assert data["compared"] == 0
    assert data["disagreements"] == []


# ---------------------------------------------------------------------------
# editing the definitions
# ---------------------------------------------------------------------------

def test_config_says_whether_definitions_can_be_edited(client, mark_path):
    assert client.get("/api/config").json()["definitions_editable"] is True
    locked = TestClient(create_app(AnnotationStore(mark_path, lock_definitions=True)))
    assert locked.get("/api/config").json()["definitions_editable"] is False
    read_only = TestClient(create_app(AnnotationStore(mark_path, read_only=True)))
    assert read_only.get("/api/config").json()["definitions_editable"] is False


def test_update_category_description(client, store):
    before = client.get("/api/config").json()["definitions_hash"]
    response = client.post("/api/definitions/category", json={"name": "Transposition", "description": "Same words, new order."})
    assert response.status_code == 200, response.text
    assert response.json()["definitions_hash"] != before
    config = client.get("/api/config").json()
    assert config["definitions_hash"] == response.json()["definitions_hash"]
    assert next(button for button in config["buttons"] if button["name"] == "Transposition")["description"] == "Same words, new order."


def test_rename_category_keeps_labels(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    response = client.post("/api/definitions/category", json={"name": "Major_Omission", "new_name": "Big_Omission"})
    assert response.status_code == 200, response.text
    assert client.get("/api/category/Big_Omission").json()["total"] == 1


def test_update_guidance(client):
    response = client.post("/api/definitions/guidance", json={"guidance": "Classify by meaning."})
    assert response.status_code == 200, response.text
    assert client.get("/api/config").json()["guidance"] == "Classify by meaning."


def test_add_and_remove_category(client, store):
    response = client.post("/api/definitions/add", json={
        "name": "Harmonisation", "description": "Brought into line with a parallel.",
    })
    assert response.status_code == 200, response.text
    assert "Harmonisation" in [button["name"] for button in client.get("/api/config").json()["buttons"]]

    store.set_classification("Mark.1.1.10", "a", "b", "Harmonisation")
    response = client.post("/api/definitions/remove", json={"name": "Harmonisation"})
    assert response.status_code == 400   # in use: needs a category to reassign to
    response = client.post("/api/definitions/remove", json={"name": "Harmonisation", "reassign_to": "Major_Substitution"})
    assert response.status_code == 200, response.text
    assert "Harmonisation" not in [button["name"] for button in client.get("/api/config").json()["buttons"]]
    # both directions are reassigned; the view lists the pair once
    assert client.get("/api/category/Major_Substitution").json()["total"] == 1


def test_add_category_with_an_inverse(client):
    response = client.post("/api/definitions/add", json={
        "name": "Gloss_Added", "description": "Adds a gloss.", "inverse_name": "Gloss_Omitted", "inverse_description": "Omits a gloss.",
    })
    assert response.status_code == 200, response.text
    buttons = {button["name"]: button for button in client.get("/api/config").json()["buttons"]}
    assert buttons["Gloss_Added"]["inverse"] == "Gloss_Omitted"


def test_definitions_locked(mark_path):
    client = TestClient(create_app(AnnotationStore(mark_path, lock_definitions=True)))
    response = client.post("/api/definitions/guidance", json={"guidance": "x"})
    assert response.status_code == 403
    client = TestClient(create_app(AnnotationStore(mark_path, read_only=True)))
    response = client.post("/api/definitions/category", json={"name": "Transposition", "description": "x"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def test_page_has_the_views(client):
    page = client.get("/").text
    for view in ("annotate", "category", "agreement"):
        assert f'data-view="{view}"' in page
