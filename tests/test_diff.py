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
    # neutral wording: a different form of the same word is not a Substitution, so nothing here suggests one
    ("και εδησεν αυτον εν φυλακη", "και εδησεν αυτον εις φυλακην", "changed", "changed: εν φυλακη → εις φυλακην"),
    ("καθως", "ως", "changed", "changed: καθως → ως"),
    ("εξηλθεν και απηλθεν", "εξελθων απηλθεν", "changed", "changed: εξηλθεν και → εξελθων"),
    ("εξηλθεν", "εξηλθον", "changed", "changed: εξηλθεν → εξηλθον"),
    ("λεγει αυτω", "λεγει αυτω", "identical", "readings are identical"),
    ("", "", "identical", "readings are identical"),
])
def test_describe_change(first, second, kind, text):
    change = describe_change(first, second)
    assert (change.kind, change.text) == (kind, text)


@pytest.mark.parametrize("first, second, kind, text", [
    # Mark.5.21.26-32: words moved and changed. The change alone ("changed: επ αυτον → μετ αυτων") hides the move.
    ("επ αυτον οχλος πολυς", "οχλος πολυς μετ αυτων", "changed", "reordered and changed: επ αυτον → μετ αυτων"),
    ("οχλος πολυς προς αυτον", "επ αυτον οχλος πολυς", "changed", "reordered and changed: προς → επ"),
    ("οχλος πολυς προς αυτον", "επ αυτω οχλος πολυς", "changed", "reordered and changed: προς αυτον → επ αυτω"),
    # moved, with words added or omitted
    ("και εδησεν αυτον εν φυλακη", "εν φυλακη και εδησεν αυτον εκει", "addition", "second reading reorders and adds: εκει"),
    ("εν φυλακη και εδησεν αυτον", "και εδησεν αυτον φυλακη", "omission", "second reading reorders and omits: εν"),
])
def test_describe_change_reordered_and_changed(first, second, kind, text):
    change = describe_change(first, second)
    assert (change.kind, change.text) == (kind, text)
    assert change.reordered


@pytest.mark.parametrize("first, second", [
    # no word moves: the same descriptions as before, not "reordered"
    ("οχλος πολυς επ αυτον", "οχλος πολυς μετ αυτων"),
    ("και εδησεν αυτον εν φυλακη", "και εδησεν αυτον εις φυλακην"),
    ("εξηλθεν και απηλθεν", "εξελθων απηλθεν"),
    ("και ειπεν αυτω", "ειπεν δε αυτοις"),        # αυτω/αυτοις are alike, but in the same order
    ("υιου θεου", "του υιου του θεου"),
    ("", "και"),
])
def test_no_reordering_when_nothing_moves(first, second):
    change = describe_change(first, second)
    assert not change.reordered
    assert "reorder" not in change.text


def test_the_unit_from_the_annotator(store):
    """ Every pair of Mark.5.21.26-32's readings that moves words says so. """
    readings = {"a": "οχλος πολυς επ αυτον", "b": "επ αυτον οχλος πολυς", "c": "οχλος πολυς επ αυτω",
                "d": "οχλος πολυς προς αυτον", "e": "οχλος πολυς παρ αυτον", "f": "οχλος πολυς μετ αυτων"}
    for first in readings:
        for second in readings:
            if first == second:
                continue
            change = describe_change(readings[first], readings[second])
            # b is the reading with the other word order: exactly the pairs with b are reordered
            assert change.reordered == ("b" in (first, second)), (first, second, change.text)


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
    ("Minor_Omission", "καθως", "ως", False),  # changed words: no clear contradiction
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
    # reading order: a→b, a→c, a→e, b→c, b→e, c→e
    # a = τω ησαια τω προφητη, b = ησαια τω προφητη, e = τοις προφηταις
    pairs = {(pair["first"], pair["second"]): pair for pair in client.get("/api/unit/Mark.1.2.8-14").json()["pairs"]}
    assert pairs[("a", "b")]["change"]["text"] == "second reading omits: τω"
    assert pairs[("b", "e")]["change"]["kind"] == "changed"
    assert pairs[("a", "b")]["warning"] is None


def test_gui_warning_after_saving_a_contradicting_label(store):
    client = TestClient(create_app(store))
    response = client.post("/api/label", json={"unit_id": "Mark.1.2.8-14", "pairs": [["b", "a"]], "category": "Minor_Omission"})
    assert response.status_code == 200  # never blocked
    pair = response.json()["pairs"][0]
    assert pair["category"] == "Minor_Omission"
    assert pair["warning"] == "Minor_Omission is for an omission, but the second reading adds: τω."


def test_change_descriptions_never_name_a_category(store):
    """ The reading aid must not point towards a category (e.g. "substitution" for a different form of the same word). """
    for unit in store.list_units(limit=500).units:
        for pair in store.get_unit(unit.id).pairs:
            assert "substitut" not in pair.change.text.lower() and "substitut" not in pair.change.kind.lower()
            assert "different words" not in pair.change.text
