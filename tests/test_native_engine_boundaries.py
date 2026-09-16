"""Ownership tests for the correctness-critical Rust engine boundaries."""

import pytest

from embers import EmberDB, EmberRecord, SessionStatus
from embers.index.graph import GRAPH_BACKEND
from embers.storage.store import STORE_LOCK_BACKEND


def test_native_graph_is_durable_and_visible_without_checkpoint(tmp_path):
    path = tmp_path / "graph-store"
    first = EmberDB.connect(path)
    left = first.write(EmberRecord(namespace="g", data={"name": "left"}))
    right = first.write(EmberRecord(namespace="g", data={"name": "right"}))

    first.link(left, right, "caused_by")

    second = EmberDB.connect(path)
    assert GRAPH_BACKEND == "rust-pyo3"
    assert [record.id for record in second.neighbors(left)] == [right]


def test_native_record_transaction_bypasses_python_wal_orchestration(tmp_path):
    db = EmberDB.connect(tmp_path / "transaction-store")
    assert STORE_LOCK_BACKEND == "rust-pyo3"

    def python_wal_must_not_run(*args, **kwargs):
        raise AssertionError("Python WAL sequencing was called")

    db._store.wal.log = python_wal_must_not_run
    record_id = db.write(EmberRecord(namespace="native", data={"value": 1}))
    assert db.get(record_id).data == {"value": 1}
    assert db._store.wal.recover() == []


def test_native_session_machine_rejects_second_terminal_transition(tmp_path):
    db = EmberDB.connect(tmp_path / "session-store")
    session_id = db.start_session("agent-a")
    db.end_session(session_id, status=SessionStatus.COMPLETED)

    with pytest.raises(ValueError, match="already closed"):
        db.end_session(session_id, status=SessionStatus.ABANDONED)
    with pytest.raises(ValueError, match="already closed"):
        db.record_discovery(session_id, "late-discovery")


def test_native_provenance_enforces_attribution_policy(tmp_path):
    db = EmberDB.connect(
        tmp_path / "provenance-store", enforce_attribution=True)
    with pytest.raises(ValueError, match="enforces agent attribution"):
        db.write(EmberRecord(agent_id=""))
