"""One observation link, one session, every existing surface."""
import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from embers import api
from embers.api.usefulness_routes import live
from embers.identity.registry import AgentRegistry
from embers.integration import observation_session as sessions
from embers.integration import research_observer as ro
from embers.integration.visualizer_access import issue as issue_view
from embers.integration.visualizer_access import revoke as revoke_view
from test_research_observer import env
from test_usefulness import rig


def _cookie(response, name):
    jar = response.cookies.get(name) or response.headers.get('set-cookie', '')
    if jar and jar != response.headers.get('set-cookie', ''):
        return jar
    match = re.search(r'(?:^|,)\s*' + re.escape(name) + r'=([^;,\s]+)', response.headers.get('set-cookie', ''))
    if match:
        return match.group(1)
    return response.cookies.get(name)


def test_unified_page_redirects_and_keeps_every_surface(env):
    *_, client, _path = env
    shell = client.get('/observe')
    assert shell.status_code == 200
    assert 'Agent memory' in shell.text and 'Namespace View' in shell.text and '>Status<' in shell.text
    assert 'consumeObserverLink' in shell.text and 'consumeViewLink' in shell.text
    assert '/v1/visualizer-access/exchange' in shell.text and "method:'GET'" in shell.text
    assert '/v1/usefulness/' not in shell.text
    assert 'ngrok-skip-browser-warning' in shell.text
    assert '/v1/observer/transport.js' in shell.text
    agent = client.get('/observe/agent')
    namespace = client.get('/observe/namespace')
    status = client.get('/status')
    assert agent.status_code == namespace.status_code == status.status_code == 200
    for marker in ('Session filter', 'Namespace filter', 'Memory filter', 'Event type filter', 'Apply filters', 'Memory groups', 'resumeObserverSession'):
        assert marker in agent.text
    for marker in ('Find a memory', '3D orbit', 'Bubbles', 'Heatmap', 'contested', 'Tokens', 'Memory Flow', 'Pairing Matrix', 'resumeNamespaceSession', 'Network'):
        assert marker in namespace.text
    assert 'Server status' in status.text and 'endpointStatus' in status.text
    assert "frame-ancestors 'self'" in agent.headers['content-security-policy']
    assert "frame-ancestors 'none'" in shell.headers['content-security-policy']
    for path, tab in (('/observer', 'tab=agent'), ('/visualizer', 'tab=namespace'), ('/visualizer?mode=observer', 'tab=agent')):
        moved = client.get(path, follow_redirects=False)
        assert moved.status_code == 307
        assert moved.headers['location'] == '/observe?' + tab
        assert client.get(path).status_code == 200


def test_observer_session_persists_until_expiry_and_revocation(env, monkeypatch, caplog):
    db, agent, *_rest, client, _path = env
    grant = ro.issue(db, agent)
    logger = logging.getLogger('embers.observer.transport')
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG):
            opened = client.post('/v1/observer/exchange', json={'code': grant['code']})
    finally:
        logger.removeHandler(caplog.handler)
    assert opened.status_code == 200
    raw = opened.headers['set-cookie']
    lowered = raw.lower()
    assert 'httponly' in lowered and 'samesite=lax' in lowered
    assert re.search(r'path=/($|;)', lowered)
    assert 'secure' not in lowered
    token = _cookie(opened, 'ember_observe_agent')
    assert token and token != grant['code']
    assert grant['code'] not in raw and grant['code'] not in opened.text and grant['code'] not in caplog.text
    assert token not in caplog.text
    assert not re.search(r'(?:^|;)\s*secure(?:;|$)', lowered)
    stored = '\n'.join(p.read_text() for p in sessions.directory(db).glob('*.json'))
    assert grant['code'] not in stored and token not in stored
    for path in ('/v1/observe/session', '/v1/observer/events', '/observe', '/observe/agent', '/observe/namespace', '/status'):
        assert client.get(path).status_code == 200
    again = client.get('/v1/observe/session').json()
    assert again['observer']['observed_agent_id'] == agent
    assert again['observer']['remaining_seconds'] > 0
    assert again['visualizer'] is None
    assert grant['code'] not in client.get('/v1/observe/session').text
    with monkeypatch.context() as clock:
        clock.setattr(ro.time, 'time', lambda: grant['expires_at'] + 5)
        clock.setattr(sessions.time, 'time', lambda: grant['expires_at'] + 5)
        assert client.get('/v1/observer/events').status_code == 403
        assert client.get('/v1/observer/stream').status_code == 403
        assert client.get('/v1/observe/session').json()['observer'] is None
    assert client.get('/observe/agent').status_code == 200
    secure = client.post('/v1/observer/exchange', json={'code': grant['code']}, headers={'X-Forwarded-Proto': 'https'})
    assert re.search(r'(?:^|;)\s*secure(?:;|$)', secure.headers['set-cookie'].lower())

    client.cookies.clear()
    fresh = ro.issue(db, agent)
    assert client.post('/v1/observer/exchange', json={'code': fresh['code']}).status_code == 200
    ro.revoke(db, fresh['observer_id'], agent)
    assert client.get('/v1/observer/events').status_code == 403
    assert client.get('/v1/observe/session').json()['observer'] is None
    assert not list(sessions.directory(db).glob('*.json')) or all(
        fresh['observer_id'] not in p.read_text() for p in sessions.directory(db).glob('*.json'))


def test_namespace_session_stream_last_event_id_and_isolation(rig, monkeypatch):
    monkeypatch.setattr(api, '_get_db', lambda: rig[0])
    client = TestClient(api.app)
    grant = issue_view(rig[0], 'experiment', 'admin')
    opened = client.post('/v1/visualizer-access/exchange', json={'code': grant['code']})
    assert opened.status_code == 200
    token = _cookie(opened, 'ember_observe_ns')
    assert token and token != grant['code'] and grant['code'] not in opened.headers['set-cookie']
    assert client.get('/v1/visualizer/experiment').status_code == 200
    assert client.get('/v1/visualizer/other').status_code == 403
    assert client.get('/v1/observer/events').status_code == 403
    state = client.get('/v1/observe/session').json()
    assert state['visualizer']['namespace'] == 'experiment' and state['observer'] is None
    # Navigation and a second read use the cookie alone.
    assert client.get('/observe?tab=namespace').status_code == 200
    assert client.get('/v1/visualizer-status/experiment').status_code == 200

    async def once(last_event_id):
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        request = Request({'type': 'http', 'method': 'GET', 'path': '/v1/visualizer-stream/experiment',
                           'query_string': b'', 'headers': [
                               (b'cookie', ('ember_observe_ns=' + token).encode()),
                               (b'last-event-id', last_event_id.encode()),
                           ]}, receive)
        return await live('experiment', request, after=99, last_event_id=last_event_id,
                          x_ember_agent_id=None, x_ember_token=None)

    async def run():
        response = await once('0')
        assert response.status_code == 200
        assert response.headers['x-ember-observation-protocol'] == 'ember-observation.v2'
        first = await anext(response.body_iterator)
        assert 'event: ready' in first and 'Last-Event-ID' not in first
        assert grant['code'] not in first and token not in first
        await response.body_iterator.aclose()
    asyncio.run(run())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        asyncio.run(once('50'))
    assert error.value.status_code == 400
    revoke_view(rig[0], grant['grant_id'], 'admin')
    assert client.get('/v1/visualizer/experiment').status_code == 403
    assert client.get('/v1/visualizer-stream/experiment').status_code == 403
    assert client.get('/v1/observe/session').json()['visualizer'] is None


def test_observer_stream_uses_session_and_last_event_id(env):
    from embers.api.observer_routes import stream
    db, agent, *rest = env
    client = rest[-2]
    grant = ro.issue(db, agent)
    opened = client.post('/v1/observer/exchange', json={'code': grant['code']})
    token = _cookie(opened, 'ember_observe_agent')
    cursor = ro.page(db, grant['code'])['cursor']

    async def run():
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        request = Request({'type': 'http', 'method': 'GET', 'path': '/v1/observer/stream',
                           'query_string': b'', 'headers': [
                               (b'cookie', ('ember_observe_agent=' + token).encode()),
                               (b'last-event-id', cursor.encode()),
                           ]}, receive)
        response = await stream(request, None, cursor)
        assert response.headers['content-type'].startswith('text/event-stream')
        first = await anext(response.body_iterator)
        assert 'event: ready' in first
        assert grant['code'] not in first and token not in first
        await response.body_iterator.aclose()
    asyncio.run(run())
    denied = TestClient(api.app).get('/v1/observer/stream', headers={'Cookie': 'ember_observe_agent=' + ('a' * 43)})
    assert denied.status_code == 403
    assert grant['code'] not in denied.text


def test_code_attempts_are_rate_limited_without_echoing_the_secret(env, monkeypatch):
    *_, client, _path = env
    monkeypatch.setattr(sessions, 'FAILURE_LIMIT', 2)
    secret = 'not-a-real-observer-code'
    headers = {'X-Forwarded-For': '203.0.113.44'}
    first = client.post('/v1/observe/exchange', json={'code': secret}, headers=headers)
    second = client.post('/v1/observe/exchange', json={'code': secret}, headers=headers)
    assert first.status_code == 403 and second.status_code == 429
    assert secret not in first.text and secret not in second.text
    blocked = client.post('/v1/visualizer-access/exchange', json={'code': 'EMBER-AAAAA-AAAAA-AAAAA-AAAAA'}, headers=headers)
    assert blocked.status_code == 429
    assert 'AAAAA' not in blocked.text


def test_observation_layout_fits_phones_and_desktops(env):
    *_, client, _path = env
    shell = client.get('/observe').text
    agent = client.get('/observe/agent').text
    namespace = client.get('/observe/namespace').text
    status = client.get('/status').text
    for page in (shell, agent, namespace, status):
        assert 'width=device-width' in page and 'viewport-fit=cover' in page
    assert 'min-height:44px' in shell and 'safe-area-inset-bottom' in shell
    assert 'data-theme=light' in shell and 'catch (error)' in shell
    boot = shell.split('async function boot', 1)[1]
    assert boot.index('catch (error)') < boot.index('show(tab)')
    for page in (agent, namespace):
        assert 'id="observation-responsive"' in page
        assert 'min-height:44px' in page and 'overflow-x:auto' in page
        assert '@media(max-width:800px)' in page and '@media(min-width:1100px)' in page
    assert 'min-height:44px' in status
    node = shutil.which('node')
    if node:
        source = next(script for script in re.findall(r'<script>(.*?)</script>', Path('embers/api/observe.html').read_text(), re.S) if 'function boot' in script)
        subprocess.run([node, '-e', 'new Function(process.argv[1])', source], check=True)


def test_both_tools_return_the_same_observation_link(env, monkeypatch):
    db, agent, token, *_rest, client, _path = env
    from embers.api import mcp_http
    monkeypatch.setattr(mcp_http, '_mcp', None)
    db.create_namespace('notes', owner=agent)

    def rpc(name, arguments):
        result = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                           'params': {'name': name, 'arguments': arguments}}).json()['result']
        assert not result['isError']
        import json
        return json.loads(result['content'][0]['text'])

    observer = rpc('ember_visualize', {'agent_id': agent, 'token': token, 'server_url': 'https://example.test'})
    visualizer = rpc('ember_visualizer_access', {'agent_id': agent, 'token': token, 'namespace': 'notes',
                                                  'server_url': 'https://example.test'})
    assert observer['visualization_url'].startswith('https://example.test/observe#code=')
    assert visualizer['viewer_url'].startswith('https://example.test/observe#code=')
    assert observer['viewer_path'].startswith('/observe#code=')
    assert visualizer['viewer_path'].startswith('/observe#code=')
    assert token not in observer['visualization_url'] and token not in visualizer['viewer_url']
