"""
Ember's Diaries — Feature #3: Provenance (WHO / WHEN / WHERE / WHY)

Behavioral tests for structured provenance, mapped to the spec's own
testing requirements (§16 → Provenance):

    * author preserved
    * agent preserved
    * session preserved
    * timestamps preserved
    * reason preserved

plus the guarantees provenance must not break or must newly provide:

    * §16 Hashing — historical hash preservation (a record with no provenance
      hashes exactly as it did before provenance fields existed)
    * provenance is folded into the content hash, so on-disk tampering with
      WHO/WHY is detected on read (relevant to "do not trust a client-supplied
      agent_id blindly")
    * §16 Multi-agent — isolated agent identities; §16 Sessions — session →
      memories, via db.get_by_agent() / db.get_by_session()
    * provenance survives a full store reopen (index rebuild + persist/load)

Run: diaries/Scripts/python.exe -m pytest tests/test_provenance.py -v
"""

from datetime import datetime

import pytest

from embers import EmberDB, EmberRecord, RecordIntegrityError
from embers.storage.format import decode, encode
from embers.integration.memory_protocol import MemoryProtocol


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "prov_store")


def _ember_path(db, record_id):
    """On-disk .ember path for a record."""
    return db._store.records_dir / f"{record_id}.ember"


# ── The record carries structured provenance ─────────────────────────────────

class TestProvenanceFields:
    def test_record_carries_and_persists_provenance(self, db):
        """A written record must retain every provenance field after a disk
        round-trip (write → read back from the .ember file)."""
        rid = db.write(EmberRecord(
            namespace="v",
            data={"content": "db uses MVCC"},
            written_by="research-agent",
            origin="postgres-docs",
            agent_id="research-agent-03",
            session_id="session-8821",
            creation_reason="Verified database behavior during investigation",
            derived_from=["memory_123", "memory_456"],
        ))
        r = db.get(rid)
        assert r.agent_id == "research-agent-03"
        assert r.session_id == "session-8821"
        assert r.creation_reason == "Verified database behavior during investigation"
        assert r.derived_from == ["memory_123", "memory_456"]
        # existing columns serve the remaining spec fields
        assert r.written_by == "research-agent"   # author
        assert r.origin == "postgres-docs"         # source

    def test_provenance_accessor_answers_the_questions(self, db):
        """record.provenance() must answer WHO/WHICH-AGENT/SESSION/WHEN/WHY/
        WHERE/DERIVED-FROM as a single structured dict (spec §3)."""
        rid = db.write(EmberRecord(
            namespace="v",
            data={"content": "x"},
            written_by="agent-03",
            origin="unit-test",
            agent_id="research-agent-03",
            session_id="session-8821",
            creation_reason="Two independent tests produced the same result.",
            derived_from=["memory_123"],
        ))
        p = db.get(rid).provenance()
        assert p["author"] == "agent-03"
        assert p["agent_id"] == "research-agent-03"
        assert p["session_id"] == "session-8821"
        assert p["creation_reason"] == "Two independent tests produced the same result."
        assert p["source"] == "unit-test"
        assert p["derived_from"] == ["memory_123"]
        # timestamp present and ISO-parseable
        assert datetime.fromisoformat(p["timestamp"])

    def test_db_get_provenance(self, db):
        """db.get_provenance(id) returns the structured dict; None when absent."""
        rid = db.write(EmberRecord(namespace="v", data={"content": "y"},
                                   agent_id="a1", session_id="s1"))
        p = db.get_provenance(rid)
        assert p["agent_id"] == "a1" and p["session_id"] == "s1"
        assert db.get_provenance("does-not-exist") is None


# ── Provenance is part of the cryptographic identity ──────────────────────────

class TestProvenanceInHash:
    def test_provenance_changes_the_hash(self, db):
        """Two records identical except for provenance must hash differently —
        proof that WHO/WHY is actually bound into the content hash, not just
        stored alongside it."""
        base = dict(namespace="v", data={"content": "same"},
                    created_at=datetime(2026, 1, 1))
        r_plain = EmberRecord(id="fixed-1", **base)
        r_agent = EmberRecord(id="fixed-1", agent_id="agent-X", **base)
        assert r_plain.compute_content_hash() != r_agent.compute_content_hash()

    def test_default_provenance_does_not_affect_hash(self, db):
        """Provenance fields explicitly set to their defaults must be omitted
        from the hash payload — so they cannot perturb the identity of a record
        that carries no provenance."""
        base = dict(id="fixed-2", namespace="v", data={"content": "same"},
                    created_at=datetime(2026, 1, 1))
        r_none = EmberRecord(**base)
        r_defaults = EmberRecord(agent_id=None, session_id=None,
                                 creation_reason=None, derived_from=[], **base)
        assert r_none.compute_content_hash() == r_defaults.compute_content_hash()
        for k in ("agent_id", "session_id", "creation_reason", "derived_from"):
            assert k not in r_none.canonical_hash_payload()

    def test_historical_record_without_provenance_still_verifies(self, db):
        """§15 / §16 historical-hash-preservation: a record sealed BEFORE
        provenance existed (its on-disk dict has no provenance keys at all)
        must still verify byte-for-byte under the current code."""
        r = EmberRecord(namespace="v", data={"content": "legacy"})
        r.seal()
        sealed_hash = r.content_hash

        # Simulate an old .ember file: the provenance keys simply do not exist.
        d = r.to_dict()
        for k in ("agent_id", "session_id", "creation_reason", "derived_from"):
            d.pop(k, None)

        restored = EmberRecord.from_dict(d)
        assert restored.content_hash == sealed_hash
        assert restored.verify_integrity() is True   # recomputed hash matches

    def test_tampering_with_provenance_is_detected(self, db):
        """SECURITY: rewriting a record's agent_id on disk (impersonation) must
        be caught on read, because provenance is inside the content hash."""
        rid = db.write(EmberRecord(namespace="v", data={"content": "z"},
                                   agent_id="honest-agent"))
        path = _ember_path(db, rid)
        blob = decode(path.read_bytes())
        blob["agent_id"] = "impostor-agent"
        path.write_bytes(encode(blob))

        with pytest.raises(RecordIntegrityError):
            db.get(rid)


# ── Multi-agent / session provenance queries ──────────────────────────────────

class TestProvenanceQueries:
    def test_isolated_agent_identities(self, db):
        """§16 Multi-agent: get_by_agent returns exactly one agent's records."""
        a1 = db.write(EmberRecord(namespace="v", data={"c": 1}, agent_id="alpha"))
        a2 = db.write(EmberRecord(namespace="v", data={"c": 2}, agent_id="alpha"))
        b1 = db.write(EmberRecord(namespace="v", data={"c": 3}, agent_id="beta"))

        alpha_ids = {r.id for r in db.get_by_agent("alpha")}
        beta_ids = {r.id for r in db.get_by_agent("beta")}
        assert alpha_ids == {a1, a2}
        assert beta_ids == {b1}
        assert alpha_ids.isdisjoint(beta_ids)

    def test_session_to_memories(self, db):
        """§16 Sessions: get_by_session returns exactly that session's writes."""
        s1a = db.write(EmberRecord(namespace="v", data={"c": 1}, session_id="s1"))
        s1b = db.write(EmberRecord(namespace="v", data={"c": 2}, session_id="s1"))
        s2a = db.write(EmberRecord(namespace="v", data={"c": 3}, session_id="s2"))

        assert {r.id for r in db.get_by_session("s1")} == {s1a, s1b}
        assert {r.id for r in db.get_by_session("s2")} == {s2a}
        assert db.get_by_session("nonexistent") == []

    def test_agent_query_rebuilds_from_store_on_reconnect(self, tmp_path):
        """Reopen WITHOUT an explicit checkpoint: the master index is empty and
        must be rebuilt from the .ember files — provenance indexes included."""
        store = tmp_path / "rebuild_store"
        db1 = EmberDB.connect(store)
        rid = db1.write(EmberRecord(namespace="v", data={"c": 1},
                                    agent_id="agent-Z", session_id="sess-Z"))

        db2 = EmberDB.connect(store)   # no checkpoint() — forces rebuild-from-store
        assert {r.id for r in db2.get_by_agent("agent-Z")} == {rid}
        assert {r.id for r in db2.get_by_session("sess-Z")} == {rid}

    def test_agent_query_survives_checkpoint_and_reload(self, tmp_path):
        """Reopen AFTER checkpoint: the persisted master.json must round-trip
        the provenance indexes through persist()/_load()."""
        store = tmp_path / "persist_store"
        db1 = EmberDB.connect(store)
        rid = db1.write(EmberRecord(namespace="v", data={"c": 1},
                                    agent_id="agent-P", session_id="sess-P"))
        db1.checkpoint()

        db2 = EmberDB.connect(store)
        assert {r.id for r in db2.get_by_agent("agent-P")} == {rid}
        assert {r.id for r in db2.get_by_session("sess-P")} == {rid}

    def test_provenance_survives_full_reopen(self, tmp_path):
        """Every provenance field is intact after a store reopen (timestamps
        preserved, etc.)."""
        store = tmp_path / "roundtrip_store"
        db1 = EmberDB.connect(store)
        rid = db1.write(EmberRecord(
            namespace="v", data={"content": "durable"},
            written_by="agent-03", origin="src",
            agent_id="research-agent-03", session_id="session-8821",
            creation_reason="reason", derived_from=["m1", "m2"],
        ))
        before = db1.get(rid).provenance()

        db2 = EmberDB.connect(store)
        after = db2.get(rid).provenance()
        assert after == before


# ── Provenance through update (new versions) ──────────────────────────────────

class TestProvenanceInUpdate:
    def test_new_version_carries_its_own_provenance(self, db):
        """An update is a new write and must record WHO made THAT change, sealed
        into the new head — while the previous version stays intact."""
        v1 = db.write(EmberRecord(namespace="v", data={"content": "v1"},
                                  agent_id="alpha", session_id="s1"))
        v2_id, _ = db.update(
            v1, {"content": "v2"},
            written_by="beta", agent_id="beta", session_id="s9",
            creation_reason="corrected after review",
            derived_from=["evidence-1"])

        head = db.get(v2_id)
        assert head.agent_id == "beta"
        assert head.session_id == "s9"
        assert head.creation_reason == "corrected after review"
        assert head.derived_from == ["evidence-1"]
        assert head.verify_integrity() is True

        # previous version remains intact with its own provenance
        old = db.get(v1, include_superseded=True)
        assert old.agent_id == "alpha" and old.session_id == "s1"
        assert old.verify_integrity() is True

    def test_provenance_is_not_inherited_on_update(self, db):
        """Provenance describes the write act, so a new version does NOT inherit
        the prior version's agent/session unless the caller supplies them."""
        v1 = db.write(EmberRecord(namespace="v", data={"content": "v1"},
                                  agent_id="alpha", session_id="s1",
                                  creation_reason="initial"))
        v2_id, _ = db.update(v1, {"content": "v2"})  # no provenance supplied
        head = db.get(v2_id)
        assert head.agent_id is None
        assert head.session_id is None
        assert head.creation_reason is None


# ── Provenance through the LLM-facing MemoryProtocol ──────────────────────────

class TestProvenanceThroughProtocol:
    def test_remember_records_provenance(self, db):
        proto = MemoryProtocol(db)
        rid = proto.remember(
            "The cache invalidation bug is in the LRU eviction path.",
            tags=["bug"],
            agent_id="debug-agent-1",
            session_id="triage-42",
            creation_reason="Reproduced twice with the same stack trace.",
            derived_from=["log-77"],
        )
        p = db.get_provenance(rid)
        assert p["agent_id"] == "debug-agent-1"
        assert p["session_id"] == "triage-42"
        assert p["creation_reason"] == "Reproduced twice with the same stack trace."
        assert p["derived_from"] == ["log-77"]

    def test_protocol_update_records_provenance(self, db):
        proto = MemoryProtocol(db)
        rid = proto.remember("initial finding", agent_id="a1", session_id="s1")
        new_id, _ = proto.update(
            rid, "revised finding",
            agent_id="a2", session_id="s2",
            creation_reason="new evidence arrived")
        p = db.get_provenance(new_id)
        assert p["agent_id"] == "a2"
        assert p["session_id"] == "s2"
        assert p["creation_reason"] == "new evidence arrived"

    def test_agent_can_be_queried_after_remember(self, db):
        proto = MemoryProtocol(db)
        r1 = proto.remember("finding one", agent_id="scout", session_id="run-1")
        r2 = proto.remember("finding two", agent_id="scout", session_id="run-1")
        ids = {r.id for r in db.get_by_agent("scout")}
        assert {r1, r2} <= ids
        assert {r.id for r in db.get_by_session("run-1")} >= {r1, r2}
