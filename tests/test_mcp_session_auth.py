"""MCP auth is session_id after register + start_session."""

from pathlib import Path
import json

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
    text = result["content"][0]["text"].lower()
    assert "session" in text or "token" in text


def test_register_start_then_write_with_session_id(mcp):
    reg = json.loads(mcp.call_tool("ember_register", {
        "name": "session-agent", "provider": "test", "model": "test",
    })["content"][0]["text"])
    agent_id, token = reg["agent_id"], reg["token"]

    started = json.loads(mcp.call_tool("ember_start_session", {
        "agent_id": agent_id,
        "token": token,
        "task": "probe",
    })["content"][0]["text"])
    sid = started["session_id"]
    assert started["agent_id"] == agent_id

    bare = mcp.call_tool("ember_write", {"content": "no id after start"})
    assert bare["isError"]

    written = mcp.call_tool("ember_write", {
        "content": "session_id write",
        "session_id": sid,
        "memory_type": "skill",
        "room": "personal",
    })
    assert not written["isError"]
    wid = json.loads(written["content"][0]["text"])["id"]
    rec = mcp.db.get(wid)
    assert rec.agent_id == agent_id
    assert rec.session_id == sid
    assert rec.data["room"] == "personal"


def test_session_id_resumes_on_new_mcp(tmp_path: Path):
    install_session_auth()
    db = EmberDB.connect(str(tmp_path / "store"))
    first = EmberMCP(db=db)
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


def test_token_plus_foreign_session_is_rejected(mcp):
    first = json.loads(mcp.call_tool("ember_register", {"name": "owner"})["content"][0]["text"])
    owned = json.loads(mcp.call_tool("ember_start_session", {
        "agent_id": first["agent_id"], "token": first["token"],
    })["content"][0]["text"])
    second = json.loads(mcp.call_tool("ember_register", {"name": "other"})["content"][0]["text"])
    result = mcp.call_tool("ember_write", {
        "content": "should not land",
        "agent_id": second["agent_id"],
        "token": second["token"],
        "session_id": owned["session_id"],
    })
    assert result["isError"]
    assert "session" in result["content"][0]["text"].lower()


def test_shared_instance_does_not_auth_the_next_caller(mcp):
    a = json.loads(mcp.call_tool("ember_register", {"name": "agent-a"})["content"][0]["text"])
    mcp.call_tool("ember_start_session", {
        "agent_id": a["agent_id"], "token": a["token"],
    })
    other = mcp.call_tool("ember_write", {"content": "stranger"})
    assert other["isError"]
