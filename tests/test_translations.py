"""
Contextual translations: an English translation of the whole verse with each reading in place, written by Claude
through MCP and stored in a sidecar YAML file (never in the TEI), shown in the GUI for each pair.
"""
import pytest
import yaml
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore, AnnotationError, ReadOnlyError
from rdgai.gui import create_app
from rdgai.mcp_server import build_server

from .test_mcp import call, list_tool_names, mark_path, store  # noqa: F401 (fixtures)

UNIT = "Mark.1.1.10"   # a: χριστου, b: OMIT


def test_set_and_get_translations(store, mark_path):
    before = mark_path.read_bytes()
    result = store.set_translations(UNIT, {"a": "The beginning of the gospel of Jesus Christ, Son of God.", "b": "The beginning of the gospel of Jesus, Son of God."})
    assert result.unit_id == UNIT
    assert sorted(result.saved) == ["a", "b"]
    assert result.missing == []
    assert store.translations_for(store.get_app(UNIT)) == {
        "a": "The beginning of the gospel of Jesus Christ, Son of God.",
        "b": "The beginning of the gospel of Jesus, Son of God.",
    }
    assert mark_path.read_bytes() == before   # the TEI is never changed

    path = mark_path.with_name("mark_sample.translations.yaml")
    assert store.translations_path == path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entry = data["units"][UNIT]
    assert entry["source"] == "claude"
    assert entry["when"]
    assert entry["readings"]["a"]["reading"] == "χριστου"
    assert entry["readings"]["a"]["translation"].startswith("The beginning")


def test_translations_are_merged_and_report_what_is_missing(store):
    result = store.set_translations("Mark.1.1.12-16", {"a": "… Son of God."})
    assert result.saved == ["a"]
    assert "b" in result.missing and "a" not in result.missing
    store.set_translations("Mark.1.1.12-16", {"b": "… Son God."})
    assert set(store.translations_for(store.get_app("Mark.1.1.12-16"))) == {"a", "b"}


def test_translation_errors(store):
    with pytest.raises(AnnotationError):
        store.set_translations(UNIT, {"zz": "x"})
    with pytest.raises(AnnotationError):
        store.set_translations(UNIT, {"a": "  "})
    with pytest.raises(AnnotationError):
        store.set_translations("Mark.99.99.99", {"a": "x"})
    with pytest.raises(AnnotationError):
        store.set_translations(UNIT, {})


def test_stale_translation_is_not_shown(store):
    """ If the reading's text has changed since it was translated, the translation no longer fits. """
    store.set_translations(UNIT, {"a": "Christ", "b": "no Christ"})
    data = yaml.safe_load(store.translations_path.read_text(encoding="utf-8"))
    data["units"][UNIT]["readings"]["a"]["reading"] = "something else"
    store.translations_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert store.translations_for(store.get_app(UNIT)) == {"b": "no Christ"}


def test_read_only_refuses_translations(mark_path):
    store = AnnotationStore(mark_path, read_only=True)
    with pytest.raises(ReadOnlyError):
        store.set_translations(UNIT, {"a": "x"})


def test_untranslated_units(store):
    assert UNIT in store.untranslated_units()
    store.set_translations(UNIT, {"a": "x", "b": "y"})
    assert UNIT not in store.untranslated_units()
    store.set_translations("Mark.1.1.12-16", {"a": "x"})   # partly translated
    assert "Mark.1.1.12-16" in store.untranslated_units()


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------

def test_mcp_set_translations_and_get_unit(store):
    server = build_server(store)
    result = call(server, "set_translations", unit_id=UNIT, translations={"a": "with Christ", "b": "without Christ"})
    assert not result.is_error
    assert result.structured_content["missing"] == []
    unit = call(server, "get_unit", unit_id=UNIT).structured_content
    assert unit["translations"] == {"a": "with Christ", "b": "without Christ"}


def test_mcp_list_untranslated(store):
    server = build_server(store)
    data = call(server, "list_untranslated", sample="all").structured_content
    assert UNIT in data["unit_ids"]
    assert data["total"] == 10
    call(server, "set_translations", unit_id=UNIT, translations={"a": "x", "b": "y"})
    data = call(server, "list_untranslated", sample="all", limit=3).structured_content
    assert data["total"] == 9
    assert len(data["unit_ids"]) == 3


def test_mcp_translation_tools_when_read_only(mark_path):
    names = list_tool_names(build_server(AnnotationStore(mark_path, read_only=True)))
    assert "set_translations" not in names
    assert "list_untranslated" in names


def test_mcp_translations_allowed_in_evaluation_mode(store):
    """ A translation reveals nothing about labels, so test units can be translated in evaluation mode too. """
    info = store.draw_sample(size=10, seed=3)
    test_unit = next(unit.id for unit in info.units if unit.role == "test")
    evaluation = AnnotationStore(store.path, evaluation=True)
    reading = store.get_unit(test_unit).readings[0].id
    result = call(build_server(evaluation), "set_translations", unit_id=test_unit, translations={reading: "x"})
    assert not result.is_error


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def test_gui_shows_translations_for_each_pair(store):
    client = TestClient(create_app(store))
    store.set_translations(UNIT, {"a": "with Christ", "b": "without Christ"})
    data = client.get(f"/api/unit/{UNIT}", params={"sample": "all"}).json()
    assert data["translations"] == {"a": "with Christ", "b": "without Christ"}
    pair = data["pairs"][0]
    assert (pair["first"], pair["second"]) == ("a", "b")
    assert pair["first_translation"] == "with Christ"
    assert pair["second_translation"] == "without Christ"


def test_gui_pair_without_translation(store):
    client = TestClient(create_app(store))
    pair = client.get(f"/api/unit/{UNIT}", params={"sample": "all"}).json()["pairs"][0]
    assert pair["first_translation"] is None
    assert pair["second_translation"] is None


def test_gui_version_changes_when_translations_change(store):
    client = TestClient(create_app(store))
    before = client.get("/api/version").json()["version"]
    store.set_translations(UNIT, {"a": "x"})
    assert client.get("/api/version").json()["version"] != before


# ---------------------------------------------------------------------------
# marking the part of the translation that renders the reading: ⸂…⸃ (⸆ where an omission falls)
# ---------------------------------------------------------------------------

def test_marked_translations_are_kept(store):
    store.set_translations(UNIT, {
        "a": "The beginning of the gospel of Jesus ⸂Christ⸃, Son of God.",
        "b": "The beginning of the gospel of Jesus⸆, Son of God.",
    })
    assert store.translations_for(store.get_app(UNIT))["a"] == "The beginning of the gospel of Jesus ⸂Christ⸃, Son of God."


def test_marks_may_be_split(store):
    """ The words of a reading can be scattered in the English word order. """
    store.set_translations("Mark.1.1.12-16", {"a": "the ⸂Son⸃ of ⸂God⸃"})


@pytest.mark.parametrize("text", ["Jesus ⸂Christ, Son of God.", "Jesus Christ⸃, Son", "⸂a ⸂b⸃ c⸃", "⸃Christ⸂"])
def test_unbalanced_marks_are_refused(store, text):
    with pytest.raises(AnnotationError):
        store.set_translations(UNIT, {"a": text})
