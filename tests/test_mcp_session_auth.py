"""MCP auth is per session, not per write."""

from pathlib import Path

import pytest

from embers.db import EmberDB
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.room_wire import install as install_room_wire
from embers.mcp.server import EmberMCP


@pytest.fixture
def mcp(tmp_path: Path):
    install_session_auth()
    install_room_wire()
    db = EmberDB.connect(str(tmp_path / "store"))
    return EmberMCP(db=db)


def test_write_without_session_is_rejected(mcp):
    result = mcp.call_tool("ember_write", {"content": "no session"})
    assert result["isError"]
    assert "session" in result["content"][0]["text"].lower()


def test_register_then_start_session_then_write_without_token(mcp):
    reg = mcp.call_tool("ember_register", {
        "name": "session-agent", "provider": "test", "model": "test",
    })
    assert not reg["isError"]
    import json
    body = json.loads(reg["content"][0]["text"])
    agent_id, token = body["agent_id"], body["token"]

    started = mcp.call_tool("ember_start_session", {
        "agent_id": agent_id,
        "token": token,
        "task": "probe",
    })
    assert not started["isError"]
    session = json.loads(started["content"][0]["text"])
    assert session["session_id"]
    assert session["agent_id"] == agent_id

    written = mcp.call_tool("ember_write", {
        "content": "bound session write",
        "memory_type": "skill",
        "room": "personal",
    })
    assert not written["isError"]
    wid = json.loads(written["content"][0]["text"])["id"]
    rec = mcp.db.get(wid)
    assert rec.agent_id == agent_id
    assert rec.session_id == session["session_id"]
    assert rec.data["room"] == "personal"


def test_session_id_resumes_on_new_mcp(tmp_path: Path):
    install_session_auth()
    db = EmberDB.connect(str(tmp_path / "store"))
    first = EmberMCP(db=db)
    import json
    reg = json.loads(first.call_tool("ember_register", {"name": "resume"})["content"][0]["text"])
    started = json.loads(first.call_tool("ember_start_session", {
        "agent_id": reg["agent_id"], "token": reg["token"],
    })["content"][0]["text"])

    second = EmberMCP(db=db)
    written = second.call_tool("ember_write", {
        "content": "resumed with session_id only",
        "session_id": started["session_id"],
    })
    assert not written["isError"]
    wid = json.loads(written["content"][0]["text"])["id"]
    rec = second.db.get(wid)
    assert rec.agent_id == reg["agent_id"]
    assert rec.session_id == started["session_id"]
