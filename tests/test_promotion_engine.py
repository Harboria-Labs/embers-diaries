"""
Ember's Diaries — Promotion Engine (spec §10–§12, configurable modes)

Behavioral tests for the routing layer that sits between a proposal and durable
memory. The mechanism (propose/promote/reject) is tested in
test_evidence_proposal.py; here we test the DECISION:

    * automatic mode — policy gates (evidence, confidence, trust, no conflict)
      decide promotion; a failing gate HOLDs (nothing written, stays PENDING)
    * "promotion ≠ true" — an admitted memory carries an explicit status, and a
      mid-confidence proposal is admitted PROVISIONAL, not VERIFIED
    * status is versioned/append-only — set_status keeps the prior status in
      history
    * consensus mode — promotes only once enough DISTINCT agents corroborate
    * human mode — never auto-promotes; explicit promote() still works
    * hybrid mode — risk routes to human-hold, clean proposals auto-promote
    * dry-run route() writes nothing
    * backwards-compat — a plain memory (no status) reads as VERIFIED

Run: diaries/Scripts/python.exe -m pytest tests/test_promotion_engine.py -v
"""

import pytest

from embers import (
    EmberDB, EmberRecord, Evidence, MemoryProposal, SourceType,
    ProposalStatus, MemoryStatus, PromotionMethod, PromotionMode,
    PromotionPolicy, PromotionOutcome,
)


# ── Fixtures / helpers ──────────────────────────────────────────────────────

def _db(tmp_path, mode=PromotionMode.AUTOMATIC, policy=None):
    return EmberDB.connect(tmp_path / "prom_store",
                           promotion_mode=mode, promotion_policy=policy)


def _evidence(agent_id="agent-A", source="tool://run/1"):
    return Evidence(source=source, source_type=SourceType.EXPERIMENTALLY_VERIFIED,
                    reference="sha256:abc", description="test passed",
                    agent_id=agent_id)


def _proposal(confidence=0.9, grounded=True, agent_id="agent-A",
              discovery=None, derivation=None):
    ev = [_evidence(agent_id=agent_id)] if grounded else []
    return MemoryProposal(
        namespace="p",
        discovery=discovery if discovery is not None else {"claim": "X holds"},
        reason="two independent tests agreed",
        evidence=ev,
        confidence=confidence,
        agent_id=agent_id,
        derivation=derivation or [],
    )


# ── Automatic mode ──────────────────────────────────────────────────────────

class TestAutomaticMode:
    def test_high_confidence_grounded_promotes(self, tmp_path):
        db = _db(tmp_path)
        pid = db.propose(_proposal(confidence=0.9))
        result = db.submit(pid)
        assert result.promoted
        assert result.decision.method == PromotionMethod.AUTOMATIC
        assert db.memory_status(result.memory_id) == MemoryStatus.VERIFIED
        # Proposal is now marked PROMOTED (append-only status transition).
        assert db.get_proposal(pid).status == ProposalStatus.PROMOTED

    def test_low_confidence_holds_and_writes_nothing(self, tmp_path):
        db = _db(tmp_path)
        pid = db.propose(_proposal(confidence=0.3))
        before = db._store.record_count()
        result = db.submit(pid)
        assert not result.promoted
        assert result.decision.outcome == PromotionOutcome.HOLD
        # Nothing written, proposal still pending — a hold leaves no trace.
        assert db._store.record_count() == before
        assert db.get_proposal(pid).status == ProposalStatus.PENDING

    def test_ungrounded_proposal_holds(self, tmp_path):
        db = _db(tmp_path)
        pid = db.propose(_proposal(confidence=0.95, grounded=False))
        result = db.submit(pid)
        assert not result.promoted
        assert any("no evidence" in r for r in result.decision.reasons)

    def test_conflicting_derivation_holds(self, tmp_path):
        db = _db(tmp_path)
        # Two contradicting durable memories, linked.
        a = db.write(EmberRecord(namespace="p", data={"claim": "X"}))
        b = db.write(EmberRecord(namespace="p", data={"claim": "not X"}))
        db.link(a, b, "contradicts")
        # A proposal derived from a conflicted memory must not silently commit.
        pid = db.propose(_proposal(confidence=0.95, derivation=[a]))
        result = db.submit(pid)
        assert not result.promoted
        assert any("conflict" in r for r in result.decision.reasons)

    def test_untrusted_agent_holds(self, tmp_path):
        db = _db(tmp_path, policy=PromotionPolicy(trusted_agents={"agent-Z"}))
        pid = db.propose(_proposal(confidence=0.95, agent_id="agent-A"))
        result = db.submit(pid)
        assert not result.promoted
        assert any("not trusted" in r for r in result.decision.reasons)


# ── "Promotion ≠ true": status semantics ────────────────────────────────────

class TestStatusSemantics:
    def test_midband_confidence_is_provisional(self, tmp_path):
        # min 0.7, verified 0.85 → 0.8 is admitted but only PROVISIONAL.
        db = _db(tmp_path, policy=PromotionPolicy(min_confidence=0.7,
                                                  verified_confidence=0.85))
        pid = db.propose(_proposal(confidence=0.8))
        result = db.submit(pid)
        assert result.promoted
        assert db.memory_status(result.memory_id) == MemoryStatus.PROVISIONAL

    def test_promotion_method_recorded_on_memory(self, tmp_path):
        db = _db(tmp_path)
        pid = db.propose(_proposal(confidence=0.9))
        mid = db.submit(pid).memory_id
        assert db.promotion_method(mid) == PromotionMethod.AUTOMATIC

    def test_set_status_is_versioned_history_preserved(self, tmp_path):
        db = _db(tmp_path)
        mid = db.submit(db.propose(_proposal(confidence=0.9))).memory_id
        assert db.memory_status(mid) == MemoryStatus.VERIFIED
        new_id, old_id = db.set_status(mid, MemoryStatus.DISPUTED,
                                       reason="conflicting evidence appeared")
        assert old_id == mid
        # Current status is disputed…
        assert db.memory_status(mid) == MemoryStatus.DISPUTED
        # …but the prior VERIFIED version is preserved in history.
        history = db.get_history(mid)
        statuses = [
            (r.data.get("_status") if isinstance(r.data, dict) else None)
            for r in history
        ]
        assert MemoryStatus.VERIFIED.value in statuses
        assert MemoryStatus.DISPUTED.value in statuses

    def test_plain_memory_reads_as_verified(self, tmp_path):
        """A memory written directly (no promotion) has no _status key and must
        read back as VERIFIED with an unchanged hash (§15 backwards-compat)."""
        db = _db(tmp_path)
        mid = db.write(EmberRecord(namespace="p", data={"claim": "direct"}))
        rec = db.get(mid)
        assert "_status" not in (rec.data or {})
        assert rec.verify_integrity()
        assert db.memory_status(mid) == MemoryStatus.VERIFIED
        assert db.promotion_method(mid) is None


# ── Consensus mode ──────────────────────────────────────────────────────────

class TestConsensusMode:
    def test_below_threshold_holds(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.CONSENSUS,
                 policy=PromotionPolicy(consensus_threshold=2))
        # Only one agent's evidence so far.
        pid = db.propose(_proposal(confidence=0.9, agent_id="agent-A"))
        assert not db.submit(pid).promoted

    def test_reaches_threshold_promotes_as_consensus(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.CONSENSUS,
                 policy=PromotionPolicy(consensus_threshold=2))
        # Build a proposal that already carries evidence from two agents.
        prop = _proposal(confidence=0.9, agent_id="agent-A")
        prop.add_evidence(_evidence(agent_id="agent-B", source="tool://run/2"))
        pid = db.propose(prop)
        result = db.submit(pid)
        assert result.promoted
        assert result.decision.method == PromotionMethod.CONSENSUS


# ── Human mode ──────────────────────────────────────────────────────────────

class TestHumanMode:
    def test_submit_always_holds(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.HUMAN)
        pid = db.propose(_proposal(confidence=0.99))
        result = db.submit(pid)
        assert not result.promoted
        assert any("human approval" in r for r in result.decision.reasons)

    def test_explicit_promote_still_works_as_human(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.HUMAN)
        pid = db.propose(_proposal(confidence=0.99))
        mid, _ = db.promote(pid, validated_by="sammie")
        assert db.promotion_method(mid) == PromotionMethod.HUMAN
        assert db.memory_status(mid) == MemoryStatus.VERIFIED


# ── Hybrid mode ─────────────────────────────────────────────────────────────

class TestHybridMode:
    def test_low_confidence_routes_to_human(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.HYBRID,
                 policy=PromotionPolicy(risk_confidence=0.5))
        pid = db.propose(_proposal(confidence=0.4))
        result = db.submit(pid)
        assert not result.promoted
        assert any("human review" in r for r in result.decision.reasons)

    def test_clean_high_confidence_auto_promotes(self, tmp_path):
        db = _db(tmp_path, mode=PromotionMode.HYBRID)
        pid = db.propose(_proposal(confidence=0.95))
        result = db.submit(pid)
        assert result.promoted
        assert result.decision.method == PromotionMethod.AUTOMATIC


# ── Dry-run route() ─────────────────────────────────────────────────────────

class TestDryRun:
    def test_route_writes_nothing(self, tmp_path):
        db = _db(tmp_path)
        pid = db.propose(_proposal(confidence=0.9))
        before = db._store.record_count()
        decision = db.promotion_route(pid)
        assert decision.will_promote
        assert db._store.record_count() == before
        assert db.get_proposal(pid).status == ProposalStatus.PENDING
