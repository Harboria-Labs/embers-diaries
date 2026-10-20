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


def test_projection_provenance_tombstones_and_closed_scope(tmp_path):
    import json
    db = EmberDB.connect(str(tmp_path / 'isolated'))
    sid = db.start_session('author', task='task', namespace='shared')
    pid = db.start_session('peer', task='task', namespace='shared')
    scope = working.create_scope(db, sid, 'author', task='task', namespace='shared')['scope_id']
    working.grant(db, scope, sid, 'author', 'peer')
    item = working.submit(db, scope, sid, 'author', {
        'client_submission_id': 'one', 'share': True, 'kind': 'question',
        'claim': 'Is the result reproducible?', 'evidence_refs': [{
            'reference': 'https://example.org/private-artifact', 'source': 'test output',
            'observed_at': '2026-10-19T00:00:00Z', 'execution_context': 'PRIVATE-CONTEXT'}]})
    working.contribute(db, scope, pid, 'peer', item['item_id'], {
        'client_contribution_id': 'peer-one', 'kind': 'contradiction', 'note': 'PRIVATE-NOTE'})
    file = working.path(db)
    before = _digest(file)
    snapshot = working.observation_snapshot(db, scope, sid, 'author')
    row = snapshot['items'][0]
    assert row['evidence_status'] == 'attached'
    assert row['evidence'][0]['source'] == 'test output'
    assert row['contributions'][0]['agent_id'] == 'peer'
    assert row['contributions'][0]['kind'] == 'contradiction'
    serialized = json.dumps(snapshot)
    for secret in (sid, pid, 'private-artifact', 'PRIVATE-CONTEXT', 'PRIVATE-NOTE'):
        assert secret not in serialized
    assert _digest(file) == before
    working.change(db, scope, sid, 'author', item['item_id'], version=2, correction='Corrected question')
    assert working.observation_snapshot(db, scope, sid, 'author')['items'][0]['version'] == 3
    working.change(db, scope, sid, 'author', item['item_id'], version=3, withdraw=True)
    assert working.observation_snapshot(db, scope, sid, 'author')['items'][0]['claim'] is None
    cursor = working.observation_snapshot(db, scope, sid, 'author')['cursor']
    working.close_scope(db, scope, sid, 'author')
    assert working.observation(db, scope, sid, 'author', after=cursor)['events'][-1]['change'] == 'scope_closed'
    assert working.observation_snapshot(db, scope, sid, 'author')['scope']['status'] == 'closed'


def test_mcp_can_delegate_existing_lobby_scope(tmp_path):
    import json
    from embers.mcp.server import EmberMCP
    from embers.mcp.tools import TOOLS
    db = EmberDB.connect(str(tmp_path / 'isolated'))
    from embers.identity.registry import AgentRegistry
    agent, token = AgentRegistry(db).register('observer-owner')
    # Use the same public MCP call surface as an agent.
    server = EmberMCP(db)
    sid = db.start_session(agent.agent_id, task='task', namespace='shared')
    scope = working.create_scope(db, sid, agent.agent_id, task='task', namespace='shared')['scope_id']
    props = next(t for t in TOOLS if t['name'] == 'ember_visualize')['inputSchema']['properties']
    assert {'lobby_scope_id', 'lobby_session_id'} <= props.keys()
    result = server.call_tool('ember_visualize', {'agent_id': agent.agent_id, 'token': token,
        'lobby_scope_id': scope, 'lobby_session_id': sid, 'server_url': 'http://localhost:8000'})
    assert not result.get('isError'), result
    grant = json.loads(result['content'][0]['text'])
    assert grant['lobby_scope_id'] == scope
    assert grant['visualization_url'].startswith('http://localhost:8000/visualizer?mode=observer#observe=')


def test_snapshot_cursor_and_rows_share_one_read_transaction(tmp_path, monkeypatch):
    from contextlib import contextmanager
    db = EmberDB.connect(str(tmp_path / 'isolated'))
    sid = db.start_session('author', task='task', namespace='shared')
    scope = working.create_scope(db, sid, 'author', task='task', namespace='shared')['scope_id']
    submit = lambda key: working.submit(db, scope, sid, 'author', {
        'client_submission_id': key, 'share': True, 'kind': 'finding', 'claim': key})
    first = submit('first')
    original = working._read_open
    raced = []
    @contextmanager
    def race(database):
        with original(database) as conn:
            def trace(sql):
                if sql.startswith('SELECT item_id,durable_id') and not raced:
                    raced.append(submit('racing'))
            conn.set_trace_callback(trace)
            yield conn
    monkeypatch.setattr(working, '_read_open', race)
    snapshot = working.observation_snapshot(db, scope, sid, 'author')
    assert [i['item_id'] for i in snapshot['items']] == [first['item_id']]
    assert working.observation(db, scope, sid, 'author', after=snapshot['cursor'])['events'][0]['item_id'] == raced[0]['item_id']


def test_observer_projection_checks_namespace_and_session_on_every_read(tmp_path):
    import pytest
    db = EmberDB.connect(str(tmp_path / 'isolated'))
    sid = db.start_session('author', task='task', namespace='shared')
    outsider = db.start_session('outsider', task='task', namespace='other')
    scope = working.create_scope(db, sid, 'author', task='task', namespace='shared')['scope_id']
    with pytest.raises(PermissionError):
        working.observation_snapshot(db, scope, outsider, 'outsider')
    db.end_session(sid)
    with pytest.raises(PermissionError):
        working.observation_snapshot(db, scope, sid, 'author')
    with pytest.raises(PermissionError):
        working.observation(db, scope, sid, 'author')


def test_lobby_client_state_machine():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for DOM/state-machine unit tests')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, '--test', 'tests/js/lobby_observer.test.cjs'], cwd=root, check=True)
