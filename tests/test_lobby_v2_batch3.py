"""Attribution and retry-safe durable handoff in isolated stores."""

import pytest

from embers.db import EmberDB
from embers.lobby import working


def setup(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    a = db.start_session("author", task="task", namespace="shared")
    b = db.start_session("peer", task="task", namespace="shared")
    scope = working.create_scope(db, a, "author", task="task", namespace="shared")["scope_id"]
    working.grant(db, scope, a, "author", "peer")
    return db, a, b, scope


def post(db, scope, sid, kind="finding"):
    return working.submit(db, scope, sid, "author", {
        "client_submission_id": kind, "share": True, "kind": kind,
        "claim": "test showed a parser failure", "uncertainty": "unverified",
        "approach": "parse large input",
        "evidence_refs": [{"reference": "https://example.org/run/1",
                           "observed_at": "2026-10-18T00:00:00Z",
                           "source": "test-result", "execution_context": "isolated pytest"}]})


def test_attributed_contradiction_and_proposal_handoff(tmp_path):
    db, a, b, scope = setup(tmp_path)
    item = post(db, scope, a)
    contribution = {"client_contribution_id": "peer-1", "kind": "contradiction",
                    "note": "a smaller input passed", "evidence_refs": [{
                        "reference": "https://example.org/run/2",
                        "observed_at": "2026-10-18T00:01:00Z"}]}
    updated = working.contribute(db, scope, b, "peer", item["item_id"], contribution)
    assert updated["version"] == 2
    assert updated["claim"] == item["claim"]
    assert updated["contributions"][0]["agent_id"] == "peer"
    assert updated["contributions"][0]["session_id"].startswith("session-sha256:")
    assert "reference" not in updated["contributions"][0]["evidence_refs"][0]
    assert working.contribute(db, scope, b, "peer", item["item_id"], contribution)["version"] == 2
    with pytest.raises(working.WorkingError):
        working.contribute(db, scope, b, "peer", item["item_id"], {**contribution, "note": "different"})
    receipt = working.handoff(db, scope, a, "author", item["item_id"])
    proposal = db.get_proposal(receipt["durable_id"])
    assert proposal is not None and len(proposal.evidence) == 2
    assert all(ev.source_type.value == "reported" for ev in proposal.evidence)
    assert proposal.discovery["uncertainty"] == "unverified"
    assert proposal.discovery["contributions"][0]["kind"] == "contradiction"
    assert working.handoff(db, scope, a, "author", item["item_id"]) == receipt
    working.close_scope(db, scope, a, "author")
    assert working.handoff(db, scope, a, "author", item["item_id"]) == receipt


def test_proposal_write_then_lost_ack_recovers_same_id(tmp_path, monkeypatch):
    db, a, _, scope = setup(tmp_path)
    item = post(db, scope, a)
    original = db.record_discovery
    def fail_once(*args, **kwargs):
        monkeypatch.setattr(db, "record_discovery", original)
        raise RuntimeError("ack failed after durable proposal")
    monkeypatch.setattr(db, "record_discovery", fail_once)
    with pytest.raises(RuntimeError):
        working.handoff(db, scope, a, "author", item["item_id"])
    recovered = working.handoff(db, scope, a, "author", item["item_id"])
    assert db.get_proposal(recovered["durable_id"]) is not None
    assert db.get_session(a).discoveries.count(recovered["durable_id"]) == 1


def test_failure_handoff_and_session_link_retry(tmp_path):
    db, a, _, scope = setup(tmp_path)
    item = post(db, scope, a, kind="failure")
    receipt = working.handoff(db, scope, a, "author", item["item_id"])
    assert receipt["kind"] == "failure"
    failure = db.get_failure(receipt["durable_id"])
    assert failure.context["uncertainty"] == "unverified"
    assert len(failure.evidence) == 1
    assert working.handoff(db, scope, a, "author", item["item_id"]) == receipt
    assert db.get_session(a).failures.count(receipt["durable_id"]) == 1
