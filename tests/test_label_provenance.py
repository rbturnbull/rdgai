"""
Every saved label records when it was saved (a <date when> in its <desc>) and the version of the category
definitions it was made under (@change pointing to a <change type="definitions"> holding the hash).
"""
import re
import shutil
from datetime import datetime, timezone, timedelta

from rdgai.apparatus import Doc
from rdgai.annotation import AnnotationStore
from rdgai.tei import find_elements

from .util import TEST_DATA_DIR
from .test_mcp import mark_path, store  # noqa: F401 (fixtures)

ISO_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
TEI = "{http://www.tei-c.org/ns/1.0}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def relation(path, unit_id, active, passive):
    doc = Doc(path)
    for pair in doc[unit_id].pairs:
        if pair.active.n == active and pair.passive.n == passive:
            return pair.relation_element()


def definition_changes(path):
    doc = Doc(path)
    return [
        change for change in find_elements(doc.tree, ".//revisionDesc/change")
        if change.attrib.get("type") == "definitions"
    ]


def test_label_records_timestamp(store, mark_path):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    result = store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission", justification="Omits του.")
    when = result.pair.provenance.when
    assert ISO_UTC.match(when)
    saved = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= saved <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert result.inverse.provenance.when == when
    assert result.pair.provenance.justification == "Omits του."

    element = relation(mark_path, "Mark.1.1.12-16", "a", "b")
    desc = element.find(f"{TEI}desc")
    assert desc.attrib["type"] == "justification"
    assert desc.text == "Omits του."
    date = desc.find(f"{TEI}date")
    assert date.attrib["when"] == when
    # TEI allows only one <desc> in <relation>
    assert len(element.findall(f"{TEI}desc")) == 1


def test_label_without_justification_still_has_timestamp(store, mark_path):
    result = store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    assert result.pair.provenance.justification == ""
    assert ISO_UTC.match(result.pair.provenance.when)
    desc = relation(mark_path, "Mark.1.1.10", "a", "b").find(f"{TEI}desc")
    assert desc.find(f"{TEI}date") is not None


def test_label_records_definitions_version(store, mark_path):
    definitions_hash = store.definitions().definitions_hash
    result = store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    assert result.pair.provenance.definitions_hash == definitions_hash
    assert result.inverse.provenance.definitions_hash == definitions_hash

    change_id = f"defs-{definitions_hash[:12]}"
    assert relation(mark_path, "Mark.1.1.10", "a", "b").attrib["change"] == f"#{change_id}"
    assert relation(mark_path, "Mark.1.1.10", "b", "a").attrib["change"] == f"#{change_id}"

    changes = definition_changes(mark_path)
    assert len(changes) == 1
    assert changes[0].attrib[XML_ID] == change_id
    assert ISO_UTC.match(changes[0].attrib["when"])
    assert changes[0].find(f"{TEI}idno").text == definitions_hash
    assert changes[0].find(f"{TEI}idno").attrib["type"] == "sha256"


def test_one_change_per_definitions_version(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    store.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    assert len(definition_changes(mark_path)) == 1

    old_hash = store.definitions().definitions_hash
    store.update_category("Transposition", description="Changed.")
    new_hash = store.definitions().definitions_hash
    store.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution")

    assert len(definition_changes(mark_path)) == 2
    # the earlier label keeps the version it was made under
    pairs = {pair.active: pair for pair in store.get_unit("Mark.1.1.10").pairs}
    assert pairs["a"].provenance.definitions_hash == old_hash
    assert store.get_unit("Mark.1.2.2").pairs[0].provenance.definitions_hash == new_hash


def test_pipeline_label_points_to_run_and_definitions(store):
    run_id = store.start_run(model="test-model")
    result = store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", resp="rdgai", cert="medium", change=run_id)
    provenance = result.pair.provenance
    tokens = provenance.change.split()
    assert tokens[0] == f"#{run_id}"
    assert tokens[1].startswith("#defs-")
    assert provenance.model == "test-model"
    assert provenance.definitions_hash == store.definitions().definitions_hash
    assert ISO_UTC.match(provenance.when)


def test_legacy_labels_have_empty_timestamp_and_version(tmp_path):
    path = tmp_path / "minimal_output.xml"
    shutil.copy(TEST_DATA_DIR / "minimal_output.xml", path)
    store = AnnotationStore(path, annotator="RT")
    classified = [pair for pair in store.get_unit("app").pairs if pair.category]
    assert classified
    for pair in classified:
        assert pair.provenance.when == ""
        assert pair.provenance.definitions_hash == ""


def test_certainty_only_change_updates_timestamp_and_version(store):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission", cert="low")
    store.update_category("Transposition", description="Changed.")
    result = store.set_certainty("Mark.1.1.10", [("a", "b")], cert="high")
    provenance = result[0].pair.provenance
    assert provenance.cert == "high"
    assert provenance.definitions_hash == store.definitions().definitions_hash
