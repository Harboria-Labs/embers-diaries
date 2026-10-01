"""Write receipts are delivery metadata, never mathematical evidence."""
import json
import pytest
from embers.db import EmberDB
from embers.identity.registry import AgentRegistry
from embers.integration import MemoryProtocol
from embers.integration.usefulness_service import enable, service, snapshot
from embers.integration import write_observation as wo, research_observer as ro
from test_usefulness import file_hashes
from embers.integration.observation_journal import journal

@pytest.fixture
def env(tmp_path):
    db=EmberDB.connect(str(tmp_path/'store'))
    agent,token=AgentRegistry(db).register('writer')
    enable(db,[])
    ctx=wo.context.set({'actor':agent.agent_id,'session':'private-session-bearer','request':'write-1'})
    yield db,agent.agent_id,tmp_path/'store'
    wo.context.reset(ctx)

def write(db,ns='A',**extra):
    return MemoryProtocol(db,default_namespace=ns).remember({'content':'A durable fictional fact','subject':'Research',**extra},primary_context='experiment')

def test_immediate_write_in_both_views_without_recall_or_feedback(env):
    db,actor,path=env;grant=ro.issue(db,actor);ledger=service(db,'A');ledger._load();before=ledger._state.copy()
    rid=write(db)
    ledger._load();assert ledger._state==before
    events=journal(db,'A').events(actor=actor)
    assert ledger.events(actor=actor)==[]
    assert len(events)==1 and events[0]['observation']['operation']=='memory_written'
    assert db._store.read(rid).verify_integrity()
    assert events[0]['observation']['durable_content_hash']==db._store.read(rid).content_hash
    page=ro.page(db,grant['code']);assert page['events'][0]['memory_ids']==[rid]
    assert page['events'][0]['pipeline']['retrieval']=='NOT OBSERVED'
    assert page['events'][0]['pipeline']['write']=='OBSERVED'
    assert 'private-session-bearer' not in json.dumps(page)
    data=snapshot(db,'A',actor,observations=True);assert rid in [n['id'] for n in data['nodes']]
    assert data['nodes'][0]['provenance']['originating_agent']==actor
    baseline=file_hashes(path)
    for _ in range(3):snapshot(db,'A',actor,observations=True);ro.page(db,grant['code'])
    assert file_hashes(path)==baseline
    assert list(wo.directory(db).glob('*.json'))==[]

def test_failed_write_has_no_success_receipt(env,monkeypatch):
    db,actor,_=env
    def fail(record):raise OSError('simulated durable store failure')
    with monkeypatch.context() as m:
        m.setattr(db._store,'write',fail)
        with pytest.raises(OSError):write(db)
    assert service(db,'A').events(actor=actor)==[]
    wo.recover(db)
    assert list(wo.directory(db).glob('*.json'))==[]

def test_restart_recovery_and_duplicate_postcommit_delivery(env,monkeypatch):
    db,actor,path=env
    real=wo.deliver
    with monkeypatch.context() as m:
        m.setattr(wo,'deliver',lambda *a:None)
        rid=write(db)
    receipt=wo.receipt_path(db,rid);pending=receipt.read_text()
    assert service(db,'A').events(actor=actor)==[]
    reopened=EmberDB.connect(str(path));enable(reopened,[])
    assert len(journal(reopened,'A').events(actor=actor))==1
    # Crash after journal commit but before receipt unlink.
    receipt.write_text(pending);wo.recover(reopened)
    assert len(journal(reopened,'A').events(actor=actor))==1
    assert not receipt.exists()

def test_provenance_is_bounded_and_namespace_safe(env):
    db,actor,_=env;grant=ro.issue(db,actor)
    a=write(db);secret=write(db,'B')
    b=write(db,consolidated=True,source_ids=[a,secret],source_data=[{'token':'never-project-this'}])
    page=ro.page(db,grant['code'])
    event=next(e for e in page['events'] if e['event_type']=='memory_consolidated')
    assert event['memory_ids']==[b]
    assert event['relationships']==[{'from':b,'to':a,'type':'derived_from','kind':'recorded_provenance'}]
    assert secret not in json.dumps(event) and 'never-project-this' not in json.dumps(event)
    assert event['changes']==[]
    assert {m['id'] for m in event['memories']}=={a,b}

def test_other_agents_filtered_at_server(env):
    db,actor,_=env;grant=ro.issue(db,actor)
    other,_=AgentRegistry(db).register('other')
    ctx=wo.context.set({'actor':other.agent_id})
    try:rid=write(db)
    finally:wo.context.reset(ctx)
    assert ro.page(db,grant['code'])['events']==[]
    assert len(journal(db,'A').events(actor=actor))==1

def test_observations_never_enter_memory_indexes_or_fur_revision(env):
    db,actor,_=env
    before=set(db._store.all_ids());rid=write(db)
    assert set(db._store.all_ids())-before=={rid}
    assert {r.id for r in db.get_namespace('A')}=={rid}
    assert service(db,'A').read(actor=actor)['revision']==0
    assert snapshot(db,'A',actor)['revision']==0
    visible=snapshot(db,'A',actor,observations=True)
    assert visible['revision']==1 and visible['source_journal_revision']==0
    assert not MemoryProtocol(db,default_namespace='A').consolidation.find_consolidation_candidates(db.get_namespace('A'))
    source=service(db,'A').apply('report',{'target':{'kind':'memory','memory_ids':[rid]},'context':'experiment','feedback_type':'CONTRIBUTED'},actor=actor,request_id='feedback')
    assert source['revision']==1
    events=journal(db,'A').events(actor=actor)
    assert [e['revision'] for e in events]==[1,2]
    assert events[-1]['source_journal_revision']==1
    assert service(db,'A').read(actor=actor)['states'][0]['value']==.6


def test_duplicate_source_retry_does_not_duplicate_observation(env):
    db,actor,_=env;rid=write(db)
    with pytest.raises(Exception):db._writer.write(db._store.read(rid))
    assert len(journal(db,'A').events(actor=actor))==1

def test_existing_capability_lifetime_limits_are_preserved(env):
    from embers.integration.visualizer_access import issue as namespace_issue
    db,actor,_=env
    with pytest.raises(ValueError):ro.issue(db,actor,ttl_seconds=7*86400)
    with pytest.raises(ValueError):namespace_issue(db,'A',actor,ttl_seconds=7*86400)
    grant=ro.issue(db,actor,ttl_seconds=86400)
    assert grant['expires_at']>0


def test_observation_journal_reads_do_not_write_even_its_own_files(env):
    from embers.integration.observation_journal import path
    db,actor,_=env;grant=ro.issue(db,actor);write(db)
    before=path(db).read_bytes()
    for _ in range(3):snapshot(db,'A',actor,observations=True);ro.page(db,grant['code'])
    assert path(db).read_bytes()==before

def test_fur_mirror_failure_recovers_at_boot_not_on_reads(env,monkeypatch):
    from embers.integration import observation_journal as oj
    db,actor,path=env;rid=write(db)
    with monkeypatch.context() as m:
        m.setattr(oj,'sync',lambda *a:(_ for _ in ()).throw(OSError('mirror unavailable')))
        event=service(db,'A').apply('report',{'target':{'kind':'memory','memory_ids':[rid]},'context':'experiment','feedback_type':'CONTRIBUTED'},actor=actor,request_id='persisted-feedback')
    assert event['revision']==1
    assert len(journal(db,'A').events(actor=actor))==1
    snapshot(db,'A',actor,observations=True)
    assert len(journal(db,'A').events(actor=actor))==1 # A viewer cannot repair/mutate.
    reopened=EmberDB.connect(str(path));enable(reopened,[])
    rows=journal(reopened,'A').events(actor=actor)
    assert len(rows)==2 and rows[-1]['source_journal_revision']==1

def test_namespace_reconnect_validates_observation_cursor_not_fur(env,monkeypatch):
    import asyncio
    from starlette.requests import Request
    from fastapi import HTTPException
    from embers import api
    from embers.api.usefulness_routes import live
    from embers.integration.visualizer_access import issue
    db,actor,_=env;rid=write(db);grant=issue(db,'A',actor)
    monkeypatch.setattr(api,'_get_db',lambda:db)
    request=Request({'type':'http','method':'GET','path':'/v1/visualizer-stream/A','headers':[(b'cookie',('ember_view='+grant['code']).encode())]})
    assert service(db,'A').read(actor=actor)['revision']==0
    async def run():
        response=await live('A',request,after=1,last_event_id=None,x_ember_agent_id=None,x_ember_token=None)
        assert response.status_code==200
        assert 'event: ready' in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        with pytest.raises(HTTPException) as error:
            await live('A',request,after=2,last_event_id=None,x_ember_agent_id=None,x_ember_token=None)
        assert error.value.status_code==400
    asyncio.run(run())
