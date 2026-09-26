"""SSE journal delivery is ordered, resumable, authorized and read-only."""
import asyncio
import json
import pytest
from test_usefulness import rig, report, file_hashes
from embers.integration.observation_stream import stream, _listeners
from embers.integration.usefulness_service import service
from embers.db import EmberDB


def decode(frame):
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))


async def connected(rig, after=0, authorize=None):
    db=rig[0]
    async def disconnected():return False
    return stream(db,'experiment','admin',after,authorize or (lambda:db.require_namespace_access('experiment','admin','read')),disconnected,interval=.01)


def test_order_catchup_idempotent_retry_and_readonly(rig):
    async def run():
        for i in range(24):report(rig,outcome=i)
        before=file_hashes(rig[-1])
        gen=await connected(rig)
        first=decode(await anext(gen));second=decode(await anext(gen))
        assert [e['revision'] for e in first['events']+second['events']]==list(range(1,25))
        assert await anext(gen)==': heartbeat\n\n'
        await gen.aclose()
        retry=await connected(rig,20)
        assert [e['revision'] for e in decode(await anext(retry))['events']]==[21,22,23,24]
        await retry.aclose()
        assert file_hashes(rig[-1])==before
        assert not _listeners
    asyncio.run(run())


def test_commit_wakeup_drop_and_restart(rig):
    async def run():
        gen=await connected(rig)
        await anext(gen)
        task=asyncio.create_task(anext(gen))
        await asyncio.sleep(0)
        event=report(rig)
        result=decode(await asyncio.wait_for(task,1))
        assert result['events'][0]['id']==event['id']
        assert rig[0]._store.read(event['id']) is not None
        await gen.aclose()
        report(rig,outcome='missed')
        reopened=EmberDB.connect(str(rig[-1]))
        async def disconnected():return False
        restored=stream(reopened,'experiment','admin',1,lambda:None,disconnected,interval=.01)
        assert [e['revision'] for e in decode(await anext(restored))['events']]==[2]
        await restored.aclose()
        assert not _listeners
    asyncio.run(run())


def test_revocation_namespace_isolation_and_failed_commit(rig,monkeypatch):
    async def run():
        allowed=[True]
        def auth():
            if not allowed[0]:raise PermissionError('revoked')
        gen=await connected(rig,authorize=auth)
        await anext(gen)
        other=service(rig[0],'private')
        other.observe({'secret':'not allowed'},actor='admin',request_id='secret')
        assert await anext(gen)==': heartbeat\n\n'
        def fail(*args,**kwargs):raise OSError('disk full')
        monkeypatch.setattr(rig[0]._writer,'write',fail)
        with pytest.raises(OSError):report(rig)
        assert rig[1].read(actor='admin')['revision']==0
        allowed[0]=False
        with pytest.raises(PermissionError):await anext(gen)
        assert not _listeners
    asyncio.run(run())


def test_route_auth_cursor_and_session_binding(rig,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    owner,token=AgentRegistry(rig[0]).register('owner')
    stranger,_=AgentRegistry(rig[0]).register('stranger')
    rig[0].create_namespace('experiment',owner=owner.agent_id)
    monkeypatch.setattr(api,'_get_db',lambda:rig[0])
    client=TestClient(api.app)
    path='/v1/visualizer-stream/experiment'
    assert client.get(path).status_code==401
    auth={'X-Ember-Agent-Id':owner.agent_id,'X-Ember-Token':token}
    assert client.get(path,headers={**auth,'Last-Event-ID':'bad'}).status_code==400
    assert client.get(path,headers={**auth,'Last-Event-ID':'1'}).status_code==400
    sid=rig[0].start_session(agent_id=stranger.agent_id)
    assert client.get(path,headers={**auth,'X-Ember-Session-Id':sid}).status_code==403
    rig[0].create_namespace('private',owner=stranger.agent_id)
    assert client.get('/v1/visualizer-stream/private',headers=auth).status_code==403


def test_session_credentials_are_not_streamed(rig):
    async def run():
        sid=rig[0].start_session(agent_id='admin')
        report(rig,session_id=sid)
        gen=await connected(rig)
        encoded=await anext(gen)
        assert sid not in encoded
        assert 'session-sha256:' in encoded
        await gen.aclose()
    asyncio.run(run())


def test_every_observable_commit_and_no_duplicate_retry_delivery(rig):
    from test_usefulness import decision
    async def run():
        gen=await connected(rig);await anext(gen)
        event=report(rig,outcome='a');first=decode(await anext(gen))
        assert len(first['events'])==1
        # A transport retry returns persisted event without appending/waking.
        payload={'target':{'kind':'memory','memory_ids':rig[2][:1]},'context':'research','feedback_type':'CONTRIBUTED',
                 'identity':{'value':'a','source':'test-outcomes','provenance':'trusted test harness'},'identity_verified':True}
        rig[1].apply('report',payload,actor='admin',request_id='1')
        assert await anext(gen)==': heartbeat\n\n'
        exp=event['experiences'][0]['id']
        actions=[lambda:report(rig,outcome='a'),
                 lambda:decision(rig,'resolve',experience_id=exp,feedback_type='UNUSED',status='accepted'),
                 lambda:report(rig,'PAIR_HELPED',kind='pair'),
                 lambda:report(rig,'GROUP_SUCCESS',kind='group'),
                 lambda:rig[1].observe({'operation':'recall','budget':{},'latency_ms':0},actor='admin',request_id='q')]
        for action in actions:
            action()
            frame=decode(await anext(gen))
            assert len(frame['events'])==1
        await gen.aclose()
    asyncio.run(run())


def test_merge_split_policy_and_cancelled_waiter(rig):
    from test_usefulness import decision
    from embers.cognitive.usefulness import UsefulnessPolicy
    from dataclasses import asdict
    async def run():
        a=report(rig,outcome='a')['experiences'][0]['id']
        b=report(rig,outcome='b')['experiences'][0]['id']
        gen=await connected(rig,2);await anext(gen)
        event=decision(rig,'merge',experience_ids=[a,b])
        assert decode(await anext(gen))['events'][0]['action']=='merge'
        merged=next(e for e in event['experiences'] if e['active'])
        decision(rig,'split',experience_id=merged['id'],partitions=[[r] for r in merged['reports']])
        assert decode(await anext(gen))['events'][0]['action']=='split'
        decision(rig,'configure',policy=asdict(UsefulnessPolicy(kappa_u=8)))
        assert decode(await anext(gen))['events'][0]['action']=='configure'
        await gen.aclose()
        idle=await connected(rig,5);await anext(idle)
        task=asyncio.create_task(anext(idle));await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not _listeners
    asyncio.run(run())
