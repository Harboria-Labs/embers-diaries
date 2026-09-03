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
