"""Batch 1 isolated-store regressions for candidate trust and conflict races."""

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os

import pytest

from embers import EmberDB, EmberRecord, ConflictStatus, RecordIntegrityError
from embers.storage.format import decode_index, encode_index
from embers.storage.format import encode


def _pair(db):
    a = db.write(EmberRecord(namespace="batch1", data={"content": "A"}))
    b = db.write(EmberRecord(namespace="batch1", data={"content": "B"}))
    return a, b


def _map_process(path, a, b, start, result):
    try:
        db = EmberDB.connect(path)
        if not start.wait(20):
            raise TimeoutError("mapping start timed out")
        result.put(("ok", db.map_conflict(a, b)))
    except BaseException as exc:
        result.put(("error", f"{type(exc).__name__}: {exc}"))


def _status_process(path, cid, status, start, result):
    try:
        db = EmberDB.connect(path)
        if not start.wait(20):
            raise TimeoutError("status start timed out")
        try:
            result.put(("ok", db.update_conflict_status(cid, status, "reviewed")))
        except ValueError as exc:
            result.put(("closed", str(exc)))
    except BaseException as exc:
        result.put(("error", f"{type(exc).__name__}: {exc}"))


def _crash_during_map(path, a, b):
    db = EmberDB.connect(path)
    db._writer._notify = lambda *_: os._exit(57)
    db.map_conflict(a, b)


def _crash_before_supersession(path, rid):
    db = EmberDB.connect(path)
    db._writer._mark_superseded = lambda *_: os._exit(58)
    db.update_conflict_status(rid, ConflictStatus.RESOLVED, "reviewed")


def test_stale_nonempty_and_wrong_type_master_rebuilt_on_reopen(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    index_file = path / "indexes" / "master.json"
    snapshot = decode_index(index_file.read_bytes())
    snapshot["records"][cid]["record_type"] = "document"
    snapshot["records"][cid]["namespace"] = "wrong"
    snapshot["records"].pop(a)
    index_file.write_bytes(encode_index(snapshot))

    reopened = EmberDB.connect(path)
    assert {c.conflict_id for c in reopened.conflicts_for(a)} == {cid}
    assert reopened._master_index.get_meta(cid)["record_type"] == "conflict"
    assert reopened._master_index.get_meta(a) is not None


def test_legacy_master_without_epoch_is_rebuilt(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    index_file = path / "indexes" / "master.json"
    snapshot = decode_index(index_file.read_bytes())
    snapshot.pop("indexed_epoch")
    index_file.write_bytes(encode_index(snapshot))
    reopened = EmberDB.connect(path)
    assert [c.conflict_id for c in reopened.conflicts_for(a)] == [cid]
    assert reopened._master_index.indexed_epoch == reopened._store.index_epoch()


def test_in_memory_wrong_type_candidate_fails_closed(tmp_path):
    db = EmberDB.connect(tmp_path / "store")
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    db._master_index._records[cid]["record_type"] = "document"
    with pytest.raises(ValueError, match="candidate metadata changed"):
        db.conflicts_for(a)


def test_committed_write_with_failed_index_callback_is_reconciled(tmp_path, monkeypatch):
    db = EmberDB.connect(tmp_path / "store")
    before = db._store.index_epoch()
    original = db._index_record
    monkeypatch.setattr(db, "_index_record", lambda record: (_ for _ in ()).throw(RuntimeError("index offline")))
    with pytest.warns(RuntimeWarning, match="durable"):
        rid = db.write(EmberRecord(namespace="batch1", data={"content": "committed"}))
    assert db._store.index_epoch() > before
    monkeypatch.setattr(db, "_index_record", original)
    assert rid in db._master_index.get_namespace_ids("batch1")
    assert db._master_index.indexed_epoch == db._store.index_epoch()


def test_interrupted_native_transaction_leaves_only_conservative_epoch(tmp_path, monkeypatch):
    import embers.storage.store as store_module
    db = EmberDB.connect(tmp_path / "store")
    before = db._store.index_epoch()
    monkeypatch.setattr(store_module, "_write_record_transaction",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("before native commit")))
    with pytest.raises(RuntimeError, match="before native commit"):
        db.write(EmberRecord(namespace="batch1", data={"content": "not committed"}))
    # A mocked function never enters Rust. The compatibility wrapper advances
    # first; the new native backend advances inside the actual transaction.
    assert db._store.index_epoch() == before + (0 if store_module.NATIVE_SOURCE_EPOCH else 1)
    assert db._master_index.get_namespace_ids("batch1") == []
    assert db._master_index.indexed_epoch == db._store.index_epoch()


def test_new_native_direct_transaction_invalidates_candidates(tmp_path):
    from embers import _native
    if not hasattr(_native, "source_epoch_protocol_version"):
        pytest.skip("installed native module predates source epoch protocol")
    db = EmberDB.connect(tmp_path / "store")
    record = EmberRecord(namespace="batch1", data={"content": "native"})
    record.seal()
    payload = record.to_dict()
    before = db._store.index_epoch()
    with db._store.lock:
        _native.write_record_transaction(
            str(db._store.root), "batch1-native", "2026-10-16T00:00:00Z",
            "2026-10-16T00:00:01Z", record.id,
            json.dumps(payload, separators=(",", ":")).encode(),
            encode(payload), None)
    assert db._store.index_epoch() == before + 1
    assert record.id in db._master_index.get_namespace_ids("batch1")


def test_new_native_direct_supersession_advances_epoch(tmp_path):
    from embers import _native
    if not hasattr(_native, "source_epoch_protocol_version"):
        pytest.skip("installed native module predates source epoch protocol")
    db = EmberDB.connect(tmp_path / "store")
    rid = db.write(EmberRecord(namespace="batch1", data={"content": "old"}))
    child, _ = db.update(rid, {"content": "new"})
    before = db._store.index_epoch()
    with db._store.lock:
        _native.write_supersession(str(db._store.root), rid, child,
                                   "2026-10-16T00:00:00Z")
    assert db._store.index_epoch() == before + 1
    assert db._master_index.get_superseded_by(rid) == child


def test_healthy_conflict_query_reads_no_unrelated_records_or_directory(tmp_path, monkeypatch):
    db = EmberDB.connect(tmp_path / "store")
    a, b = _pair(db)
    for i in range(20):
        db.write(EmberRecord(namespace="batch1", data={"content": str(i)}))
    cid = db.map_conflict(a, b)
    reads = []
    original = db._store.read
    monkeypatch.setattr(db._store, "read", lambda rid: (reads.append(rid), original(rid))[1])
    monkeypatch.setattr(db._store, "all_ids", lambda: (_ for _ in ()).throw(AssertionError("directory scan")))
    assert {c.conflict_id for c in db.conflicts_for(a)} == {cid}
    assert reads == [a, cid, cid]


def test_other_handle_write_and_deprecation_visible_without_enumeration(tmp_path, monkeypatch):
    path = tmp_path / "store"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    rid = first.write(EmberRecord(namespace="batch1", data={"content": "new"}))
    scans = []
    original = second._store.all_ids
    monkeypatch.setattr(second._store, "all_ids", lambda: (scans.append(1), original())[1])
    assert rid in second._master_index.get_namespace_ids("batch1")
    assert len(scans) == 1  # explicit catch-up reconciliation
    scans.clear()
    assert rid in second._master_index.get_namespace_ids("batch1")
    assert scans == []  # healthy routine lookup
    first.deprecate(rid)
    assert rid not in second._master_index.get_namespace_ids("batch1")
    assert len(scans) == 1  # sidecar-only change triggers catch-up
    scans.clear()
    assert rid in second._master_index.get_namespace_ids("batch1", include_deprecated=True)
    assert scans == []


def test_interrupted_supersession_repairs_unique_child_on_reopen(tmp_path, monkeypatch):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    rid = db.write(EmberRecord(namespace="batch1", data={"content": "old"}))
    monkeypatch.setattr(db._writer, "_mark_superseded", lambda *_: (_ for _ in ()).throw(RuntimeError("sidecar interrupted")))
    with pytest.raises(RuntimeError, match="sidecar interrupted"):
        db.update(rid, {"content": "new"})
    reopened = EmberDB.connect(path)
    history = reopened.get_history(rid)
    assert [r.data["content"] for r in history] == ["old", "new"]


def test_wrong_supersession_sidecar_fails_closed(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    rid = db.write(EmberRecord(namespace="batch1", data={"content": "old"}))
    db.update(rid, {"content": "new"})
    sidecar = path / "supersessions" / f"{rid}.superseded"
    value = decode_index(sidecar.read_bytes())
    value["new_id"] = "wrong-child"
    sidecar.write_bytes(encode_index(value))
    with pytest.raises(ValueError, match="Inconsistent supersession"):
        EmberDB.connect(path)


def test_concurrent_reverse_mapping_and_terminal_status_are_serialized(tmp_path):
    path = tmp_path / "store"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    a, b = _pair(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(first.map_conflict, a, b, "agent-a")
        right = pool.submit(second.map_conflict, b, a, "agent-b")
        assert left.result() == right.result()
    cid = left.result()
    assert len(first.conflict_records()) == 1
    def finish(db, status):
        try:
            return ("ok", db.update_conflict_status(cid, status, "reviewed"))
        except ValueError as exc:
            return ("closed", str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(finish, first, ConflictStatus.RESOLVED)
        right = pool.submit(finish, second, ConflictStatus.ACCEPTED_BOTH)
        outcomes = [left.result(), right.result()]
    assert sorted(result[0] for result in outcomes) == ["closed", "ok"]
    assert len(EmberDB.connect(path).get_history(cid)) == 2


def test_relevant_conflict_corruption_still_fails(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    record = path / "records" / f"{cid}.ember"
    raw = bytearray(record.read_bytes())
    raw[-1] ^= 1
    record.write_bytes(raw)
    with pytest.raises((RecordIntegrityError, ValueError)):
        db.conflicts_for(a)


def test_independent_processes_map_one_pair_and_reopen(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    ctx = multiprocessing.get_context("spawn")
    start, result = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_map_process, args=(str(path), x, y, start, result))
               for x, y in ((a, b), (b, a))]
    for worker in workers:
        worker.start()
    start.set()
    outcomes = [result.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == "ok"
    reopened = EmberDB.connect(path)
    assert len(reopened.conflict_records()) == 1
    assert len(db.conflicts_for(a)) == 1  # warm handle catches process changes
    assert reopened._master_index.indexed_epoch == reopened._store.index_epoch()


def test_independent_processes_choose_one_terminal_status(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    ctx = multiprocessing.get_context("spawn")
    start, result = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_status_process, args=(str(path), cid, status, start, result))
               for status in (ConflictStatus.RESOLVED, ConflictStatus.ACCEPTED_BOTH)]
    for worker in workers:
        worker.start()
    start.set()
    outcomes = [result.get(timeout=30) for _ in workers]
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0
    assert sorted(item[0] for item in outcomes) == ["closed", "ok"]
    reopened = EmberDB.connect(path)
    assert len(reopened.get_history(cid)) == 2
    assert reopened.get_conflict(cid).status in {
        ConflictStatus.RESOLVED, ConflictStatus.ACCEPTED_BOTH}


def test_wal_checkpoint_keeps_epoch_and_candidates(tmp_path, monkeypatch):
    path = tmp_path / "store"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    a, b = _pair(first)
    cid = first.map_conflict(a, b)
    second.checkpoint()
    epoch = first._store.index_epoch()
    assert second._master_index.indexed_epoch == epoch
    monkeypatch.setattr(first._store, "all_ids", lambda: (_ for _ in ()).throw(AssertionError("directory scan")))
    assert [c.conflict_id for c in first.conflicts_for(a)] == [cid]
    assert EmberDB.connect(path)._store.index_epoch() == epoch


def test_killed_mapping_process_recovers_committed_conflict(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_crash_during_map, args=(str(path), a, b))
    worker.start()
    worker.join(timeout=30)
    assert worker.exitcode == 57
    reopened = EmberDB.connect(path)
    conflicts = reopened.conflicts_for(a)
    assert len(conflicts) == 1
    assert reopened.map_conflict(b, a) == conflicts[0].conflict_id
    assert [r.id for r in reopened.conflicts(a)] == [b]


def test_killed_status_process_repairs_unique_supersession(tmp_path):
    path = tmp_path / "store"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_crash_before_supersession,
                         args=(str(path), cid))
    worker.start()
    worker.join(timeout=30)
    assert worker.exitcode == 58
    reopened = EmberDB.connect(path)
    assert [r.version for r in reopened.get_history(cid)] == [1, 2]
    assert reopened.get_conflict(cid).status == ConflictStatus.RESOLVED
