"""Behavioral tests for Rust-owned physical-store primitives."""

import builtins

from embers.core.record import EmberRecord
from embers.storage.store import PhysicalStore, STORE_LOCK_BACKEND


def test_native_atomic_write_never_overwrites_existing_file(tmp_path):
    from embers._native import atomic_write_new

    destination = tmp_path / "record.ember"
    assert atomic_write_new(str(destination), b"first") is True
    assert atomic_write_new(str(destination), b"second") is False
    assert destination.read_bytes() == b"first"


def test_physical_record_publish_does_not_use_python_file_io(
    tmp_path, monkeypatch,
):
    assert STORE_LOCK_BACKEND == "rust-pyo3"
    store = PhysicalStore(tmp_path / "store")
    record = EmberRecord(namespace="native", data={"value": 1})
    record.seal()

    def reject_python_file_io(*args, **kwargs):
        raise AssertionError("record persistence used Python file I/O")

    monkeypatch.setattr(builtins, "open", reject_python_file_io)
    store._write_record_file(record)
    assert store.exists(record.id)
    assert store.read(record.id).verify_integrity()
