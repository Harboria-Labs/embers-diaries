"""Delegated Live Lobby view has read authority and zero Lobby write effect."""

import hashlib

from fastapi.testclient import TestClient

import embers.api as api
import embers.api.v1 as v1
from embers.db import EmberDB
from embers.lobby import working


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_explicit_lobby_observation_is_read_only_and_revocable(tmp_path):
    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._registry = None
    v1._registry = None
    with TestClient(api.app) as client:
        owner = client.post("/v1/agents/register", json={"name": "owner"}).json()
        peer = client.post("/v1/agents/register", json={"name": "peer"}).json()
        oh = {"X-Ember-Agent-Id": owner["agent_id"], "X-Ember-Token": owner["token"]}
        ph = {"X-Ember-Agent-Id": peer["agent_id"], "X-Ember-Token": peer["token"]}
        sid = client.post("/v1/sessions", headers=oh, json={"task": "task", "namespace": "shared"}).json()["session_id"]
        pid = client.post("/v1/sessions", headers=ph, json={"task": "task", "namespace": "shared"}).json()["session_id"]
        scope = client.post("/v1/lobby/v2/scopes", headers=oh,
                            json={"session_id": sid, "task": "task", "namespace": "shared"}).json()["scope_id"]
        working.grant(api._db, scope, sid, owner["agent_id"], peer["agent_id"])
        item = working.submit(api._db, scope, sid, owner["agent_id"], {
            "client_submission_id": "one", "share": True, "kind": "finding",
            "claim": "observed isolated result", "uncertainty": "unverified"})
        assert client.get("/v1/observer/lobby").status_code == 403
        ordinary = client.post("/v1/observer-access", headers=oh, json={}).json()
        assert client.post("/v1/observer/exchange", json={"code": ordinary["code"]}).status_code == 200
        assert client.get("/v1/observer/lobby").status_code == 403
        delegated = client.post("/v1/observer-access", headers=oh,
            json={"lobby_scope_id": scope, "lobby_session_id": sid}).json()
        assert client.post("/v1/observer/exchange", json={"code": delegated["code"]}).status_code == 200
        file = working.path(api._db)
        before = _digest(file)
        page = client.get("/v1/observer/lobby")
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["items"][0]["item_id"] == item["item_id"]
        assert body["items"][0]["evidence_count"] == 0
        assert peer["agent_id"] in body["scope"]["members"]
        assert client.post(f"/v1/lobby/v2/scopes/{scope}/items", json={}).status_code == 401
        assert _digest(file) == before
        from embers.integration import research_observer
        peer_grant = research_observer.issue(api._db, peer["agent_id"],
            lobby_scope_id=scope, lobby_session_id=pid)
        assert client.post("/v1/observer/exchange", json={"code": peer_grant["code"]}).status_code == 200
        assert client.get("/v1/observer/lobby").status_code == 200
        working.revoke(api._db, scope, sid, owner["agent_id"], peer["agent_id"])
        assert client.get("/v1/observer/lobby").status_code == 403
        assert client.post("/v1/observer/exchange", json={"code": delegated["code"]}).status_code == 200
        assert client.get("/v1/observer/lobby").status_code == 200
        # The capability itself can be revoked without touching Lobby rows.
        research_observer.revoke(api._db, delegated["observer_id"], owner["agent_id"])
        assert client.get("/v1/observer/lobby").status_code == 403


def test_lobby_panel_is_present_only_on_observer_page():
    from embers.api.observer_build import render
    observer = render()
    assert 'id="lobbyToggle"' in observer
    assert 'id="lobbyView"' in observer
    assert 'EmberLobbyObserver' in observer
    assert 'id="lobbyView"' not in render(namespace=True)
