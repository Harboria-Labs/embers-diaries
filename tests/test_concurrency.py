"""
Ember's Diaries — Feature #6: Optimistic Concurrency (compare-and-set updates)

Behavioral tests mapped to the spec's §6 requirements:

    * a caller may pass the content hash it read as a precondition; the update
      succeeds only if the lineage head still has that hash
    * if another writer advanced the head in between, the write is REFUSED with
      a structured ConcurrentModificationError — never a silent lost update,
      never a silent fork
    * the error carries enough context to retry: expected/actual hash, the
      superseded record id, current + attempted version, and the current head id
    * two racing writers are serialized by the write lock: exactly one wins,
      the other conflicts; the winner's version is the one that survives
    * omitting expected_hash preserves the original behavior, INCLUDING the
      deliberate branching of Feature #2 (supersede a non-head to fork)
    * append-only is never violated: a refused write persists nothing, and a
      conflicting-then-retried write leaves the full chain intact

Run: diaries/Scripts/python.exe -m pytest tests/test_concurrency.py -v
"""

import multiprocessing
import threading

import pytest

from embers import EmberDB, EmberRecord, ConcurrentModificationError
from embers.storage.store import PhysicalStore, STORE_LOCK_BACKEND


def _physical_store_process_writer(store_path, tag, start_event, result_queue):
    """Spawn-safe worker used to prove the native lock crosses processes."""
    try:
        store = PhysicalStore(store_path)
        record = EmberRecord(namespace="process", data={"writer": tag})
        record.seal()
        if not start_event.wait(20):
            raise TimeoutError("process writers did not receive the start signal")
        result_queue.put(("ok", store.write(record)))
    except BaseException as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


# ── Fixtures / helpers ──────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "cas_store")


def _write(db, content, **kw):
    return db.write(EmberRecord(namespace="c", data={"content": content}, **kw))


def _hash_of(db, record_id):
    """The sealed content hash of a record, as a caller would have read it."""
    rec = db.get(record_id, include_superseded=True)
    return rec.content_hash


# ── The happy path: CAS with a fresh hash succeeds ──────────────────────────

class TestCompareAndSetSucceeds:
    def test_update_with_current_hash_succeeds(self, db):
        rid = _write(db, "v1")
        h = _hash_of(db, rid)
        new_id, old_id = db.update(rid, {"content": "v2"}, expected_hash=h)
        assert old_id == rid
        assert db.get_current(rid).data["content"] == "v2"

    def test_sequential_cas_chain_reads_head_each_time(self, db):
        """Read head hash, update, re-read new head hash, update again."""
        rid = _write(db, "v1")
        new1, _ = db.update(rid, {"content": "v2"},
                            expected_hash=_hash_of(db, rid))
        new2, old2 = db.update(new1, {"content": "v3"},
                               expected_hash=_hash_of(db, new1))
        assert old2 == new1
        assert db.get_current(rid).data["content"] == "v3"
        # Full lineage preserved: v1 → v2 → v3.
        chain = [r.data["content"] for r in db.get_history(rid)]
        assert chain == ["v1", "v2", "v3"]


# ── The refusal path: stale hash is rejected, not lost ──────────────────────

class TestStaleHashRejected:
    def test_stale_hash_raises_conflict(self, db):
        rid = _write(db, "v1")
        stale = _hash_of(db, rid)
        # Someone else advances the head (no expected_hash → ordinary update).
        db.update(rid, {"content": "v2-other"})
        # Our CAS with the now-stale hash must be refused.
        with pytest.raises(ConcurrentModificationError) as ei:
            db.update(rid, {"content": "v2-mine"}, expected_hash=stale)
        err = ei.value
        assert err.expected_hash == stale
        assert err.actual_hash != stale
        # Head is now v2-other (version 2); our attempt would have been v3.
        assert err.current_version == 2
        assert err.attempted_version == 3

    def test_conflict_points_to_current_head_for_retry(self, db):
        rid = _write(db, "v1")
        stale = _hash_of(db, rid)
        winner_id, _ = db.update(rid, {"content": "v2-other"})
        with pytest.raises(ConcurrentModificationError) as ei:
            db.update(rid, {"content": "v2-mine"}, expected_hash=stale)
        # current_id is what the caller should re-read to retry.
        assert ei.value.current_id == winner_id
        # And retrying against the fresh head succeeds.
        fresh = _hash_of(db, winner_id)
        new_id, old_id = db.update(winner_id, {"content": "v3-mine"},
                                   expected_hash=fresh)
        assert old_id == winner_id
        assert db.get_current(rid).data["content"] == "v3-mine"

    def test_refused_write_persists_nothing(self, db):
        rid = _write(db, "v1")
        stale = _hash_of(db, rid)
        db.update(rid, {"content": "v2-other"})
        head_before = db.get_current(rid).id
        with pytest.raises(ConcurrentModificationError):
            db.update(rid, {"content": "v2-mine"}, expected_hash=stale)
        # No new version was created by the refused write.
        assert db.get_current(rid).id == head_before
        assert len(db.get_history(rid)) == 2  # v1, v2-other only

    def test_error_to_dict_is_structured(self, db):
        rid = _write(db, "v1")
        stale = _hash_of(db, rid)
        db.update(rid, {"content": "v2-other"})
        with pytest.raises(ConcurrentModificationError) as ei:
            db.update(rid, {"content": "x"}, expected_hash=stale)
        d = ei.value.to_dict()
        assert d["error"] == "ConcurrentModificationError"
        assert d["expected_hash"] == stale
        assert set(d) >= {
            "record_id", "expected_hash", "actual_hash",
            "current_version", "attempted_version", "current_id",
        }


# ── CAS resolves to the HEAD even when passed an older id ───────────────────

class TestCasResolvesHead:
    def test_cas_on_old_id_with_head_hash_supersedes_head(self, db):
        """Passing an already-superseded id but the CURRENT head's hash is a
        valid CAS — it targets the head, not the stale id (no fork)."""
        rid = _write(db, "v1")
        head_id, _ = db.update(rid, {"content": "v2"})
        head_hash = _hash_of(db, head_id)
        # Caller still holds the old id but read the fresh head hash.
        new_id, old_id = db.update(rid, {"content": "v3"},
                                   expected_hash=head_hash)
        assert old_id == head_id           # superseded the head, not v1
        assert db.get_current(rid).data["content"] == "v3"
        # Still strictly linear — no branch was created.
        assert len(db.current_versions(rid)) == 1
        assert db.current_versions(rid)[0].id == new_id


# ── Omitting expected_hash preserves branching (Feature #2) ─────────────────

class TestBranchingStillAllowedWithoutHash:
    def test_supersede_nonhead_without_hash_forks(self, db):
        rid = _write(db, "v1")
        v2, _ = db.update(rid, {"content": "v2"})
        # Deliberately fork from v1 (a non-head) — allowed without expected_hash.
        v2b, _ = db.update(rid, {"content": "v2-branch"})
        heads = db.current_versions(rid)
        assert len(heads) == 2
        assert {"v2", "v2-branch"} == {h.data["content"] for h in heads}


# ── Two racing writers are serialized: exactly one wins ─────────────────────

class TestConcurrentWritersSerialized:
    def test_two_threads_same_base_only_one_wins(self, db):
        rid = _write(db, "v1")
        base_hash = _hash_of(db, rid)

        results = {}
        barrier = threading.Barrier(2)

        def writer(tag):
            barrier.wait()  # maximize the race
            try:
                new_id, _ = db.update(rid, {"content": f"v2-{tag}"},
                                      expected_hash=base_hash)
                results[tag] = ("ok", new_id)
            except ConcurrentModificationError as e:
                results[tag] = ("conflict", e.current_id)

        t1 = threading.Thread(target=writer, args=("A",))
        t2 = threading.Thread(target=writer, args=("B",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        outcomes = sorted(v[0] for v in results.values())
        assert outcomes == ["conflict", "ok"], results
        # The single head reflects the winner; no silent lost update, no fork.
        heads = db.current_versions(rid)
        assert len(heads) == 1
        winner_tag = [t for t, v in results.items() if v[0] == "ok"][0]
        assert heads[0].data["content"] == f"v2-{winner_tag}"

    def test_many_writers_chain_stays_linear(self, db):
        """A pile-up of CAS writers, each re-reading the head and retrying on
        conflict, must produce a single linear chain with no lost writes."""
        rid = _write(db, "v0")
        n = 8
        committed = []
        commit_lock = threading.Lock()

        def writer(tag):
            while True:
                head = db.get_current(rid)
                try:
                    new_id, _ = db.update(
                        head.id, {"content": f"w{tag}"},
                        expected_hash=head.content_hash)
                    with commit_lock:
                        committed.append(tag)
                    return
                except ConcurrentModificationError:
                    continue  # re-read head and retry

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Every writer eventually committed exactly once.
        assert sorted(committed) == list(range(n))
        # Single head, and the chain length accounts for every commit.
        heads = db.current_versions(rid)
        assert len(heads) == 1
        assert len(db.get_history(rid)) == n + 1  # v0 + n writes

    def test_many_threads_across_handles_keep_one_complete_chain(self, tmp_path):
        """Separate handles for one store must share the write transaction.

        This is the failure mode that is easy to miss when all callers use
        one EmberDB object: each handle used to own a different lock, so a
        supersession sidecar could race with another handle and make a newly
        returned head unreadable.
        """
        store_path = tmp_path / "shared_store"
        first = EmberDB.connect(store_path)
        rid = _write(first, "v0")
        handles = [first] + [EmberDB.connect(store_path) for _ in range(3)]
        # This is the root-cause assertion, not a probabilistic symptom check:
        # every in-process handle for one physical store must serialize on the
        # same transaction lock.
        assert all(handle._writer.lock is first._writer.lock
                   for handle in handles)
        n = 16
        committed = []
        errors = []
        result_lock = threading.Lock()

        def writer(handle, tag):
            while True:
                try:
                    head = handle.get_current(rid)
                    if head is None:
                        raise AssertionError("current version disappeared")
                    handle.update(
                        head.id, {"content": f"w{tag}"},
                        expected_hash=head.content_hash)
                    with result_lock:
                        committed.append(tag)
                    return
                except ConcurrentModificationError:
                    continue
                except Exception as exc:
                    with result_lock:
                        errors.append(exc)
                    return

        threads = [threading.Thread(target=writer,
                                    args=(handles[i % len(handles)], i))
                   for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert sorted(committed) == list(range(n))
        history = first.get_history(rid)
        assert [record.version for record in history] == list(range(1, n + 2))
        assert first._store.record_count() == n + 1
        assert len({handle.get_current(rid).id for handle in handles}) == 1

    def test_independent_processes_serialize_physical_store_transactions(
        self, tmp_path,
    ):
        assert STORE_LOCK_BACKEND == "rust-pyo3"
        store_path = str(tmp_path / "process_store")
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        process_count = 6
        processes = [
            context.Process(
                target=_physical_store_process_writer,
                args=(store_path, tag, start_event, result_queue),
            )
            for tag in range(process_count)
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(30)

        assert [process.exitcode for process in processes] == [0] * process_count
        results = [result_queue.get(timeout=5) for _ in processes]
        assert all(status == "ok" for status, _ in results), results

        store = PhysicalStore(store_path)
        assert store.record_count() == process_count
        assert store.stats()["meta"]["record_count"] == process_count
        assert store.wal.recover() == []
