"""
Ember's Diaries — Feature #7 (Conflict Engine)

Behavioral tests mapped to the spec's §7 requirements:

    Conflict (semantic contradiction between two durable memories)
        * a conflict is mapped as a first-class CONFLICT record with the §7
          shape (memory_a / memory_b / detected_* / type / status / resolution)
        * mapping is SYMMETRIC — map(A, B) and map(B, A) are the same conflict
        * mapping is idempotent while a conflict is live — no duplicate records
        * the contradiction is visible via the graph too (conflicts())
        * NEITHER contradicting memory is ever deleted — the spec's absolute rule
        * the lifecycle (open → investigating → resolved | accepted_both) is
          append-only: a transition is a NEW version, full triage history kept
        * a closed conflict does not block mapping a fresh one if the
          contradiction resurfaces
        * conflicts survive a rebuild-from-store (§15)

Run: diaries/Scripts/python.exe -m pytest tests/test_conflict_engine.py -v
"""

import pytest

from embers import (
    EmberDB, EmberRecord, RecordType, ConflictType, ConflictStatus,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "conflict_store")


def _memory(db, content, namespace="cf"):
    """Write a durable memory and return its id."""
    return db.write(EmberRecord(
        namespace=namespace, data={"content": content}, tags=["memory"]))


def _pair(db):
    """Two memories with contradictory claims."""
    a = _memory(db, "The system uses PostgreSQL.")
    b = _memory(db, "The system uses MongoDB.")
    return a, b


# ── Mapping a conflict (§7 shape) ───────────────────────────────────────────────

class TestMapConflict:
    def test_maps_first_class_conflict_record(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b, detected_by="triage-agent",
                              note="storage backend disagreement")

        c = db.get_conflict(cid)
        assert c is not None
        # The §7 shape is all present.
        assert {c.memory_a, c.memory_b} == {a, b}
        assert c.conflict_type == ConflictType.SEMANTIC
        assert c.status == ConflictStatus.OPEN
        assert c.detected_by == "triage-agent"
        assert c.note == "storage backend disagreement"
        assert c.resolution == ""

        # It is stored as a real CONFLICT record.
        rec = db.get(cid)
        assert rec.record_type == RecordType.CONFLICT

    def test_conflict_is_symmetric(self, db):
        a, b = _pair(db)
        # Map in one direction, then attempt the reverse — same conflict.
        cid1 = db.map_conflict(a, b)
        cid2 = db.map_conflict(b, a)
        assert cid1 == cid2
        # Only one conflict record exists for the pair.
        assert len(db.conflict_records()) == 1

    def test_mapping_is_idempotent_while_live(self, db):
        a, b = _pair(db)
        cid1 = db.map_conflict(a, b)
        cid2 = db.map_conflict(a, b)
        cid3 = db.map_conflict(a, b, detected_by="someone-else")
        assert cid1 == cid2 == cid3
        assert len(db.conflict_records()) == 1

    def test_contradiction_visible_on_graph(self, db):
        a, b = _pair(db)
        db.map_conflict(a, b)
        # conflicts() reads the symmetric CONTRADICTS edge — both directions.
        assert b in [r.id for r in db.conflicts(a)]
        assert a in [r.id for r in db.conflicts(b)]

    def test_cannot_conflict_a_memory_with_itself(self, db):
        a = _memory(db, "self")
        with pytest.raises(ValueError):
            db.map_conflict(a, a)

    def test_missing_memory_raises(self, db):
        a = _memory(db, "exists")
        with pytest.raises(KeyError):
            db.map_conflict(a, "does-not-exist")


# ── The absolute rule: neither memory is destroyed ─────────────────────────────

class TestNeitherMemoryDeleted:
    def test_mapping_leaves_both_memories_intact(self, db):
        a, b = _pair(db)
        db.map_conflict(a, b)
        assert db.exists(a) and db.exists(b)
        assert db.get(a).data["content"] == "The system uses PostgreSQL."
        assert db.get(b).data["content"] == "The system uses MongoDB."

    def test_resolving_leaves_both_memories_intact(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b)
        db.resolve_conflict(cid, resolution="PostgreSQL is correct as of v2.")
        # The resolution is a recorded DECISION — neither memory is deleted.
        assert db.exists(a) and db.exists(b)
        assert db.get(a) is not None
        assert db.get(b) is not None


# ── Lifecycle: append-only transitions ─────────────────────────────────────────

class TestLifecycle:
    def test_resolve_records_status_and_resolution(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b)
        db.resolve_conflict(cid, resolution="Chose PostgreSQL.")
        c = db.get_conflict(cid)
        assert c.status == ConflictStatus.RESOLVED
        assert c.resolution == "Chose PostgreSQL."

    def test_investigating_then_accepted_both(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b)
        db.update_conflict_status(cid, ConflictStatus.INVESTIGATING)
        assert db.get_conflict(cid).status == ConflictStatus.INVESTIGATING
        db.update_conflict_status(
            cid, ConflictStatus.ACCEPTED_BOTH,
            resolution="Both true in different deployments.")
        c = db.get_conflict(cid)
        assert c.status == ConflictStatus.ACCEPTED_BOTH
        assert c.resolution == "Both true in different deployments."

    def test_transition_is_append_only_history_preserved(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b)
        db.update_conflict_status(cid, ConflictStatus.INVESTIGATING)
        db.resolve_conflict(cid, resolution="done")
        # Full triage history is retained (open → investigating → resolved).
        history = db.get_history(cid)
        statuses = [h.data["status"] for h in history]
        assert statuses == ["open", "investigating", "resolved"]

    def test_open_conflicts_filter(self, db):
        a, b = _pair(db)
        c = _memory(db, "The cache TTL is 60s.")
        d = _memory(db, "The cache TTL is 300s.")
        open_cid = db.map_conflict(a, b)
        closed_cid = db.map_conflict(c, d)
        db.resolve_conflict(closed_cid, resolution="300s wins")

        open_only = db.conflict_records(status=ConflictStatus.OPEN)
        open_ids = [x.conflict_id for x in open_only]
        assert open_cid in open_ids
        assert closed_cid not in open_ids

    def test_conflicts_for_memory(self, db):
        a, b = _pair(db)
        c = _memory(db, "unrelated memory")
        cid = db.map_conflict(a, b)
        involving_a = db.conflicts_for(a)
        assert [x.conflict_id for x in involving_a] == [cid]
        assert db.conflicts_for(c) == []

    def test_conflicts_for_excludes_closed_by_default(self, db):
        a, b = _pair(db)
        cid = db.map_conflict(a, b)
        db.resolve_conflict(cid, resolution="done")
        assert db.conflicts_for(a) == []
        # But the full history is available on request.
        assert [x.conflict_id for x in db.conflicts_for(a, include_closed=True)] == [cid]


# ── Re-mapping after a conflict is closed ──────────────────────────────────────

class TestReopenAfterClose:
    def test_closed_conflict_does_not_block_a_fresh_mapping(self, db):
        a, b = _pair(db)
        first = db.map_conflict(a, b)
        db.resolve_conflict(first, resolution="resolved once")
        # The contradiction resurfaces → a NEW conflict may be mapped.
        second = db.map_conflict(a, b)
        assert second != first
        # Two conflict lineages now exist for the pair (one closed, one open).
        all_for_pair = db.conflicts_for(a, include_closed=True)
        assert {x.conflict_id for x in all_for_pair} == {first, second}


# ── Backwards compatibility (§15) ──────────────────────────────────────────────

class TestRebuildFromStore:
    def test_conflicts_survive_reload(self, db, tmp_path):
        a, b = _pair(db)
        cid = db.map_conflict(a, b, detected_by="agent-x")
        db.update_conflict_status(cid, ConflictStatus.INVESTIGATING)

        # Reconnect from disk — indexes rebuild from the store.
        db2 = EmberDB.connect(tmp_path / "conflict_store")
        c = db2.get_conflict(cid)
        assert c is not None
        assert c.status == ConflictStatus.INVESTIGATING
        assert {c.memory_a, c.memory_b} == {a, b}
