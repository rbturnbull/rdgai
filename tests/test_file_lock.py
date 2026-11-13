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
