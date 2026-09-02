"""
Ember's Diaries — Feature #13 (Failures Should Be First-Class Information)

Behavioral tests mapped to the spec's §13 requirements:

    Failure (a failed approach, recorded so others don't repeat it)
        * a failure is stored as a FAILURE record with the §13 shape
          (FAILED / CAUSE / EVIDENCE) and is attributable to an agent
        * OTHER agents can see it — the anti-repetition query is the whole point:
              Agent A tries X → fails
              Agent B checks → sees the failure → tries Y instead
        * matching is phrasing-tolerant (case/whitespace-normalized approach)
        * a failure needs an `approach` (the lookup key) — rejected without one
        * evidence-grounded failures are distinguishable from bare assertions (§5)
        * failures tie into sessions (#9) automatically
        * "Failures can later be promoted into durable memory if sufficiently
          valuable" — through the ordinary proposal → Promotion Engine pipeline,
          stamping promoted_to; an unpromoted failure is still preserved
        * §15: failures survive a rebuild-from-store

Run: diaries/Scripts/python.exe -m pytest tests/test_failures.py -v
"""

import pytest

from embers import (
    EmberDB, Evidence, Failure, RecordType, SourceType, MemoryStatus,
    PromotionMode, PromotionPolicy,
)


@pytest.fixture
def db(tmp_path):
    return EmberDB.connect(tmp_path / "failure_store")


def _evidence(source="tool://test_run_8271", **kw):
    return Evidence(
        source=source,
        source_type=kw.pop("stype", SourceType.EXPERIMENTALLY_VERIFIED),
        reference=kw.pop("reference", "sha256:run8271"),
        description=kw.pop("description", "parser OOM at 4.2 GB"),
        **kw)


def _failure(approach="Approach X with dataset Y",
             failed="Approach X does not work with dataset Y.",
             cause="Parser exceeds memory limit.",
             evidence=None, agent_id="agent-a", namespace="default", **kw):
    return Failure(
        namespace=namespace, approach=approach, failed=failed, cause=cause,
        evidence=evidence if evidence is not None else [_evidence()],
        agent_id=agent_id, **kw)


# ── The §13 shape ───────────────────────────────────────────────────────────────

class TestFailureShape:
    def test_failure_stored_as_first_class_record(self, db):
        fid = db.report_failure(_failure())
        f = db.get_failure(fid)
        assert f is not None
        assert f.approach == "Approach X with dataset Y"
        assert f.failed == "Approach X does not work with dataset Y."
        assert f.cause == "Parser exceeds memory limit."
        assert f.agent_id == "agent-a"
        assert f.promoted_to is None

        rec = db.get(fid)
        assert rec.record_type == RecordType.FAILURE
        assert rec.agent_id == "agent-a"

    def test_summary_is_the_spec_shape(self, db):
        f = _failure()
        text = f.summary()
        assert "FAILED: Approach X does not work with dataset Y." in text
        assert "CAUSE: Parser exceeds memory limit." in text
        assert "EVIDENCE: tool://test_run_8271" in text

    def test_approach_is_required(self, db):
        # Without an approach nobody can look the failure up, so it cannot
        # prevent a repeat — the whole purpose of §13.
        with pytest.raises(ValueError):
            _failure(approach="   ")

    def test_evidence_grounding_is_distinguishable(self, db):
        grounded = db.report_failure(_failure())
        bare = db.report_failure(_failure(approach="Approach Z", evidence=[]))
        assert db.get_failure(grounded).is_grounded() is True
        assert db.get_failure(bare).is_grounded() is False

    def test_evidence_identity_is_preserved(self, db):
        ev = _evidence()
        fid = db.report_failure(_failure(evidence=[ev]))
        stored = db.get_failure(fid).evidence[0]
        assert stored.evidence_id == ev.evidence_id
        assert stored.content_hash == ev.content_hash
        assert stored.source == "tool://test_run_8271"


# ── The anti-repetition cycle — the whole point of §13 ──────────────────────────

class TestOtherAgentsCanSee:
    def test_agent_b_sees_agent_a_failure(self, db):
        # Agent A tries X → fails.
        db.report_failure(_failure(agent_id="agent-a"))

        # Agent B checks BEFORE spending the same effort.
        assert db.has_failed("Approach X with dataset Y") is True
        seen = db.failures_for_approach("Approach X with dataset Y")
        assert len(seen) == 1
        assert seen[0].agent_id == "agent-a"
        assert seen[0].cause == "Parser exceeds memory limit."

    def test_untried_approach_is_not_reported_as_failed(self, db):
        db.report_failure(_failure())
        # Agent B's alternative Y has not been tried — it is free to proceed.
        assert db.has_failed("Approach Y with dataset Y") is False
        assert db.failures_for_approach("Approach Y with dataset Y") == []

    def test_matching_tolerates_phrasing_differences(self, db):
        db.report_failure(_failure(approach="Approach X with dataset Y"))
        # Same attempt, sloppier phrasing — still recognised.
        assert db.has_failed("approach x  with  DATASET y.") is True

    def test_failures_are_visible_across_agents(self, db):
        db.report_failure(_failure(agent_id="agent-a"))
        db.report_failure(_failure(approach="Approach Q", agent_id="agent-b"))
        # The default view is cross-agent — Agent C sees both.
        assert len(db.failures()) == 2
        # And can still narrow to one agent when it wants to.
        assert len(db.failures(agent_id="agent-b")) == 1

    def test_multiple_agents_failing_same_approach_all_recorded(self, db):
        db.report_failure(_failure(agent_id="agent-a"))
        db.report_failure(_failure(agent_id="agent-b"))
        # Both attempts are preserved — the repeated failure is itself a signal
        # that this is structural, not a one-off.
        got = db.failures_for_approach("Approach X with dataset Y")
        assert {f.agent_id for f in got} == {"agent-a", "agent-b"}


# ── Session integration (#9) ────────────────────────────────────────────────────

class TestSessionIntegration:
    def test_failure_lands_on_its_session_account(self, db):
        sid = db.start_session(agent_id="agent-a", task="make X work")
        fid = db.report_failure(_failure(session_id=sid))
        # #9's curated account picked it up automatically.
        assert fid in db.get_session(sid).failures
        assert fid in {r.id for r in db.session_failures(sid)}

    def test_failure_without_first_class_session_still_records(self, db):
        # A bare session_id string (no SESSION record) must not break reporting;
        # provenance still links them.
        fid = db.report_failure(_failure(session_id="loose-session-id"))
        assert db.get_failure(fid) is not None
        assert db.get(fid).session_id == "loose-session-id"
        assert len(db.failures(session_id="loose-session-id")) == 1


# ── Promotion into durable memory (§13's last line) ─────────────────────────────

class TestFailurePromotion:
    def test_valuable_failure_promotes_to_memory(self, db):
        fid = db.report_failure(_failure())
        result = db.promote_failure(
            fid, lesson="Streaming parsers are required for dataset Y.",
            confidence=0.9)
        assert result.promoted is True

        memory = db.get(result.memory_id)
        assert memory.data["lesson"] == "Streaming parsers are required for dataset Y."
        assert memory.data["kind"] == "failure"
        assert memory.data["cause"] == "Parser exceeds memory limit."
        # It went through the real pipeline, so it carries epistemic status (§12).
        assert db.memory_status(result.memory_id) == MemoryStatus.VERIFIED
        # And is derived from the failure record it came from.
        assert fid in memory.derived_from

        # The failure now points at the memory — as a NEW version.
        assert db.get_failure(fid).promoted_to == result.memory_id

    def test_promotion_goes_through_the_promotion_engine(self, db):
        # A HUMAN-mode engine must not auto-promote a failure either — the
        # failure path is not a shortcut past the §12 checkpoint.
        human_db = EmberDB.connect(db._path, promotion_mode=PromotionMode.HUMAN)
        fid = human_db.report_failure(_failure())
        result = human_db.promote_failure(fid, lesson="lesson")
        assert result.promoted is False
        assert human_db.get_failure(fid).promoted_to is None

    def test_ungrounded_failure_is_held_not_promoted(self, db):
        # No evidence → the automatic gates hold it (a bare assertion is not
        # enough to become durable memory, §5).
        fid = db.report_failure(_failure(approach="Approach W", evidence=[]))
        result = db.promote_failure(fid, lesson="W is bad")
        assert result.promoted is False
        assert db.get_failure(fid).promoted_to is None

    def test_unpromoted_failure_is_still_preserved(self, db):
        fid = db.report_failure(_failure(approach="Approach W", evidence=[]))
        db.promote_failure(fid, lesson="W is bad")
        # Nothing was deleted — the failure remains queryable.
        assert db.get_failure(fid) is not None
        assert db.has_failed("Approach W") is True

    def test_promote_without_submit_returns_proposal(self, db):
        fid = db.report_failure(_failure())
        proposal_id = db.promote_failure(fid, lesson="lesson", submit=False)
        assert isinstance(proposal_id, str)
        assert db.get_proposal(proposal_id) is not None
        # Not yet promoted — a human/consensus flow decides separately.
        assert db.get_failure(fid).promoted_to is None

    def test_promoted_only_filter(self, db):
        f1 = db.report_failure(_failure())
        f2 = db.report_failure(_failure(approach="Approach W", evidence=[]))
        db.promote_failure(f1, lesson="lesson", confidence=0.9)
        db.promote_failure(f2, lesson="lesson")
        promoted = db.failures(promoted_only=True)
        assert [f.failure_id for f in promoted] == [f1]


# ── Backwards compatibility (§15) ──────────────────────────────────────────────

class TestRebuildFromStore:
    def test_failures_survive_reload(self, db, tmp_path):
        fid = db.report_failure(_failure())
        db.promote_failure(fid, lesson="Streaming parsers required.",
                           confidence=0.9)

        db2 = EmberDB.connect(tmp_path / "failure_store")
        f = db2.get_failure(fid)
        assert f is not None
        assert f.cause == "Parser exceeds memory limit."
        assert f.promoted_to is not None
        assert db2.has_failed("Approach X with dataset Y") is True
        assert f.evidence[0].source == "tool://test_run_8271"
