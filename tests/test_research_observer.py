import asyncio
import json
import time
import pytest
from fastapi.testclient import TestClient
from embers import api
from embers.db import EmberDB
from embers.identity.registry import AgentRegistry
from embers.integration import MemoryProtocol
from embers.integration.usefulness_service import service
from embers.integration import research_observer as ro
from test_usefulness import file_hashes


@pytest.fixture
def env(tmp_path,monkeypatch):
    db=EmberDB.connect(str(tmp_path/'store'))
    agent,token=AgentRegistry(db).register('observed')
    other,other_token=AgentRegistry(db).register('other')
    monkeypatch.setattr(api,'_get_db',lambda:db)
    return db,agent.agent_id,token,other.agent_id,other_token,TestClient(api.app),tmp_path/'store'


def event(env,ns,actor=None,session=None,context='research'):
    db,agent,*_=env;actor=actor or agent
    proto=MemoryProtocol(db,default_namespace=ns)
    rid=proto.remember({'content':'fictional '+ns,'subject':'Observer fixture','verify_status':'hypothesis'},primary_context=context)
    ledger=service(db,ns)
    e=ledger.apply('report',{'target':{'kind':'memory','memory_ids':[rid]},'context':context,
                    'feedback_type':'CONTRIBUTED','session_id':session},actor=actor,request_id=str(time.time_ns()))
    return e,rid


def test_follows_namespaces_sessions_persistence_and_no_mutation(env):
    db,agent,_,other,_,client,path=env
    event(env,'past')
    before=file_hashes(path)
    grant=ro.issue(db,agent)
    assert file_hashes(path)==before # Grant metadata is outside memory storage.
    first=ro.page(db,grant['code']);assert first['events']==[]
    s1=db.start_session(agent_id=agent);e1,r1=event(env,'A',session=s1)
    db.end_session(s1)
    s2=db.start_session(agent_id=agent);e2,r2=event(env,'B',session=s2)
    event(env,'A',actor=other);event(env,'secret',actor=other)
    before=file_hashes(path)
    data=ro.page(db,grant['code'],first['cursor'])
    assert [e['namespace'] for e in data['events']]==['A','B']
    assert [e['event_id'] for e in data['events']]==[e1['id'],e2['id']]
    assert all(e['observed_agent_id']==agent for e in data['events'])
    assert s1 not in json.dumps(data) and s2 not in json.dumps(data)
    assert data['events'][0]['session']!=data['events'][1]['session']
    assert ro.page(db,grant['code'],data['cursor'])['events']==[]
    reopened=EmberDB.connect(str(path))
    assert ro.page(reopened,grant['code'],first['cursor'])['events']==data['events']
    assert file_hashes(path)==before


def test_scope_filters_and_no_cross_agent_or_namespace_leak(env):
    db,agent,_,other,*_=env
    with pytest.raises(PermissionError):ro.issue(db,agent,observer_target=other)
    db.create_namespace('private',owner=other)
    with pytest.raises(PermissionError):ro.issue(db,agent,namespaces=['private'])
    grant=ro.issue(db,agent,namespaces=['A','B'])
    s=db.start_session(agent_id=agent)
    ea,ra=event(env,'A',session=s);eb,rb=event(env,'B',context='math')
    event(env,'outside');event(env,'A',actor=other)
    for filters,expected in [({'namespace':'A'},ea),({'context':'math'},eb),({'memory':rb},eb),({'session':ro._ref(s)},ea)]:
        rows=ro.page(db,grant['code'],filters=filters)['events']
        assert [r['event_id'] for r in rows]==[expected['id']]
    assert len(ro.page(db,grant['code'],filters={'event_type':'CONTRIBUTED'})['events'])==2
    assert ro.page(db,grant['code'],filters={'namespace':'private'})['events']==[]
    db.create_namespace('A',owner=other)
    assert [r['namespace'] for r in ro.page(db,grant['code'])['events']]==['B']


def test_expiry_revocation_session_scoping_and_cursor_binding(env,monkeypatch):
    db,agent,_,other,*_=env
    s=db.start_session(agent_id=agent)
    grant=ro.issue(db,agent,span_sessions=False,session_id=s)
    event(env,'A',session=s);event(env,'A')
    assert len(ro.page(db,grant['code'])['events'])==1
    db.end_session(s)
    with pytest.raises(PermissionError):ro.verify(db,grant['code'])
    grant=ro.issue(db,agent)
    with pytest.raises(PermissionError):ro.revoke(db,grant['observer_id'],other)
    second=ro.issue(db,agent)
    with pytest.raises(ValueError):ro.page(db,second['code'],ro.page(db,grant['code'])['cursor'])
    ro.revoke(db,grant['observer_id'],agent)
    with pytest.raises(PermissionError):ro.verify(db,grant['code'])
    monkeypatch.setattr(ro.time,'time',lambda:second['expires_at']+1)
    with pytest.raises(PermissionError):ro.verify(db,second['code'])


def test_capability_has_no_ordinary_api_or_namespace_authority(env):
    db,agent,token,other,_,client,path=env
    assert client.get('/v1/observer/events').status_code==403
    assert client.get('/v1/observer/stream').status_code==403
    auth={'X-Ember-Agent-Id':agent,'X-Ember-Token':token}
    denied=client.post('/v1/observer-access',headers=auth,json={'observer_target':other})
    assert denied.status_code==403
    grant=client.post('/v1/observer-access',headers=auth,json={}).json()
    assert 'visualization_url' in grant
    before=file_hashes(path)
    assert client.post('/v1/observer/exchange',json={'code':grant['code']}).status_code==200
    assert client.get('/v1/observer/events').status_code==200
    assert client.get('/v1/visualizer/A').status_code==401
    assert client.post('/v1/usefulness/A',json={}).status_code==401
    assert client.post('/v1/memory/write',json={'content':'no'}).status_code==401
    assert client.post('/v1/observer-access',json={}).status_code==401
    assert file_hashes(path)==before


def test_actual_stream_wakeup_filter_catchup_and_zero_read_writes(env):
    db,agent,_,other,_,_,path=env
    grant=ro.issue(db,agent)
    async def disconnected():return False
    async def run():
        gen=ro.stream(db,grant['code'],None,{},disconnected,interval=3600)
        assert 'event: ready' in await anext(gen)
        initial=await anext(gen)
        cursor=json.loads(next(x[6:] for x in initial.splitlines() if x.startswith('data: ')))['cursor']
        pending=asyncio.create_task(anext(gen));await asyncio.sleep(.02)
        e,r=event(env,'A')
        frame=await asyncio.wait_for(pending,1)
        assert e['id'] in frame and grant['code'] not in frame
        data=json.loads(next(x[6:] for x in frame.splitlines() if x.startswith('data: ')))
        cursor=data['cursor'];await gen.aclose()
        event(env,'foreign',actor=other);e2,_=event(env,'B')
        before=file_hashes(path)
        gen=ro.stream(db,grant['code'],cursor,{},disconnected,interval=.01)
        await anext(gen);frame=await anext(gen)
        assert e2['id'] in frame and 'foreign' not in frame
        assert await anext(gen)==': heartbeat\n\n'
        await gen.aclose();assert file_hashes(path)==before
        ro.revoke(db,grant['observer_id'],agent)
        gen=ro.stream(db,grant['code'],cursor,{},disconnected)
        with pytest.raises(PermissionError):await anext(gen)
    asyncio.run(run())


def test_bounded_pages_and_mcp_discovery(env,monkeypatch):
    db,agent,token,_,_,client,path=env
    from embers.api import mcp_http
    monkeypatch.setattr(mcp_http,'_mcp',None)
    def rpc(method,params):return client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params}).json()['result']
    assert 'ember_visualize' in {x['name'] for x in rpc('tools/list',{})['tools']}
    result=rpc('tools/call',{'name':'ember_visualize','arguments':{'agent_id':agent,'token':token,'server_url':'https://example.test'}})
    assert not result['isError']
    grant=json.loads(result['content'][0]['text'])
    assert grant['visualization_url'].startswith('https://example.test/visualizer?mode=observer#observe=')
    for i in range(8):event(env,'A' if i%2 else 'B')
    data=ro.page(db,grant['code'],limit=3)
    assert len(data['events'])==3 and data['more']
    ids=[e['event_id'] for e in data['events']]
    while data['more']:
        data=ro.page(db,grant['code'],data['cursor'],limit=3);ids.extend(e['event_id'] for e in data['events'])
    assert len(set(ids))==len(ids)==8
    assert len(json.dumps(data).encode())<=data['limits']['page_bytes']
    assert client.get('/visualizer?mode=observer').status_code==200
    assert 'Namespace View' in client.get('/visualizer?mode=observer').text


def test_namespace_revisions_remain_ordered_when_clocks_go_backwards(env):
    db,agent,*_=env
    grant=ro.issue(db,agent)
    ledger=service(db,'A');clock=[20.];ledger.clock=lambda:clock[0]
    first,_=event(env,'A');clock[0]=10.;second,_=event(env,'A')
    service(db,'B').clock=lambda:15.
    third,_=event(env,'B')
    cursor=None;rows=[]
    while True:
        data=ro.page(db,grant['code'],cursor,limit=1);rows.extend(data['events']);cursor=data['cursor']
        if not data['more']:break
    assert [e['revision'] for e in rows if e['namespace']=='A']==[1,2]
    assert {e['event_id'] for e in rows}=={first['id'],second['id'],third['id']}


def test_oversized_observation_is_explicitly_bounded_without_losing_identity(env):
    db,agent,*_=env;grant=ro.issue(db,agent)
    rid=service(db,'A').observe({'operation':'large-test','query':'x'*40000},actor=agent,request_id='large')
    data=ro.page(db,grant['code'])
    assert data['events'][0]['event_id']==rid
    assert data['events'][0]['details_omitted'] is True
    assert len(json.dumps(data).encode())<data['limits']['page_bytes']


def test_transport_status_is_authorized_readonly_and_never_renews(env):
    db,agent,_,_,_,client,path=env
    assert client.get('/v1/observer/status').status_code==403
    assert client.get('/v1/observer/stream').status_code==403
    grant=ro.issue(db,agent)
    client.post('/v1/observer/exchange',json={'code':grant['code']})
    before=file_hashes(path);cap=ro.verify(db,grant['code']).copy()
    for _ in range(3):
        response=client.get('/v1/observer/status');assert response.status_code==200
        data=response.json();assert data['observed_agent_id']==agent
        assert data['expires_at']==grant['expires_at'] and 0<data['remaining_seconds']<=3600
        assert grant['code'] not in response.text
    assert ro.verify(db,grant['code'])==cap and file_hashes(path)==before
    ro.revoke(db,grant['observer_id'],agent)
    assert client.get('/v1/observer/status').status_code==403
    assert client.get('/v1/observer/stream').status_code==403
    assert file_hashes(path)==before


def test_transport_route_cancellation_cleans_subscriber_and_headers(env,caplog):
    from embers.api.observer_routes import stream
    from embers.integration.observation_stream import _listeners,key
    from starlette.requests import Request
    db,agent,*rest=env;path=rest[-1];grant=ro.issue(db,agent)
    async def run():
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        request=Request({'type':'http','method':'GET','path':'/v1/observer/stream',
            'query_string':b'','headers':[(b'cookie',('ember_observer='+grant['code']).encode())]},receive)
        response=await stream(request,None,None)
        assert response.headers['content-type'].startswith('text/event-stream')
        assert response.headers['content-encoding']=='identity'
        assert response.headers['x-accel-buffering']=='no'
        assert 'no-transform' in response.headers['cache-control']
        assert 'content-length' not in response.headers
        before=file_hashes(path);iterator=response.body_iterator
        first=await anext(iterator)
        assert response.headers['x-ember-stream-id'] in first and 'expires_at' in first
        assert key(db,None) in _listeners
        await anext(iterator)  # Initial vector checkpoint, no mutation.
        pending=asyncio.create_task(anext(iterator));await asyncio.sleep(.02);pending.cancel()
        with pytest.raises(asyncio.CancelledError):await pending
        await iterator.aclose()
        assert key(db,None) not in _listeners
        assert file_hashes(path)==before
    import logging
    logger=logging.getLogger('embers.observer.transport')
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO,logger=logger.name):asyncio.run(run())
    finally:logger.removeHandler(caplog.handler)
    assert 'reason=asgi_cancelled' in caplog.text and grant['code'] not in caplog.text


def test_observer_transport_assets_and_no_healthy_body_timeout(env):
    *_,client,path=env
    html=client.get('/visualizer?mode=observer')
    assert '/v1/observer/transport.js' in html.text
    assert "script-src 'self'" in html.headers['content-security-policy']
    js=client.get('/v1/observer/transport.js')
    assert js.status_code==200 and 'new EventSource(' in js.text
    assert "active.abort(),10000" not in html.text


def test_build_identity_fingerprinted_asset_integrity_and_readonly(env,monkeypatch):
    import base64
    import hashlib
    import re
    from embers.api import observer_build as build
    *_,client,path=env
    before=file_hashes(path)
    def forbidden():raise AssertionError('Public build inspection must not open memory')
    monkeypatch.setattr(api,'_get_db',forbidden)
    info=client.get('/v1/observer/build')
    assert info.status_code==200
    data=info.json()
    assert data['transport_matches_baseline'] is False
    assert data['transport_client_version'].startswith('shared-transport-v3')
    assert data['server_git_commit'] is None  # Never pretend a source fingerprint is a commit.
    html=client.get('/visualizer?mode=observer')
    assert '__OBSERVER_' not in html.text
    assert 'Observer client: shared-transport-v3' in html.text
    url=re.search(r'<script src="([^"]+)" integrity="([^"]+)"',html.text)
    assert url and url[1]==data['transport_asset_url']
    script=client.get(url[1])
    digest=hashlib.sha256(script.content).hexdigest()
    assert digest==data['transport_sha256']==build.TRANSPORT_SHA256
    assert url[2]=='sha256-'+base64.b64encode(bytes.fromhex(digest)).decode()
    for response in (info,html,script):
        assert 'no-store' in response.headers['cache-control']
        assert 'no-transform' in response.headers['cache-control']
        assert response.headers['x-ember-observer-build']==data['server_build']
        assert response.headers['x-ember-transport-sha256']==digest
        assert 'set-cookie' not in response.headers
    assert client.get('/v1/observer/transport.js?v=stale').status_code==409
    assert client.get('/visualizer').headers['x-ember-transport-sha256']==digest
    assert file_hashes(path)==before


def test_build_render_uses_process_asset_snapshot(env,monkeypatch):
    from embers.api import observer_build as build
    from pathlib import Path
    *_,client,path=env
    def forbidden(*args,**kwargs):raise AssertionError('Asset reread after server import')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    monkeypatch.setattr(Path,'read_text',forbidden)
    assert client.get('/v1/observer/transport.js').content==build.TRANSPORT
    assert client.get('/visualizer?mode=observer').status_code==200
    assert client.get('/v1/observer/build').json()['server_build']==build.SERVER_BUILD


def test_ella_correction_arrives_on_open_stream_without_observer_mutation(env):
    from embers.cognitive.epistemic import EpistemicLedger
    from embers.core.evidence import Evidence
    db,agent,*_=env
    grant=ro.issue(db,agent)
    proto=MemoryProtocol(db,default_namespace='ella-observer')
    rid=proto.remember({'content':'observer epistemic claim'},written_by=agent,agent_id=agent)
    evidence=Evidence(source='fixture',reference='observer-evidence',agent_id=agent)
    eid=db.attach_evidence(rid,evidence)
    ledger=EpistemicLedger(db,'ella-observer')
    version=db._store.read(rid).content_hash

    async def disconnected():return False
    async def run():
        gen=ro.stream(db,grant['code'],None,{},disconnected,interval=3600)
        assert 'event: ready' in await anext(gen)
        initial=await anext(gen)
        cursor=json.loads(next(x[6:] for x in initial.splitlines() if x.startswith('data: ')))['cursor']

        pending=asyncio.create_task(anext(gen));await asyncio.sleep(.02)
        first=ledger.apply('report',dict(target_memory_id=rid,target_memory_version=version,
            evidence_id=eid,polarity='SUPPORTS',strength='WEAK',
            assessment_note='initial observable assessment'),actor=agent,
            request_id='ella-observer-report',expected_revision=0)
        frame=await asyncio.wait_for(pending,1)
        payload=json.loads(next(x[6:] for x in frame.splitlines() if x.startswith('data: ')))
        assert payload['events'][0]['epistemic']['score']==first['score']
        cursor=payload['cursor']

        state,events=ledger.load()
        aid=next(iter(state['assessments']))
        pending=asyncio.create_task(anext(gen));await asyncio.sleep(.02)
        revised=ledger.apply('revise',dict(target_memory_id=rid,target_memory_version=version,
            evidence_id=eid,assessment_id=aid,polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='corrected observable assessment'),actor=agent,
            request_id='ella-observer-revise',expected_revision=len(events))
        frame=await asyncio.wait_for(pending,1)
        payload=json.loads(next(x[6:] for x in frame.splitlines() if x.startswith('data: ')))
        event=payload['events'][0]
        assert event['event_type']=='epistemic_revise'
        assert event['epistemic']['score']==revised['score']
        assert event['epistemic']['score']>first['score']
        await gen.aclose()

    asyncio.run(run())
    # Reading/replaying observer state must not create another ELLA revision.
    before=len(ledger.load()[1])
    ro.page(db,grant['code'])
    assert len(ledger.load()[1])==before
