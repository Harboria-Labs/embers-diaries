"""Feature 29: session answers what happened. Feature 16: auth policy tests."""

from pathlib import Path
import json

import pytest

from embers.db import EmberDB
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.lobby_surface import install as install_lobby, STORE
from embers.mcp.session_collab import install as install_collab
from embers.mcp.server import EmberMCP, TOOLS


def _body(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def mcp(tmp_path: Path):
    STORE.reset()
    install_session_auth()
    install_lobby()
    install_collab()
    return EmberMCP(db=EmberDB.connect(str(tmp_path / "store")))


def _pair(mcp, name, task="parser"):
    reg = _body(mcp.call_tool("ember_register", {"name": name}))
    started = _body(mcp.call_tool("ember_start_session", {
        "agent_id": reg["agent_id"], "token": reg["token"], "task": task,
    }))
    return started["session_id"], started["agent_id"], reg["token"]


def test_session_lists_write_discovery_failure(mcp):
    sid, agent, token = _pair(mcp, "a")
    written = _body(mcp.call_tool("ember_write", {
        "content": "committed finding",
        "session_id": sid,
        "room": "task",
        "memory_type": "raw",
    }))
    proposed = _body(mcp.call_tool("ember_propose_memory", {
        "discovery": {"content": "stream it"},
        "reason": "reproduced",
        "session_id": sid,
        "confidence": 0.8,
    }))
    failed = _body(mcp.call_tool("ember_report_failure", {
        "approach": "load-all",
        "failed": "OOM",
        "session_id": sid,
    }))
    view = _body(mcp.call_tool("ember_get_session", {"session_id": sid}))
    assert view["work"]["task"] == "parser"
    assert written["id"] in view["work"]["memories"]
    assert proposed["proposal_id"] in view["work"]["discoveries"]
    assert failed["failure_id"] in view["work"]["failures"]
    assert view["board"] is None
    assert view["work"]["lobby_posts"] == []


def test_lobby_promote_and_close_land_on_session(mcp):
    sid, agent, token = _pair(mcp, "a")
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    disc = _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "discovery", "body": "stream the parser",
    }))
    promo = _body(mcp.call_tool("ember_lobby", {
        "action": "promote", "session_id": sid, "post_id": disc["post_id"],
    }))
    fail = _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "failure", "body": "X OOM", "approach": "X",
    }))
    assert fail["type"] == "failure"
    closed = _body(mcp.call_tool("ember_lobby", {
        "action": "close", "session_id": sid,
    }))
    view = _body(mcp.call_tool("ember_get_session", {"session_id": sid}))
    assert promo["proposal_id"] in view["work"]["discoveries"]
    flushed = closed["flushed_failures"][0]["failure_id"]
    assert flushed in view["work"]["failures"]
    assert view["work"]["board"] is None


def test_read_without_session_or_token_is_rejected(mcp):
    sid, agent, token = _pair(mcp, "a")
    written = _body(mcp.call_tool("ember_write", {
        "content": "secret note", "session_id": sid,
    }))
    bare = mcp.call_tool("ember_read", {"record_id": written["id"]})
    assert bare["isError"]
    ok = mcp.call_tool("ember_read", {
        "record_id": written["id"], "session_id": sid,
    })
    assert not ok["isError"]


def test_lobby_is_on_tools_list_after_install():
    install_lobby()
    names = {t["name"] for t in TOOLS}
    assert "ember_lobby" in names
