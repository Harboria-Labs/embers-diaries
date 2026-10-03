"""Frozen Pairing V1 exercised against native selection and existing FUR journal."""
from copy import deepcopy
import hashlib
import json
import pytest
from embers.db import EmberDB
from embers.integration import MemoryProtocol, consolidated
from embers.integration.pairing import select, link
from embers.integration.usefulness_service import enable, service
from embers.core.domain import call
from embers.cognitive.usefulness import canonical, derive

@pytest.fixture
def rig(tmp_path):
    db=EmberDB.connect(str(tmp_path/'store')); enable(db,{'admin'})
    proto=MemoryProtocol(db,default_namespace='pairs')
    ids=[proto.remember({'content':f'memory {i}','verify_status':'hypothesis'},primary_context='home') for i in range(5)]
    return db,ids,proto

def feedback(rig,b=1,typ='PAIR_HELPED',context='ctx',relation='explains',a=0,outcome=None):
    db,ids,_=rig;l=service(db,'pairs');l._load()
    payload=dict(target=dict(kind='pair',memory_ids=[ids[a],ids[b]],relation=relation),context=context,feedback_type=typ)
    if outcome is not None:payload.update(identity=dict(value=outcome,source='tests',provenance='fixture'),identity_verified=True)
    return l.apply('report',payload,actor='admin',request_id=f'r{len(l._events)}')

def edge(rig,b=1,a=0,context='ctx',relation='explains'):
    return link(rig[0],'admin',source=rig[1][a],target=rig[1][b],relation=relation,primary_context=context)

def chosen(rig,direct=None,context='ctx'):
    return select(rig[0],'pairs',direct if direct is not None else rig[1][:1],context)[1]

def recall(rig,request='q',scores=None):
    return consolidated.recall(rig[0],'pairs','admin',query_id=request,direct_scores=scores or {rig[1][0]:.8},elapsed=1,context='ctx')

def resolve(rig,e,typ,status='accepted'):
    l=service(rig[0],'pairs');l._load()
    return l.apply('resolve',dict(experience_id=e,feedback_type=typ,status=status,reason='test correction'),actor='admin',request_id=f'd{len(l._events)}',expected_revision=len(l._events))


def test_max_one_highest_no_recursion_and_provenance(rig):
    db,ids,_=rig
    edge(rig);edge(rig,b=2);edge(rig,a=2,b=3)
    feedback(rig);feedback(rig,b=2,outcome='one');feedback(rig,b=2,outcome='two');feedback(rig,a=2,b=3)
    out=recall(rig)
    assert out['direct_ids']==ids[:1] and out['primary_memory_id']==ids[0]
    assert out['selected_ids']==[ids[0],ids[2]]
    route=out['pair_expansion'];assert route['source']==ids[0] and route['target']==ids[2]
    assert route['relation']=='explains' and route['context']=='ctx' and route['W']==pytest.approx(2/3)
    assert route['query_request_id']=='q'
    assert json.loads(out['context'])[1]['retrieval']==route
    assert ids[2] not in service(db,'pairs')._load()['activation_state']['"ctx"']['memories']
    from embers.integration.research_observer import _project
    projected=_project(db,service(db,'pairs')._events[-1])
    assert projected['pair_expansion']==route and projected['direct_ids']==ids[:1]
    assert projected['pipeline']['pair_expansion']=='OBSERVED'
    assert ids[2] not in projected['actual_H']

@pytest.mark.parametrize('case',['missing_edge','missing_context','other_context','other_relation','reverse','neutral','negative','unresolved','direct','all_direct','empty'])
def test_ineligible(rig,case):
    db,ids,_=rig
    if case=='missing_context':db.link(ids[0],ids[1],'explains')
    elif case!='missing_edge':edge(rig,context='other' if case=='other_context' else 'ctx',relation='requires' if case=='other_relation' else 'explains',a=1 if case=='reverse' else 0,b=0 if case=='reverse' else 1)
    e=feedback(rig,typ='UNUSED' if case=='neutral' else 'PAIR_IRRELEVANT' if case=='negative' else 'PAIR_HELPED')
    if case=='unresolved':resolve(rig,e['experiences'][0]['id'],None,'unresolved')
    direct=ids[:2] if case in ('direct','all_direct') else [] if case=='empty' else ids[:1]
    assert chosen(rig,direct) is None


def test_explicit_unset_distinct_from_missing_and_endpoint_context(rig):
    edge(rig,context=None);feedback(rig,context=None)
    assert chosen(rig,context=None)['target']==rig[1][1]
    assert chosen(rig,context='home') is None


def test_skip_direct_choose_next_and_tie_stable(rig):
    edge(rig);edge(rig,b=2);feedback(rig);feedback(rig,b=2)
    first=chosen(rig)
    assert all(chosen(rig)==first for _ in range(5))
    assert chosen(rig,rig[1][:2])['target']==rig[1][2]
    assert chosen(rig,rig[1][:3]) is None
    db,ids,_=rig
    state=service(db,'pairs')._load()
    args=dict(direct_ids=ids[:1],context='ctx',states=derive(state['experiences'],state['policy']),neutral=.5,edges=db._graph_index.get_edges(ids[0]))
    a=call('pair_select',args);args['edges'].reverse();assert call('pair_select',args)==a


def test_feedback_correction_duplicates_conflicts_and_exposure(rig):
    db,ids,_=rig;edge(rig);edge(rig,b=2)
    first=feedback(rig,outcome='same');feedback(rig,outcome='same')
    l=service(db,'pairs');states=deepcopy(l.read(actor='admin')['states'])
    assert states[0]['N_eff']==1
    before={i:db._reader.get(i,track_access=False).to_dict() for i in ids}
    for n in range(4):
        assert chosen(rig)['target']==ids[1]
        recall(rig,request=f'expose{n}')
    assert l.read(actor='admin')['states']==states
    assert before=={i:db._reader.get(i,track_access=False).to_dict() for i in ids}
    feedback(rig,typ='PAIR_IRRELEVANT',outcome='same')
    assert chosen(rig) is None  # Existing FUR consensus removes conflicting evidence.
    resolve(rig,first['experiences'][0]['id'],'PAIR_HELPED')
    assert chosen(rig)['target']==ids[1]
    feedback(rig,b=2,outcome='b1');feedback(rig,b=2,outcome='b2')
    assert chosen(rig)['target']==ids[2]
    resolve(rig,first['experiences'][0]['id'],'PAIR_IRRELEVANT')
    assert chosen(rig)['target']==ids[2]
    assert before=={i:db._reader.get(i,track_access=False).to_dict() for i in ids}


def test_pair_feedback_locality_and_unused(rig):
    db,ids,_=rig;edge(rig);feedback(rig)
    feedback(rig,a=1,b=0,typ='PAIR_IRRELEVANT')
    feedback(rig,relation='warns_about',typ='PAIR_IRRELEVANT')
    feedback(rig,context='CTX',typ='PAIR_IRRELEVANT')
    feedback(rig,typ='UNUSED')
    assert chosen(rig)['W']==.6
    assert all(s['metric']=='W' for s in service(db,'pairs').read(actor='admin')['states'])
    assert all(db._reader.get(i,track_access=False).data['verify_status']=='hypothesis' for i in ids)


def test_capacity_rejects_best_without_substitution(rig):
    db,ids,proto=rig
    huge=proto.remember('large '*8000);ids[1]=huge
    edge(rig);edge(rig,b=2);feedback(rig,outcome='h1');feedback(rig,outcome='h2');feedback(rig,b=2)
    out=recall(rig)
    assert out['selected_ids']==ids[:1] and out['pair_expansion'] is None
    assert chosen(rig)['target']==huge


def test_primary_is_first_admitted_not_first_input(rig):
    db,ids,proto=rig
    huge=proto.remember('large '*8000)
    # Direct order is taken from native admission, including its latent reservation.
    scores={huge:.9,ids[0]:.8}
    edge(rig);feedback(rig)
    out=recall(rig,scores=scores)
    assert out['primary_memory_id']==ids[0]
    assert out['selected_ids']==ids[:2]


def test_selector_readonly_restart_and_cross_namespace(rig,tmp_path):
    db,ids,proto=rig;edge(rig);feedback(rig)
    root=db._graph_index._index_file.parents[2]
    def hashes():return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.parent.rglob('*') if p.is_file()}
    before=hashes();one=chosen(rig);assert hashes()==before
    reopened=EmberDB.connect(str(root));enable(reopened,{'admin'})
    assert select(reopened,'pairs',ids[:1],'ctx')[1]==one
    alien=proto.remember('alien',namespace='other');db.link(ids[0],alien,'requires',primary_context='ctx')
    # FUR namespace validation already rejects evidence for alien targets.
    with pytest.raises((ValueError,PermissionError)):
        service(db,'pairs').apply('report',dict(target=dict(kind='pair',memory_ids=[ids[0],alien],relation='requires'),context='ctx',feedback_type='PAIR_HELPED'),actor='admin',request_id='alien')
    assert chosen(rig)==one


def test_large_hub_no_incoming_count_advantage():
    target=lambda b:dict(kind='pair',memory_ids=['A',b],relation='explains')
    edges=[dict(target=str(i),edge_type='explains',edge_id=str(i),metadata={'primary_context':'ctx'},weight=100000-i) for i in range(10000)]
    states={canonical([target(str(i)),'ctx']):dict(metric='W',value=.6 if i!=9999 else .9,experiences=[dict(resolution_status='accepted',effective_weight=1)]) for i in range(10000)}
    args=dict(direct_ids=['A'],context='ctx',neutral=.5,edges=edges,states=states)
    assert call('pair_select',args)['target']=='9999'
    edges.reverse();assert call('pair_select',args)['target']=='9999'

@pytest.mark.parametrize('format',['structured','raw','text','messages'])
def test_legacy_direct_recall_preserves_order_and_capacity(rig,monkeypatch,format):
    db,ids,proto=rig;edge(rig);feedback(rig)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(ids[0],track_access=False),1.)])
    from dataclasses import replace
    proto.search_config=replace(proto.search_config,semantic_enabled=False)
    out=proto.recall('query',top_k=2,primary_context='ctx',format=format)
    assert out['direct_ids']==ids[:1] and out['pair_expansion']['target']==ids[1]
    assert [r['id'] for r in out['results']]==ids[:2]
    out=proto.recall('query',top_k=1,primary_context='ctx',format=format)
    assert out['direct_ids']==ids[:1] and out['pair_expansion']['target']==ids[1]
    assert [r['id'] for r in out['results']]==ids[:2]


def test_api_mcp_relationship_and_existing_feedback(rig,monkeypatch):
    from embers import api
    from fastapi.testclient import TestClient
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP,TOOLS
    db,ids,_=rig;agent,token=AgentRegistry(db).register('pair-agent');enable(db,{agent.agent_id})
    monkeypatch.setattr(api,'_get_db',lambda:db)
    c=TestClient(api.app);headers={'X-Ember-Agent-Id':agent.agent_id,'X-Ember-Token':token}
    body=dict(source=ids[0],target=ids[1],relation='explains',primary_context='ctx')
    assert c.post('/v1/memory/pair-relationship',json=body).status_code==401
    r=c.post('/v1/memory/pair-relationship',headers=headers,json=body);assert r.status_code==200,r.text
    assert c.post('/v1/memory/pair-relationship',headers=headers,json=body).json()==r.json()
    assert 'ember_pair_relationship' in {t['name'] for t in TOOLS}
    mcp=EmberMCP(db=db)
    response=mcp.call_tool('ember_pair_relationship',{**body,'agent_id':agent.agent_id,'token':token})
    assert not response.get('isError')
    response=mcp.call_tool('ember_usefulness_update',dict(agent_id=agent.agent_id,token=token,namespace='pairs',action='report',request_id='route-feedback',payload=dict(target=dict(kind='pair',memory_ids=ids[:2],relation='explains'),context='ctx',feedback_type='PAIR_HELPED',query_request_id='retrieval-ref')))
    assert not response.get('isError'),response
    assert chosen(rig)['W']==.6


def test_pair_merge_split_reuses_existing_recompute(rig):
    edge(rig)
    a=feedback(rig,outcome='a');b=feedback(rig,outcome='b')
    l=service(rig[0],'pairs')
    assert chosen(rig)['W']==pytest.approx(2/3)
    merged=l.apply('merge',dict(experience_ids=[a['experiences'][0]['id'],b['experiences'][0]['id']],reason='same underlying outcome'),actor='admin',request_id='merge',expected_revision=len(l._events))
    assert chosen(rig)['W']==.6
    active=next(e for e in l.read(actor='admin')['experiences'] if e['active'])
    l.apply('split',dict(experience_id=active['id'],partitions=[[a['report']['id']],[b['report']['id']]],reason='distinct outcomes'),actor='admin',request_id='split',expected_revision=len(l._events))
    assert chosen(rig)['W']==pytest.approx(2/3)


def test_best_unavailable_does_not_substitute(rig):
    db,ids,proto=rig;edge(rig);edge(rig,b=2)
    feedback(rig,outcome='one');feedback(rig,outcome='two');feedback(rig,b=2)
    proto.forget(ids[1])
    assert chosen(rig) is None


def test_nondefault_neutral_and_unresolved_evidence_not_counted(rig):
    edge(rig);first=feedback(rig,outcome='a');feedback(rig,outcome='b')
    resolve(rig,first['experiences'][0]['id'],None,'unresolved')
    # Existing FUR retains the other accepted outcome; no new pair-level veto.
    assert chosen(rig)['W']==.6
    state=service(rig[0],'pairs')._load()
    states=derive(state['experiences'],state['policy'])
    args=dict(direct_ids=rig[1][:1],context='ctx',neutral=.7,states=states,edges=rig[0]._graph_index.get_edges(rig[1][0]))
    assert call('pair_select',args) is None


def test_incoming_paths_do_not_compete(rig):
    edge(rig);feedback(rig)
    edge(rig,a=2,b=0);feedback(rig,a=2,b=0,outcome='one');feedback(rig,a=2,b=0,outcome='two')
    for _ in range(10):rig[0].link(rig[1][3],rig[1][2],'explains',primary_context='ctx')
    assert chosen(rig)['target']==rig[1][1]


def test_recall_retry_replays_original_pair_after_feedback(rig):
    db,ids,_=rig;edge(rig);e=feedback(rig)
    original=recall(rig)
    resolve(rig,e['experiences'][0]['id'],'PAIR_IRRELEVANT')
    assert recall(rig)==original
    assert recall(rig,request='new')['pair_expansion'] is None
    root=db._graph_index._index_file.parents[2]
    reopened=EmberDB.connect(str(root));enable(reopened,{'admin'})
    assert consolidated.recall(reopened,'pairs','admin',query_id='q',direct_scores={ids[0]:.8},elapsed=1,context='ctx')==original


def test_link_does_not_learn_and_traversal_does_not_count_access(rig,monkeypatch):
    db,ids,proto=rig;l=service(db,'pairs');before=deepcopy(l._load())
    a=edge(rig);assert edge(rig)==a and l._load()==before
    feedback(rig)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(ids[0],track_access=False),1.)])
    from dataclasses import replace
    proto.search_config=replace(proto.search_config,semantic_enabled=False)
    pair_before=db._reader.get(ids[1],track_access=False).to_dict()
    proto.recall('query',top_k=2,primary_context='ctx',format='structured')
    assert db._reader.get(ids[1],track_access=False).to_dict()==pair_before


def test_result_count_capacity_is_authoritative(rig):
    db,ids,_=rig;edge(rig);feedback(rig)
    settings=consolidated.settings(db,'pairs','admin')
    consolidated.configure(db,'pairs','admin',dict(config={**settings['config'],'max_results':1},policy=settings['policy'],reason='one item capacity',request_id='config',expected_revision=settings['journal_revision']))
    out=recall(rig)
    assert out['selected_ids']==ids[:1] and out['pair_expansion'] is None


@pytest.mark.parametrize('format',['structured','raw'])
def test_ordinary_two_direct_plus_one_pair(rig,monkeypatch,format):
    from dataclasses import replace
    db,ids,proto=rig;edge(rig);feedback(rig)
    proto.search_config=replace(proto.search_config,semantic_enabled=False)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(ids[0],track_access=False),1.),(db._reader.get(ids[2],track_access=False),.5)])
    out=proto.recall('query',top_k=2,primary_context='ctx',format=format)
    assert out['direct_ids']==[ids[0],ids[2]]
    assert out['primary_memory_id']==ids[0]
    assert out['pair_expansion']['target']==ids[1]
    assert [r['id'] for r in out['results']]==[ids[0],ids[2],ids[1]]


@pytest.mark.parametrize('format',['structured','raw','text','messages'])
def test_ordinary_hard_total_cap_preserves_direct(rig,monkeypatch,format):
    from dataclasses import replace
    db,ids,proto=rig;edge(rig);feedback(rig)
    proto.search_config=replace(proto.search_config,semantic_enabled=False,max_results=1)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(ids[0],track_access=False),1.)])
    out=proto.recall('query',top_k=1,primary_context='ctx',format=format)
    assert out['direct_ids']==ids[:1]
    assert out['pair_expansion'] is None
    assert [r['id'] for r in out['results']]==ids[:1]


@pytest.mark.parametrize('format',['text','messages'])
def test_ordinary_token_capacity_preserves_direct_no_substitution(rig,monkeypatch,format):
    from dataclasses import replace
    from embers.integration import pairing
    db,ids,proto=rig;edge(rig);feedback(rig,outcome='strong-one');feedback(rig,outcome='strong-two')
    edge(rig,b=2);feedback(rig,b=2,outcome='weaker')
    proto.search_config=replace(proto.search_config,semantic_enabled=False)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(ids[0],track_access=False),1.)])
    with monkeypatch.context() as m:
        m.setattr(pairing,'select',lambda *a,**k:(None,None))
        baseline=proto.recall('query',top_k=1,primary_context='ctx',format=format)
    content=baseline['memories']
    proto.context_builder.max_tokens=(proto.context_builder._estimate_tokens(content) if format=='text'
        else sum(len(x['content']) for x in content)/proto.context_builder._chars_per_token)+1
    original=pairing.select;attempts=[]
    def track(*args,**kwargs):
        result=original(*args,**kwargs);attempts.append(result[1]['target']);return result
    monkeypatch.setattr(pairing,'select',track)
    out=proto.recall('query',top_k=1,primary_context='ctx',format=format)
    assert attempts==[ids[1]]
    assert out['direct_ids']==ids[:1] and out['pair_expansion'] is None
    if format=='text':
        assert out['memories']==content
    else:
        assert [m['content'] for m in out['memories']]==[m['content'] for m in content]
        assert [m['metadata']['ember_record_id'] for m in out['memories']]==ids[:1]
    assert [r['id'] for r in out['results']]==ids[:1]
