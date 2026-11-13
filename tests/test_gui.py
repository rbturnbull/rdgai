"""
The annotation GUI: button layout generated from an arbitrary interpGrp, the JSON API used by the page,
and sharing one AnnotationStore with the MCP server.
"""
import shutil

import pytest
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore, Category
from rdgai.gui import create_app, button_layout, KEYS
from rdgai.mcp_server import build_server

from .util import TEST_DATA_DIR
from .test_mcp import call, mark_path, store  # noqa: F401 (fixtures)


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def make_categories(*names, groups=None):
    groups = groups or {}
    return [Category(name=name, description=f"Definition of {name}", inverse=name, group=groups.get(name, "")) for name in names]


# ---------------------------------------------------------------------------
# button layout
# ---------------------------------------------------------------------------

def layout(buttons):
    return [(button.name, button.row, button.column, button.key) for button in buttons]


def test_layout_plain_grid_in_document_order():
    buttons = button_layout(make_categories("category1", "category2", "category3"))
    assert layout(buttons) == [
        ("category1", 0, 0, "1"),
        ("category2", 0, 1, "2"),
        ("category3", 0, 2, "3"),
    ]
    assert buttons[0].description == "Definition of category1"


MARK_CATEGORIES = [
    "Orthography", "Minor_Inflection", "Major_Inflection", "Minor_Substitution", "Major_Substitution",
    "Minor_Addition", "Minor_Omission", "Major_Addition", "Major_Omission", "Transposition",
]


def test_layout_grouped_by_prefix_aligns_columns():
    buttons = button_layout(make_categories(*MARK_CATEGORIES), group_by_prefix="_")
    assert layout(buttons) == [
        ("Orthography", 0, 0, "1"),
        ("Transposition", 0, 1, "2"),
        ("Minor_Inflection", 1, 0, "3"),
        ("Minor_Substitution", 1, 1, "4"),
        ("Minor_Addition", 1, 2, "5"),
        ("Minor_Omission", 1, 3, "6"),
        ("Major_Inflection", 2, 0, "7"),
        ("Major_Substitution", 2, 1, "8"),
        ("Major_Addition", 2, 2, "9"),
        ("Major_Omission", 2, 3, "0"),
    ]
    assert [button.group for button in buttons[:3]] == ["", "", "Minor"]


def test_layout_grouped_by_interp_type():
    categories = make_categories("A", "B", "C", "D", groups={"A": "minor", "B": "major", "C": "minor"})
    assert layout(button_layout(categories)) == [
        ("A", 0, 0, "1"),
        ("C", 0, 1, "2"),
        ("B", 1, 0, "3"),
        ("D", 2, 0, "4"),
    ]


def test_layout_keys_beyond_ten():
    buttons = button_layout(make_categories(*[f"c{index}" for index in range(23)]))
    assert [button.key for button in buttons] == KEYS[:20] + [None, None, None]
    assert KEYS[9] == "0"
    assert KEYS[10] == "shift+1"
    assert KEYS[19] == "shift+0"


def test_layout_from_an_arbitrary_interpgrp(tmp_path):
    path = tmp_path / "inverses.xml"
    shutil.copy(TEST_DATA_DIR / "inverses.xml", path)
    store = AnnotationStore(path, read_only=True)
    config = TestClient(create_app(store)).get("/api/config").json()
    assert [button["name"] for button in config["buttons"]] == ["Addition", "Omission", "Major_Addition", "Major_Omission", "Substitution"]
    assert config["buttons"][0]["inverse"] == "Omission"


# ---------------------------------------------------------------------------
# config and page
# ---------------------------------------------------------------------------

def test_config(client, store):
    config = client.get("/api/config").json()
    assert [button["name"] for button in config["buttons"]] == MARK_CATEGORIES
    assert config["buttons"][0]["description"].startswith("The same words")
    assert config["definitions_hash"] == store.definitions().definitions_hash
    assert "Decision order" in config["guidance"]
    assert config["annotator"] == "RT"
    assert config["read_only"] is False
    assert config["certainty"] == ["high", "medium", "low"]
    assert config["file"] == "mark_sample.xml"


def test_config_group_by_prefix(store):
    config = TestClient(create_app(store, group_by_prefix="_")).get("/api/config").json()
    assert config["buttons"][2]["name"] == "Minor_Inflection"
    assert config["buttons"][2]["row"] == 1


def test_page_and_static_files(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>rdgai annotate</title>" in page.text
    assert 'src="static/app.js"' in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/logic.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# ---------------------------------------------------------------------------
# queue and unit view
# ---------------------------------------------------------------------------

def test_queue_defaults_to_all_units_without_a_sample(client):
    queue = client.get("/api/queue").json()
    assert queue["total"] == 10
    assert queue["filters"]["sample"] == "all"
    assert queue["units"][0]["id"] == "Mark.1.1.10"


def test_queue_defaults_to_the_sample(client, store):
    info = store.draw_sample(size=4, seed=1)
    queue = client.get("/api/queue").json()
    assert queue["filters"]["sample"] == "sampled"
    assert [unit["id"] for unit in queue["units"]] == [unit.id for unit in info.units]
    assert queue["units"][0]["sample_role"] in ("example", "test")


def test_queue_filters(client, store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    queue = client.get("/api/queue", params={"sample": "all", "status": "unclassified", "verse": "Mark.1.1"}).json()
    assert [unit["id"] for unit in queue["units"]] == ["Mark.1.1.12-16"]


def test_unit_view(client):
    data = client.get("/api/unit/Mark.1.1.12-16", params={"sample": "all"}).json()
    assert data["id"] == "Mark.1.1.12-16"
    assert data["sample_role"] is None
    assert data["position"] == {"index": 1, "total": 10}
    assert data["prev"] == "Mark.1.1.10"
    assert data["next"] == "Mark.1.2.2"
    assert data["next_unclassified"] == "Mark.1.2.2"
    assert data["progress"] == {"pairs_done": 0, "pairs_total": 15}
    assert data["context"] == {"before": "αρχη του ευαγγελιου ιησου χριστου", "lemma": "υιου του θεου", "after": ""}

    readings = {reading["id"]: reading for reading in data["readings"]}
    assert readings["a"]["witness_count"] == 151
    assert readings["f"]["text"] == "OMIT"
    assert readings["b"]["witnesses"] == ["03", "05", "019", "032", "732", "2148"]

    # pairs in display order: better-attested first
    assert [(pair["first"], pair["second"]) for pair in data["pairs"]][:3] == [("a", "b"), ("a", "e"), ("a", "f")]
    first = data["pairs"][0]
    assert first["first_text"] == "υιου του θεου"
    assert first["second_text"] == "υιου θεου"
    assert first["category"] is None
    assert first["provenance"] is None
    assert first["suggestions"] == []
    assert first["reason"] == ""


def test_unit_not_in_queue_has_no_position(client, store):
    store.draw_sample(size=2, seed=1)
    queue_ids = [unit["id"] for unit in client.get("/api/queue").json()["units"]]
    outside = next(unit.id for unit in store.list_units(limit=500).units if unit.id not in queue_ids)
    data = client.get(f"/api/unit/{outside}").json()
    assert data["position"] is None
    assert data["next"] == queue_ids[0]


def test_next_unclassified_wraps_and_skips_classified(client, store):
    for unit in store.list_units(limit=500).units:
        if unit.id not in ("Mark.1.1.10", "Mark.1.5.20-22"):
            store.set_classifications(unit.id, [(pair.active, pair.passive) for pair in store.get_unit(unit.id).pairs], "Major_Substitution")
    data = client.get("/api/unit/Mark.1.2.2", params={"sample": "all"}).json()
    assert data["next_unclassified"] == "Mark.1.5.20-22"
    data = client.get("/api/unit/Mark.1.5.20-22", params={"sample": "all"}).json()
    assert data["next_unclassified"] == "Mark.1.1.10"


def test_unit_view_notes_link_to_units(tmp_path):
    path = tmp_path / "ecm_notes.xml"
    shutil.copy(TEST_DATA_DIR / "ecm_notes.xml", path)
    client = TestClient(create_app(AnnotationStore(path, annotator="RT")))
    data = client.get("/api/unit/Mark.1.2.4").json()
    readings = {reading["id"]: reading for reading in data["readings"]}
    assert readings["a"]["text"] == "θεου"
    assert readings["a"]["notes"][0]["references"][0]["unit_id"] == "Mark.1.2.8"
    assert data["context"]["lemma"] == "θεου"


def test_unknown_unit(client):
    response = client.get("/api/unit/Mark.99.1.1")
    assert response.status_code == 404
    assert "No variation unit" in response.json()["error"]


# ---------------------------------------------------------------------------
# labelling
# ---------------------------------------------------------------------------

def test_label_saves_first_to_second_with_inverse(client, store):
    response = client.post("/api/label", json={
        "unit_id": "Mark.1.2.8-14", "pairs": [["e", "b"]], "category": "Minor_Addition",
        "cert": "medium", "justification": "adds the article", "expected": {"e|b": None},
    })
    assert response.status_code == 200, response.text
    pair = response.json()["pairs"][0]
    assert (pair["first"], pair["second"], pair["category"]) == ("e", "b", "Minor_Addition")
    assert pair["provenance"]["cert"] == "medium"
    assert pair["provenance"]["resp"] == "#RT"
    assert pair["reason"] == "adds the article"

    # the store saved the inverse for the reverse direction
    by_direction = {(pair.active, pair.passive): pair for pair in store.get_unit("Mark.1.2.8-14").pairs}
    assert by_direction[("b", "e")].category == "Minor_Omission"


def test_label_replaces_not_adds(client, mark_path):
    for category in ("Minor_Omission", "Major_Omission"):
        client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": category})
    from .test_mcp import relations_in_file
    relations = relations_in_file(mark_path, "Mark.1.1.10")
    assert len(relations) == 2
    assert {relation["ana"] for relation in relations} == {"#Major_Omission", "#Major_Addition"}


def test_label_null_category_clears(client, store):
    client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Major_Omission"})
    response = client.post("/api/label", json={
        "unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": None, "expected": {"a|b": "Major_Omission"},
    })
    assert response.status_code == 200
    assert response.json()["pairs"][0]["category"] is None
    assert store.get_unit("Mark.1.1.10").status == "unclassified"


def test_bulk_label(client, store):
    response = client.post("/api/label", json={
        "unit_id": "Mark.1.1.12-16", "pairs": [["a", "c"], ["b", "c"], ["e", "c"]], "category": "Major_Substitution",
        "expected": {"a|c": None, "b|c": None, "e|c": None},
    })
    assert response.status_code == 200
    assert [pair["category"] for pair in response.json()["pairs"]] == ["Major_Substitution"] * 3
    assert store.get_unit("Mark.1.1.12-16").status == "partial"


def test_bulk_clear(client, store):
    client.post("/api/label", json={"unit_id": "Mark.1.1.12-16", "pairs": [["a", "c"], ["b", "c"]], "category": "Major_Substitution"})
    client.post("/api/label", json={"unit_id": "Mark.1.1.12-16", "pairs": [["a", "c"], ["b", "c"]], "category": None})
    assert store.get_unit("Mark.1.1.12-16").status == "unclassified"


def test_label_conflict(client, store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")  # e.g. by Claude through MCP
    before = mark_path.read_bytes()
    response = client.post("/api/label", json={
        "unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Minor_Omission", "expected": {"a|b": None},
    })
    assert response.status_code == 409
    assert response.json()["current"] == {"a|b": "Major_Omission"}
    assert mark_path.read_bytes() == before


def test_label_errors(client):
    response = client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Nonsense"})
    assert response.status_code == 400
    assert "Unknown category" in response.json()["error"]
    response = client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [], "category": "Major_Omission"})
    assert response.status_code == 400


def test_certainty(client, store):
    client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Major_Omission"})
    response = client.post("/api/certainty", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "cert": "low"})
    assert response.status_code == 200
    assert response.json()["pairs"][0]["provenance"]["cert"] == "low"
    response = client.post("/api/certainty", json={"unit_id": "Mark.1.1.12-16", "pairs": [["a", "b"]], "cert": "low"})
    assert response.status_code == 400


def test_read_only(mark_path):
    client = TestClient(create_app(AnnotationStore(mark_path, read_only=True)))
    assert client.get("/api/config").json()["read_only"] is True
    response = client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Major_Omission"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------

def test_suggestions_shown_with_prefilled_reason(client, store):
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits του", source="claude")
    pair = client.get("/api/unit/Mark.1.1.12-16").json()["pairs"][0]
    assert [(suggestion["category"], suggestion["source"]) for suggestion in pair["suggestions"]] == [("Minor_Omission", "claude")]
    assert pair["suggestions"][0]["justification"] == "omits του"
    assert pair["reason"] == "omits του"


def test_suggestion_shown_in_display_direction(client, store):
    store.suggest_classification("Mark.1.1.12-16", "b", "a", "Minor_Addition", source="claude")
    pair = client.get("/api/unit/Mark.1.1.12-16").json()["pairs"][0]
    assert (pair["first"], pair["second"]) == ("a", "b")
    assert pair["suggestions"][0]["category"] == "Minor_Omission"


def test_reason_is_own_justification_once_labelled(client, store):
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="omits του", source="claude")
    client.post("/api/label", json={"unit_id": "Mark.1.1.12-16", "pairs": [["a", "b"]], "category": "Minor_Omission", "justification": "article only"})
    pair = client.get("/api/unit/Mark.1.1.12-16").json()["pairs"][0]
    assert pair["reason"] == "article only"


def test_suggestions_hidden_on_test_units(client, store):
    info = store.draw_sample(size=10, seed=3)
    test_unit = next(unit.id for unit in info.units if unit.role == "test")
    example_unit = next(unit.id for unit in info.units if unit.role == "example")
    for unit_id in (test_unit, example_unit):
        first = store.ordered_pairs(store.get_app(unit_id))[0]
        store.suggest_classification(unit_id, first.active.n, first.passive.n, "Major_Substitution", justification="x", source="claude")

    test_pair = client.get(f"/api/unit/{test_unit}").json()["pairs"][0]
    assert test_pair["suggestions"] == []
    assert test_pair["reason"] == ""
    example_pair = client.get(f"/api/unit/{example_unit}").json()["pairs"][0]
    assert example_pair["suggestions"][0]["category"] == "Major_Substitution"


# ---------------------------------------------------------------------------
# one store shared with the MCP server
# ---------------------------------------------------------------------------

def test_gui_labels_visible_through_mcp_and_back(store):
    client = TestClient(create_app(store))
    server = build_server(store)

    client.post("/api/label", json={"unit_id": "Mark.1.1.10", "pairs": [["a", "b"]], "category": "Major_Omission"})
    unit = call(server, "get_unit", unit_id="Mark.1.1.10").structured_content
    assert unit["pairs"][0]["category"] == "Major_Omission"

    call(server, "set_classification", unit_id="Mark.1.2.2", active="a", passive="b", category="Minor_Substitution")
    pair = client.get("/api/unit/Mark.1.2.2").json()["pairs"][0]
    assert pair["category"] == "Minor_Substitution"


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def test_cli_options():
    from typer.testing import CliRunner
    from rdgai.main import app
    runner = CliRunner()
    annotate = runner.invoke(app, ["annotate", "--help"]).output
    for option in ("--port", "--annotator", "--group-by-prefix", "--read-only"):
        assert option in annotate
    assert "--gui-port" in runner.invoke(app, ["serve-mcp", "--help"]).output
    assert "--group-by-prefix" in runner.invoke(app, ["serve-mcp", "--help"]).output
