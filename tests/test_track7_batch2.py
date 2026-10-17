"""Batch 2: current conflict outcomes govern promotion admission."""

import json
import multiprocessing
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from embers import (EmberDB, EmberRecord, Evidence, MemoryProposal,
                    ConflictStatus, PromotionMode, PromotionPolicy,
                    PromotionOutcome, SourceType, AccessLevel, RecordType)
from embers.core.integrity import RecordIntegrityError


def _db(tmp_path, mode=PromotionMode.AUTOMATIC):
    return EmberDB.connect(tmp_path / "batch2", promotion_mode=mode,
                           promotion_policy=PromotionPolicy())


def _proposal(db, source):
    proposal = MemoryProposal(
        namespace="batch2", discovery={"claim": "candidate"},
        confidence=0.95, agent_id="agent-a", derivation=[source],
        evidence=[Evidence(source="tool://batch2", source_type=SourceType.EXPERIMENTALLY_VERIFIED,
                           reference="sha256:batch2", agent_id="agent-a")],
    )
    return db.propose(proposal)


def _pair(db):
    a = db.write(EmberRecord(namespace="batch2", data={"claim": "A"}))
    b = db.write(EmberRecord(namespace="batch2", data={"claim": "B"}))
    return a, b


def _process_submit(store_path, proposal_id, start, results):
    db = EmberDB.connect(store_path)
    start.wait(15)
    results.put(("submit", db.submit(proposal_id).promoted))


def _process_resolve(store_path, conflict_id, winner_id, start, results):
    db = EmberDB.connect(store_path)
    start.wait(15)
    db.update_conflict_status(conflict_id, ConflictStatus.RESOLVED,
                              resolution="other memory won", winner_id=winner_id)
    results.put(("resolved", True))


@pytest.mark.parametrize("status,winner,expected", [
    (ConflictStatus.OPEN, None, False),
    (ConflictStatus.INVESTIGATING, None, False),
    (ConflictStatus.RESOLVED, "a", True),
    (ConflictStatus.RESOLVED, "b", False),
    (ConflictStatus.RESOLVED, None, False),
    (ConflictStatus.ACCEPTED_BOTH, None, True),
    (ConflictStatus.SUPERSEDED, None, True),
])
def test_current_mapped_status_and_outcome(tmp_path, status, winner, expected):
    db = _db(tmp_path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    if status != ConflictStatus.OPEN:
        db.update_conflict_status(
            cid, status, resolution="review completed" if status != ConflictStatus.INVESTIGATING else "",
            winner_id={"a": a, "b": b}.get(winner),
        )
    pid = _proposal(db, a)
    decision = db.promotion_route(pid)
    assert decision.will_promote is expected
    assert db.submit(pid).promoted is expected
    assert [r.data["status"] for r in db.get_history(cid)][0] == "open"


def test_mapped_conflict_applies_without_graph_edge(tmp_path):
    db = _db(tmp_path)
    a, b = _pair(db)
    db.map_conflict(a, b)
    db._graph_index.remove_edge(a, b, "contradicts")
    assert not db.submit(_proposal(db, a)).promoted


def test_graph_only_conflict_holds(tmp_path):
    db = _db(tmp_path)
    a, b = _pair(db)
    db.link(a, b, "contradicts")
    assert not db.submit(_proposal(db, a)).promoted


def test_missing_derivation_fails_closed(tmp_path):
    db = _db(tmp_path)
    assert db.promotion_route(_proposal(db, "missing")).outcome == PromotionOutcome.HOLD


def test_any_of_multiple_conflicts_blocks(tmp_path):
    db = _db(tmp_path)
    a, b = _pair(db)
    c = db.write(EmberRecord(namespace="batch2", data={"claim": "C"}))
    first = db.map_conflict(a, b)
    db.update_conflict_status(first, ConflictStatus.RESOLVED,
                              resolution="A won", winner_id=a)
    db.map_conflict(a, c)
    assert not db.submit(_proposal(db, a)).promoted


@pytest.mark.parametrize("mode", [PromotionMode.CONSENSUS, PromotionMode.HYBRID])
def test_other_automatic_modes_cannot_override_open_conflict(tmp_path, mode):
    db = _db(tmp_path, mode)
    a, b = _pair(db)
    db.map_conflict(a, b)
    assert not db.submit(_proposal(db, a)).promoted


def test_direct_promotion_cannot_bypass_conflict_hold(tmp_path):
    db = _db(tmp_path, PromotionMode.HUMAN)
    a, b = _pair(db)
    db.map_conflict(a, b)
    pid = _proposal(db, a)
    with pytest.raises(ValueError, match="conflict"):
        db.promote(pid, validated_by="reviewer")
    assert db.get_proposal(pid).status.value == "pending"


def test_two_handles_cannot_promote_same_proposal_twice(tmp_path):
    path = tmp_path / "batch2"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    pid = _proposal(first, _pair(first)[0])
    gate = Barrier(2)

    def submit(db):
        gate.wait(timeout=10)
        return db.submit(pid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (first, second)))
    assert sum(result.promoted for result in results) == 1
    assert first.get_proposal(pid).status.value == "promoted"


def test_interrupted_promotion_resumes_one_memory(tmp_path, monkeypatch):
    path = tmp_path / "batch2"
    db = EmberDB.connect(path)
    pid = _proposal(db, _pair(db)[0])
    original = db._write_evidence_record

    def interrupted(*args):
        raise RuntimeError("injected after durable memory write")

    monkeypatch.setattr(db, "_write_evidence_record", interrupted)
    with pytest.raises(RuntimeError, match="injected"):
        db.promote(pid)
    monkeypatch.setattr(db, "_write_evidence_record", original)

    reopened = EmberDB.connect(path)
    memory_id, _ = reopened.promote(pid)
    assert reopened.get(memory_id).record_type == RecordType.NODE
    assert reopened.get_proposal(pid).status.value == "promoted"
    nodes = [rid for rid in reopened._store.all_ids()
             if reopened._store.read(rid).record_type == RecordType.NODE]
    assert nodes == [memory_id]


def test_private_proposal_requires_namespace_access(tmp_path):
    db = _db(tmp_path)
    db.create_namespace("batch2", access_level=AccessLevel.PRIVATE,
                        owner="agent-a")
    pid = _proposal(db, _pair(db)[0])
    with pytest.raises(PermissionError):
        db.promotion_route(pid, actor_id="stranger")
    with pytest.raises(PermissionError):
        db.submit(pid, validated_by="stranger")
    assert db.promotion_route(pid, actor_id="agent-a").will_promote
    assert db.submit(pid, validated_by="agent-a").promoted


def test_concurrent_losing_resolution_never_allows_promotion(tmp_path):
    path = tmp_path / "batch2"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    a, b = _pair(first)
    cid = first.map_conflict(a, b)
    pid = _proposal(first, a)
    gate = Barrier(2)

    def resolve():
        gate.wait(timeout=10)
        first.update_conflict_status(cid, ConflictStatus.RESOLVED,
                                     resolution="B won", winner_id=b)

    def submit():
        gate.wait(timeout=10)
        return second.submit(pid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        resolved = pool.submit(resolve)
        submitted = pool.submit(submit)
        resolved.result(timeout=20)
        result = submitted.result(timeout=20)
    assert not result.promoted
    assert first.get_proposal(pid).status.value == "pending"


def test_human_hold_reports_conflict_and_blocks_manual_override(tmp_path):
    db = _db(tmp_path, PromotionMode.HUMAN)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    pid = _proposal(db, a)
    route = db.promotion_route(pid)
    assert route.outcome == PromotionOutcome.HOLD
    assert cid in " ".join(route.reasons)
    with pytest.raises(ValueError, match="conflict"):
        db.promote(pid, validated_by="agent-a")


def test_corrupt_derivation_is_not_ignored(tmp_path, monkeypatch):
    db = _db(tmp_path)
    a, _ = _pair(db)
    pid = _proposal(db, a)
    original = db._reader.get

    def corrupt(record_id, *args, **kwargs):
        if record_id == a:
            raise RecordIntegrityError("injected relevant corruption")
        return original(record_id, *args, **kwargs)

    monkeypatch.setattr(db._reader, "get", corrupt)
    with pytest.raises(RecordIntegrityError, match="relevant"):
        db.promotion_route(pid)
    with pytest.raises(RecordIntegrityError, match="relevant"):
        db.submit(pid)


def test_reject_and_promote_share_one_terminal_proposal_head(tmp_path):
    path = tmp_path / "batch2"
    first = EmberDB.connect(path)
    second = EmberDB.connect(path)
    pid = _proposal(first, _pair(first)[0])
    gate = Barrier(2)

    def promote():
        gate.wait(timeout=10)
        return first.submit(pid)

    def reject():
        gate.wait(timeout=10)
        try:
            return second.reject(pid, reason="reviewed", rejected_by="agent-b")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        promoted = pool.submit(promote)
        rejected = pool.submit(reject)
        result = promoted.result(timeout=20)
        rejection = rejected.result(timeout=20)
    assert result.promoted != bool(rejection)
    assert first.get_proposal(pid).status.value in ("promoted", "rejected")
    assert len(first.get_history(pid)) == 2


def test_rest_and_mcp_route_same_current_conflict_status(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP

    db = _db(tmp_path)
    agent, token = AgentRegistry(db).register("batch2-reviewer")
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    pid = _proposal(db, a)
    monkeypatch.setattr(api, "_get_db", lambda: db)
    headers = {"X-Ember-Agent-Id": agent.agent_id, "X-Ember-Token": token}
    client = TestClient(api.app)
    mcp = EmberMCP(db=db)

    def routes():
        rest = client.post("/v1/memory/promotion_route", headers=headers,
                           json={"proposal_id": pid})
        assert rest.status_code == 200, rest.text
        response = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "ember_promotion_route", "arguments": {
                                   "proposal_id": pid, "agent_id": agent.agent_id,
                                   "token": token}}})
        assert not response["result"]["isError"]
        tool = json.loads(response["result"]["content"][0]["text"])
        return rest.json(), tool

    rest, tool = routes()
    assert rest["outcome"] == tool["outcome"] == "hold"
    assert cid in " ".join(rest["reasons"])
    db.update_conflict_status(cid, ConflictStatus.RESOLVED,
                              resolution="A supported", winner_id=a)
    rest, tool = routes()
    assert rest["outcome"] == tool["outcome"] == "promote"


def test_process_resolution_and_submit_never_admit_loser(tmp_path):
    path = tmp_path / "batch2"
    db = EmberDB.connect(path)
    a, b = _pair(db)
    cid = db.map_conflict(a, b)
    pid = _proposal(db, a)
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    workers = [
        ctx.Process(target=_process_submit, args=(str(path), pid, start, results)),
        ctx.Process(target=_process_resolve, args=(str(path), cid, b, start, results)),
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert dict(results.get(timeout=2) for _ in workers) == {
        "submit": False, "resolved": True}
    reopened = EmberDB.connect(path)
    assert reopened.get_proposal(pid).status.value == "pending"
