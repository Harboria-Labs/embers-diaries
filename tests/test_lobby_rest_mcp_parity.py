"""REST/MCP lobby parity — spec §20's real requirement.

§20 lists four lobby routes and then states the constraint that matters:

    MCP ───────┐
               ├──→ Ember Core → Ember Store
    REST ──────┘

    "There must NOT be two separate implementations of memory behavior."

Route registration alone does not prove that. These tests drive BOTH surfaces
against one EmberDB and one LobbyStore and assert that work done on either one
is visible and actionable from the other — and that the lobby's structural
guards (no `personal` room, no invented post types, no room drift) survive the
crossing to HTTP rather than living only in the MCP adapter.
"""

import json
from pathlib import Path

import pytest

from embers.db import EmberDB
from embers.mcp.server import EmberMCP
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.lobby_surface import install as install_lobby, STORE


@pytest.fixture
def surfaces(tmp_path: Path):
    """One store, one lobby, two front doors.

    Both surfaces are handed the SAME EmberDB instance, and the REST routes
    reach the lobby through `embers.mcp.lobby_surface.STORE` — so if the two
    ever drift into separate stores, these tests fail rather than pass quietly.
    """
    import embers.api as api
    import embers.api.v1 as v1
    from tests.test_v1_promotion import ASGIClient

    db = EmberDB.connect(str(tmp_path / "store"))
    api._db = db
    api._protocol = None
    api._registry = None
    v1._registry = None
    v1._protocol = None
    STORE.reset()
    install_session_auth()
    install_lobby()
    return ASGIClient(api.app), EmberMCP(db=db), db


def _mcp_body(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


def _register_rest(client, name):
    """Register over HTTP and return both the REST headers and the raw pair.

    The same agent_id/token pair authenticates on MCP, which is what lets a
    single agent hop surfaces mid-task.
    """
    body = client.post("/v1/agents/register", json={"name": name}).json()
    headers = {"X-Ember-Agent-Id": body["agent_id"], "X-Ember-Token": body["token"]}
    return headers, body["agent_id"], body["token"]


# ---------------------------------------------------------------- parity ----

def test_post_published_over_mcp_is_visible_over_rest(surfaces):
    """An MCP agent publishes; an HTTP-only agent reads the same board."""
    client, mcp, _db = surfaces
    headers, agent_id, token = _register_rest(client, "hopper")
    sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": agent_id, "token": token, "task": "parser",
    }))["session_id"]

    post = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    post = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "discovery", "body": "parser dies above 10k records",
    }))

    updates = client.get("/v1/lobby/updates", headers=headers,
                         params={"session_id": sid})
    assert updates.status_code == 200, updates.text
    seen = updates.json()["updates"]
    assert [p["post_id"] for p in seen] == [post["post_id"]]
    assert seen[0]["body"] == "parser dies above 10k records"

    status = client.get("/v1/lobby/status", headers=headers,
                        params={"session_id": sid})
    assert status.json()["status"] == "open"


def test_post_published_over_rest_is_visible_and_promotable_over_mcp(surfaces):
    """The other direction: HTTP publishes, MCP sees it and promotes it."""
    client, mcp, db = surfaces
    headers, agent_id, token = _register_rest(client, "rester")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser", "namespace": "shared"}).json()["session_id"]

    published = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "discovery", "body": "streaming the parser fixes it",
    })
    assert published.status_code == 200, published.text
    post_id = published.json()["post_id"]

    board = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "board", "session_id": sid,
    }))
    assert [p["post_id"] for p in board["posts"]] == [post_id]

    promoted = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "promote", "session_id": sid, "post_id": post_id,
    }))
    assert promoted["kind"] == "proposal"
    # The proposal is real in the shared store, not a lobby-local echo.
    assert db.get_proposal(promoted["proposal_id"]) is not None


def test_rest_promotion_carries_sealed_evidence(surfaces):
    """Promotion must ground the memory, not just move text across the line.

    This is the gap that showed up in real agent use: a discovery reached
    memory with nothing supporting it. A lobby post carries at minimum its
    author's observation, and one more per corroborator.

    The chain is `lobby post → proposal → durable memory`, and evidence lives
    in two different shapes along it by design: inline in the proposal's data
    (so it is inside the content hash and cannot be swapped after sealing),
    then as its own hashed EVIDENCE record linked by a SUPPORTS edge once
    `promote()` admits the memory. This test walks the whole chain and checks
    both shapes, because checking only the second one would pass against a
    lobby that dropped the evidence and let `attach_evidence` supply it later.
    """
    client, mcp, db = surfaces
    headers, agent_id, token = _register_rest(client, "author")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser"}).json()["session_id"]
    post_id = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "discovery", "body": "chunked reads hold memory flat",
    }).json()["post_id"]

    # A second agent corroborates over MCP — the only surface that can.
    _other_headers, other_id, other_token = _register_rest(client, "witness")
    other_sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": other_id, "token": other_token, "task": "parser",
    }))["session_id"]
    _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": other_sid, "task": "parser", "room": "task",
    }))
    _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "corroborate", "session_id": other_sid, "post_id": post_id,
    }))

    promoted = client.post("/v1/lobby/promote", headers=headers,
                           json={"session_id": sid, "post_id": post_id})
    assert promoted.status_code == 200, promoted.text
    proposal_id = promoted.json()["proposal_id"]
    assert promoted.json()["corroborators"] == 1

    # Shape one: sealed inline on the proposal, author + corroborator, and
    # attributed to DIFFERENT agents — which is what CONSENSUS promotion counts.
    proposal = db.get_proposal(proposal_id)
    assert len(proposal.evidence) == 2
    assert {ev.agent_id for ev in proposal.evidence} == {agent_id, other_id}
    # Sealed, and the seal still verifies — the evidence has not been edited
    # since the lobby handed it over.
    assert all(ev.content_hash and ev.verify_integrity()
               for ev in proposal.evidence)

    # Shape two: EVIDENCE records + SUPPORTS edges on the durable memory,
    # readable over HTTP once the proposal is admitted.
    admitted = client.post("/v1/memory/promote", headers=headers,
                           json={"proposal_id": proposal_id})
    assert admitted.status_code == 200, admitted.text
    memory_id = admitted.json()["memory_id"]

    evidence = client.get(f"/v1/memory/{memory_id}/evidence", headers=headers)
    assert evidence.status_code == 200, evidence.text
    rows = evidence.json()["evidence"]
    assert len(rows) == 2, rows
    assert {r["agent_id"] for r in rows} == {agent_id, other_id}


def test_rest_promotion_of_failure_reaches_durable_failures(surfaces):
    """A failure post promotes into a first-class failure record (§13)."""
    client, _mcp, _db = surfaces
    headers, _agent_id, _token = _register_rest(client, "burned")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser"}).json()["session_id"]
    post_id = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "failure", "body": "approach X exceeds the memory limit",
        "approach": "X",
    }).json()["post_id"]

    promoted = client.post("/v1/lobby/promote", headers=headers,
                           json={"session_id": sid, "post_id": post_id})
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["kind"] == "failure"

    listed = client.get("/v1/failures", headers=headers, params={"approach": "X"})
    assert listed.status_code == 200, listed.text
    approaches = [f["approach"] for f in listed.json()["failures"]]
    assert "X" in approaches


# ------------------------------------------------------- structural guards --

def test_rest_publish_rejects_personal_room(surfaces):
    """`personal` is not in ALLOWED_ROOMS. The HTTP door must not be looser."""
    client, _mcp, _db = surfaces
    headers, _agent_id, _token = _register_rest(client, "leaky")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "private"}).json()["session_id"]

    response = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "private", "room": "personal",
        "type": "discovery", "body": "my own note",
    })

    assert response.status_code == 400, response.text
    # And nothing was opened as a side effect of the rejected call.
    status = client.get("/v1/lobby/status", headers=headers,
                        params={"session_id": sid})
    assert status.json()["status"] == "idle"


def test_rest_publish_rejects_unknown_post_type(surfaces):
    """ALLOWED_TYPES is failure | discovery | question. No free-text kinds."""
    client, _mcp, _db = surfaces
    headers, _agent_id, _token = _register_rest(client, "inventor")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser"}).json()["session_id"]

    response = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "status", "body": "still working on it",
    })

    assert response.status_code == 400, response.text


def test_rest_publish_rejects_room_drift_on_an_open_board(surfaces):
    """A board is opened in one room. Later posts may not migrate it."""
    client, _mcp, _db = surfaces
    headers, _agent_id, _token = _register_rest(client, "drifter")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser"}).json()["session_id"]
    first = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "discovery", "body": "opened in task",
    })
    assert first.status_code == 200, first.text

    response = client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "room": "project",
        "type": "discovery", "body": "now claiming project",
    })

    assert response.status_code == 400, response.text


def test_lobby_stays_out_of_durable_memory_until_promoted(surfaces):
    """§11: lobby posts live outside the WAL. Publishing writes nothing."""
    client, _mcp, db = surfaces
    headers, _agent_id, _token = _register_rest(client, "ephemeral")
    sid = client.post("/v1/sessions", headers=headers,
                      json={"task": "parser"}).json()["session_id"]
    before = db.stats()["record_count"]

    client.post("/v1/lobby/publish", headers=headers, json={
        "session_id": sid, "task": "parser", "room": "task",
        "type": "discovery", "body": "nothing durable should come of this",
    })

    assert db.stats()["record_count"] == before


# --------------------------------------------------- namespace resolution ---
#
# The board join key is (task, namespace, room), and `_promote` files the
# durable record into board["namespace"]. So a namespace resolved differently
# by the two surfaces splits agents onto invisible boards and misfiles memory.
# Both regressions below were live; see embers/lobby/context.py.

def test_board_inherits_the_session_namespace_not_the_protocol_default(surfaces):
    """An agent working in namespace X gets a board — and a promotion — in X.

    The MCP adapter used to resolve the board namespace from
    `protocol.namespace`, ignoring the session entirely. An agent that opened
    a session in "parser-work" opened its board in "memories", and the
    proposal promoted from that board was filed under "memories" too.
    """
    client, mcp, db = surfaces
    _headers, agent_id, token = _register_rest(client, "scoped")
    sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": agent_id, "token": token,
        "task": "parser", "namespace": "parser-work",
    }))["session_id"]
    assert db.get_session(sid).namespace == "parser-work"
    assert mcp.protocol.namespace != "parser-work", (
        "this test is only meaningful while the protocol default differs")

    opened = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser", "room": "task",
    }))
    assert opened["namespace"] == "parser-work"

    post = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "publish", "session_id": sid, "room": "task",
        "type": "discovery", "body": "chunked reads hold memory flat",
    }))
    promoted = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "promote", "session_id": sid, "post_id": post["post_id"],
    }))
    assert db.get_proposal(promoted["proposal_id"]).namespace == "parser-work"


def test_explicit_namespace_still_wins_over_the_session(surfaces):
    """Resolution order is explicit → session → surface default."""
    client, mcp, _db = surfaces
    _headers, agent_id, token = _register_rest(client, "deliberate")
    sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": agent_id, "token": token,
        "task": "parser", "namespace": "parser-work",
    }))["session_id"]

    opened = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": sid, "task": "parser",
        "room": "task", "namespace": "shared-findings",
    }))

    assert opened["namespace"] == "shared-findings"


def test_mcp_and_rest_agents_on_one_task_share_one_board(surfaces):
    """The default path must converge, not just the explicit one.

    `POST /v1/sessions` defaulted a session's namespace to "default" while
    `ember_start_session` defaulted it to the protocol namespace. Neither
    agent named a namespace, both named the same task and room, and they
    still ended up on two boards that could not see each other.
    """
    client, mcp, _db = surfaces
    rest_headers, _rest_id, _rest_token = _register_rest(client, "http-side")
    _mcp_headers, mcp_id, mcp_token = _register_rest(client, "mcp-side")

    rest_sid = client.post("/v1/sessions", headers=rest_headers,
                           json={"task": "parser"}).json()["session_id"]
    mcp_sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": mcp_id, "token": mcp_token, "task": "parser",
    }))["session_id"]

    rest_post = client.post("/v1/lobby/publish", headers=rest_headers, json={
        "session_id": rest_sid, "task": "parser", "room": "task",
        "type": "discovery", "body": "found it from the HTTP side",
    })
    assert rest_post.status_code == 200, rest_post.text
    mcp_board = _mcp_body(mcp.call_tool("ember_lobby", {
        "action": "open", "session_id": mcp_sid, "task": "parser", "room": "task",
    }))

    # One board, both sessions on it, and the HTTP agent's post is visible.
    assert sorted(mcp_board["participants"]) == sorted([rest_sid, mcp_sid])
    assert [p["post_id"] for p in mcp_board["posts"]] == [
        rest_post.json()["post_id"]]


def test_rest_session_namespace_matches_mcp_session_namespace(surfaces):
    """The two doors agree on what an unspecified namespace means."""
    client, mcp, db = surfaces
    rest_headers, _rest_id, _rest_token = _register_rest(client, "door-a")
    _mcp_headers, mcp_id, mcp_token = _register_rest(client, "door-b")

    rest_sid = client.post("/v1/sessions", headers=rest_headers,
                           json={"task": "t"}).json()["session_id"]
    mcp_sid = _mcp_body(mcp.call_tool("ember_start_session", {
        "agent_id": mcp_id, "token": mcp_token, "task": "t",
    }))["session_id"]

    assert db.get_session(rest_sid).namespace == db.get_session(mcp_sid).namespace
