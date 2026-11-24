"""
The sample's progress, for the GUI's progress bars (GET /api/sample-progress): per role (example, test), the labelled
pairs out of all the pairs to label, and the complete units out of all units.
"""
import pytest
from starlette.testclient import TestClient

from rdgai.gui import create_app

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def test_no_sample(client):
    assert client.get("/api/sample-progress").json()["sample"] is False


def test_progress_per_role(client, store):
    info = store.draw_sample(size=10, seed=3)
    by_role = {role: [unit.id for unit in info.units if unit.role == role] for role in ("example", "test")}
    totals = {role: sum(len(store.work_pairs(store.get_app(unit_id))) for unit_id in ids) for role, ids in by_role.items()}

    data = client.get("/api/sample-progress").json()
    assert data["sample"] is True
    for role in ("example", "test"):
        assert data[role] == {"pairs_done": 0, "pairs_total": totals[role], "units_done": 0, "units_total": len(by_role[role])}

    # label every pair of one example unit, and one pair of another
    first, second = by_role["example"][:2]
    for pair in store.work_pairs(store.get_app(first)):
        store.set_classification(first, pair.active.n, pair.passive.n, "Minor_Omission")
    pair = store.work_pairs(store.get_app(second))[0]
    store.set_classification(second, pair.active.n, pair.passive.n, "Minor_Omission")

    example = client.get("/api/sample-progress").json()["example"]
    assert example["pairs_done"] == len(store.work_pairs(store.get_app(first))) + 1
    complete = [unit_id for unit_id in by_role["example"]
                if all(store.is_classified(pair) for pair in store.work_pairs(store.get_app(unit_id)))]
    assert first in complete
    assert example["units_done"] == len(complete)
    assert client.get("/api/sample-progress").json()["test"]["pairs_done"] == 0


def test_whole_file_totals(client, store):
    """ "All pairs" and "All units" are the whole file, not just the sample. """
    store.draw_sample(size=10, seed=3)
    apps = store.doc.apps
    expected_total = sum(len(store.work_pairs(app)) for app in apps)
    data = client.get("/api/sample-progress").json()
    assert data["file"] == {"pairs_done": 0, "pairs_total": expected_total, "units_done": data["file"]["units_done"], "units_total": len(apps)}
    # units with nothing to label count as complete
    assert data["file"]["units_done"] == sum(not store.work_pairs(app) for app in apps)

    # a label anywhere in the file counts, in either direction
    app = apps[0]
    pair = store.work_pairs(app)[0]
    store.set_classification(str(app), pair.passive.n, pair.active.n, "Minor_Omission")
    assert client.get("/api/sample-progress").json()["file"]["pairs_done"] == 1


def test_whole_file_totals_without_a_sample(client):
    data = client.get("/api/sample-progress").json()
    assert data["sample"] is False
    assert data["file"]["units_total"] > 0


def test_whole_file_totals_follow_another_process(client, store, mark_path):
    """ A label written by another process is counted once the file is re-read. """
    from rdgai.annotation import AnnotationStore
    before = client.get("/api/sample-progress").json()["file"]["pairs_done"]
    other = AnnotationStore(mark_path, annotator="RT")
    app = other.doc.apps[1]
    pair = other.work_pairs(app)[0]
    other.set_classification(str(app), pair.active.n, pair.passive.n, "Minor_Omission")
    assert client.get("/api/sample-progress").json()["file"]["pairs_done"] == before + 1
