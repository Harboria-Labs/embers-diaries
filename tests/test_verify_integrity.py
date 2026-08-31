"""
Ember's Diaries — Independent Verification Suite for Feature #1
(Cryptographic Memory Identity)

This file is authored by the *verification* agent, deliberately kept separate
from the implementing agent's `tests/test_integrity.py` so the two never
collide in the shared working tree.

Its job is adversarial: assert the *guarantees the feature promises* (per
implementation record 0001), not merely that classes/fields exist. Where a
guarantee is currently broken, the corresponding test is expected to FAIL
until the implementation is corrected — the failure is the evidence.

Run: diaries/Scripts/python.exe -m pytest tests/test_verify_integrity.py -v
"""

from datetime import datetime, timezone

import pytest

from embers import EmberDB, EmberRecord, RecordIntegrityError, HASH_BACKEND
from embers.storage.format import decode, encode


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "verify_store")


def _head_record_path(db, record_id):
    """On-disk .ember path for a record, resolved via the physical store."""
    return db._store.records_dir / f"{record_id}.ember"


# ── Backend sanity ──────────────────────────────────────────────────────────

class TestBackend:
    def test_rust_backend_is_active(self):
        """The compiled PyO3 core must be the live hasher, not the fallback.

        If this reports 'python-fallback', the native extension failed to load
        and the whole 'Rust core' claim is hollow — worth knowing loudly.
        """
        assert HASH_BACKEND in ("rust-pyo3", "python-fallback")
        # We expect the built extension in this environment specifically:
        assert HASH_BACKEND == "rust-pyo3", (
            f"Expected the Rust core to be loaded, got {HASH_BACKEND!r}"
        )


# ── Sealing guarantees ──────────────────────────────────────────────────────

class TestSealing:
    def test_direct_write_is_sealed(self, db):
        """A record written via db.write() must carry a content hash."""
        r = EmberRecord(namespace="v", data={"content": "v1"})
        rid = db.write(r)
        stored = db.get(rid)
        assert stored.content_hash is not None
        assert stored.verify_integrity() is True

    def test_update_result_is_sealed(self, db):
        """REGRESSION GUARD: the record produced by update() must ALSO be sealed.

        Record 0001 states 'New writes are sealed before persistence.' An
        update is a new write (a new immutable version). If the superseding
        record is left unsealed, the *current* version of an updated memory
        has no integrity protection at all.
        """
        rid = db.write(EmberRecord(namespace="v", data={"content": "v1"}))
        new_id, _ = db.update(rid, {"content": "v2"})
        head = db.get(new_id)
        assert head.content_hash is not None, (
            "update() produced an UNSEALED record — the live/head version of "
            "an updated memory is not integrity-protected."
        )
        assert head.verify_integrity() is True

    def test_multistep_chain_is_cryptographically_linked(self, db):
        """Each version must bind to its parent's PERSISTED content hash.

        v3.parent_hash must equal the content_hash actually stored on v2 — not
        a hash recomputed on the fly because v2 was never sealed.
        """
        v1 = db.write(EmberRecord(namespace="v", data={"content": "v1"}))
        v2_id, _ = db.update(v1, {"content": "v2"})
        v3_id, _ = db.update(v2_id, {"content": "v3"})

        v2 = db.get(v2_id, include_superseded=True)
        v3 = db.get(v3_id)

        assert v2.content_hash is not None, "v2 (a superseded version) was never sealed"
        assert v3.parent_hash == v2.content_hash, (
            "Broken chain: v3.parent_hash does not match the hash persisted on v2."
        )


# ── Tamper detection ────────────────────────────────────────────────────────

class TestTamperDetection:
    def test_tampering_with_directly_written_record_is_detected(self, db):
        """On-disk mutation of a sealed record must be caught on read."""
        r = EmberRecord(namespace="v", data={"content": "genuine"})
        rid = db.write(r)

        path = _head_record_path(db, rid)
        blob = decode(path.read_bytes())
        blob["data"]["content"] = "tampered"
        path.write_bytes(encode(blob))

        with pytest.raises(RecordIntegrityError):
            db.get(rid)

    def test_tampering_with_updated_head_is_detected(self, db):
        """SECURITY GUARD: tampering with the *current* (update-created) version
        must be detected. This is the most important record in a chain — the
        one recall() returns — so if any record is protected, it must be.
        """
        rid = db.write(EmberRecord(namespace="v", data={"content": "v1"}))
        new_id, _ = db.update(rid, {"content": "v2"})

        path = _head_record_path(db, new_id)
        blob = decode(path.read_bytes())
        blob["data"]["content"] = "tampered-head"
        path.write_bytes(encode(blob))

        with pytest.raises(RecordIntegrityError):
            db.get(new_id)


# ── Determinism / persistence ────────────────────────────────────────────────

class TestDeterminism:
    def test_hash_survives_reconnect(self, tmp_path):
        """A sealed record must still verify after a full store reopen
        (exercises the msgpack encode → disk → decode → verify path)."""
        store = tmp_path / "reconnect_store"
        db1 = EmberDB.connect(store)
        rid = db1.write(EmberRecord(
            namespace="v",
            data={"content": "persist", "n": 42, "f": 0.125, "nested": {"b": 2, "a": 1}},
        ))
        original_hash = db1.get(rid).content_hash

        db2 = EmberDB.connect(store)
        reread = db2.get(rid)
        assert reread.content_hash == original_hash
        assert reread.verify_integrity() is True

    def test_numeric_and_nested_payload_roundtrips_without_false_tamper(self, db):
        """Floats/ints/nested dicts must not produce a hash mismatch purely from
        the msgpack round-trip (guards against a canonicalization/serialization
        drift that would flag genuine records as tampered)."""
        rid = db.write(EmberRecord(
            namespace="v",
            data={"ints": [1, 2, 3], "floats": [0.1, 0.2, 0.3], "map": {"z": 1, "a": 2}},
        ))
        # Re-read from disk (not the in-memory object) and verify.
        assert db.get(rid).verify_integrity() is True
