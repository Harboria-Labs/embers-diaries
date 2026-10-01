"""Persisted Rust-domain integration: no test uses an independent Python equation implementation."""
from dataclasses import asdict
import hashlib
import json
import pytest
from embers.db import EmberDB
from embers.integration import MemoryProtocol
from embers.integration import consolidated
from embers.integration.usefulness_service import service,enable,snapshot
from embers.core.domain import call,explicit_truth,MODEL_VERSION
from embers.cognitive.usefulness import UsefulnessPolicy

@pytest.fixture
def rig(tmp_path):
    db=EmberDB.connect(str(tmp_path/'store'));enable(db,{'admin'})
    proto=MemoryProtocol(db,default_namespace='research')
    ids=[proto.remember({'content':f'fictional research {i}','verify_status':'hypothesis'},primary_context='exact') for i in range(3)]
    return db,ids,tmp_path/'store'

def report(db,ids,typ='CONTRIBUTED',request='report',**kwargs):
    return service(db,'research').apply('report',{'target':{'kind':'memory','memory_ids':ids[:1]},'context':'exact','feedback_type':typ,**kwargs},actor='admin',request_id=request)

def run(db,ids,qid='q1',q=.8,dt=1,**kwargs):
    return consolidated.recall(db,'research','admin',query_id=qid,direct_scores={ids[0]:q},elapsed=dt,context='exact',**kwargs)

def test_rust_projection_restart_retry_and_correction(rig):
    db,ids,root=rig;first=report(db,ids);one=run(db,ids);d=one['dynamics'][0]
    assert d['U']==.6 and d['N_eff']==1
    assert d['Q_prime']==pytest.approx(.84)
    expected=call('activation',{'config':call('config_default',None),'activation':.02,'Q':.8,'direct':.8,'U':.6,'elapsed':1})
    assert all(d[k]==v for k,v in expected.items())
    ledger=service(db,'research');before=len(ledger._events)
    assert run(db,ids)==one and len(ledger._events)==before
    with pytest.raises(ValueError,match='reused'):run(db,ids,dt=2)
    reopened=EmberDB.connect(str(root));enable(reopened,{'admin'})
    assert run(reopened,ids)==one
    frozen=service(reopened,'research')._load()['activation_state']
    event=service(reopened,'research').apply('resolve',{'experience_id':first['experiences'][0]['id'],'feedback_type':'MISLEADING','status':'accepted','reason':'corrected'},actor='admin',request_id='correction',expected_revision=before)
    assert service(reopened,'research')._load()['activation_state']==frozen
    assert event['transitions'][0]['modulation']['after']['Q_prime']<event['transitions'][0]['modulation']['before']['Q_prime']
    two=run(reopened,ids,qid='q2');assert two['dynamics'][0]['activation_before']==d['activation']
    assert two['dynamics'][0]['U']==pytest.approx(1/3)
    assert db._reader.get(ids[0]).data['verify_status']=='hypothesis'


def test_config_atomic_validation_cas_restart(rig):
    db,ids,root=rig;cfg=consolidated.settings(db,'research','admin');bad={**cfg['config'],'alpha':.99}
    def configure(c,req,rev,policy=None):return consolidated.configure(db,'research','admin',dict(config=c,policy=policy or cfg['policy'],reason='experiment',request_id=req,expected_revision=rev))
    before=set(db._store.all_ids())
    with pytest.raises(ValueError,match='recovery'):configure(bad,'bad',0)
    assert set(db._store.all_ids())==before
    changed={**cfg['config'],'alpha':.3};ev=configure(changed,'config-1',0)
    assert configure(changed,'config-1',0)==ev
    assert consolidated.settings(db,'research','admin')['configuration_revision']==1
    reopened=EmberDB.connect(str(root));enable(reopened,{'admin'})
    assert consolidated.settings(reopened,'research','admin')['config']==changed
    with pytest.raises(ValueError,match='stale'):configure(changed,'stale',0)
    run(db,ids)
    with pytest.raises(ValueError,match='floor'):configure({**changed,'epsilon_a':.03},'floor',2)
    pol={**cfg['policy'],'u0':.25}
    configure(changed,'prior',2,pol)
    assert consolidated.settings(db,'research','admin')['policy']['u0']==.25


def test_no_exposure_or_pair_coupling_and_budget(rig):
    db,ids,_=rig;report(db,ids)
    l=service(db,'research');l.apply('report',{'target':{'kind':'pair','memory_ids':ids[:2],'relation':'explains'},'context':'exact','feedback_type':'PAIR_HELPED'},actor='admin',request_id='pair')
    for _ in range(10):db.record_access(ids[0])
    first=run(db,ids,dt=0)
    assert first['candidate_ids']==ids[:1] and first['dynamics'][0]['activation']==.02
    assert first['pair_expansion']=='NOT CONNECTED'
    for i in range(3):run(db,ids,qid=f'repeat-{i}')
    assert service(db,'research').read(actor='admin')['states'][0]['N_eff']==1
    prior=run(db,ids,qid='positive',dt=10)['dynamics'][0]['activation']
    zero=run(db,ids,qid='zero',q=0,dt=10)['dynamics'][0]
    assert zero['Q_prime']==0 and zero['activation']<prior
    cfg=consolidated.settings(db,'research','admin')
    policy={**cfg['config'],'memory_token_cap':1,'neighborhood_token_cap':1}
    consolidated.configure(db,'research','admin',dict(config=policy,policy=cfg['policy'],reason='tiny budget',request_id='tiny',expected_revision=cfg['journal_revision']))
    assert run(db,ids,qid='bounded')['selected_ids']==[]


def test_exact_context_and_no_python_math(rig):
    db,ids,_=rig;report(db,ids)
    result=consolidated.recall(db,'research','admin',query_id='other',direct_scores={ids[0]:.8},elapsed=1,context='Exact')
    assert result['dynamics'][0]['U']==.5 and result['dynamics'][0]['context']=='Exact'
    source=__import__('pathlib').Path('embers/cognitive/usefulness.py').read_text()
    assert "call('fur_apply'" in source and 'fraction =' not in source
    assert explicit_truth(db._reader.get(ids[0]))['status']=='hypothesis'
    raw=MemoryProtocol(db,default_namespace='research').remember('raw')
    assert explicit_truth(db._reader.get(raw))['status']=='hypothesis'


def test_observer_committed_dynamics_and_readonly(rig):
    from embers.integration.research_observer import issue,page
    db,ids,root=rig;grant=issue(db,'admin');result=run(db,ids)
    def hashes():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=hashes();out=page(db,grant['code']);assert hashes()==before
    e=out['events'][0];assert e['dynamics']==result['dynamics'];assert e['model_version']==MODEL_VERSION
    snap=snapshot(db,'research','admin');assert next(n for n in snap['nodes'] if n['id']==ids[0])['dynamics']==result['dynamics'][0]
    assert hashes()==before


def test_api_mcp_settings_auth_and_recall(rig,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP,TOOLS
    db,ids,_=rig;agent,token=AgentRegistry(db).register('research');enable(db,{agent.agent_id})
    monkeypatch.setattr(api,'_get_db',lambda:db)
    client=TestClient(api.app);headers={'X-Ember-Agent-Id':agent.agent_id,'X-Ember-Token':token}
    assert client.get('/v1/research/settings/research').status_code==401
    settings=client.get('/v1/research/settings/research',headers=headers);assert settings.status_code==200
    assert len(settings.json()['fields'])>=20
    body={'query_id':'api-q','direct_scores':{ids[0]:.8},'elapsed':1,'context':'exact'}
    r=client.post('/v1/research/recall/research',headers=headers,json=body);assert r.status_code==200,r.text
    assert client.post('/v1/research/recall/research',headers=headers,json=body).json()==r.json()
    assert client.get('/research/settings').status_code==200
    names={x['name'] for x in TOOLS};assert {'ember_research_recall','ember_research_settings','ember_research_configure'}<=names
    from embers.integration.research_observer import issue
    code=issue(db,agent.agent_id)['code'];client.post('/v1/observer/exchange',json={'code':code})
    assert client.post('/v1/research/settings/research',json={}).status_code==401


def test_historical_python_golden_reducer_parity():
    """Frozen pre-migration outputs, no competing Python runtime reducer."""
    from pathlib import Path
    fixture=json.loads(Path('tests/fixtures/fur_pre_rust_v1.json').read_text())
    for case in fixture['cases']:
        result=call('fur_apply',case['command'])
        for key,expected in case['expected'].items():
            actual=result[key]
            if key=='transitions':actual=[{k:v for k,v in x.items() if k!='modulation'} for x in actual]
            assert actual==expected,(case['command']['action'],key)
        event={k:v for k,v in result.items() if k!='report' or v is not None}
        state=call('fur_reduce',dict(state=case['command']['state'],event=event))
        assert call('fur_derive',dict(experiences=state['experiences'],policy=state['policy']))==case['projection']


def test_truth_explicit_status_and_native_final_budget(rig):
    db,ids,_=rig
    projection=call('truth',dict(data={'_status':'disputed','verify_status':'verified','confidence':1},annotations=[],open_conflict=False))
    assert projection['status']=='disputed' and projection['source']=='_status'
    assert call('truth',dict(data={'_status':'superseded'},annotations=[],open_conflict=False))['status']=='superseded'
    cfg=call('config_default',None)
    with pytest.raises(ValueError,match='wrapper'):call('admit',dict(config=cfg,final_tokens=cfg['token_budget']+1))
