"""
The unit open in the GUI, so that Claude (through MCP) can act on "the verse I'm on", e.g. translate it.
It is kept in a small hidden file next to the TEI file, so another process (e.g. a second MCP server) sees it too.
"""
import pytest
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore, AnnotationError
from rdgai.gui import create_app
from rdgai.mcp_server import build_server

from .test_mcp import call, list_tool_names, mark_path, store  # noqa: F401 (fixtures)


def test_viewing_is_shared_between_processes(store, mark_path):
    assert store.viewing() is None
    store.set_viewing("Mark.1.1.12-16")
    other = AnnotationStore(mark_path)
    viewing = other.viewing()
    assert viewing.unit_id == "Mark.1.1.12-16"
    assert viewing.when
    assert store.viewing_path == mark_path.with_name(".mark_sample.xml.viewing.json")


def test_viewing_unknown_unit(store):
    with pytest.raises(AnnotationError):
        store.set_viewing("Mark.99.1.1")


def test_viewing_on_a_read_only_store_writes_no_file(mark_path):
    store = AnnotationStore(mark_path, read_only=True)
    store.set_viewing("Mark.1.1.10")
    assert store.viewing().unit_id == "Mark.1.1.10"
    assert not store.viewing_path.exists()
    assert not store.lock_path.exists()


def test_gui_reports_the_unit_it_shows(store):
    client = TestClient(create_app(store))
    response = client.post("/api/viewing", json={"unit_id": "Mark.1.2.2"})
    assert response.status_code == 200
    assert store.viewing().unit_id == "Mark.1.2.2"
    assert client.post("/api/viewing", json={"unit_id": "nope"}).status_code == 404


def test_mcp_get_current_unit(store):
    server = build_server(store)
    result = call(server, "get_current_unit")
    assert result.is_error   # nothing open yet
    store.set_viewing("Mark.1.1.10")
    unit = call(server, "get_current_unit").structured_content
    assert unit["id"] == "Mark.1.1.10"
    assert [reading["id"] for reading in unit["readings"]][:2] == ["a", "b"]


def test_mcp_get_current_unit_on_read_only_server(mark_path):
    assert "get_current_unit" in list_tool_names(build_server(AnnotationStore(mark_path, read_only=True)))
