"""
The annotator's profile and the header's details: the full name lives in the TEI (<respStmt xml:id="RT"><name>),
the short name shown in the header's circle and an optional header title live in a display sidecar file.
"""
import yaml
from starlette.testclient import TestClient

from rdgai.annotation import AnnotationStore
from rdgai.gui import create_app

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


def test_full_name_comes_from_the_tei(store, mark_path):
    store.set_annotator_name("Robert Turnbull")
    assert "<name>Robert Turnbull</name>" in mark_path.read_text(encoding="utf-8")
    # another server started without --annotator-name finds the name in the file
    assert AnnotationStore(mark_path, annotator="RT").annotator_full_name() == "Robert Turnbull"


def test_full_name_falls_back_to_the_command_line(mark_path):
    assert AnnotationStore(mark_path, annotator="RT", annotator_name="Test Annotator").annotator_full_name() == "Test Annotator"
    assert AnnotationStore(mark_path, annotator="RT").annotator_full_name() is None


def test_renaming_updates_the_existing_resp_stmt(store, mark_path):
    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")   # adds <respStmt xml:id="RT">
    store.set_annotator_name("R. Turnbull")
    text = mark_path.read_text(encoding="utf-8")
    assert text.count('<respStmt xml:id="RT">') == 1
    assert "<name>R. Turnbull</name>" in text and "<name>Test Annotator</name>" not in text


def test_config_profile_defaults(store):
    config = TestClient(create_app(store)).get("/api/config").json()
    assert config["profile"] == {"id": "RT", "name": "Test Annotator", "short_name": "TA", "editable": True}
    assert config["title"] is None


def test_edit_profile(store, mark_path):
    client = TestClient(create_app(store))
    response = client.post("/api/profile", json={"name": "Robert Turnbull", "short_name": "Rob", "title": "Mark: transcriptional changes"})
    assert response.status_code == 200, response.text
    config = client.get("/api/config").json()
    assert config["profile"]["name"] == "Robert Turnbull"
    assert config["profile"]["short_name"] == "Rob"
    assert config["title"] == "Mark: transcriptional changes"
    assert "<name>Robert Turnbull</name>" in mark_path.read_text(encoding="utf-8")
    display = yaml.safe_load(mark_path.with_name("mark_sample.display.yaml").read_text(encoding="utf-8"))
    assert display == {"title": "Mark: transcriptional changes", "annotators": {"RT": {"short_name": "Rob"}}}


def test_empty_short_name_and_title_fall_back(store):
    client = TestClient(create_app(store))
    client.post("/api/profile", json={"name": "Robert Turnbull", "short_name": "Rob", "title": "Mark"})
    client.post("/api/profile", json={"name": "Robert Turnbull", "short_name": " ", "title": ""})
    config = client.get("/api/config").json()
    assert config["profile"]["short_name"] == "RT"   # initials of the full name
    assert config["title"] is None


def test_profile_read_only(mark_path):
    client = TestClient(create_app(AnnotationStore(mark_path, annotator="RT", read_only=True)))
    assert client.get("/api/config").json()["profile"]["editable"] is False
    assert client.post("/api/profile", json={"name": "X"}).status_code == 403


def test_profile_without_an_annotator(mark_path):
    client = TestClient(create_app(AnnotationStore(mark_path)))
    assert client.get("/api/config").json()["profile"] == {"id": None, "name": None, "short_name": None, "editable": False}
    assert client.post("/api/profile", json={"name": "X"}).status_code == 400
    # the header title can still be set
    assert client.post("/api/profile", json={"title": "Mark"}).status_code == 200
