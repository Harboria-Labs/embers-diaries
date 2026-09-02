"""Behavioral tests for cryptographic memory identity."""

from datetime import datetime, timezone

import pytest

from embers import EmberDB, EmberRecord, RecordIntegrityError
from embers.storage.format import decode, encode


def fixed_record(**overrides):
    values = {
        "id": "11111111-1111-1111-1111-111111111111",
        "namespace": "integrity",
        "data": {"content": "stable", "nested": {"b": 2, "a": 1}},
        "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "written_by": "test-agent",
        "tags": ["hashing"],
    }
    values.update(overrides)
    return EmberRecord(**values)


def test_identical_canonical_content_produces_same_hash():
    first = fixed_record()
    second = fixed_record(data={"nested": {"a": 1, "b": 2}, "content": "stable"})

    assert first.compute_content_hash() == second.compute_content_hash()


def test_changing_content_changes_hash():
    first = fixed_record()
    changed = fixed_record(data={"content": "changed", "nested": {"a": 1, "b": 2}})

    assert first.compute_content_hash() != changed.compute_content_hash()


def test_changing_parent_changes_hash():
    first = fixed_record(parent_hash="a" * 64)
    changed = fixed_record(parent_hash="b" * 64)

    assert first.compute_content_hash() != changed.compute_content_hash()


def test_update_preserves_historical_hash_and_links_parent(tmp_path):
    db = EmberDB.connect(tmp_path / "store")
    original = fixed_record()
    original_id = db.write(original)
    original_hash = original.content_hash

    new_id, _ = db.update(original_id, {"content": "version two"})
    old = db.get(original_id, include_superseded=True)
    new = db.get(new_id)

    assert old.content_hash == original_hash
    assert old.verify_integrity() is True
    assert new.version == 2
    assert new.parent_hash == original_hash
    assert new.content_hash != original_hash


def test_in_memory_mutation_is_detected_after_sealing():
    record = fixed_record()
    record.seal()
    record.data["content"] = "tampered"

    with pytest.raises(RecordIntegrityError):
        record.verify_integrity()


def test_persisted_mutation_is_detected_on_read(tmp_path):
    db = EmberDB.connect(tmp_path / "store")
    record = fixed_record()
    db.write(record)

    record_path = tmp_path / "store" / "records" / f"{record.id}.ember"
    stored = decode(record_path.read_bytes())
    stored["data"]["content"] = "tampered on disk"
    record_path.write_bytes(encode(stored))

    with pytest.raises(RecordIntegrityError):
        db.get(record.id)


def test_legacy_unhashed_record_remains_loadable():
    legacy = fixed_record().to_dict()
    legacy.pop("content_hash")
    legacy.pop("parent_hash")
    legacy.pop("version")

    restored = EmberRecord.from_dict(legacy)

    assert restored.version == 1
    assert restored.parent_hash is None
    assert restored.content_hash is None

