import json
import time
import pytest
from fastapi.testclient import TestClient
from embers import api
from embers.identity.registry import AgentRegistry
from embers.integration.visualizer_access import issue, verify, revoke
from test_usefulness import rig, report, file_hashes


@pytest.fixture
def client(rig,monkeypatch):
    monkeypatch.setattr(api,'_get_db',lambda:rig[0])
    return TestClient(api.app)


def test_code_exchange_read_only_scope_and_no_mutation(rig,client):
    actor,token=AgentRegistry(rig[0]).register('viewer-issuer')
    rig[0].create_namespace('experiment',owner=actor.agent_id)
    grant=issue(rig[0],'experiment',actor.agent_id)
    assert grant['code'] not in json.dumps(rig[0]._store.read(grant['grant_id']).to_dict())
    before=file_hashes(rig[-1])
    response=client.post('/v1/visualizer-access/exchange',json={'code':grant['code']})
    assert response.status_code==200 and response.json()['namespace']=='experiment'
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert client.get('/v1/visualizer/experiment').status_code==200
    assert client.get('/v1/visualizer/other').status_code==403
    assert client.get('/v1/visualizer-tokenization/experiment',params={'memory_id':rig[2][0]}).status_code==200
    assert client.post('/v1/usefulness/experiment',json={}).status_code==401
    assert client.post('/v1/memory/write',json={'content':'must not write'}).status_code==401
    assert client.post('/v1/visualizer-access',json={'namespace':'experiment'}).status_code==401
    assert file_hashes(rig[-1])==before
    assert token not in response.text


def test_expiry_revocation_restart_and_session_binding(rig,monkeypatch):
    from embers.db import EmberDB
    grant=issue(rig[0],'experiment','admin',ttl_seconds=60)
    assert verify(EmberDB.connect(str(rig[-1])),grant['code'])['actor']=='admin'
    with pytest.raises(PermissionError):revoke(rig[0],grant['grant_id'],'outsider')
    revoke(rig[0],grant['grant_id'],'admin')
    with pytest.raises(PermissionError):verify(rig[0],grant['code'])
    grant=issue(rig[0],'experiment','admin',ttl_seconds=60)
    import embers.integration.visualizer_access as module
    monkeypatch.setattr(module.time,'time',lambda:grant['expires_at']+1)
    with pytest.raises(PermissionError):verify(rig[0],grant['code'])


def test_grant_rechecks_namespace_permissions(rig):
    rig[0].create_namespace('private',owner='owner')
    with pytest.raises(PermissionError):issue(rig[0],'private','outsider')
    grant=issue(rig[0],'experiment','admin')
    rig[0].create_namespace('experiment',owner='new-owner')
    with pytest.raises(PermissionError):verify(rig[0],grant['code'])


def test_tokenization_ids_are_real_and_scoped(rig,client):
    import tiktoken
    grant=issue(rig[0],'experiment','admin')
    client.post('/v1/visualizer-access/exchange',json={'code':grant['code']})
    before=file_hashes(rig[-1])
    data=client.get('/v1/visualizer-tokenization/experiment',params={'memory_id':rig[2][0]}).json()
    expected=tiktoken.get_encoding('cl100k_base').encode('latent memory 0')
    assert [t['id'] for t in data['tokens']]==expected
    assert data['token_count']==len(expected)
    assert file_hashes(rig[-1])==before
    assert client.get('/v1/visualizer-tokenization/other',params={'memory_id':rig[2][0]}).status_code==403


def test_heat_survives_event_page_and_is_never_inferred_from_u(rig):
    from embers.integration.usefulness_service import snapshot
    report(rig)
    assert snapshot(rig[0],'experiment','admin')['nodes'][0]['heat'] is None
    rid=rig[2][0]
    rig[1].observe({'operation':'ember_candidate_recall','observed_heat':{rid:.37},'context':'actual-context',
                   'heat_source':'legacy Candidate 04 receipt','returned_ids':[rid], 'budget':{},'latency_ms':1},actor='admin',request_id='heat')
    report(rig)
    node=snapshot(rig[0],'experiment','admin',after=2,limit=1)['nodes'][0]
    assert node['heat']==.37 and node['heat_context']=='actual-context'
    assert node['usefulness'][0]['value']==.6


def test_http_mcp_grant_tool_is_discoverable(rig,client,monkeypatch):
    from embers.api import mcp_http
    monkeypatch.setattr(mcp_http,'_mcp',None)
    actor,token=AgentRegistry(rig[0]).register('mcp-issuer')
    def rpc(method,params):return client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params}).json()['result']
    assert 'ember_visualizer_access' in {t['name'] for t in rpc('tools/list',{})['tools']}
    output=rpc('tools/call',{'name':'ember_visualizer_access','arguments':{'agent_id':actor.agent_id,'token':token,
                   'namespace':'experiment','server_url':'https://example.test/mcp'}})
    assert not output['isError']
    grant=json.loads(output['content'][0]['text'])
    assert grant['viewer_url'].startswith('https://example.test/visualizer#view=EMBER-')
    assert token not in json.dumps(grant)
    assert client.post('/v1/visualizer-access/exchange',json={'code':grant['code']}).status_code==200


def test_session_grant_and_live_revocation_are_read_only(rig):
    import asyncio
    from embers.integration.observation_stream import stream
    db=rig[0]
    session=db.start_session(agent_id='admin')
    with pytest.raises(PermissionError):issue(db,'experiment','outsider',session_id=session)
    grant=issue(db,'experiment','admin',session_id=session)
    async def run():
        async def disconnected():return False
        gen=stream(db,'experiment','admin',0,lambda:verify(db,grant['code'],'experiment'),disconnected,interval=.01)
        before=file_hashes(rig[-1])
        assert 'event: patch' in await anext(gen)
        assert await anext(gen)==': heartbeat\n\n'
        assert file_hashes(rig[-1])==before
        db.end_session(session)
        with pytest.raises(PermissionError):await anext(gen)
    asyncio.run(run())
    with pytest.raises(PermissionError):verify(db,grant['code'])


def test_code_normalization_is_idempotent_even_with_matching_prefix():
    from embers.integration.visualizer_access import normalize
    raw='EMBER'+'A'*15
    assert normalize(normalize('EMBER-'+raw))==raw


def test_view_code_cannot_learn_bearer_sessions_from_snapshot(rig,client):
    sid=rig[0].start_session(agent_id='admin')
    report(rig,session_id=sid)
    grant=issue(rig[0],'experiment','admin',session_id=sid)
    client.post('/v1/visualizer-access/exchange',json={'code':grant['code']})
    before=file_hashes(rig[-1])
    response=client.get('/v1/visualizer/experiment')
    assert response.status_code==200
    assert sid not in response.text
    assert 'session-sha256:' in response.text
    assert grant['code'] not in response.text
    assert file_hashes(rig[-1])==before
