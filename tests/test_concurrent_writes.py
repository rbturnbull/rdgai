"""
Two processes writing the same TEI file (e.g. the MCP server Claude uses and the GUI) must never lose each other's work.

The incident (LOST_SUGGESTIONS_BUG.md): suggestions written through the MCP reported success, then vanished when the
annotator saved a label in the GUI. A store re-read the file outside the write lock and recorded the file's identity
*after* parsing it; if the other process saved in between, the store held the old content under the new identity, so
its next write skipped the reload and saved over the other process's work.

Two AnnotationStore instances on one file stand in for the two processes (the write lock is a per-open-file flock, so
two stores in one process exclude each other as two processes do).
"""
import threading

import pytest
from starlette.testclient import TestClient

import rdgai.annotation as annotation_module
from rdgai.annotation import AnnotationStore
from rdgai.gui import create_app

from .test_mcp import mark_path  # noqa: F401 (fixture)

UNIT = "Mark.1.1.12-16"   # readings a-f


def open_store(path) -> AnnotationStore:
    return AnnotationStore(path, annotator="RT", annotator_name="Test Annotator")


def suggestions_in_file(path, unit_id=UNIT) -> set[tuple[str, str, str]]:
    fresh = AnnotationStore(path, read_only=True)
    return {(s.active, s.passive, s.category) for s in fresh.get_suggestions(unit_id)}


def category_in_file(path, active, passive, unit_id=UNIT):
    fresh = AnnotationStore(path, read_only=True)
    pair = next(p for p in fresh.get_unit(unit_id).pairs if (p.active, p.passive) == (active, passive))
    return pair.category


@pytest.fixture
def save_during_next_load(monkeypatch):
    """
    Makes the other store save (through `action`) while this store is re-reading the file: after the content is read,
    before the store has finished loading. This is the interleaving that lost the suggestions.
    """
    real_doc = annotation_module.Doc
    pending = []

    def doc_then_other_process_saves(*args, **kwargs):
        doc = real_doc(*args, **kwargs)
        while pending:
            pending.pop(0)()
        return doc

    monkeypatch.setattr(annotation_module, "Doc", doc_then_other_process_saves)
    return pending.append


def test_a_save_during_a_reload_is_not_lost(mark_path, save_during_next_load):
    gui, mcp = open_store(mark_path), open_store(mark_path)
    # the MCP writes; the GUI's next read reloads, and while it does the MCP writes again
    mcp.suggest_classification(UNIT, "a", "b", "Minor_Omission", justification="first", source="claude")
    save_during_next_load(lambda: mcp.suggest_classification(UNIT, "a", "c", "Minor_Substitution", justification="second", source="claude"))
    gui.get_unit(UNIT)   # a read: reloads the changed file
    # the annotator saves a label in the GUI
    gui.set_classification(UNIT, "a", "d", "Minor_Addition")

    assert ("a", "c", "Minor_Substitution") in suggestions_in_file(mark_path)   # was lost
    assert ("a", "b", "Minor_Omission") in suggestions_in_file(mark_path)
    assert category_in_file(mark_path, "a", "d") == "Minor_Addition"


def test_a_label_saved_during_a_reload_is_not_lost(mark_path, save_during_next_load):
    """ The same for labels: a human label must never be dropped either. """
    gui, mcp = open_store(mark_path), open_store(mark_path)
    gui.set_classification(UNIT, "a", "b", "Minor_Omission")
    save_during_next_load(lambda: gui.set_classification(UNIT, "a", "c", "Minor_Substitution"))
    mcp.get_unit(UNIT)
    mcp.suggest_classification(UNIT, "a", "d", "Minor_Addition", source="claude")

    assert category_in_file(mark_path, "a", "c") == "Minor_Substitution"
    assert category_in_file(mark_path, "a", "b") == "Minor_Omission"


def test_writes_are_not_fooled_by_an_unchanged_file_identity(mark_path, monkeypatch):
    """
    Even if the file's identity (inode, mtime, size) looked unchanged after another process saved (a reused inode),
    a write must notice the new content and keep the other process's work.
    """
    gui, mcp = open_store(mark_path), open_store(mark_path)
    gui.get_unit(UNIT)
    stale_identity = gui._file_id
    mcp.suggest_classification(UNIT, "a", "b", "Minor_Omission", justification="kept", source="claude")
    monkeypatch.setattr(gui, "file_id", lambda: stale_identity)
    gui.set_classification(UNIT, "a", "c", "Minor_Substitution")

    assert ("a", "b", "Minor_Omission") in suggestions_in_file(mark_path)
    assert category_in_file(mark_path, "a", "c") == "Minor_Substitution"


def test_through_the_gui_endpoints(mark_path, save_during_next_load):
    """ The incident itself: the MCP suggests while the GUI polls, then the annotator labels in the GUI. """
    gui_store, mcp = open_store(mark_path), open_store(mark_path)
    client = TestClient(create_app(gui_store))
    mcp.suggest_classification(UNIT, "a", "b", "Minor_Omission", justification="first", source="claude")
    save_during_next_load(lambda: mcp.suggest_classification(UNIT, "b", "c", "Minor_Substitution", justification="second", source="claude"))
    assert client.get(f"/api/unit/{UNIT}?sample=all").status_code == 200   # the GUI's refresh

    response = client.post("/api/label", json={"unit_id": UNIT, "pairs": [["a", "d"]], "category": "Minor_Addition"})
    assert response.status_code == 200, response.text
    assert ("b", "c", "Minor_Substitution") in suggestions_in_file(mark_path)
    assert category_in_file(mark_path, "a", "d") == "Minor_Addition"


def test_interleaved_writes_from_two_stores_lose_nothing(mark_path):
    """ Many suggestions and labels from two stores at once, with reads in between: every write is in the file. """
    gui, mcp = open_store(mark_path), open_store(mark_path)
    pairs = [(x, y) for x in "abcdef" for y in "abcdef" if x < y]   # 15 unordered pairs
    suggested, labelled = pairs[::2], pairs[1::2]
    errors = []

    def run(action):
        try:
            action()
        except Exception as error:   # pragma: no cover (reported below)
            errors.append(error)

    threads = []
    for active, passive in suggested:
        threads.append(threading.Thread(target=run, args=(lambda a=active, p=passive: mcp.suggest_classification(UNIT, a, p, "Minor_Substitution", justification=f"{a}{p}", source="claude"),)))
    for active, passive in labelled:
        threads.append(threading.Thread(target=run, args=(lambda a=active, p=passive: gui.set_classification(UNIT, a, p, "Minor_Addition"),)))
    for _ in range(6):   # the GUI's polling and Claude's reads
        threads.append(threading.Thread(target=run, args=(lambda: (gui.get_unit(UNIT), mcp.get_unit(UNIT)),)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    in_file = suggestions_in_file(mark_path)
    for active, passive in suggested:
        assert (active, passive, "Minor_Substitution") in in_file, (active, passive)
    for active, passive in labelled:
        assert category_in_file(mark_path, active, passive) == "Minor_Addition", (active, passive)
