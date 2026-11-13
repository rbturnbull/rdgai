"""
A computed description of the change from the first reading to the second (in display order). It is a reading aid
for the annotator, never a label; a warning flags labels that clearly contradict it.
"""
import pytest
from starlette.testclient import TestClient

from rdgai.diff import describe_change, label_warning
from rdgai.gui import create_app
from rdgai.mcp_server import build_server

from .test_mcp import call, mark_path, store  # noqa: F401 (fixtures)


@pytest.mark.parametrize("first, second, kind, text", [
    # Mark.2.27.20 in display order: OMIT (151 witnesses) → και (16)
    ("", "και", "addition", "second reading adds: και"),
    ("μετανοιας", "", "omission", "second reading omits: μετανοιας"),
    ("υιου του θεου", "υιου θεου", "omission", "second reading omits: του"),
    ("υιου θεου", "του υιου του θεου", "addition", "second reading adds: του … του"),
    ("και εδησεν αυτον εν φυλακη", "εν φυλακη και εδησεν αυτον", "reordering", "same words, reordered"),
    ("και εδησεν αυτον εν φυλακη", "και εδησεν αυτον εις φυλακην", "substitution", "different words: εν φυλακη → εις φυλακην"),
    ("καθως", "ως", "substitution", "different words: καθως → ως"),
    ("εξηλθεν και απηλθεν", "εξελθων απηλθεν", "substitution", "different words: εξηλθεν και → εξελθων"),
    ("λεγει αυτω", "λεγει αυτω", "identical", "readings are identical"),
    ("", "", "identical", "readings are identical"),
])
def test_describe_change(first, second, kind, text):
    change = describe_change(first, second)
    assert (change.kind, change.text) == (kind, text)


def test_describe_change_lists_the_words():
    change = describe_change("υιου θεου", "του υιου του θεου")
    assert change.added == ["του", "του"]
    assert change.removed == []
    change = describe_change("και εδησεν αυτον εν φυλακη", "και εδησεν αυτον εις φυλακην")
    assert change.removed == ["εν", "φυλακη"]
    assert change.added == ["εις", "φυλακην"]


@pytest.mark.parametrize("category, first, second, warns", [
    ("Minor_Omission", "", "και", True),       # the tester's mistake on Mark.2.27.20
    ("Major_Omission", "", "και", True),
    ("Minor_Addition", "", "και", False),
    ("Minor_Addition", "μετανοιας", "", True),
    ("Major_Omission", "μετανοιας", "", False),
    ("Minor_Substitution", "", "και", False),  # other categories are not checked
    ("Minor_Omission", "καθως", "ως", False),  # a substitution: no clear contradiction
    ("Deletion", "", "και", True),             # other wording for the same idea
    ("Insertion", "μετανοιας", "", True),
    (None, "", "και", False),
])
def test_label_warning(category, first, second, warns):
    warning = label_warning(category, describe_change(first, second))
    assert bool(warning) == warns
    if warns:
        assert category in warning


def test_warning_text():
    warning = label_warning("Minor_Omission", describe_change("", "και"))
    assert warning == "Minor_Omission is for an omission, but the second reading adds: και."


# ---------------------------------------------------------------------------
# in the MCP and the GUI
# ---------------------------------------------------------------------------

def test_get_unit_describes_each_pair_in_its_direction(store):
    pairs = {(pair.active, pair.passive): pair for pair in store.get_unit("Mark.1.1.12-16").pairs}
    assert pairs[("a", "b")].change.text == "second reading omits: του"
    assert pairs[("a", "d")].change.text == "second reading adds: του"
    assert pairs[("a", "f")].change.kind == "omission"
    assert pairs[("a", "b")].warning is None


def test_mcp_reports_a_warning_for_a_contradicting_label(store):
    store.set_classification("Mark.1.1.12-16", "a", "d", "Minor_Omission")
    unit = call(build_server(store), "get_unit", unit_id="Mark.1.1.12-16").structured_content
    pair = next(pair for pair in unit["pairs"] if (pair["active"], pair["passive"]) == ("a", "d"))
    assert pair["change"] == {"kind": "addition", "text": "second reading adds: του"}
    assert pair["warning"] == "Minor_Omission is for an omission, but the second reading adds: του."


def test_gui_describes_pairs_in_display_order(store):
    client = TestClient(create_app(store))
    # witnesses: a 11, b 29, c 3, e 126 -> display e→b, e→a, e→c, b→a, ...
    # b = ησαια τω προφητη, a = τω ησαια τω προφητη, e = τοις προφηταις
    pairs = {(pair["first"], pair["second"]): pair for pair in client.get("/api/unit/Mark.1.2.8-14").json()["pairs"]}
    assert pairs[("b", "a")]["change"]["text"] == "second reading adds: τω"
    assert pairs[("e", "b")]["change"]["kind"] == "substitution"
    assert pairs[("b", "a")]["warning"] is None


def test_gui_warning_after_saving_a_contradicting_label(store):
    client = TestClient(create_app(store))
    response = client.post("/api/label", json={"unit_id": "Mark.1.2.8-14", "pairs": [["b", "a"]], "category": "Minor_Omission"})
    assert response.status_code == 200  # never blocked
    pair = response.json()["pairs"][0]
    assert pair["category"] == "Minor_Omission"
    assert pair["warning"] == "Minor_Omission is for an omission, but the second reading adds: τω."
