"""Behavioral boundary tests for the first Rust WAL migration slice."""

import builtins

import pytest

from embers.core.record import EmberRecord
from embers.engine.wal import WAL_BACKEND, WALEntry, WriteAheadLog
from embers.storage.store import PhysicalStore


def test_native_wal_backend_is_active():
    assert WAL_BACKEND == "rust-pyo3", (
        "the live WAL append path must use the compiled PyO3 backend")


def test_pending_and_commit_frames_round_trip_through_native_append(
    tmp_path, monkeypatch,
):
    wal = WriteAheadLog(tmp_path)
    python_open = builtins.open

    def reject_python_append(path, mode="r", *args, **kwargs):
        if "a" in mode:
            raise AssertionError("WAL append fell back to Python file I/O")
        return python_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", reject_python_append)
    entry = wal.log("write", "record-1", {"content": "line one\nline two"})
    assert [item["wal_id"] for item in wal.recover()] == [entry.wal_id]

    wal.commit(entry.wal_id)
    assert wal.recover() == []
    frames = wal.path.read_bytes().splitlines()
    assert len(frames) == 2


def test_native_wal_rejects_unframed_multiline_payload(tmp_path):
    from embers._native import append_wal_line

    with pytest.raises(ValueError, match="one JSON line"):
        append_wal_line(str(tmp_path / "wal.jsonl"), b"{}\n{}")
    assert not (tmp_path / "wal.jsonl").exists()


def test_rust_owns_pending_and_commit_envelope_encoding(tmp_path, monkeypatch):
    wal = WriteAheadLog(tmp_path)

    def reject_python_envelope(self):
        raise AssertionError("Python encoded the WAL envelope")

    monkeypatch.setattr(WALEntry, "to_dict", reject_python_envelope)
    entry = wal.log("write", "record-1", {"value": 7})
    wal.commit(entry.wal_id)

    pending, committed = [
        __import__("json").loads(line)
        for line in wal.path.read_text(encoding="utf-8").splitlines()
    ]
    assert pending == {
        "wal_id": entry.wal_id,
        "operation": "write",
        "record_id": "record-1",
        "data": {"value": 7},
        "status": "PENDING",
        "timestamp": entry.timestamp,
    }
    assert committed["wal_id"] == entry.wal_id
    assert committed["status"] == "COMMITTED"


def test_python_checkpoint_keeps_native_pending_frame_compatible(tmp_path):
    wal = WriteAheadLog(tmp_path)
    pending = wal.log("write", "record-pending", {"value": 1})
    committed = wal.log("write", "record-committed", {"value": 2})
    wal.commit(committed.wal_id)

    wal.checkpoint()

    recovered = wal.recover()
    assert [item["wal_id"] for item in recovered] == [pending.wal_id]
    assert len(wal.path.read_bytes().splitlines()) == 1


def test_recovery_and_checkpoint_do_not_use_python_file_io(
    tmp_path, monkeypatch,
):
    wal = WriteAheadLog(tmp_path)
    pending = wal.log("write", "record-pending", {"value": 1})
    committed = wal.log("write", "record-committed", {"value": 2})
    wal.commit(committed.wal_id)

    def reject_python_file_io(*args, **kwargs):
        raise AssertionError("WAL recovery/checkpoint used Python file I/O")

    monkeypatch.setattr(builtins, "open", reject_python_file_io)
    assert [item["wal_id"] for item in wal.recover()] == [pending.wal_id]
    wal.checkpoint()
    assert [item["wal_id"] for item in wal.recover()] == [pending.wal_id]


def test_native_pending_frame_replays_through_python_recovery(tmp_path):
    store_path = tmp_path / "store"
    store = PhysicalStore(store_path)
    record = EmberRecord(namespace="recovery", data={"value": "from WAL"})
    record.seal()
    store.wal.log("write", record.id, record.to_dict())

    recovered_store = PhysicalStore(store_path)

    recovered = recovered_store.read(record.id)
    assert recovered is not None
    assert recovered.data == {"value": "from WAL"}
    assert recovered.verify_integrity()
    assert recovered_store.wal.recover() == []
