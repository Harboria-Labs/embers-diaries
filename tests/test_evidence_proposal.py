"""
Ember's Diaries — Features #4 (Discovery Before Memory) + #5 (Evidence Integrity)

Behavioral tests mapped to the spec's §16 requirements:

    Evidence
        * evidence attached to proposal
        * evidence identity preserved (evidence_id + content_hash survive
          proposal → promotion → reload)
        * evidence traceability (CLAIM → EVIDENCE → SOURCE, with source_type
          distinguishing observed / inferred / reported / … )

    Promotion
        * discovery → proposal (a proposal is stored, not yet a memory)
        * proposal → validated memory (promote() creates the durable memory,
          grounded by SUPPORTS edges to its evidence)
        * rejected proposals remain DISTINGUISHABLE from committed memories

Plus the multi-agent confirmation model (several agents attach independent
evidence to ONE memory without ever superseding it) and §15 backwards
compatibility (the evidence/proposal graph survives a rebuild-from-store).

Run: diaries/Scripts/python.exe -m pytest tests/test_evidence_proposal.py -v
"""

import pytest

from embers import (
    EmberDB, Evidence, MemoryProposal, SourceType, ProposalStatus, RecordType,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "ep_store")


def _evidence(source="tool://run/42", stype=SourceType.EXPERIMENTALLY_VERIFIED,
              **kw):
    return Evidence(source=source, source_type=stype,
                    reference=kw.pop("reference", "sha256:abc"),
                    description=kw.pop("description", "unit test passed"),
                    **kw)


def _proposal(db, discovery="X holds under load", reason="two independent runs agreed",
              confidence=0.9, evidence=None, **kw):
    p = MemoryProposal(
        namespace="ep", discovery={"content": discovery}, reason=reason,
        confidence=confidence, evidence=evidence or [], **kw)
    return p


# ── Evidence identity (§5) ─────────────────────────────────────────────────────

class TestEvidenceIdentity:
    def test_evidence_has_stable_hash_identity(self):
        ev = _evidence()
        h1 = ev.seal()
        assert ev.content_hash == h1
        assert ev.verify_integrity() is True
        # re-sealing is idempotent, hash unchanged
        assert ev.seal() == h1

    def test_tampering_breaks_integrity(self):
        ev = _evidence()
        ev.seal()
        ev.source = "tool://somewhere-else"   # mutate a sealed immutable field
        assert ev.verify_integrity() is False

    def test_source_type_distinguishes_provenance_kinds(self):
        """§5: a future agent must be able to tell HOW a claim was known."""
        observed = _evidence(stype=SourceType.DIRECTLY_OBSERVED)
        reported = _evidence(stype=SourceType.REPORTED)
        assert observed.source_type != reported.source_type
        # round-trips through dict form
        assert Evidence.from_dict(reported.to_dict()).source_type is SourceType.REPORTED

    def test_two_agents_observing_produce_distinct_evidence(self):
        """Attribution is part of identity: the 'same' observation by two
        agents is two evidence objects, each its own act."""
        a = Evidence(source="s", agent_id="agent-A", session_id="s1")
        b = Evidence(source="s", agent_id="agent-B", session_id="s2")
        assert a.seal() != b.seal()


# ── Proposals carry evidence (§4 + §16 Evidence) ───────────────────────────────

class TestProposalEvidence:
    def test_evidence_attached_to_proposal(self):
        p = _proposal(None)
        p.add_evidence(_evidence(source="doc://paper/1"))
        p.add_evidence(_evidence(source="tool://run/7"))
        assert p.is_grounded()
        assert len(p.evidence) == 2
        # sources are derived from the evidence
        assert set(p.sources) == {"doc://paper/1", "tool://run/7"}

    def test_bare_assertion_has_no_evidence(self):
        """CLAIM → agent assertion: a proposal with no evidence is not grounded
        and is distinguishable from an evidence-backed one."""
        p = _proposal(None)
        assert p.is_grounded() is False

    def test_proposal_does_not_store_chain_of_thought(self):
        """§4: the proposal stores a concise structured `reason`, evidence,
        confidence — never a private reasoning transcript. We assert the stored
        payload contains only the structured fields."""
        p = _proposal(None, evidence=[_evidence()])
        payload = p.to_record_payload()
        assert set(payload) == {
            "proposal_id", "discovery", "reason", "evidence",
            "sources", "confidence", "derivation", "status",
        }


# ── Discovery → proposal → validated memory (§16 Promotion) ────────────────────

class TestPromotion:
    def test_discovery_stored_as_proposal_not_memory(self, db):
        p = _proposal(db, evidence=[_evidence()])
        pid = db.propose(p)
        # it exists as a PROPOSAL, pending
        prop = db.get_proposal(pid)
        assert prop.status is ProposalStatus.PENDING
        stored = db.get(pid, include_superseded=True, include_deprecated=True)
        assert stored.record_type is RecordType.PROPOSAL

    def test_proposal_promotes_to_grounded_memory(self, db):
        ev = _evidence(source="tool://run/1")
        p = _proposal(db, evidence=[ev])
        pid = db.propose(p)

        memory_id, _ = db.promote(pid, validated_by="validator")
        mem = db.get(memory_id)
        assert mem is not None
        # The discovery is preserved verbatim; promotion additionally stamps the
        # memory's epistemic status (§12) under reserved keys.
        assert mem.data["content"] == "X holds under load"
        assert mem.data["_status"] == "verified"
        assert mem.data["_promotion_method"] == "human"
        # provenance carried over from the proposal
        assert mem.creation_reason == "two independent runs agreed"
        assert mem.confidence == 0.9

        # the memory is grounded: its evidence is reachable via SUPPORTS
        support = db.evidence_for(memory_id)
        assert {r.id for r in support} == {ev.evidence_id}

        # the proposal is now marked promoted (append-only status transition)
        assert db.get_proposal(pid).status is ProposalStatus.PROMOTED

    def test_evidence_identity_preserved_through_promotion(self, db):
        ev = _evidence(source="doc://source/9")
        original_hash = ev.seal()
        p = _proposal(db, evidence=[ev])
        pid = db.propose(p)
        memory_id, _ = db.promote(pid)

        stored_ev = db.get_evidence(ev.evidence_id)
        assert stored_ev is not None
        assert stored_ev.evidence_id == ev.evidence_id
        assert stored_ev.content_hash == original_hash      # identity preserved
        assert stored_ev.verify_integrity() is True
        assert stored_ev.source == "doc://source/9"
        assert stored_ev.source_type is SourceType.EXPERIMENTALLY_VERIFIED

    def test_evidence_traceable_claim_evidence_source(self, db):
        """The full chain: memory → SUPPORTS → evidence → source/source_type."""
        ev = _evidence(source="sensor://temp/room-3",
                       stype=SourceType.DIRECTLY_OBSERVED)
        pid = db.propose(_proposal(db, evidence=[ev]))
        memory_id, _ = db.promote(pid)

        support = db.evidence_for(memory_id)
        assert len(support) == 1
        traced = db.get_evidence(support[0].id)
        assert traced.source == "sensor://temp/room-3"
        assert traced.source_type is SourceType.DIRECTLY_OBSERVED

    def test_rejected_proposal_distinguishable_from_committed(self, db):
        """§16 Promotion: a rejected proposal is never a memory and stays
        permanently distinguishable from one that was committed."""
        good = _proposal(db, discovery="kept", evidence=[_evidence()])
        bad = _proposal(db, discovery="thrown out", evidence=[_evidence()])
        good_id = db.propose(good)
        bad_id = db.propose(bad)

        memory_id, _ = db.promote(good_id)
        rejected_head = db.reject(bad_id, reason="evidence too weak")

        # rejected proposal: still present, marked rejected, never a memory
        rej = db.get_proposal(bad_id)
        assert rej.status is ProposalStatus.REJECTED
        # it did not become a durable NODE memory
        assert db.get(memory_id).record_type is RecordType.NODE
        rej_rec = db.get(rejected_head, include_superseded=True,
                         include_deprecated=True)
        assert rej_rec.record_type is RecordType.PROPOSAL
        assert rej_rec.data.get("rejection_reason") == "evidence too weak"

    def test_cannot_promote_a_rejected_proposal(self, db):
        pid = db.propose(_proposal(db, evidence=[_evidence()]))
        db.reject(pid)
        with pytest.raises(ValueError):
            db.promote(pid)

    def test_proposals_listing_filters_by_status(self, db):
        p1 = db.propose(_proposal(db, discovery="a", evidence=[_evidence()]))
        p2 = db.propose(_proposal(db, discovery="b", evidence=[_evidence()]))
        p3 = db.propose(_proposal(db, discovery="c", evidence=[_evidence()]))
        db.promote(p1)
        db.reject(p2)
        # p3 stays pending
        pending = db.proposals("ep", status=ProposalStatus.PENDING)
        assert {p.proposal_id for p in pending} == {p3}
        promoted = db.proposals("ep", status=ProposalStatus.PROMOTED)
        assert {p.proposal_id for p in promoted} == {p1}
        rejected = db.proposals("ep", status=ProposalStatus.REJECTED)
        assert {p.proposal_id for p in rejected} == {p2}


# ── Multi-agent independent confirmation (the user's memory model) ─────────────

class TestMultiAgentEvidence:
    def test_many_agents_attach_evidence_to_one_memory_without_superseding(self, db):
        """Several agents independently confirm ONE memory over time. Each adds
        an EVIDENCE record + SUPPORTS edge; the memory itself is NEVER modified
        (append-only), so its content hash is unchanged and its confirmation
        trail only grows."""
        pid = db.propose(_proposal(db, evidence=[
            _evidence(source="tool://run/1", agent_id="agent-A")]))
        memory_id, _ = db.promote(pid)
        head_hash = db.get(memory_id).content_hash

        # two more agents attach independent evidence later
        db.attach_evidence(memory_id, Evidence(
            source="tool://run/2", source_type=SourceType.EXPERIMENTALLY_VERIFIED,
            agent_id="agent-B"))
        db.attach_evidence(memory_id, Evidence(
            source="report://agent-C/note", source_type=SourceType.REPORTED,
            agent_id="agent-C"))

        support = db.evidence_for(memory_id)
        assert len(support) == 3
        # the memory was not superseded and its hash is untouched
        assert db.get(memory_id) is not None
        assert db.get(memory_id).content_hash == head_hash
        # provenance kinds are visible per piece of evidence
        kinds = {db.get_evidence(r.id).source_type for r in support}
        assert SourceType.REPORTED in kinds


# ── Backwards compatibility / persistence (§15) ────────────────────────────────

class TestPersistenceAndRebuild:
    def test_grounding_survives_rebuild_from_store(self, tmp_path):
        store = tmp_path / "rebuild_ep"
        db1 = EmberDB.connect(store)
        ev = _evidence(source="doc://x")
        pid = db1.propose(_proposal(db1, evidence=[ev]))
        memory_id, _ = db1.promote(pid)

        # reconnect with no checkpoint → indexes rebuilt from .ember records
        db2 = EmberDB.connect(store)
        support = db2.evidence_for(memory_id)
        assert {r.id for r in support} == {ev.evidence_id}
        assert db2.get_evidence(ev.evidence_id).verify_integrity() is True
        assert db2.get_proposal(pid).status is ProposalStatus.PROMOTED

    def test_existing_record_types_unaffected(self, db):
        """§15: adding EVIDENCE/PROPOSAL record types must not disturb ordinary
        records — a plain write still round-trips and verifies."""
        from embers import EmberRecord
        rid = db.write(EmberRecord(namespace="ep", data={"content": "plain"}))
        r = db.get(rid)
        assert r.record_type is RecordType.DOCUMENT
        assert r.verify_integrity() is True
