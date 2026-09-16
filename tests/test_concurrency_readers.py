"""Readers against a live writer — the untested half of the concurrency fix.

`tests/test_concurrency.py` covers writers racing writers: CAS refusals, one
winner, a linear chain, cross-process serialization. What it does not cover is
a reader walking a version chain *while* a writer advances it, which is the
other half of what the fix changed.

Three things used to make that unsafe, all of them now closed:

  * `WriteEngine` built its own `threading.RLock()` per instance, so two
    `EmberDB` handles on one store held two different locks and did not
    exclude each other at all;
  * `EmberDB.update()` called `_master_index.mark_superseded()` *after*
    `writer.update()` returned, leaving a window in which the new version was
    on disk but the supersession map still pointed at the old head — a reader
    landing in that window saw a chain with a hole in it; and
  * `ReadEngine.get_current()` / `get_history()` walked the chain without
    holding any lock.

The first test below is structural: it proves chain reads are serialized
against write transactions, so it fails deterministically if the lock is
dropped again rather than only failing on an unlucky interleaving. The rest
are stress tests that assert the invariant the hole used to break — a version
chain is always contiguous, always complete, and never momentarily headless.
"""

import threading

import pytest

from embers import EmberDB, EmberRecord


@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "reader_store")


def _write(db, content):
    return db.write(EmberRecord(namespace="c", data={"content": content}))


# ── Structural: chain reads are inside the write transaction ────────────────

def test_get_current_waits_for_an_in_flight_write_transaction(db):
    """A chain read must not slip inside an open write transaction.

    Holding the store lock stands in for a write in progress. If
    `get_current()` returns while it is held, the reader is walking the
    supersession map at a moment when a writer may have published a record
    but not yet linked it — exactly the window that produced a chain with a
    hole.
    """
    rid = _write(db, "v1")
    reader_finished = threading.Event()
    reader_result = {}

    def read_head():
        reader_result["head"] = db.get_current(rid)
        reader_finished.set()

    with db._store.lock:
        reader = threading.Thread(target=read_head, daemon=True)
        reader.start()
        # Generous: this asserts blocking, so a slow machine only makes the
        # assertion safer. A reader that ignores the lock returns in microseconds.
        assert not reader_finished.wait(1.5), (
            "get_current() completed while a write transaction was open — "
            "chain reads are no longer serialized against writers")

    reader.join(10)
    assert reader_finished.is_set(), "reader never completed after the lock was released"
    assert reader_result["head"].data["content"] == "v1"


def test_get_history_waits_for_an_in_flight_write_transaction(db):
    """Same guarantee for the full-lineage walk."""
    rid = _write(db, "v1")
    db.update(rid, {"content": "v2"})
    reader_finished = threading.Event()
    reader_result = {}

    def read_history():
        reader_result["history"] = db.get_history(rid)
        reader_finished.set()

    with db._store.lock:
        reader = threading.Thread(target=read_history, daemon=True)
        reader.start()
        assert not reader_finished.wait(1.5), (
            "get_history() completed while a write transaction was open")

    reader.join(10)
    assert reader_finished.is_set()
    assert [r.data["content"] for r in reader_result["history"]] == ["v1", "v2"]


def test_supersession_link_is_written_inside_the_write_transaction(db):
    """The supersession map is updated before the lock is released.

    `EmberDB._after_write` marks the link, and `WriteEngine.update()` fires
    that callback while still holding the store lock. So by the time any
    other thread can acquire the lock, the chain is already whole — there is
    no observable state where the new version exists but is unlinked.
    """
    rid = _write(db, "v1")
    observed = {}

    def observe():
        # Blocks until the writer's transaction closes, then reads.
        with db._store.lock:
            observed["head"] = db.get_current(rid)
            observed["history"] = db.get_history(rid)

    watcher = threading.Thread(target=observe)
    with db._store.lock:
        watcher.start()
        new_id, _ = db.update(rid, {"content": "v2"},
                              expected_hash=db.get(rid, include_superseded=True).content_hash)
    watcher.join(10)

    assert observed["head"].id == new_id
    assert [r.version for r in observed["history"]] == [1, 2]


# ── Stress: the invariant the hole used to break ────────────────────────────

def _read_snapshot(handle, rid):
    """Read head and history as ONE snapshot.

    Taken as two separate calls these are two *individually* consistent reads
    at two different instants, and a live writer legitimately advances between
    them — so `head.version == history[-1].version` is only meaningful when
    both come from inside one held transaction. Holding the store lock across
    the pair is what makes the comparison a real invariant instead of a race
    against the writer.
    """
    with handle._store.lock:
        return handle.get_current(rid), handle.get_history(rid)


def _assert_chain_sound(handle, rid):
    """A lineage is contiguous, complete, and headed. No holes, no duplicates."""
    head, history = _read_snapshot(handle, rid)
    assert head is not None, f"{rid} had no current version"
    versions = [r.version for r in history]
    assert versions == sorted(versions), f"history out of order: {versions}"
    # The symptom the fix exists for: a reader landing between "record
    # published" and "supersession linked" saw [1, 2, 4, 5, ...].
    assert versions == list(range(1, len(versions) + 1)), (
        f"version chain has a hole or a duplicate: {versions}")
    assert head.version == versions[-1], (
        f"head is v{head.version} but history ends at v{versions[-1]}")


def test_readers_never_see_a_broken_chain_during_updates(db):
    """One writer advances a lineage 40 times; four readers watch throughout.

    This is the probabilistic companion to the structural tests above: it
    would have caught the original intermittent failure, and it catches any
    future regression that reopens the window without removing the lock.
    """
    rid = _write(db, "v0")
    updates = 40
    done = threading.Event()
    failures = []
    failure_lock = threading.Lock()

    def writer():
        try:
            for i in range(updates):
                head = db.get_current(rid)
                db.update(head.id, {"content": f"v{i + 1}"},
                          expected_hash=head.content_hash)
        except Exception as exc:  # pragma: no cover — reported, not swallowed
            with failure_lock:
                failures.append(f"writer: {type(exc).__name__}: {exc}")
        finally:
            done.set()

    def reader(tag):
        reads = 0
        try:
            while not done.is_set():
                _assert_chain_sound(db, rid)
                reads += 1
            # One final read after the writer has finished.
            _assert_chain_sound(db, rid)
        except Exception as exc:
            with failure_lock:
                failures.append(f"reader {tag} after {reads} reads: "
                                f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer)]
    threads += [threading.Thread(target=reader, args=(t,)) for t in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)

    assert failures == [], failures
    assert db.get_current(rid).version == updates + 1
    assert len(db.get_history(rid)) == updates + 1


def test_readers_on_a_second_handle_see_a_sound_chain(tmp_path):
    """The same guarantee when the reader holds a *different* EmberDB handle.

    Per-instance write locks were the root cause, so the cross-handle case is
    the one that actually regressed. The lock identity assertion makes the
    reason explicit rather than leaving it to the race.
    """
    store_path = tmp_path / "shared_reader_store"
    writer_handle = EmberDB.connect(store_path)
    reader_handle = EmberDB.connect(store_path)
    assert writer_handle._store.lock is reader_handle._store.lock, (
        "two handles for one store hold different locks — the original bug")

    rid = _write(writer_handle, "v0")
    updates = 30
    done = threading.Event()
    failures = []

    def write_all():
        try:
            for i in range(updates):
                head = writer_handle.get_current(rid)
                writer_handle.update(head.id, {"content": f"v{i + 1}"},
                                     expected_hash=head.content_hash)
        except Exception as exc:  # pragma: no cover
            failures.append(f"writer: {type(exc).__name__}: {exc}")
        finally:
            done.set()

    def read_all():
        try:
            while not done.is_set():
                _assert_chain_sound(reader_handle, rid)
            _assert_chain_sound(reader_handle, rid)
        except Exception as exc:
            failures.append(f"reader: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=write_all),
               threading.Thread(target=read_all)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)

    assert failures == [], failures
    # Both handles agree on one head, and it is the last write.
    assert (writer_handle.get_current(rid).id
            == reader_handle.get_current(rid).id)
    assert reader_handle.get_current(rid).version == updates + 1
