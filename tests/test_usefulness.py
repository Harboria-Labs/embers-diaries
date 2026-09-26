"""Feedback contract F01–F24 against the real native append-only store."""
import hashlib
import json
from dataclasses import asdict
import pytest
from embers.db import EmberDB
from embers.integration import MemoryProtocol
from embers.cognitive.usefulness import UsefulnessLedger, UsefulnessPolicy
from embers.integration.usefulness_service import snapshot, enable


@pytest.fixture
def rig(tmp_path):
    db = EmberDB.connect(str(tmp_path / 'store'))
    proto = MemoryProtocol(db, default_namespace='experiment')
    ids = [proto.remember({'content':f'latent memory {i}', 'subject':'LADC', 'verify_status':'hypothesis'}, primary_context='research') for i in range(3)]
    clock = [100.]
    ledger = UsefulnessLedger(db, 'experiment', admins={'admin'}, clock=lambda:clock[0])
    db._usefulness_services = {'experiment':ledger}
    return db, ledger, ids, clock, tmp_path / 'store'


def report(rig, typ='CONTRIBUTED', *, outcome=None, context='research', kind='memory', ids=None, actor='admin', **extra):
    db, ledger, memories, *_ = rig
    target = {'kind':kind, 'memory_ids':ids or (memories[:2] if kind != 'memory' else memories[:1])}
    if kind == 'pair': target['relation'] = 'explains'
    payload = dict(target=target, context=context, feedback_type=typ, **extra)
    if outcome is not None:
        payload.update(identity={'value':str(outcome),'source':'test-outcomes','provenance':'trusted test harness'},identity_verified=True)
    return ledger.apply('report',payload,actor=actor,request_id=str(ledger.read(actor=actor)['revision']+1))


def state(rig, context='research', kind='memory'):
    return next(s for s in rig[1].read(actor='admin')['states'] if s['context']==context and s['target']['kind']==kind)


def decision(rig, action, **payload):
    ledger = rig[1]
    revision = ledger.read(actor='admin')['revision']
    return ledger.apply(action, dict(reason='test evidence decision',**payload), actor='admin',request_id=f'decision-{revision}',expected_revision=revision)


@pytest.mark.parametrize('count',[1,10,100],ids=['F01','F02','F03'])
def test_distinct_outcomes(rig,count):
    for n in range(count): report(rig,outcome=n)
    assert state(rig)['value'] == pytest.approx((2+count)/(4+count))
    assert state(rig)['N_eff'] == count


def test_f04_mixed(rig):
    for n in range(10): report(rig,'CONTRIBUTED' if n%2 else 'IRRELEVANT',outcome=n)
    assert state(rig)['value']==.5 and state(rig)['N_eff']==10


def test_f05_f24_duplicates_visible(rig):
    for _ in range(5): report(rig,outcome='ticket-481')
    assert state(rig)['value']==.6 and state(rig)['N_eff']==1
    view=snapshot(rig[0],'experiment','admin')
    assert len(view['reports'])==5 and len(view['experiences'])==1
    assert view['diagnostics']['duplicate_reports_collapsed']==4
    delta=view['events'][-1]['transitions'][0]
    assert delta['before']==delta['after']


def test_f06_f13_structural_rewritten_queries(rig):
    for n in range(5): report(rig,query_request_id=f'rewritten-{n}')
    assert state(rig)['N_eff']==1
    assert len(rig[1].read(actor='admin')['reports'])==5


def test_f07_distributed_unidentified_limitation(rig):
    report(rig,actor='a'); report(rig,actor='b')
    rig[3][0]+=61
    report(rig,actor='a')
    assert state(rig)['N_eff']==3
    assert all(e['identity_mode']=='structural' for e in rig[1].read(actor='admin')['experiences'])


def test_f08_severity(rig):
    report(rig,'IRRELEVANT',context='a');report(rig,'MISLEADING',context='b')
    assert state(rig,'a')['value']==.4
    assert state(rig,'b')['value']==pytest.approx(1/3)
    assert state(rig,'b')['N_eff']==2


def test_f09_f10_corrections_replace_effective_contribution(rig):
    old=report(rig,outcome='old')['experiences'][0]['id']
    for n in range(10): report(rig,outcome=n)
    decision(rig,'resolve',experience_id=old,feedback_type='UNUSED',status='accepted')
    assert state(rig)['N_eff']==10 and state(rig)['value']==pytest.approx(12/14)
    report(rig,'UNUSED',experience_id=old)
    assert state(rig)['N_eff']==10
    decision(rig,'resolve',experience_id=old,feedback_type='MISLEADING',status='accepted')
    assert state(rig)['value']==pytest.approx(12/16)
    assert len(rig[1].read(actor='admin')['reports'])==12


def test_f11_unused_1000(rig):
    for _ in range(1000): report(rig,'UNUSED')
    assert state(rig)['N_eff']==0 and state(rig)['value']==.5
    assert len(rig[1].read(actor='admin')['reports'])==1000
    out=snapshot(rig[0],'experiment','admin',after=990,limit=10)
    assert out['next_after']>990 and len(json.dumps(out,separators=(',',':'),ensure_ascii=False).encode())<=262144


def test_f12_exact_context_locality(rig):
    report(rig,context='research');report(rig,'MISLEADING',context='Research')
    report(rig,'UNUSED',context=None)
    assert state(rig)['value']==.6
    assert state(rig,'Research')['value']==pytest.approx(1/3)
    assert state(rig,None)['N_eff']==0


def test_f14_directional_pair_explicit_only(rig):
    report(rig,'UNUSED'); report(rig,'UNUSED',ids=[rig[2][1]])
    assert not any(s['metric']=='W' for s in rig[1].read(actor='admin')['states'])
    report(rig,'PAIR_HELPED',kind='pair')
    report(rig,'PAIR_IRRELEVANT',kind='pair',ids=list(reversed(rig[2][:2])))
    pairs=[s for s in rig[1].read(actor='admin')['states'] if s['metric']=='W']
    assert [s['value'] for s in pairs]==[.6,.4]
    assert state(rig)['N_eff']==0


def test_f15_group_no_invented_credit(rig):
    report(rig,'GROUP_SUCCESS',kind='group',ids=rig[2])
    assert rig[1].read(actor='admin')['states']==[]
    view=snapshot(rig[0],'experiment','admin')
    assert view['diagnostics']['group_outcomes']==1
    assert all(not n['used'] for n in view['nodes'])


def test_f16_structural_truth_firewall(rig,monkeypatch):
    db,ledger,ids,*_=rig
    before={i:db._store.read(i).to_dict() for i in ids}
    write=db._writer.write
    calls=[]
    def guarded(record,*args,**kwargs):
        assert record.id not in ids and record.id.startswith(ledger.prefix)
        assert record.record_type.value=='raw' and not record.retrieval_candidate
        calls.append(record.id)
        return write(record,*args,**kwargs)
    monkeypatch.setattr(db._writer,'write',guarded)
    for typ in ['CONTRIBUTED','IRRELEVANT','MISLEADING','UNUSED']: report(rig,typ,outcome=typ)
    assert len(calls)==4
    assert before=={i:db._store.read(i).to_dict() for i in ids}
    assert all(e['truth_before']==e['truth_after'] for e in ledger.events(actor='admin'))


def test_f17_merge_retains_identity_aliases(rig):
    a=report(rig,outcome='a')['experiences'][0]['id']
    b=report(rig,outcome='b')['experiences'][0]['id']
    assert state(rig)['N_eff']==2
    decision(rig,'merge',experience_ids=[a,b])
    report(rig,outcome='a')
    assert state(rig)['N_eff']==1
    assert len([e for e in rig[1].read(actor='admin')['experiences'] if e['active']])==1


def test_f18_split_conserves_reports(rig):
    a=report(rig,outcome='same');b=report(rig,outcome='same')
    exp=a['experiences'][0]['id']
    decision(rig,'split',experience_id=exp,partitions=[[a['report']['id']],[b['report']['id']]])
    assert state(rig)['N_eff']==2
    with pytest.raises(ValueError,match='explicit experience_id'):report(rig,outcome='same')


def test_f19_f20_conflict_unresolved_no_majority(rig):
    report(rig,outcome='same');report(rig,outcome='same');report(rig,'IRRELEVANT',outcome='same')
    assert state(rig)['value']==.5 and state(rig)['N_eff']==0
    exp=rig[1].read(actor='admin')['experiences'][0]
    assert exp['resolution_status']=='unresolved' and len(exp['reports'])==3
    decision(rig,'resolve',experience_id=exp['id'],feedback_type='IRRELEVANT',status='accepted')
    assert state(rig)['value']==.4


def file_hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_f21_f22_f23_visualizer_authorization_readonly_replay(rig,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    db,ledger,ids,_,root=rig
    owner,token=AgentRegistry(db).register('owner')
    outsider,other_token=AgentRegistry(db).register('outsider')
    db.create_namespace('experiment',owner=owner.agent_id)
    ledger.admins=frozenset({owner.agent_id})
    report(rig,actor=owner.agent_id,outcome='x')
    monkeypatch.setattr(api,'_get_db',lambda:db)
    client=TestClient(api.app)
    headers={'X-Ember-Agent-Id':owner.agent_id,'X-Ember-Token':token}
    before=file_hashes(root)
    assert client.get('/v1/visualizer/experiment').status_code==401
    denied=client.get('/v1/visualizer/experiment',headers={'X-Ember-Agent-Id':outsider.agent_id,'X-Ember-Token':other_token})
    assert denied.status_code==403 and ids[0] not in denied.text
    assert client.post('/v1/visualizer/experiment',headers=headers,json={}).status_code==405
    one=client.get('/v1/visualizer/experiment',headers=headers)
    two=client.get('/v1/visualizer/experiment',headers=headers)
    assert one.status_code==200 and one.json()==two.json()
    assert file_hashes(root)==before
    html=client.get('/visualizer').text
    assert "method:'GET'" in html and "method:'POST'" not in html


def test_restart_idempotence_and_policy(rig):
    event=report(rig,outcome='restart')
    root=rig[-1]
    db=EmberDB.connect(str(root));ledger=UsefulnessLedger(db,'experiment',admins={'admin'})
    assert ledger.read(actor='admin')==rig[1].read(actor='admin')
    payload={'target':{'kind':'memory','memory_ids':rig[2][:1]},'context':'research','feedback_type':'CONTRIBUTED',
             'identity':{'value':'restart','source':'test-outcomes','provenance':'trusted test harness'},'identity_verified':True}
    assert ledger.apply('report',payload,actor='admin',request_id='1')==event
    policy=asdict(UsefulnessPolicy(kappa_u=8,u0=.25))
    decision(rig,'configure',policy=policy)
    assert state(rig)['value']==pytest.approx(3/9)
    assert ledger.read(actor='admin')['policy']==policy


def test_untrusted_identity_not_authority_and_admin_cas(rig):
    with pytest.raises(PermissionError):report(rig,outcome='spoof',actor='ordinary')
    report(rig,identity={'value':'claim','source':'agent','provenance':'unverified'})
    assert rig[1].read(actor='admin')['experiences'][0]['identity_mode']=='structural'
    with pytest.raises(ValueError,match='stale'):
        rig[1].apply('configure',{'policy':asdict(UsefulnessPolicy()),'reason':'x'},actor='admin',request_id='bad',expected_revision=0)
    with pytest.raises(PermissionError):
        rig[1].apply('configure',{},actor='ordinary',request_id='bad')


def test_rest_mutation_and_request_observation(rig,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    db=rig[0];agent,token=AgentRegistry(db).register('http')
    from embers.api import v1
    monkeypatch.setattr(v1,'_protocol',MemoryProtocol(db))
    enable(db,{agent.agent_id});rig[1].admins=frozenset({agent.agent_id})
    monkeypatch.setattr(api,'_get_db',lambda:db)
    client=TestClient(api.app);auth={'X-Ember-Agent-Id':agent.agent_id,'X-Ember-Token':token}
    body={'action':'report','request_id':'http1','payload':{'target':{'kind':'memory','memory_ids':rig[2][:1]},'context':'research','feedback_type':'CONTRIBUTED'}}
    assert client.post('/v1/usefulness/experiment',json=body,headers=auth).status_code==200
    assert client.post('/v1/memory/orient',json={'namespace':'experiment','clues':'latent'},headers=auth).status_code==200
    out=client.get('/v1/visualizer/experiment',headers=auth).json()
    assert out['events'][-1]['action']=='observation'
    assert out['events'][-1]['observation']['returned_ids']


def test_session_clustering_and_filtered_replay(rig):
    sid=rig[0].start_session(agent_id='admin',task='check evidence')
    one=report(rig,session_id=sid,query_request_id='q1')
    rig[3][0]+=61  # outside no-session window, inside actual-session window
    report(rig,session_id=sid,query_request_id='q2')
    assert state(rig)['N_eff']==1
    assert len(rig[1].events(actor='admin',session_id=sid))==2
    assert rig[1].events(actor='admin',request_id='q1')[0]['id']==one['id']
    with pytest.raises(PermissionError):report(rig,actor='other',session_id=sid)


def test_visualizer_excludes_cross_namespace_edges(rig):
    report(rig)
    foreign=MemoryProtocol(rig[0],default_namespace='private').remember('secret outside')
    rig[0].link(rig[2][0],foreign)
    out=snapshot(rig[0],'experiment','admin')
    assert foreign not in json.dumps(out) and 'secret outside' not in json.dumps(out)


def test_mcp_usefulness_and_observation(tmp_path):
    from embers.mcp.server import EmberMCP, TOOLS
    from embers.mcp.session_auth import install
    from test_feedback_lifecycle import _body
    install()
    mcp=EmberMCP(store_path=str(tmp_path/'mcp'))
    reg=_body(mcp.call_tool('ember_register',{'name':'feedback'}))
    auth={k:reg[k] for k in ('agent_id','token')}
    enable(mcp.db,{reg['agent_id']})
    mid=_body(mcp.call_tool('ember_write',dict(content='latent example',**auth)))['id']
    body=dict(namespace='memories',action='report',request_id='mcp-report',payload={
        'target':{'kind':'memory','memory_ids':[mid]},'context':'LADC','feedback_type':'CONTRIBUTED'},**auth)
    event=_body(mcp.call_tool('ember_usefulness_update',body))
    assert event['transitions'][0]['after']['value']==.6
    assert _body(mcp.call_tool('ember_usefulness_update',body))==event
    _body(mcp.call_tool('ember_recall',dict(query='latent',primary_context='LADC',**auth)))
    out=_body(mcp.call_tool('ember_usefulness_state',dict(namespace='memories',**auth)))
    assert out['events'][-1]['observation']['context']=='LADC'
    assert out['events'][-1]['observation']['returned_ids']==[mid]
    assert {'ember_usefulness_update','ember_usefulness_state'}<={t['name'] for t in TOOLS}


def test_http_mcp_catalog_and_calls(rig,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.api import mcp_http
    from embers.identity.registry import AgentRegistry
    db=rig[0];agent,token=AgentRegistry(db).register('http-mcp')
    monkeypatch.setattr(api,'_get_db',lambda:db)
    monkeypatch.setattr(mcp_http,'_mcp',None)
    client=TestClient(api.app)
    def rpc(method,params=None):
        response=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
        assert response.status_code==200
        return response.json()['result']
    assert rpc('initialize')['capabilities']['tools']=={}
    catalog={t['name']:t for t in rpc('tools/list')['tools']}
    assert {'ember_usefulness_update','ember_usefulness_state'} <= set(catalog)
    assert 'feedback_type' in catalog['ember_usefulness_update']['inputSchema']['properties']['payload']['properties']
    args={'namespace':'experiment','agent_id':agent.agent_id,'token':token}
    event=rpc('tools/call',{'name':'ember_usefulness_update','arguments':dict(args,action='report',request_id='http-mcp',payload={
        'target':{'kind':'memory','memory_ids':rig[2][:1]},'context':'research','feedback_type':'CONTRIBUTED'})})
    assert not event['isError']
    state_reply=rpc('tools/call',{'name':'ember_usefulness_state','arguments':args})
    assert not state_reply['isError']
    assert json.loads(state_reply['content'][0]['text'])['states'][0]['value']==.6
    denied=rpc('tools/call',{'name':'ember_usefulness_state','arguments':{'namespace':'experiment'}})
    assert denied['isError']
