"""Feedback + lifecycle + opt-in maintenance on current main."""

from pathlib import Path
import json
import pytest

from embers.db import EmberDB
from embers.db_feedback import bind as bind_feedback
bind_feedback(EmberDB)
from embers.core.record import EmberRecord
from embers.core.feedback import Feedback, FeedbackOutcome
from embers.core.types import RecordType
from embers.cognitive.lifecycle import LifecycleState
from embers.integration import MemoryProtocol
from embers.maintenance import run_maintenance_cycle
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.lobby_surface import install as install_lobby, STORE
from embers.mcp.session_collab import install as install_collab
from embers.mcp.feedback_surface import install as install_feedback
from embers.mcp.server import EmberMCP, TOOLS


def _body(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def db(tmp_path: Path):
    return EmberDB.connect(str(tmp_path / "store"))


def test_feedback_on_memory_does_not_rewrite(db):
    mid = db.write(EmberRecord(namespace="memories", data={"content": "door is green"}))
    before = db.get(mid).content_hash
    fid = db.give_feedback(mid, Feedback(
        memory_id=mid, agent_id="a", outcome=FeedbackOutcome.USEFUL,
    ))
    assert db.get(mid).content_hash == before
    assert db.get(fid).record_type.value == "feedback"
    assert len(db.feedback_for(mid)) == 1


def test_feedback_rejects_proposal(db):
    from embers.core.proposal import MemoryProposal
    pid = db.propose(MemoryProposal(discovery={"content": "x"}, reason="r"))
    with pytest.raises(ValueError):
        db.give_feedback(pid, Feedback(
            memory_id=pid, agent_id="a", outcome=FeedbackOutcome.INCORRECT,
        ))


def test_lifecycle_disputed_uses_mapped_conflict(db):
    proto = MemoryProtocol(db)
    a = db.write(EmberRecord(namespace="memories", data={"content": "green"}))
    b = db.write(EmberRecord(namespace="memories", data={"content": "red"}))
    report = proto.get_lifecycle(a)
    assert report.state == LifecycleState.ACTIVE
    db.map_conflict(a, b, detected_by="agent")
    report = proto.get_lifecycle(a)
    assert report.state == LifecycleState.DISPUTED
    assert report.has_open_conflict is True


def test_maintenance_cycle_does_not_raise(db):
    proto = MemoryProtocol(db)
    db.write(EmberRecord(namespace="memories", data={"content": "n"}))
    out = run_maintenance_cycle(proto, ["memories"])
    assert "memories" in out


@pytest.fixture
def mcp(tmp_path: Path):
    STORE.reset()
    install_session_auth()
    install_lobby()
    install_collab()
    install_feedback()
    return EmberMCP(db=EmberDB.connect(str(tmp_path / "mcp")))


def test_feedback_tools_listed(mcp):
    names = {t["name"] for t in TOOLS}
    assert "ember_feedback" in names
    assert "ember_lifecycle" in names
    assert "ember_run_maintenance" in names


def test_mcp_feedback_session_and_work_view(mcp):
    reg = _body(mcp.call_tool("ember_register", {"name": "fb"}))
    started = _body(mcp.call_tool("ember_start_session", {
        "agent_id": reg["agent_id"], "token": reg["token"], "task": "fb-task",
    }))
    sid = started["session_id"]
    written = _body(mcp.call_tool("ember_write", {
        "content": "used later", "session_id": sid, "room": "task",
    }))
    fb = _body(mcp.call_tool("ember_feedback", {
        "memory_id": written["id"], "outcome": "useful", "session_id": sid,
    }))
    view = _body(mcp.call_tool("ember_get_session", {"session_id": sid}))
    assert fb["feedback_id"] in view["work"]["feedback"]
    life = _body(mcp.call_tool("ember_lifecycle", {
        "record_id": written["id"], "session_id": sid,
    }))
    assert life["state"] in {
        "active", "verified", "reinforced", "weakening", "stale",
        "disputed", "archived",
    }


def test_mcp_explicit_channel_round_trip(mcp):
    reg = _body(mcp.call_tool("ember_register", {"name": "split-feedback"}))
    started = _body(mcp.call_tool("ember_start_session", {
        "agent_id": reg["agent_id"], "token": reg["token"], "task": "channel test",
    }))
    sid = started["session_id"]
    written = _body(mcp.call_tool("ember_write", {
        "content": "contextual information", "session_id": sid, "room": "task",
    }))
    args = {
        "memory_id": written["id"], "session_id": sid,
        "schema_version": 2, "channel": "relevance", "outcome": "useful",
        "outcome_id": "task-result", "context_id": "task-context",
        "context": {"task": "channel test"}, "signal": .5,
    }
    result = _body(mcp.call_tool("ember_feedback", args))
    stored = mcp.db.get_feedback(result["feedback_id"])
    assert stored.channel == "relevance"
    assert stored.agent_id == reg["agent_id"]
    assert stored.signal == .5
    invalid = dict(args, accuracy=.9)
    assert mcp.call_tool("ember_feedback", invalid)["isError"]


def test_rest_explicit_channel_round_trip(db, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    pytest.importorskip("fastapi")
    from fastapi import HTTPException
    from embers.api import feedback_routes

    monkeypatch.setattr(feedback_routes, "_db", lambda: db)
    monkeypatch.setattr(feedback_routes, "_agent",
                        lambda *_: SimpleNamespace(agent_id="authenticated"))
    mid = db.write(EmberRecord(namespace="memories", data={"content": "claim"}))
    body = {
        "schema_version": 2, "channel": "correctness", "outcome": "contradicted",
        "outcome_id": "observation", "context_id": "task",
        "context": {"task": "check"}, "supporting_refs": ["trace-1"],
        "agent_id": "spoofed",
    }
    result = asyncio.run(feedback_routes.give_feedback(
        mid, body, "authenticated", "token", None,
    ))
    stored = db.get_feedback(result["feedback_id"])
    assert stored.agent_id == "authenticated"
    assert stored.channel == "correctness"
    assert stored.signal is None
    assert db.get(mid).confidence == 1.0
    with pytest.raises(HTTPException) as error:
        asyncio.run(feedback_routes.give_feedback(
            mid, dict(body, signal=1), "authenticated", "token", None,
        ))
    assert error.value.status_code == 400


def test_mcp_durable_resolution_and_projection(mcp):
    reg = _body(mcp.call_tool("ember_register", {"name": "resolver"}))
    auth = {"agent_id": reg["agent_id"], "token": reg["token"]}
    mid = _body(mcp.call_tool("ember_write", {
        "content": "useful clue", "namespace": "memories", **auth,
    }))["id"]
    fid = _body(mcp.call_tool("ember_feedback", {
        "memory_id": mid, "schema_version": 2, "channel": "relevance",
        "outcome": "useful", "outcome_id": "outcome", "context_id": "debug",
        "context": {"task": "debug"}, "signal": 1, **auth,
    }))["feedback_id"]
    mcp.db.relevance_journal(
        namespace="memories", context_id="debug", context={"task": "debug"},
        authorized_resolvers=frozenset({reg["agent_id"]}),
        memory_rate=.25, pair_rate=.25,
    )
    command = {
        "namespace": "memories", "context_id": "debug",
        "request_id": "resolve-1", "expected_revision": 0,
        "decision": {"outcome_id": "outcome", "status": "accepted",
                     "credits": [{"memory_version": mid, "value": 1}],
                     "report_ids": [fid], "reason": "observed result"},
        **auth,
    }
    first = _body(mcp.call_tool("ember_resolve_relevance", command))
    assert first["revision"] == 1
    assert _body(mcp.call_tool("ember_resolve_relevance", command)) == first
    state = _body(mcp.call_tool("ember_relevance_state", {
        "namespace": "memories", "context_id": "debug", **auth,
    }))
    assert state["memory_bias"] == {mid: .25}
    assert state["generation"] == 1
    other = _body(mcp.call_tool("ember_register", {"name": "not-resolver"}))
    denied = mcp.call_tool("ember_resolve_relevance", dict(
        command, agent_id=other["agent_id"], token=other["token"],
    ))
    assert denied["isError"]


def test_rest_resolution_and_correction_conflict(db, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    pytest.importorskip("fastapi")
    from fastapi import HTTPException
    from embers.api import feedback_routes
    from embers.core.feedback import Feedback

    monkeypatch.setattr(feedback_routes, "_db", lambda: db)
    monkeypatch.setattr(feedback_routes, "_agent",
                        lambda *_: SimpleNamespace(agent_id="resolver"))
    mid = db.write(EmberRecord(namespace="memories", data={"content": "claim"}))
    fid = db.give_feedback(mid, Feedback.from_submission(mid, "reporter", {
        "schema_version": 2, "channel": "relevance", "outcome": "useful",
        "outcome_id": "result", "context_id": "debug", "context": {"task": "debug"},
        "signal": 1,
    }))
    db.relevance_journal(
        namespace="memories", context_id="debug", context={"task": "debug"},
        authorized_resolvers=frozenset({"resolver"}), memory_rate=.25, pair_rate=.25,
    )
    command = {
        "request_id": "request", "expected_revision": 0,
        "decision": {"outcome_id": "result", "status": "accepted",
                     "credits": [{"memory_version": mid, "value": 1}],
                     "report_ids": [fid], "reason": "observed contribution"},
    }
    result = asyncio.run(feedback_routes.resolve_relevance(
        "memories", "debug", command, "resolver", "token",
    ))
    assert result["revision"] == 1
    stale = dict(command, request_id="new",
                 decision=dict(command["decision"], status="retracted", credits=[]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(feedback_routes.resolve_relevance(
            "memories", "debug", stale, "resolver", "token",
        ))
    assert exc.value.status_code == 409
    state = asyncio.run(feedback_routes.relevance_projection(
        "memories", "debug", "resolver", "token",
    ))
    assert state["memory_bias"] == {mid: .25}


def test_candidate_recall_mcp_authenticated(mcp):
    from embers.integration.candidate_recall import CandidateRecall
    reg = _body(mcp.call_tool('ember_register', {'name': 'candidate'}))
    auth = {'agent_id': reg['agent_id'], 'token': reg['token']}
    mid = _body(mcp.call_tool('ember_write', {'content': 'useful clue', 'namespace': 'memories', **auth}))['id']
    journal = mcp.db.relevance_journal(namespace='memories', context_id='debug', context={'task': 'debug'},
        authorized_resolvers=frozenset({reg['agent_id']}), memory_rate=.25, pair_rate=.25)
    CandidateRecall(journal, token_counter=lambda text: len(text.split()), tokenizer_id='words-fixture')
    args = dict(namespace='memories', context_id='debug', query_id='q', direct_scores={mid: 1}, elapsed=2, **auth)
    result = _body(mcp.call_tool('ember_candidate_recall', args))
    assert result['selected_ids'] == [mid]
    assert _body(mcp.call_tool('ember_candidate_recall', args)) == result
    assert mcp.call_tool('ember_candidate_recall', dict(args, token='bad'))['isError']
