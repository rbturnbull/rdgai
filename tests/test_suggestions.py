"""
Suggestions (from Claude or the rdgai pipeline) are stored separately from labels, in
<listRelation type="suggestion">, which teiphy ignores. They never count as classifications.
"""
import pytest

from rdgai.apparatus import Doc
from rdgai.annotation import AnnotationStore, AnnotationError, ReadOnlyError
from rdgai.mcp_server import build_server
from rdgai.tei import find_elements

from .test_mcp import call, list_tool_names, mark_path, store  # noqa: F401 (fixtures)

TEI = "{http://www.tei-c.org/ns/1.0}"


def list_relations(path, unit_id, type_):
    doc = Doc(path)
    return [
        dict(relation.attrib)
        for list_relation in find_elements(doc[unit_id].element, f".//listRelation[@type='{type_}']")
        for relation in list_relation.findall(f"{TEI}relation")
    ]


def test_suggestion_is_not_a_label(store, mark_path):
    result = store.suggest_classification(
        "Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="Omits the article.", source="claude",
    )
    assert result.suggestion.category == "Minor_Omission"
    assert result.suggestion.source == "claude"
    assert result.suggestion.justification == "Omits the article."
    assert result.inverse.category == "Minor_Addition"
    assert result.inverse.active == "b"

    # nothing in the transcriptional list (which teiphy reads)
    assert list_relations(mark_path, "Mark.1.1.12-16", "transcriptional") == []
    suggestions = list_relations(mark_path, "Mark.1.1.12-16", "suggestion")
    assert {(relation["active"], relation["passive"], relation["ana"], relation["resp"]) for relation in suggestions} == {
        ("a", "b", "#Minor_Omission", "#claude"),
        ("b", "a", "#Minor_Addition", "#claude"),
    }

    # the pair is still unclassified
    assert store.get_unit("Mark.1.1.12-16").pairs[0].category is None
    assert store.list_units(status="unclassified").total == 10
    assert all(count.total == 0 for count in store.category_counts().categories)
    # the source is declared in the header
    assert '<respStmt xml:id="claude">' in mark_path.read_text()


def test_suggestion_records_timestamp(store):
    result = store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    assert result.suggestion.when.endswith("Z")


def test_same_source_replaces_other_sources_add(store, mark_path):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="claude")
    store.suggest_classification("Mark.1.1.10", "b", "a", "Major_Addition", source="rdgai:gpt-4o")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="rdgai:other-model")

    suggestions = store.get_suggestions("Mark.1.1.10", "a", "b")
    by_source = {suggestion.source: suggestion for suggestion in suggestions}
    assert set(by_source) == {"claude", "rdgai:gpt-4o", "rdgai:other-model"}
    assert by_source["claude"].category == "Minor_Omission"
    # stored as b → a, reported for the direction asked
    assert by_source["rdgai:gpt-4o"].category == "Major_Omission"
    assert by_source["rdgai:gpt-4o"].model == "gpt-4o"
    assert len(list_relations(mark_path, "Mark.1.1.10", "suggestion")) == 6


def test_get_suggestions_filters(store):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.suggest_classification("Mark.1.1.12-16", "a", "c", "Major_Substitution", source="claude")
    store.suggest_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", source="rdgai:gpt-4o")

    assert len(store.get_suggestions()) == 3  # one per unordered pair and source
    assert len(store.get_suggestions("Mark.1.1.12-16")) == 2
    assert [suggestion.source for suggestion in store.get_suggestions(source="rdgai:gpt-4o")] == ["rdgai:gpt-4o"]
    reverse = store.get_suggestions("Mark.1.1.12-16", "c", "a")
    assert [(suggestion.active, suggestion.passive, suggestion.category) for suggestion in reverse] == [("c", "a", "Major_Substitution")]


def test_accepting_a_suggestion(store):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="omits χριστου", source="claude")
    result = store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", justification="omits χριστου")
    assert result.pair.provenance.resp == "#RT"
    # the suggestion is kept as a record
    assert len(store.get_suggestions("Mark.1.1.10")) == 1


def test_clear_suggestions(store, mark_path):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="rdgai:gpt-4o")
    store.clear_suggestions("Mark.1.1.10", "b", "a", source="claude")
    assert [suggestion.source for suggestion in store.get_suggestions("Mark.1.1.10")] == ["rdgai:gpt-4o"]
    store.clear_suggestions("Mark.1.1.10", "a", "b")
    assert store.get_suggestions("Mark.1.1.10") == []
    assert list_relations(mark_path, "Mark.1.1.10", "suggestion") == []


@pytest.mark.parametrize("kwargs, message", [
    (dict(category="Nonsense"), "Unknown category"),
    (dict(passive="z"), "has no reading"),
    (dict(cert="certain"), "cert"),
    (dict(source="bad source"), "source"),
])
def test_suggestion_errors(store, mark_path, kwargs, message):
    arguments = dict(unit_id="Mark.1.1.10", active="a", passive="b", category="Major_Omission", source="claude")
    arguments.update(kwargs)
    before = mark_path.read_bytes()
    with pytest.raises(AnnotationError, match=message):
        store.suggest_classification(**arguments)
    assert mark_path.read_bytes() == before


def test_suggestions_read_only(mark_path):
    store = AnnotationStore(mark_path, read_only=True)
    with pytest.raises(ReadOnlyError):
        store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")


def test_renaming_a_category_updates_suggestions(store):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.update_category("Major_Omission", new_name="Major_Minus")
    assert store.get_suggestions("Mark.1.1.10", "a", "b")[0].category == "Major_Minus"


def test_reassigning_a_category_moves_suggestions(store):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="claude")
    store.remove_category("Minor_Omission", reassign_to="Major_Omission")
    assert store.get_suggestions("Mark.1.1.10", "a", "b")[0].category == "Major_Omission"


def test_removing_a_category_used_only_by_suggestions(store):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="claude")
    result = store.remove_category("Minor_Omission")
    assert "1 suggestion" in result.message
    assert store.get_suggestions("Mark.1.1.10") == []


def test_clean_does_not_merge_suggestions(store, mark_path):
    store.suggest_classification("Mark.1.1.10", "a", "b", "Major_Omission", source="claude")
    store.suggest_classification("Mark.1.1.10", "a", "b", "Minor_Omission", source="rdgai:gpt-4o")
    doc = Doc(mark_path)
    doc.clean(output=mark_path)
    assert len(list_relations(mark_path, "Mark.1.1.10", "suggestion")) == 4


def test_suggestion_tools_through_mcp(store):
    server = build_server(store)
    result = call(
        server, "suggest_classification",
        unit_id="Mark.1.1.10", active="a", passive="b", category="Major_Omission", justification="omits", source="claude",
    )
    assert not result.is_error
    assert result.structured_content["suggestion"]["category"] == "Major_Omission"

    data = call(server, "get_suggestions", unit_id="Mark.1.1.10").structured_content
    assert [suggestion["category"] for suggestion in data["suggestions"]] == ["Major_Omission"]

    result = call(server, "suggest_classification", unit_id="Mark.1.1.10", active="a", passive="b", category="Nonsense")
    assert result.is_error


def test_suggestion_tools_listed(store, mark_path):
    names = list_tool_names(build_server(store))
    assert "suggest_classification" in names
    assert "get_suggestions" in names
    read_only = list_tool_names(build_server(AnnotationStore(mark_path, read_only=True)))
    assert "suggest_classification" not in read_only
    assert "get_suggestions" in read_only
