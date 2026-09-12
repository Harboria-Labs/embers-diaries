"""On-demand lobby board. Not memory."""

from pathlib import Path
import json

import pytest

from embers.db import EmberDB
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.lobby_surface import install as install_lobby, STORE
from embers.mcp.server import EmberMCP


def _body(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def mcp(tmp_path: Path):
    STORE.reset()
    install_session_auth()
    install_lobby()
    return EmberMCP(db=EmberDB.connect(str(tmp_path / "store")))


def _pair(mcp, name):
    reg = _body(mcp.call_tool("ember_register", {"name": name}))
    started = _body(mcp.call_tool("ember_start_session", {
        "agent_id": reg["agent_id"], "token": reg["token"], "task": "shared",
    }))
    return started["session_id"], started["agent_id"]


def test_personal_room_rejected(mcp):
    sid, _ = _pair(mcp, "a")
    bad = mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "x", "room": "personal",
    })
    assert bad["isError"]


def test_unscoped_room_rejected(mcp):
    sid, _ = _pair(mcp, "a")
    bad = mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "x", "room": "unscoped",
    })
    assert bad["isError"]


def test_no_session_id_rejected(mcp):
    result = mcp.call_tool("ember_lobby", {"action": "open", "task": "x", "room": "task"})
    assert result["isError"]


def test_open_publish_board(mcp):
    sid, _ = _pair(mcp, "a")
    opened = _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    assert opened["status"] == "open"
    post = _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "failure", "body": "approach X OOM", "approach": "X",
    }))
    assert post["type"] == "failure"
    board = _body(mcp.call_tool("ember_lobby", {"action": "board", "session_id": sid}))
    assert len(board["posts"]) == 1


def test_cannot_corroborate_own_post(mcp):
    sid, _ = _pair(mcp, "a")
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    post = _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "discovery", "body": "stream it",
    }))
    own = mcp.call_tool("ember_lobby", {
        "action": "corroborate", "session_id": sid, "post_id": post["post_id"],
    })
    assert own["isError"]


def test_other_agent_corroborates_and_promote(mcp):
    a_sid, _ = _pair(mcp, "agent-a")
    b_sid, _ = _pair(mcp, "agent-b")
    opened = _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": a_sid, "task": "parser", "room": "task",
    }))
    joined = _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": b_sid, "task": "parser", "room": "task",
    }))
    assert joined["board_id"] == opened["board_id"]
    post = _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": a_sid, "room": "task",
        "type": "discovery", "body": "stream the parser",
    }))
    cor = _body(mcp.call_tool("ember_lobby", {
        "action": "corroborate", "session_id": b_sid, "post_id": post["post_id"],
    }))
    assert len(cor["corroborations"]) == 1
    promo = _body(mcp.call_tool("ember_lobby", {
        "action": "promote", "session_id": a_sid, "post_id": post["post_id"],
    }))
    assert promo["kind"] == "proposal"
    assert promo["corroborators"] == 1
    proposal = mcp.db.get_proposal(promo["proposal_id"])
    assert len(proposal.evidence) == 2


def test_stranger_cannot_close(mcp):
    a_sid, _ = _pair(mcp, "agent-a")
    b_sid, _ = _pair(mcp, "agent-b")
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": a_sid, "task": "parser", "room": "task",
    }))
    closed = mcp.call_tool("ember_lobby", {"action": "close", "session_id": b_sid})
    assert closed["isError"]


def test_close_flushes_unpromoted_failures(mcp):
    sid, _ = _pair(mcp, "a")
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    _body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "failure", "body": "X OOM", "approach": "X",
    }))
    closed = _body(mcp.call_tool("ember_lobby", {"action": "close", "session_id": sid}))
    assert closed["status"] == "closed"
    assert len(closed["flushed_failures"]) == 1
    assert mcp.db.failures_for_approach("X")


def test_get_session_always_has_board_key(mcp):
    sid, _ = _pair(mcp, "a")
    raw = mcp.call_tool("ember_get_session", {"session_id": sid})
    body = _body(raw)
    assert "board" in body
    assert body["board"] is None
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "project",
    }))
    body = _body(mcp.call_tool("ember_get_session", {"session_id": sid}))
    assert body["board"]["room"] == "project"


def test_shared_instance_does_not_inherit_board(mcp):
    a_sid, _ = _pair(mcp, "agent-a")
    _body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": a_sid, "task": "parser", "room": "task",
    }))
    b_sid, _ = _pair(mcp, "agent-b")
    other = mcp.call_tool("ember_lobby", {"action": "board", "session_id": b_sid})
    assert other["isError"]
