"""
Only one writer at a time: writes take an advisory lock on a lock file next to the TEI file, and a store
notices when another process has replaced the file, even if the modification time looks unchanged.
"""
import os
import fcntl

import pytest

from rdgai.annotation import AnnotationStore, AnnotationError

from .test_mcp import mark_path, store  # noqa: F401 (fixtures)


def test_lock_path(store, mark_path):
    assert store.lock_path == mark_path.with_name(f".{mark_path.name}.lock")


def test_write_waits_for_lock_then_fails(mark_path):
    store = AnnotationStore(mark_path, annotator="RT", lock_timeout=0.2)
    before = mark_path.read_bytes()
    with open(store.lock_path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with pytest.raises(AnnotationError, match="locked by another"):
            store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
        fcntl.flock(handle, fcntl.LOCK_UN)
    assert mark_path.read_bytes() == before

    store.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    assert store.get_unit("Mark.1.1.10").pairs[0].category == "Major_Omission"


def test_lock_released_after_error(store):
    with pytest.raises(AnnotationError):
        store.set_classification("Mark.1.1.10", "a", "b", "Nonsense")
    with open(store.lock_path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise if still held
        fcntl.flock(handle, fcntl.LOCK_UN)


def test_detects_replaced_file_with_same_mtime(store, mark_path):
    stat = mark_path.stat()
    other = AnnotationStore(mark_path, annotator="XY")
    other.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    # make the replaced file look as if its modification time had not changed
    os.utime(mark_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    pair = store.get_unit("Mark.1.1.10").pairs[0]
    assert pair.category == "Major_Omission"
    assert pair.provenance.resp == "#XY"


def test_two_stores_never_lose_writes(mark_path):
    first = AnnotationStore(mark_path, annotator="RT")
    second = AnnotationStore(mark_path, annotator="XY")
    first.set_classification("Mark.1.1.10", "a", "b", "Major_Omission")
    second.set_classification("Mark.1.1.12-16", "a", "b", "Minor_Omission")
    first.set_classification("Mark.1.2.2", "a", "b", "Minor_Substitution")

    reloaded = AnnotationStore(mark_path, read_only=True)
    assert reloaded.get_unit("Mark.1.1.10").pairs[0].category == "Major_Omission"
    assert reloaded.get_unit("Mark.1.1.12-16").pairs[0].category == "Minor_Omission"
    assert reloaded.get_unit("Mark.1.2.2").pairs[0].category == "Minor_Substitution"


def test_concurrent_writes_from_two_stores_all_survive(mark_path):
    """ Like Claude Desktop's two serve-mcp processes: each writes under the lock, on freshly loaded state. """
    import threading

    first = AnnotationStore(mark_path, annotator="RT")
    second = AnnotationStore(mark_path, annotator="XY")
    units = [unit.id for unit in first.list_units(limit=500).units]
    jobs = {first: [], second: []}
    for index, unit_id in enumerate(units):
        pair = first.get_unit(unit_id).pairs[0]
        jobs[first if index % 2 == 0 else second].append((unit_id, pair.active, pair.passive))

    errors = []

    def work(store):
        try:
            for repeat in range(3):
                for unit_id, active, passive in jobs[store]:
                    store.set_classification(unit_id, active, passive, ["Major_Substitution", "Minor_Substitution", "Transposition"][repeat])
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=work, args=(store,)) for store in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    reloaded = AnnotationStore(mark_path, read_only=True)
    for store, pairs in jobs.items():
        for unit_id, active, passive in pairs:
            pair = next(pair for pair in reloaded.get_unit(unit_id).pairs if (pair.active, pair.passive) == (active, passive))
            assert pair.category == "Transposition", unit_id
            assert pair.provenance.resp == f"#{store.annotator}"
    # and each store reads the other's writes (every unit now has a label)
    for store in (first, second):
        assert all(unit.classified_count >= 1 for unit in store.list_units(limit=500).units)
