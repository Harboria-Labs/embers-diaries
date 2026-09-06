import json
from pathlib import Path

from embers import EmberDB
from embers.mcp.server import EmberMCP, TOOLS


def test_initialize_and_list_tools(tmp_path: Path):
    mcp = EmberMCP(db=EmberDB.connect(str(tmp_path / "s")))
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "ember-diaries"
    listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "ember_register" in names
    assert "ember_write" in names
    assert names == {t["name"] for t in TOOLS}


def test_register_then_write_roundtrip(tmp_path: Path):
    db = EmberDB.connect(str(tmp_path / "s"))
    mcp = EmberMCP(db=db)
    reg = mcp.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ember_register",
                    "arguments": {"name": "coder", "provider": "local", "model": "test"}},
    })
    payload = json.loads(reg["result"]["content"][0]["text"])
    agent_id, token = payload["agent_id"], payload["token"]
    written = mcp.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "ember_write", "arguments": {
            "content": "parser fails above 10000",
            "agent_id": agent_id, "token": token, "namespace": "memories",
        }},
    })
    assert written["result"]["isError"] is False
    rid = json.loads(written["result"]["content"][0]["text"])["id"]
    rec = db.get(rid)
    assert rec.agent_id == agent_id
    assert rec.data["content"] == "parser fails above 10000"


def test_write_rejects_bad_token(tmp_path: Path):
    mcp = EmberMCP(db=EmberDB.connect(str(tmp_path / "s")))
    bad = mcp.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ember_write", "arguments": {
            "content": "x", "agent_id": "agent-nope", "token": "nope",
        }},
    })
    assert bad["result"]["isError"] is True


def _agent(mcp):
    """Register an agent over MCP, return (agent_id, token)."""
    reg = mcp.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ember_register",
                    "arguments": {"name": "luna", "provider": "local", "model": "test"}},
    })
    payload = json.loads(reg["result"]["content"][0]["text"])
    return payload["agent_id"], payload["token"]


def _call(mcp, tool, args):
    res = mcp.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    return res["result"]


class TestProposalPromotionOverMCP:
    """The gap this closes: ember_propose_memory correctly sealed real Evidence,
    but NOTHING on the MCP surface could turn a proposal into durable memory.
    Evidence went in and dead-ended — never reachable as a memory."""

    def test_evidence_backed_proposal_reaches_durable_memory(self, tmp_path: Path):
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)

        proposed = _call(mcp, "ember_propose_memory", {
            "discovery": {"content": "the parser fails above 10000 rows"},
            "reason": "reproduced on three inputs",
            "confidence": 0.9,
            "namespace": "memories",
            "evidence": [
                {"source": "run-1.log", "source_type": "directly_observed",
                 "description": "crash at 10001"},
                {"source": "run-2.log", "source_type": "directly_observed",
                 "description": "crash at 12000"},
            ],
            "agent_id": agent_id, "token": token,
        })
        assert proposed["isError"] is False
        pid = json.loads(proposed["content"][0]["text"])["proposal_id"]

        # THE FIX: the proposal can now be routed into durable memory over MCP.
        submitted = _call(mcp, "ember_submit", {
            "proposal_id": pid, "agent_id": agent_id, "token": token})
        assert submitted["isError"] is False
        result = json.loads(submitted["content"][0]["text"])
        assert result["promoted"] is True, result
        memory_id = result["memory_id"]

        # The durable memory exists, and the evidence came WITH it.
        memory = db.get(memory_id)
        assert memory is not None
        evidence = db.evidence_for(memory_id)
        assert len(evidence) == 2, "both sealed evidence records must support it"
        sources = {e.data.get("source") for e in evidence}
        assert sources == {"run-1.log", "run-2.log"}

        # And it is readable back over MCP — the agent can see its own memory.
        read = _call(mcp, "ember_evidence_for", {
            "memory_id": memory_id, "agent_id": agent_id, "token": token})
        assert read["isError"] is False
        assert len(json.loads(read["content"][0]["text"])) == 2

    def test_promotion_carries_status_and_method(self, tmp_path: Path):
        """Promotion != 'this is true'. The memory must report HOW it was
        admitted and with WHAT epistemic status."""
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)
        pid = json.loads(_call(mcp, "ember_propose_memory", {
            "discovery": {"content": "cache warms in 40ms"},
            "reason": "measured", "confidence": 0.95, "namespace": "memories",
            "evidence": [{"source": "bench.txt", "source_type": "directly_observed"}],
            "agent_id": agent_id, "token": token,
        })["content"][0]["text"])["proposal_id"]

        out = json.loads(_call(mcp, "ember_submit", {
            "proposal_id": pid, "agent_id": agent_id, "token": token,
        })["content"][0]["text"])
        assert out["promoted"] is True
        assert out["method"] == "automatic", out
        assert out["status"] in {"verified", "provisional"}

    def test_dry_run_route_writes_nothing(self, tmp_path: Path):
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)
        pid = json.loads(_call(mcp, "ember_propose_memory", {
            "discovery": {"content": "x"}, "reason": "r", "confidence": 0.9,
            "namespace": "memories",
            "evidence": [{"source": "s", "source_type": "directly_observed"}],
            "agent_id": agent_id, "token": token,
        })["content"][0]["text"])["proposal_id"]

        def count():
            return db.stats()["indexes"]["master"]["total_records"]

        before = count()
        routed = _call(mcp, "ember_promotion_route", {
            "proposal_id": pid, "agent_id": agent_id, "token": token})
        assert routed["isError"] is False
        assert "outcome" in json.loads(routed["content"][0]["text"])
        assert count() == before, "dry run must not write"

    def test_ungrounded_proposal_holds_and_stays_pending(self, tmp_path: Path):
        """A proposal with no evidence must NOT silently become memory."""
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)
        pid = json.loads(_call(mcp, "ember_propose_memory", {
            "discovery": {"content": "unsupported claim"}, "reason": "hunch",
            "confidence": 0.9, "namespace": "memories",
            "agent_id": agent_id, "token": token,
        })["content"][0]["text"])["proposal_id"]

        out = json.loads(_call(mcp, "ember_submit", {
            "proposal_id": pid, "agent_id": agent_id, "token": token,
        })["content"][0]["text"])
        assert out["promoted"] is False
        assert out["memory_id"] is None
        assert db.get_proposal(pid).status.value == "pending"

    def test_explicit_promote_and_reject(self, tmp_path: Path):
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)

        def propose(content):
            return json.loads(_call(mcp, "ember_propose_memory", {
                "discovery": {"content": content}, "reason": "r",
                "confidence": 0.4, "namespace": "memories",
                "agent_id": agent_id, "token": token,
            })["content"][0]["text"])["proposal_id"]

        # explicit human promote works even where the engine would HOLD
        pid = propose("human vouches for this")
        promoted = _call(mcp, "ember_promote", {
            "proposal_id": pid, "agent_id": agent_id, "token": token})
        assert promoted["isError"] is False
        body = json.loads(promoted["content"][0]["text"])
        assert db.get(body["memory_id"]) is not None
        assert body["method"] == "human"

        # reject keeps the proposal permanently distinguishable, never a memory
        pid2 = propose("not good enough")
        rejected = _call(mcp, "ember_reject", {
            "proposal_id": pid2, "reason": "insufficient evidence",
            "agent_id": agent_id, "token": token})
        assert rejected["isError"] is False
        assert db.get_proposal(pid2).status.value == "rejected"

    def test_attach_evidence_to_existing_memory(self, tmp_path: Path):
        """Multi-agent confirmation: another agent grounds an existing memory
        without superseding it."""
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)
        pid = json.loads(_call(mcp, "ember_propose_memory", {
            "discovery": {"content": "retry storm at 3am"}, "reason": "seen",
            "confidence": 0.9, "namespace": "memories",
            "evidence": [{"source": "a.log", "source_type": "directly_observed"}],
            "agent_id": agent_id, "token": token,
        })["content"][0]["text"])["proposal_id"]
        memory_id = json.loads(_call(mcp, "ember_submit", {
            "proposal_id": pid, "agent_id": agent_id, "token": token,
        })["content"][0]["text"])["memory_id"]

        hash_before = db.get(memory_id).content_hash
        added = _call(mcp, "ember_attach_evidence", {
            "memory_id": memory_id,
            "source": "b.log", "source_type": "directly_observed",
            "description": "independent confirmation",
            "agent_id": agent_id, "token": token})
        assert added["isError"] is False
        assert len(db.evidence_for(memory_id)) == 2
        assert db.get(memory_id).content_hash == hash_before, \
            "attaching evidence must not modify the memory"

    def test_list_proposals_surfaces_pending_work(self, tmp_path: Path):
        db = EmberDB.connect(str(tmp_path / "s"))
        mcp = EmberMCP(db=db)
        agent_id, token = _agent(mcp)
        _call(mcp, "ember_propose_memory", {
            "discovery": {"content": "pending thing"}, "reason": "r",
            "confidence": 0.2, "namespace": "memories",
            "agent_id": agent_id, "token": token})

        listed = _call(mcp, "ember_list_proposals", {
            "namespace": "memories", "status": "pending",
            "agent_id": agent_id, "token": token})
        assert listed["isError"] is False
        body = json.loads(listed["content"][0]["text"])
        assert len(body) == 1
        assert body[0]["status"] == "pending"

    def test_promotion_tools_require_auth(self, tmp_path: Path):
        mcp = EmberMCP(db=EmberDB.connect(str(tmp_path / "s")))
        for tool in ("ember_submit", "ember_promote", "ember_reject",
                     "ember_promotion_route", "ember_list_proposals",
                     "ember_evidence_for", "ember_attach_evidence"):
            res = _call(mcp, tool, {"proposal_id": "p", "memory_id": "m",
                                    "namespace": "memories"})
            assert res["isError"] is True, f"{tool} must require auth"

    def test_new_tools_are_advertised(self, tmp_path: Path):
        mcp = EmberMCP(db=EmberDB.connect(str(tmp_path / "s")))
        listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        for tool in ("ember_submit", "ember_promote", "ember_reject",
                     "ember_promotion_route", "ember_attach_evidence",
                     "ember_evidence_for", "ember_list_proposals"):
            assert tool in names, f"{tool} must be discoverable via tools/list"


def test_read_tools_require_authentication(tmp_path: Path):
    """Reads must be gated like writes: an anonymous client can't drain a
    shared store that enforces agent identity."""
    db = EmberDB.connect(str(tmp_path / "s"))
    mcp = EmberMCP(db=db)

    # an agent writes a memory
    reg = mcp.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ember_register",
                    "arguments": {"name": "reader", "provider": "local", "model": "test"}},
    })
    payload = json.loads(reg["result"]["content"][0]["text"])
    agent_id, token = payload["agent_id"], payload["token"]
    written = mcp.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "ember_write", "arguments": {
            "content": "secret note", "agent_id": agent_id, "token": token,
            "namespace": "memories",
        }},
    })
    rid = json.loads(written["result"]["content"][0]["text"])["id"]

    # authenticated read succeeds
    ok = mcp.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "ember_read", "arguments": {
            "record_id": rid, "agent_id": agent_id, "token": token,
        }},
    })
    assert ok["result"]["isError"] is False

    # the SAME read without credentials is refused
    for tool, args in [
        ("ember_read", {"record_id": rid}),
        ("ember_search", {"query": "secret"}),
        ("ember_get_history", {"record_id": rid}),
        ("ember_get_graph", {"record_id": rid}),
        ("ember_get_session", {"session_id": "sess-none"}),
    ]:
        res = mcp.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        assert res["result"]["isError"] is True, f"{tool} must require auth"
