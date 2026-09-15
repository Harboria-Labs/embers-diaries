"""Provider-neutral REST mirror for the ephemeral lobby."""

import pytest

from embers import EmberDB
from embers.mcp.lobby_surface import STORE


@pytest.fixture
def client(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1
    from tests.test_v1_promotion import ASGIClient

    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._protocol = None
    api._registry = None
    v1._registry = None
    v1._protocol = None
    STORE.reset()
    return ASGIClient(api.app)


def _auth(client):
    response = client.post("/v1/agents/register", json={"name": "rest-lobby"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "X-Ember-Agent-Id": body["agent_id"],
        "X-Ember-Token": body["token"],
    }


def test_lobby_publish_updates_status_and_promote(client):
    auth = _auth(client)
    started = client.post("/v1/sessions", headers=auth,
                          json={"task": "rest parser", "namespace": "rest"})
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]

    response = client.post("/v1/lobby/publish", headers=auth, json={
        "session_id": sid, "task": "rest parser", "room": "task",
        "type": "discovery", "body": "stream the parser",
    })
    assert response.status_code == 200, response.text
    post = response.json()
    assert post["type"] == "discovery"

    session_headers = {**auth, "X-Ember-Session-Id": sid}
    status = client.get("/v1/lobby/status", headers=session_headers)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "open"

    updates = client.get("/v1/lobby/updates", headers=session_headers)
    assert updates.status_code == 200, updates.text
    assert updates.json()["updates"][0]["post_id"] == post["post_id"]

    promoted = client.post("/v1/lobby/promote", headers=auth, json={
        "session_id": sid, "post_id": post["post_id"],
    })
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["kind"] == "proposal"
    session = client.get(f"/v1/sessions/{sid}", headers=auth)
    assert promoted.json()["proposal_id"] in session.json()["discoveries"]


def test_lobby_status_is_idle_before_first_publish(client):
    auth = _auth(client)
    started = client.post("/v1/sessions", headers=auth,
                          json={"task": "quiet lobby"})
    sid = started.json()["session_id"]

    response = client.get(
        "/v1/lobby/status", headers={**auth, "X-Ember-Session-Id": sid})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "session_id": sid, "status": "idle", "board": None,
    }


def test_lobby_rejects_another_agents_session(client):
    first_auth = _auth(client)
    first_session = client.post(
        "/v1/sessions", headers=first_auth, json={"task": "private task"})
    sid = first_session.json()["session_id"]
    second_auth = _auth(client)

    response = client.get(
        "/v1/lobby/updates", headers=second_auth, params={"session_id": sid})

    assert response.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("post", "/v1/lobby/publish"),
    ("get", "/v1/lobby/updates"),
    ("get", "/v1/lobby/status"),
    ("post", "/v1/lobby/promote"),
])
def test_lobby_routes_require_authentication(client, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
