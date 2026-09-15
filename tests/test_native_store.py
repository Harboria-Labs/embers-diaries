"""Behavioral tests for Rust-owned physical-store primitives."""

import builtins

from embers.core.record import EmberRecord
from embers.storage.format import STORAGE_FORMAT_BACKEND, decode, encode
from embers.storage.store import PhysicalStore, STORE_LOCK_BACKEND


def test_native_atomic_write_never_overwrites_existing_file(tmp_path):
    from embers._native import atomic_write_new

    destination = tmp_path / "record.ember"
    assert atomic_write_new(str(destination), b"first") is True
    assert atomic_write_new(str(destination), b"second") is False
    assert destination.read_bytes() == b"first"


def test_native_atomic_replace_publishes_complete_contents(tmp_path):
    from embers._native import atomic_replace

    destination = tmp_path / "metadata.json"
    destination.write_bytes(b"old")
    atomic_replace(str(destination), b"complete-new-value")
    assert destination.read_bytes() == b"complete-new-value"


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


def test_native_record_codec_round_trips_nested_and_binary_values():
    assert STORAGE_FORMAT_BACKEND == "rust-rmpv"
    record = {
        "text": "ember 🔥",
        "binary": b"\x00\xff",
        "numbers": [-1, 0, 2**63, 0.125],
        "nested": {"items": [True, None, {"value": 7}]},
    }
    assert decode(encode(record)) == record


def test_native_decoder_reads_legacy_json_records():
    raw = b'{"id":"legacy","version":1,"content_hash":null}'
    assert decode(raw) == {
        "id": "legacy",
        "version": 1,
        "content_hash": None,
    }
