"""Phase 5 integration checks using only pytest's isolated temporary store."""

import random

from embers import (ConflictStatus, EmberDB, EmberRecord, Evidence,
                    MemoryProposal, PromotionMode, PromotionPolicy, RecordType,
                    SourceType)
from embers.cognitive.epistemic import EpistemicLedger
from embers.integration import MemoryProtocol
from embers.maintenance import run_integrity_audit


def test_write_retrieve_conflict_promote_audit_and_ella(tmp_path):
    db = EmberDB.connect(tmp_path / "integrated", promotion_mode=PromotionMode.AUTOMATIC,
                         promotion_policy=PromotionPolicy())
    protocol = MemoryProtocol(db, default_namespace="phase5")
    a = protocol.remember({"content": "phase5 searchable claim alpha"}, room="personal")
    b = protocol.remember({"content": "phase5 opposing claim beta"}, room="personal")
    assert a in [record.id for record, _ in db.search("phase5 searchable", "phase5")]
    assert db._master_index.get_meta(a)["namespace"] == "phase5"
    cid = db.map_conflict(a, b)
    assert [c.conflict_id for c in db.conflicts_for(a)] == [cid]
    proposal = MemoryProposal(namespace="phase5", discovery={"claim": "admit alpha"},
                              confidence=0.95, agent_id="agent-a", derivation=[a],
                              evidence=[Evidence(source="tool://phase5",
                                                 source_type=SourceType.EXPERIMENTALLY_VERIFIED,
                                                 reference="phase5-artifact", agent_id="agent-a")])
    pid = db.propose(proposal)
    assert not db.submit(pid).promoted
    db.update_conflict_status(cid, ConflictStatus.RESOLVED,
                              resolution="alpha supported", winner_id=a)
    admitted = db.submit(pid)
    assert admitted.promoted
    assert db.get_conflict(cid).status == ConflictStatus.RESOLVED
    assert len(db.get_history(cid)) == 2
    audit = run_integrity_audit(db, ["phase5"], "agent-a")
    assert audit["status"] == "complete" and audit["certified"]
    ledger = EpistemicLedger(db, "phase5")
    before = ledger.read(admitted.memory_id, "agent-a")
    assert before["base_epistemic_verdict"] != "VERIFIED"
    evidence_id = db.attach_evidence(admitted.memory_id,
                                     Evidence(reference="independent phase5 evidence"))
    version = db._store.read(admitted.memory_id).content_hash
    first = ledger.apply("report", {"target_memory_id": admitted.memory_id,
                          "target_memory_version": version, "evidence_id": evidence_id,
                          "polarity": "SUPPORTS", "strength": "WEAK",
                          "assessment_note": "phase5 independent report"}, actor="agent-a",
                         request_id="phase5-report", expected_revision=0)
    assert ledger.read(admitted.memory_id, "agent-a")["score"] == first["score"]
    assert ledger.load()[0] == ledger._current().state


def test_indexed_conflicts_match_physical_reference_for_seeded_pairs(tmp_path):
    seed = 7105
    rng = random.Random(seed)
    db = EmberDB.connect(tmp_path / "differential")
    ids = [db.write(EmberRecord(namespace="phase5", data={"value": i}))
           for i in range(12)]
    for _ in range(8):
        a, b = rng.sample(ids, 2)
        db.map_conflict(a, b)
    source = []
    for rid in db._store.all_ids():
        record = db._store.read_strict(rid)
        if record.record_type == RecordType.CONFLICT:
            source.append(record)
    expected = {record.id for record in source}
    actual = set(db._master_index.get_type_ids(RecordType.CONFLICT.value, "phase5"))
    assert actual == expected
    for memory_id in ids:
        source_ids = {record.data["conflict_id"] for record in source
                      if memory_id in (record.data["memory_a"], record.data["memory_b"])}
        assert {conflict.conflict_id for conflict in db.conflicts_for(memory_id)} == source_ids


def test_seeded_incremental_ella_matches_every_authoritative_revision(tmp_path):
    seed = 7117
    rng = random.Random(seed)
    db = EmberDB.connect(tmp_path / "ella-differential")
    rid = db.write(EmberRecord(namespace="phase5", data={"claim": "test claim"}))
    ledger = EpistemicLedger(db, "phase5")
    version = db._store.read(rid).content_hash
    for revision in range(12):
        eid = db.attach_evidence(rid, Evidence(reference=f"seed-{seed}-evidence-{revision}"))
        response = ledger.apply("report", {
            "target_memory_id": rid, "target_memory_version": version,
            "evidence_id": eid, "polarity": rng.choice(["SUPPORTS", "OPPOSES"]),
            "strength": rng.choice(["WEAK", "MEDIUM"]),
            "assessment_note": f"seed {seed} revision {revision}",
        }, actor="agent-a", request_id=f"seed-{seed}-{revision}",
            expected_revision=revision)
        authoritative_state, events = ledger.load()
        assert len(events) == revision + 1
        assert ledger._current().state == authoritative_state
        assert ledger.read(rid, "agent-a")["score"] == response["score"]


def test_fingerprint_runs_on_healthy_lookup_and_physical_scan_is_epoch_gated(tmp_path, monkeypatch):
    path = tmp_path / "fingerprint"
    db = EmberDB.connect(path)
    a = db.write(EmberRecord(namespace="phase5", data={"claim": "A"}))
    b = db.write(EmberRecord(namespace="phase5", data={"claim": "B"}))
    db.map_conflict(a, b)
    other = EmberDB.connect(path)
    calls = {"fingerprint": 0, "all_ids": 0}
    original_fingerprint = db._master_index._candidate_fingerprint
    original_ids = db._store.all_ids

    def fingerprint():
        calls["fingerprint"] += 1
        return original_fingerprint()

    def all_ids():
        calls["all_ids"] += 1
        return original_ids()

    monkeypatch.setattr(db._master_index, "_candidate_fingerprint", fingerprint)
    monkeypatch.setattr(db._store, "all_ids", all_ids)
    db.conflicts_for(a)
    db.conflicts_for(a)
    assert calls == {"fingerprint": 2, "all_ids": 0}

    other.write(EmberRecord(namespace="phase5", data={"claim": "external"}))
    db.conflicts_for(a)
    assert calls == {"fingerprint": 4, "all_ids": 1}
    db.conflicts_for(a)
    assert calls == {"fingerprint": 5, "all_ids": 1}

    db._reconcile_master_index()
    assert calls == {"fingerprint": 6, "all_ids": 2}
