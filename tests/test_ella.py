"""ELLA V1: native projection, exact-version journal and epistemic firewalls."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import pytest
from embers.db import EmberDB
from embers.core.evidence import Evidence
from embers.core.domain import call,explicit_truth
from embers.integration import MemoryProtocol
from embers.integration.usefulness_service import enable,service
from embers.cognitive.epistemic import EpistemicLedger

@pytest.fixture
def rig(tmp_path):
    db=EmberDB.connect(str(tmp_path/'store'));enable(db,{'admin'})
    proto=MemoryProtocol(db,default_namespace='ella')
    rid=proto.remember({'content':'exact claim','verify_status':'hypothesis'},room='personal')
    return db,proto,rid,EpistemicLedger(db,'ella')

def ev(rig,ref=None,**kw):
    db,_,rid,_=rig
    return db.attach_evidence(rid,Evidence(reference=ref or '',**kw))

def apply(rig,action='report',actor='admin',**payload):
    db,_,rid,l=rig;_,events=l.load()
    body=dict(target_memory_id=rid,target_memory_version=db._store.read(rid).content_hash,**({'assessment_note':'observable test justification'} if action in ('report','confirm','revise','resolve') else {'reason':'observable test justification'}),**payload)
    return l.apply(action,body,actor=actor,request_id='request-'+str(len(events)+1),expected_revision=len(events))

def report(rig,eid,polarity='SUPPORTS',strength='STRONG',actor='admin'):
    return apply(rig,evidence_id=eid,polarity=polarity,strength=strength,actor=actor)


def test_legacy_hash_and_lineage_hash():
    old=Evidence(reference='artifact');old.seal();raw=old.to_dict()
    assert 'hash_version' not in raw and Evidence.from_dict(raw).verify_integrity()
    new=Evidence(origin='origin',origin_confidence='AGENT_DECLARED',derived_from=['prior']);new.seal()
    assert new.hash_version==2 and Evidence.from_dict(new.to_dict()).verify_integrity()
    # Existing V2 hash semantics stay stable after request provenance was added.
    v2_payload=new.to_dict();assert 'request_id' not in v2_payload
    assert Evidence.from_dict(v2_payload).compute_content_hash()==new.content_hash
    req=Evidence(origin='origin',origin_confidence='AGENT_DECLARED',request_id='request-1');req.seal()
    assert req.hash_version==3 and Evidence.from_dict(req.to_dict()).verify_integrity()
    new.origin='changed';assert not new.verify_integrity()
    with pytest.raises(ValueError):Evidence.from_dict(dict(raw,origin='unsigned addition'))


@pytest.mark.parametrize('polarity,verdict',[('SUPPORTS','VERIFIED'),('OPPOSES','DISFAVORED')])
def test_crossing_confirm_distinct_and_restart(rig,polarity,verdict):
    db,p,rid,l=rig;a=ev(rig,'one');b=ev(rig,'two')
    one=report(rig,a,polarity);assert one['base_epistemic_verdict']=='PROVISIONAL' and not one['confirmation_required']
    two=report(rig,b,polarity);assert two['confirmation_required'] and two['base_epistemic_verdict']=='PROVISIONAL'
    aid=two['confirmation_assessment_ids'][0]
    with pytest.raises(PermissionError):apply(rig,'confirm',evidence_id=b,polarity=polarity,strength='STRONG',confirmation_of=aid)
    done=apply(rig,'confirm',actor='second',evidence_id=b,polarity=polarity,strength='STRONG',confirmation_of=aid)
    assert done['base_epistemic_verdict']==verdict and done['accepted_unit_count']==2
    reopened=EmberDB.connect(db._path);enable(reopened,{'admin'})
    assert EpistemicLedger(reopened,'ella').read(rid,'admin')['score']==done['score']
    assert explicit_truth(db._reader.get(rid))['base_epistemic_verdict']==verdict


@pytest.mark.parametrize('strength',['WEAK','MEDIUM'])
def test_confirmation_lower_strength_prevents_crossing(rig,strength):
    a=ev(rig,'one');b=ev(rig,'two');report(rig,a);pending=report(rig,b)
    result=apply(rig,'confirm',actor='second',evidence_id=b,polarity='SUPPORTS',strength=strength,confirmation_of=pending['confirmation_assessment_ids'][0])
    policy=call('ella_policy_default',None)
    assert result['score']==pytest.approx(policy['strengths']['STRONG']+policy['strengths'][strength])
    assert result['base_epistemic_verdict']=='PROVISIONAL'


def test_opposing_confirmation_stays_unresolved(rig):
    a=ev(rig,'one');b=ev(rig,'two');report(rig,a);pending=report(rig,b)
    result=apply(rig,'confirm',actor='second',evidence_id=b,polarity='OPPOSES',strength='STRONG',confirmation_of=pending['confirmation_assessment_ids'][0])
    assert not result['confirmation_required']
    assert result['resolution_required'] and result['confirmation_disagreement']
    assert result['evidence_dispute'] and result['public_epistemic_state']=='DISPUTED'
    assert result['base_epistemic_verdict']=='PROVISIONAL'
    assert len(result['resolution_assessment_groups'])==1
    # A disagreement is not a new threshold-crossing proposal that a third
    # assessor may confirm. V1 requires explicit resolution; no voting chain.
    _,_,_,ledger=rig
    state,_=ledger.load()
    disagreement=next(a for a in state['assessments'].values() if a.get('confirmation_of'))
    with pytest.raises(ValueError,match='explicit resolution'):
        apply(rig,'confirm',actor='third',evidence_id=b,polarity='OPPOSES',
              strength='STRONG',confirmation_of=disagreement['assessment_id'])


def test_confirmation_disagreement_resolution_must_cover_full_pair(rig):
    db,_,rid,l=rig
    a=ev(rig,'one');b=ev(rig,'two');report(rig,a);pending=report(rig,b)
    disputed=apply(rig,'confirm',actor='second',evidence_id=b,polarity='OPPOSES',
                   strength='STRONG',confirmation_of=pending['confirmation_assessment_ids'][0])
    group=disputed['resolution_assessment_groups'][0]
    state,events=l.load()
    with pytest.raises(ValueError,match='complete confirmation disagreement'):
        l.apply('resolve',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,evidence_id=b,
            assessment_ids=[group[0]],polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='partial disagreement resolution must fail'),
            actor='admin',request_id='partial-confirmation-resolution',expected_revision=len(events))
    state,events=l.load()
    resolved=l.apply('resolve',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,evidence_id=b,
        assessment_ids=group,polarity='SUPPORTS',strength='MEDIUM',
        assessment_note='resolve complete threshold confirmation disagreement'),
        actor='admin',request_id='full-confirmation-resolution',expected_revision=len(events))
    assert not resolved['resolution_required']
    assert not resolved['confirmation_disagreement']


def test_same_reference_many_assessors_one_unit(rig):
    for i in range(10):result=report(rig,ev(rig,'same-artifact'),actor='assessor-'+str(i))
    assert result['raw_evidence_count']==10 and result['accepted_unit_count']==1 and result['hard_collapsed_count']==9
    assert result['score']==pytest.approx(call('ella_policy_default',None)['strengths']['STRONG'])


def test_same_request_identity_is_one_hard_epistemic_unit(rig):
    a=ev(rig,source='tool://inventory',request_id='tool-request-42')
    b=ev(rig,source='tool://inventory',request_id='tool-request-42')
    report(rig,a,strength='MEDIUM',actor='assessor-a')
    result=report(rig,b,strength='MEDIUM',actor='assessor-b')
    assert result['raw_evidence_count']==2
    assert result['accepted_unit_count']==1
    assert result['hard_collapsed_count']==1
    assert result['lineage_coverage']==1
    assert result['unresolved_independence_count']==0
    assert result['score']==pytest.approx(call('ella_policy_default',None)['strengths']['MEDIUM'])


def test_same_request_text_from_different_sources_is_not_forced_dependent(rig):
    a=ev(rig,source='tool://one',request_id='42')
    b=ev(rig,source='tool://two',request_id='42')
    report(rig,a,strength='WEAK',actor='assessor-a')
    result=report(rig,b,strength='WEAK',actor='assessor-b')
    assert result['accepted_unit_count']==2


def test_full_recompute_merge_split_withdraw_correct(rig):
    db,p,rid,l=rig;a=ev(rig,'one');b=ev(rig,'two')
    report(rig,a,strength='MEDIUM');result=report(rig,b,strength='MEDIUM');assert result['accepted_unit_count']==2
    merged=apply(rig,'merge',evidence_ids=[a,b]);assert merged['accepted_unit_count']==1
    split=apply(rig,'split',group_id=merged['event_id']);assert split['accepted_unit_count']==2
    state,_=l.load();aid=next(iter(state['assessments']))
    result=apply(rig,'withdraw',assessment_id=aid);assert result['accepted_unit_count']==1
    result=apply(rig,'correct_evidence',evidence_id=b,lineage={'reference':'one'});assert result['hard_collapsed_count']==1
    result=apply(rig,'invalidate_evidence',evidence_id=b);assert result['accepted_unit_count']==0


def test_retry_cas_and_invalid_numeric_strength(rig):
    db,p,rid,l=rig;eid=ev(rig);body=dict(target_memory_id=rid,target_memory_version=db._store.read(rid).content_hash,evidence_id=eid,polarity='SUPPORTS',strength='WEAK',assessment_note='test')
    first=l.apply('report',body,actor='admin',request_id='retry',expected_revision=0)
    assert l.apply('report',body,actor='admin',request_id='retry',expected_revision=0)==first
    with pytest.raises(ValueError,match='stale'):l.apply('report',body,actor='admin',request_id='new',expected_revision=0)
    with pytest.raises(ValueError):l.apply('report',dict(body,strength=1.5),actor='admin',request_id='bad',expected_revision=1)
    with pytest.raises(ValueError):l.apply('report',dict(body,llr=10),actor='admin',request_id='bad2',expected_revision=1)


def test_firewall_and_room_compatibility(rig,monkeypatch):
    db,p,rid,l=rig;f=service(db,'ella');before=deepcopy(f._load());report(rig,ev(rig,'one'))
    assert f._load()==before
    epistemic=deepcopy(l.read(rid,'admin'))
    f.apply('report',dict(target=dict(kind='memory',memory_ids=[rid]),context='ctx',feedback_type='CONTRIBUTED'),actor='admin',request_id='useful')
    assert l.read(rid,'admin')==epistemic
    p.search_config=replace(p.search_config,semantic_enabled=False)
    monkeypatch.setattr(db,'search',lambda *a,**k:[(db._reader.get(rid,track_access=False),1.)])
    assert p.recall('exact',room='project',format='raw')==[]
    assert p.recall('exact',room='personal',format='raw')[0].id==rid
    assert db._store.read(rid).data['room']=='personal'


def test_annotations_not_authority_no_evidence_vs_balanced(rig):
    db,p,rid,l=rig
    assert explicit_truth(db._reader.get(rid))['status']=='provisional'
    p.verify(rid,status='verified',note='legacy annotation')
    assert explicit_truth(db._reader.get(rid))['status']=='provisional'
    assert not l.read(rid,'admin')['assessment_started']
    report(rig,ev(rig,'one'),strength='MEDIUM');state=report(rig,ev(rig,'two'),polarity='OPPOSES',strength='MEDIUM')
    assert state['score']==0 and state['assessment_started'] and state['accepted_unit_count']==2 and state['evidence_dispute']


def test_known_limitation_unlinked_hidden_origin_can_verify(rig):
    # Different reports of one hidden real-world source have no structural link.
    a=ev(rig,source='host-one',agent_id='one');b=ev(rig,source='host-two',agent_id='two')
    report(rig,a);pending=report(rig,b)
    result=apply(rig,'confirm',actor='independent-id-only',evidence_id=b,polarity='SUPPORTS',strength='STRONG',confirmation_of=pending['confirmation_assessment_ids'][0])
    assert result['base_epistemic_verdict']=='VERIFIED'
    assert result['lineage_coverage']==0 and result['unresolved_independence_count']==2


def test_projection_order_is_deterministic(rig):
    db,p,rid,l=rig
    a=ev(rig,'one');b=ev(rig,'two')
    report(rig,a,strength='MEDIUM',actor='z-assessor')
    report(rig,b,strength='WEAK',actor='a-assessor')
    first=l.read(rid,'admin')
    state,events=l.load()
    # Reordering dictionary insertion must not alter the canonical projection.
    state['assessments']={k:state['assessments'][k] for k in reversed(list(state['assessments']))}
    second=l.project(rid,state,len(events))
    assert first['units']==second['units']
    assert json.dumps(first['units'],sort_keys=True)==json.dumps(second['units'],sort_keys=True)


def test_legacy_verify_status_does_not_create_parallel_lifecycle_truth(rig):
    _,proto,_,_=rig
    legacy=proto.remember({'content':'legacy verified marker','verify_status':'verified'},namespace='ella')
    report=proto.get_lifecycle(legacy)
    assert report.state.value!='verified'


def test_legacy_message_context_does_not_present_old_verification_as_truth(rig):
    db,_,rid,_=rig
    rec=db._reader.get(rid,track_access=False)
    rec.data['verify_status']='verified'
    from embers.integration.context import ContextBuilder
    messages=ContextBuilder(max_tokens=1000).build_message_context([rec])
    assert messages
    assert 'see_epistemic_state' in messages[0]['content']
    assert '| verified]' not in messages[0]['content']


def test_legacy_truth_markers_are_not_injected_by_text_or_structured_recall(rig):
    db,proto,rid,_=rig
    rec=db._reader.get(rid,track_access=False)
    rec.data['verify_status']='verified'
    rec.data['_status']='verified'
    proto.verify(rid,status='verified',note='legacy audit only')
    rec=db._reader.get(rid,track_access=False)
    from embers.integration.context import ContextBuilder
    builder=ContextBuilder(max_tokens=1000)
    text_context=builder.build_text_context([rec])
    assert 'verify_status:' not in text_context
    assert '_status:' not in text_context
    assert 'Verification status updated to: verified' not in text_context
    assert 'epistemic: see_epistemic_state' in text_context
    structured=builder.build_structured_context([rec])[0]
    assert 'verify_status' not in structured['data']
    assert '_status' not in structured['data']
    assert structured['epistemic_state']=='see_epistemic_state'


def test_high_confidence_promotion_stays_provisional(rig):
    db,_,_,_=rig
    from embers.core.proposal import MemoryProposal
    from embers.core.types import SourceType
    evidence=Evidence(source='tool://promotion',source_type=SourceType.EXPERIMENTALLY_VERIFIED,
                      reference='promotion-evidence',agent_id='agent')
    pid=db.propose(MemoryProposal(namespace='ella',discovery={'content':'promoted claim'},
                                  reason='admission test',evidence=[evidence],
                                  confidence=.99,agent_id='agent'))
    result=db.submit(pid)
    assert result.promoted
    assert db.memory_status(result.memory_id).value=='provisional'


def test_explicit_promotion_status_override_is_rejected(rig):
    db,_,_,_=rig
    from embers.core.proposal import MemoryProposal
    from embers.core.types import MemoryStatus
    pid=db.propose(MemoryProposal(namespace='ella',discovery={'content':'human admit'},
                                  reason='manual admission',confidence=.2,agent_id='agent'))
    with pytest.raises(ValueError,match='admission only'):
        db.promote(pid,status=MemoryStatus.VERIFIED)


def test_research_recall_capacity_ignores_ella_projection_growth(rig):
    db,_,rid,l=rig
    from embers.integration import consolidated
    from embers.integration.usefulness_service import enable
    enable(db,{'admin'})
    before=consolidated.recall(db,'ella','admin',query_id='before',
        direct_scores={rid:1.0},elapsed=0,context='ctx',format='structured')
    assert rid in before['selected_ids']
    # Grow canonical ELLA state while leaving memory content and retrieval signals untouched.
    for i in range(8):
        eid=ev(rig,f'artifact-{i}')
        report(rig,eid,strength='WEAK',actor=f'assessor-{i}')
    after=consolidated.recall(db,'ella','admin',query_id='after',
        direct_scores={rid:1.0},elapsed=0,context='ctx',format='structured')
    assert after['selected_ids']==before['selected_ids']
    assert rid in after['epistemic']
    assert len(json.dumps(after['epistemic'][rid],sort_keys=True)) > len(json.dumps(before['epistemic'][rid],sort_keys=True))
    # The capacity-counted memory block carries only a constant pointer to the
    # separate canonical ELLA metadata; it never re-exposes legacy truth authority.
    rendered=json.loads(after['context'])
    assert rendered[0]['truth_status']=='see_epistemic_metadata'
    assert rendered[0]['truth_projection']=={'source':'epistemic_metadata','projection_version':'ella-v1'}
    assert 'support_mass' not in rendered[0]['truth_projection']
    assert '_status' not in rendered[0]['data']
    assert 'verify_status' not in rendered[0]['data']


def test_conflict_overlay_never_mutates_score(rig):
    db,p,rid,l=rig
    from embers.core.types import ConflictStatus
    other=p.remember({'content':'contradictory claim'},namespace='ella')
    eid=ev(rig,'support');report(rig,eid,strength='MEDIUM')
    baseline=l.read(rid,'admin')
    cid=db.map_conflict(rid,other,detected_by='admin')
    opened=l.read(rid,'admin')
    assert opened['score']==baseline['score']
    assert opened['base_epistemic_verdict']==baseline['base_epistemic_verdict']
    assert opened['public_epistemic_state']=='DISPUTED'
    assert opened['conflict_overlay']=='open'

    db.update_conflict_status(cid,ConflictStatus.INVESTIGATING,'checking','admin')
    investigating=l.read(rid,'admin')
    assert investigating['score']==baseline['score']
    assert investigating['public_epistemic_state']=='DISPUTED'
    assert investigating['conflict_overlay']=='investigating'

    db.update_conflict_status(cid,ConflictStatus.ACCEPTED_BOTH,'both are contextually valid','admin')
    accepted=l.read(rid,'admin')
    assert accepted['score']==baseline['score']
    assert accepted['base_epistemic_verdict']==baseline['base_epistemic_verdict']
    assert accepted['public_epistemic_state']==baseline['public_epistemic_state']
    assert accepted['conflict_overlay']=='none'


def test_conflict_winner_does_not_change_ella_score(rig):
    db,p,rid,l=rig
    from embers.core.types import ConflictStatus
    other=p.remember({'content':'opposite'},namespace='ella')
    report(rig,ev(rig,'support'),strength='MEDIUM')
    before=l.read(rid,'admin')
    cid=db.map_conflict(rid,other,detected_by='admin')
    db.update_conflict_status(cid,ConflictStatus.RESOLVED,'prefer first','admin',winner_id=rid)
    after=l.read(rid,'admin')
    assert after['score']==before['score']
    assert after['support_mass']==before['support_mass']
    assert after['opposition_mass']==before['opposition_mass']


def test_source_type_and_origin_confidence_do_not_set_strength(rig):
    from embers.core.types import SourceType
    db,_,rid,l=rig
    strong_source=Evidence(source='sensor',source_type=SourceType.EXPERIMENTALLY_VERIFIED,
        reference='a',origin='sensor-origin',origin_confidence='AGENT_DECLARED')
    weak_source=Evidence(source='person',source_type=SourceType.REPORTED,
        reference='b',origin='person-origin',origin_confidence='UNKNOWN')
    a=db.attach_evidence(rid,strong_source);b=db.attach_evidence(rid,weak_source)
    one=report(rig,a,strength='WEAK',actor='one')
    two=report(rig,b,strength='WEAK',actor='two')
    expected=2*call('ella_policy_default',None)['strengths']['WEAK']
    assert two['score']==pytest.approx(expected)
    assert all(unit['magnitude']==pytest.approx(call('ella_policy_default',None)['strengths']['WEAK'])
               for unit in two['units'] if unit['status']=='accepted')


def test_mcp_origin_semantics_and_reserved_system_confirmation(tmp_path):
    import json as _json
    from embers.mcp.server import EmberMCP
    db=EmberDB.connect(str(tmp_path/'mcp-origin'));mcp=EmberMCP(db=db)
    reg=mcp.call_tool('ember_register',{'name':'assessor','provider':'local','model':'test'})
    auth=_json.loads(reg['content'][0]['text'])
    def call_tool(name,args):
        return mcp.call_tool(name,{**args,'agent_id':auth['agent_id'],'token':auth['token']})
    written=call_tool('ember_write',{'content':'claim','namespace':'origin'})
    rid=_json.loads(written['content'][0]['text'])['id']

    unknown=call_tool('ember_attach_evidence',{'memory_id':rid,'source':'report'})
    assert not unknown['isError']
    unknown_id=_json.loads(unknown['content'][0]['text'])['evidence_id']
    stored=db.get_evidence(unknown_id)
    assert stored.origin=='unknown' and stored.origin_confidence=='UNKNOWN'

    declared=call_tool('ember_attach_evidence',{'memory_id':rid,'source':'report-2',
        'origin':'person:alice','origin_confidence':'AGENT_DECLARED','event_id':'observation-2'})
    assert not declared['isError']
    declared_id=_json.loads(declared['content'][0]['text'])['evidence_id']
    stored=db.get_evidence(declared_id)
    assert stored.origin=='person:alice' and stored.origin_confidence=='AGENT_DECLARED'
    assert stored.event_id=='observation-2'

    denied=call_tool('ember_attach_evidence',{'memory_id':rid,'source':'report-3',
        'origin':'person:bob','origin_confidence':'SYSTEM_CONFIRMED'})
    assert denied['isError']


def test_mcp_schema_exposes_lineage_without_system_confirmed():
    from embers.mcp.tools import TOOLS
    attach=next(t for t in TOOLS if t['name']=='ember_attach_evidence')
    props=attach['inputSchema']['properties']
    assert {'origin','origin_confidence','event_id','derived_from'} <= set(props)
    assert props['origin_confidence']['enum']==['UNKNOWN','AGENT_DECLARED']
    propose=next(t for t in TOOLS if t['name']=='ember_propose_memory')
    item=propose['inputSchema']['properties']['evidence']['items']
    assert item['properties']['origin_confidence']['enum']==['UNKNOWN','AGENT_DECLARED']


def test_epistemic_rest_mcp_state_parity(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP
    db=EmberDB.connect(str(tmp_path/'parity-store'))
    agent,token=AgentRegistry(db).register('ella-parity')
    proto=MemoryProtocol(db,default_namespace='parity')
    rid=proto.remember({'content':'parity claim'},written_by=agent.agent_id,agent_id=agent.agent_id)
    evidence=Evidence(source='fixture',reference='parity-evidence',agent_id=agent.agent_id,
                      origin='fixture-origin',origin_confidence='AGENT_DECLARED')
    eid=db.attach_evidence(rid,evidence)
    version=db._store.read(rid).content_hash
    monkeypatch.setattr(api,'_get_db',lambda:db)
    client=TestClient(api.app)
    headers={'X-Ember-Agent-Id':agent.agent_id,'X-Ember-Token':token}
    body={'action':'report','request_id':'rest-report','expected_revision':0,'payload':{
        'target_memory_id':rid,'target_memory_version':version,'evidence_id':eid,
        'polarity':'SUPPORTS','strength':'MEDIUM','assessment_note':'REST parity assessment'}}
    response=client.post('/v1/epistemic/feedback/parity',headers=headers,json=body)
    assert response.status_code==200,response.text
    rest_state=response.json()

    mcp=EmberMCP(db=db)
    mcp_state=mcp.call_tool('ember_epistemic_state',{'namespace':'parity','memory_id':rid,
        'agent_id':agent.agent_id,'token':token})
    assert not mcp_state['isError']
    mcp_state=json.loads(mcp_state['content'][0]['text'])
    canonical=('base_epistemic_verdict','public_epistemic_state','score','support_mass',
               'opposition_mass','accepted_unit_count','raw_evidence_count',
               'assessment_started','epistemic_revision','evidence_dispute','conflict_overlay')
    assert {k:mcp_state[k] for k in canonical}=={k:rest_state[k] for k in canonical}

    # MCP mutation must project identically back through REST.
    eid2=db.attach_evidence(rid,Evidence(source='fixture-2',reference='parity-evidence-2',
        agent_id=agent.agent_id,origin='fixture-origin-2',origin_confidence='AGENT_DECLARED'))
    mcp_mut=mcp.call_tool('ember_epistemic_feedback',{'namespace':'parity','action':'report',
        'request_id':'mcp-report','expected_revision':1,'payload':{
            'target_memory_id':rid,'target_memory_version':version,'evidence_id':eid2,
            'polarity':'OPPOSES','strength':'WEAK','assessment_note':'MCP parity assessment'},
        'agent_id':agent.agent_id,'token':token})
    assert not mcp_mut['isError']
    rest_read=client.get('/v1/epistemic/state/parity',headers=headers,params={'memory_id':rid})
    assert rest_read.status_code==200,rest_read.text
    assert rest_read.json()['score']==json.loads(mcp_mut['content'][0]['text'])['score']


def test_carry_forward_is_explicit_same_lineage_and_not_new_independence(rig):
    db,p,old_id,_=rig
    evidence_id=ev(rig,'versioned-evidence')
    new_id,_=db.update(old_id,{'content':'exact claim revised','verify_status':'hypothesis'},written_by='admin')
    ledger=EpistemicLedger(db,'ella')
    _,events=ledger.load()
    carried=ledger.apply('carry_forward',dict(target_memory_id=new_id,
        target_memory_version=db._store.read(new_id).content_hash,
        source_memory_id=old_id,source_memory_version=db._store.read(old_id).content_hash,
        evidence_ids=[evidence_id],reason='same claim lineage, evidence still applicable'),
        actor='admin',request_id='carry-version',expected_revision=len(events))
    assert carried['raw_evidence_count']==1
    _,events=ledger.load()
    assessed=ledger.apply('report',dict(target_memory_id=new_id,
        target_memory_version=db._store.read(new_id).content_hash,
        evidence_id=evidence_id,polarity='SUPPORTS',strength='MEDIUM',
        assessment_note='reassessed against revised claim'),actor='admin',
        request_id='assess-carried',expected_revision=len(events))
    assert assessed['accepted_unit_count']==1

    # Carried evidence is part of the target version's accepted evidence set:
    # it can be dependency-resolved together with evidence attached directly
    # to the revised claim, without inventing a second copy of the carried item.
    direct=db.attach_evidence(new_id,Evidence(reference='new-version-evidence'))
    _,events=ledger.load()
    with_direct=ledger.apply('report',dict(target_memory_id=new_id,
        target_memory_version=db._store.read(new_id).content_hash,
        evidence_id=direct,polarity='SUPPORTS',strength='MEDIUM',
        assessment_note='direct evidence on revised claim'),actor='admin',
        request_id='assess-direct-new-version',expected_revision=len(events))
    assert with_direct['accepted_unit_count']==2
    _,events=ledger.load()
    merged=ledger.apply('merge',dict(target_memory_id=new_id,
        target_memory_version=db._store.read(new_id).content_hash,
        evidence_ids=[evidence_id,direct],reason='same underlying observation'),
        actor='admin',request_id='merge-carried-with-direct',expected_revision=len(events))
    assert merged['accepted_unit_count']==1

    unrelated=p.remember({'content':'unrelated claim'},namespace='ella')
    _,events=ledger.load()
    with pytest.raises(ValueError,match='same memory history'):
        ledger.apply('carry_forward',dict(target_memory_id=unrelated,
            target_memory_version=db._store.read(unrelated).content_hash,
            source_memory_id=old_id,source_memory_version=db._store.read(old_id).content_hash,
            evidence_ids=[evidence_id],reason='must not cross unrelated claims'),
            actor='admin',request_id='bad-carry',expected_revision=len(events))


def test_epistemic_actions_reject_unused_fields(rig):
    db,_,rid,l=rig;eid=ev(rig,'strict-fields')
    _,events=l.load()
    base=dict(target_memory_id=rid,target_memory_version=db._store.read(rid).content_hash,
              evidence_id=eid,polarity='SUPPORTS',strength='WEAK',
              assessment_note='strict schema test',confirmation_of='should-not-be-accepted')
    with pytest.raises(ValueError,match='unsupported assessment fields'):
        l.apply('report',base,actor='admin',request_id='extra-field',expected_revision=len(events))


def test_unknown_origin_is_not_lineage_coverage_or_soft_dependency(rig):
    db,_,rid,l=rig
    a=db.attach_evidence(rid,Evidence(source='relay-a',origin='unknown',origin_confidence='UNKNOWN'))
    b=db.attach_evidence(rid,Evidence(source='relay-b',origin='unknown',origin_confidence='UNKNOWN'))
    report(rig,a,strength='WEAK',actor='one')
    state=report(rig,b,strength='WEAK',actor='two')
    assert state['accepted_unit_count']==2
    assert state['lineage_coverage']==0
    assert state['unresolved_independence_count']==2

    raw,events=l.load()
    configured={**raw['policy'],'soft_same_origin':True}
    result=l.apply('configure',dict(target_memory_id=rid,target_memory_version=db._store.read(rid).content_hash,
        policy=configured,reason='test unknown-origin soft grouping'),actor='admin',
        request_id='unknown-origin-policy',expected_revision=len(events))
    assert result['accepted_unit_count']==2
    assert result['soft_cluster_count']==0


def test_unknown_origin_cannot_be_agent_declared():
    with pytest.raises(ValueError,match='unknown origin'):
        Evidence(origin='unknown',origin_confidence='AGENT_DECLARED')


def test_same_evidence_record_can_have_claim_specific_polarity(rig):
    db,p,first,l=rig
    second=p.remember({'content':'different exact claim'},namespace='ella')
    evidence=Evidence(source='shared-artifact',reference='shared-evidence',
                      origin='artifact:shared',origin_confidence='AGENT_DECLARED')
    eid=db.attach_evidence(first,evidence)
    assert db.attach_evidence(second,evidence)==eid

    _,events=l.load()
    positive=l.apply('report',dict(target_memory_id=first,
        target_memory_version=db._store.read(first).content_hash,evidence_id=eid,
        polarity='SUPPORTS',strength='MEDIUM',assessment_note='supports first exact claim'),
        actor='assessor-one',request_id='claim-one',expected_revision=len(events))
    _,events=l.load()
    negative=l.apply('report',dict(target_memory_id=second,
        target_memory_version=db._store.read(second).content_hash,evidence_id=eid,
        polarity='OPPOSES',strength='MEDIUM',assessment_note='opposes second exact claim'),
        actor='assessor-one',request_id='claim-two',expected_revision=len(events))
    assert positive['score']>0
    assert negative['score']<0
    assert l.read(first,'assessor-one')['score']==positive['score']
    assert l.read(second,'assessor-one')['score']==negative['score']


def test_lineage_correction_cannot_self_upgrade_to_system_confirmed(rig):
    db,_,rid,l=rig;eid=ev(rig,'lineage-correction')
    _,events=l.load()
    with pytest.raises(PermissionError,match='SYSTEM_CONFIRMED'):
        l.apply('correct_evidence',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            evidence_id=eid,lineage={'origin':'claimed-origin','origin_confidence':'SYSTEM_CONFIRMED'},
            reason='attempted authority upgrade'),actor='admin',
            request_id='forbidden-origin-upgrade',expected_revision=len(events))


def test_assessment_lifecycle_rejects_reusing_inactive_records(rig):
    db,_,rid,l=rig;eid=ev(rig,'lifecycle')
    first=report(rig,eid,strength='WEAK')
    state,events=l.load();aid=next(iter(state['assessments']))
    withdrawn=l.apply('withdraw',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,assessment_id=aid,
        reason='withdraw mistaken assessment'),actor='admin',request_id='withdraw-once',
        expected_revision=len(events))
    assert withdrawn['accepted_unit_count']==0
    _,events=l.load()
    with pytest.raises(ValueError,match='active assessments'):
        l.apply('withdraw',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,assessment_id=aid,
            reason='cannot withdraw twice'),actor='admin',request_id='withdraw-twice',
            expected_revision=len(events))
    with pytest.raises(ValueError,match='active assessments'):
        l.apply('revise',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,evidence_id=eid,
            assessment_id=aid,polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='cannot revive withdrawn record'),actor='admin',
            request_id='revise-withdrawn',expected_revision=len(events))


def test_explicit_resolution_replaces_mixed_assessment_without_multiplying_mass(rig):
    db,_,rid,l=rig;eid=ev(rig,'resolution')
    report(rig,eid,polarity='SUPPORTS',strength='STRONG',actor='one')
    mixed=report(rig,eid,polarity='OPPOSES',strength='STRONG',actor='two')
    assert mixed['accepted_unit_count']==0 and mixed['evidence_dispute']
    state,events=l.load();chosen=sorted(state['assessments'])
    resolved=l.apply('resolve',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,evidence_id=eid,
        assessment_ids=chosen,polarity='SUPPORTS',strength='MEDIUM',
        assessment_note='authorized resolution from observable evidence'),
        actor='admin',request_id='resolve-mixed',expected_revision=len(events))
    assert resolved['accepted_unit_count']==1
    assert resolved['score']==pytest.approx(call('ella_policy_default',None)['strengths']['MEDIUM'])


def test_resolution_cannot_erase_independent_opposing_units(rig):
    db,_,rid,l=rig
    support=ev(rig,'independent-support')
    oppose=ev(rig,'independent-oppose')
    report(rig,support,polarity='SUPPORTS',strength='MEDIUM',actor='one')
    report(rig,oppose,polarity='OPPOSES',strength='MEDIUM',actor='two')
    state,events=l.load()
    chosen=sorted(state['assessments'])
    with pytest.raises(ValueError,match='one complete unresolved epistemic unit'):
        l.apply('resolve',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            evidence_id=support,assessment_ids=chosen,polarity='SUPPORTS',
            strength='MEDIUM',assessment_note='must not erase an independent opposing unit'),
            actor='admin',request_id='bad-cross-unit-resolution',expected_revision=len(events))


def test_resolution_can_cover_hard_dependent_mixed_unit(rig):
    db,_,rid,l=rig
    first=ev(rig,'same-resolution-artifact')
    second=ev(rig,'same-resolution-artifact')
    report(rig,first,polarity='SUPPORTS',strength='STRONG',actor='one')
    mixed=report(rig,second,polarity='OPPOSES',strength='STRONG',actor='two')
    assert mixed['accepted_unit_count']==0
    unresolved=next(u for u in mixed['units'] if u['status']=='unresolved')
    assert set(unresolved['evidence_ids'])=={first,second}
    state,events=l.load()
    resolved=l.apply('resolve',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        evidence_id=first,assessment_ids=unresolved['assessment_ids'],
        polarity='SUPPORTS',strength='MEDIUM',
        assessment_note='resolve one detected dependent epistemic unit'),
        actor='admin',request_id='resolve-dependent-unit',expected_revision=len(events))
    assert resolved['accepted_unit_count']==1


def test_revision_cannot_move_assessment_to_different_evidence(rig):
    db,_,rid,l=rig
    first=ev(rig,'revision-first')
    second=ev(rig,'revision-second')
    report(rig,first,strength='WEAK')
    state,events=l.load()
    aid=next(iter(state['assessments']))
    with pytest.raises(ValueError,match='preserve exact claim version and evidence'):
        l.apply('revise',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            evidence_id=second,assessment_id=aid,polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='must not move an assessment to another evidence record'),
            actor='admin',request_id='cross-evidence-revision',expected_revision=len(events))


def test_memory_status_preserves_disfavored_ella_verdict(rig):
    db,_,rid,l=rig
    from embers.core.types import MemoryStatus
    first=ev(rig,'oppose-one')
    second=ev(rig,'oppose-two')
    report(rig,first,polarity='OPPOSES',strength='STRONG',actor='one')
    pending=report(rig,second,polarity='OPPOSES',strength='STRONG',actor='two')
    state,events=l.load()
    evidence_id=state['assessments'][pending['confirmation_assessment_ids'][0]]['evidence_id']
    done=l.apply('confirm',dict(
        target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        evidence_id=evidence_id,
        polarity='OPPOSES',
        strength='STRONG',
        assessment_note='independent lower-threshold confirmation',
        confirmation_of=pending['confirmation_assessment_ids'][0]),
        actor='three',request_id='confirm-disfavored-status',
        expected_revision=len(events))
    assert done['base_epistemic_verdict']=='DISFAVORED'
    assert db.memory_status(rid) is MemoryStatus.DISFAVORED


def test_confirmation_disagreement_parent_cannot_be_orphaned(rig):
    db,_,rid,l=rig
    first=ev(rig,'parent-one')
    second=ev(rig,'parent-two')
    report(rig,first,polarity='SUPPORTS',strength='STRONG',actor='one')
    pending=report(rig,second,polarity='SUPPORTS',strength='STRONG',actor='two')
    parent=pending['confirmation_assessment_ids'][0]
    disputed=apply(rig,'confirm',actor='three',evidence_id=second,
                   polarity='OPPOSES',strength='STRONG',
                   confirmation_of=parent)
    group=disputed['resolution_assessment_groups'][0]
    state,events=l.load()

    with pytest.raises(ValueError,match='confirmation child'):
        l.apply('withdraw',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            assessment_id=parent,reason='must not orphan active disagreement'),
            actor='admin',request_id='withdraw-parent-first',
            expected_revision=len(events))

    with pytest.raises(ValueError,match='confirmation-linked'):
        l.apply('revise',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            evidence_id=second,assessment_id=parent,
            polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='must not revise linked parent'),
            actor='admin',request_id='revise-linked-parent',
            expected_revision=len(events))

    child=next(aid for aid in group if aid!=parent)
    with pytest.raises(ValueError,match='confirmation-linked'):
        l.apply('revise',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            evidence_id=second,assessment_id=child,
            polarity='SUPPORTS',strength='MEDIUM',
            assessment_note='withdraw child then submit a fresh confirmation'),
            actor='three',request_id='revise-linked-child',
            expected_revision=len(events))

    cleared=l.apply('withdraw',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        assessment_id=child,reason='withdraw mistaken opposing confirmation'),
        actor='three',request_id='withdraw-child',
        expected_revision=len(events))
    assert cleared['confirmation_required']
    assert not cleared['resolution_required']

    _,events=l.load()
    removed=l.apply('withdraw',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        assessment_id=parent,reason='withdraw original threshold proposal'),
        actor='admin',request_id='withdraw-parent-after-child',
        expected_revision=len(events))
    assert not removed['confirmation_required']
    assert not removed['resolution_required']


def test_withdrawing_accepted_confirmation_restores_pending_crossing(rig):
    db,_,rid,l=rig
    first=ev(rig,'restore-one')
    second=ev(rig,'restore-two')
    report(rig,first,polarity='SUPPORTS',strength='STRONG',actor='one')
    pending=report(rig,second,polarity='SUPPORTS',strength='STRONG',actor='two')
    parent=pending['confirmation_assessment_ids'][0]
    state,events=l.load()
    confirmed=l.apply('confirm',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        evidence_id=state['assessments'][parent]['evidence_id'],
        polarity='SUPPORTS',strength='STRONG',
        assessment_note='temporary confirmation',
        confirmation_of=parent),
        actor='three',request_id='temporary-confirmation',
        expected_revision=len(events))
    assert confirmed['base_epistemic_verdict']=='VERIFIED'
    state,events=l.load()
    child=next(a['assessment_id'] for a in state['assessments'].values()
               if a.get('confirmation_of')==parent and a['status']=='accepted')
    restored=l.apply('withdraw',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        assessment_id=child,reason='confirmation withdrawn after correction'),
        actor='three',request_id='withdraw-confirmation',
        expected_revision=len(events))
    assert restored['base_epistemic_verdict']=='PROVISIONAL'
    assert restored['confirmation_required']
    assert restored['confirmation_assessment_ids']==[parent]


def test_epistemic_decision_authority_is_separate_from_usefulness_admins(rig):
    db,_,rid,l=rig
    from embers.cognitive.epistemic import enable_epistemic
    eid=ev(rig,'authority-separation')
    report(rig,eid,strength='WEAK',actor='ordinary')
    state,events=l.load()
    aid=next(a['assessment_id'] for a in state['assessments'].values()
             if a['evidence_id']==eid)

    enable_epistemic(db,{'epistemic-admin'})
    db._usefulness_admins=frozenset({'fur-admin'})

    with pytest.raises(PermissionError):
        l.apply('withdraw',dict(target_memory_id=rid,
            target_memory_version=db._store.read(rid).content_hash,
            assessment_id=aid,reason='FUR admin has no ELLA authority'),
            actor='fur-admin',request_id='fur-admin-epistemic-denied',
            expected_revision=len(events))

    result=l.apply('withdraw',dict(target_memory_id=rid,
        target_memory_version=db._store.read(rid).content_hash,
        assessment_id=aid,reason='ELLA admin decision'),
        actor='epistemic-admin',request_id='epistemic-admin-allowed',
        expected_revision=len(events))
    assert result['accepted_unit_count']==0


def test_new_db_evidence_submissions_get_explicit_unknown_origin_but_legacy_hashes_survive(rig):
    db,_,rid,_=rig
    fresh=Evidence(source="fresh-direct")
    fresh_id=db.attach_evidence(rid,fresh)
    stored=db.get_evidence(fresh_id)
    assert stored.origin=="unknown"
    assert stored.origin_confidence=="UNKNOWN"
    assert stored.hash_version>=2

    # A caller cannot manufacture a new sealed V1 object to bypass the origin
    # requirement. Existing stored legacy records remain readable/reusable.
    legacy=Evidence(reference="legacy-v1-artifact")
    legacy.seal()
    assert legacy.hash_version==1 and legacy.origin is None
    with pytest.raises(ValueError,match="requires origin identity"):
        db.attach_evidence(rid,legacy)

    # Simulate a pre-ELLA legacy evidence record already present in storage,
    # then prove re-attaching that exact identity remains compatible.
    from embers.core.record import EmberRecord
    from embers.core.types import RecordType, EdgeType
    from embers.core.record import EdgeRef
    legacy_rec=EmberRecord(id=legacy.evidence_id,namespace="ella",record_type=RecordType.EVIDENCE,
        data=legacy.to_dict(),connections=[EdgeRef(edge_id=f"supports:{legacy.evidence_id}:{rid}",
        target_id=rid,edge_type=EdgeType.SUPPORTS,label="supports")],retrieval_candidate=False)
    db._writer.write(legacy_rec)
    db._graph_index.add_edge(legacy.evidence_id,rid,EdgeType.SUPPORTS.value,
        edge_id=f"supports:{legacy.evidence_id}:{rid}",label="supports")
    assert db.attach_evidence(rid,legacy)==legacy.evidence_id
    restored=db.get_evidence(legacy.evidence_id)
    assert restored.hash_version==1 and restored.origin is None

    from embers.core.proposal import MemoryProposal
    proposal_evidence=Evidence(source="proposal-fresh")
    pid=db.propose(MemoryProposal(namespace="ella",discovery={"content":"origin proposal"},
        reason="origin normalization check",evidence=[proposal_evidence],confidence=.7))
    proposal=db.get_proposal(pid)
    assert proposal.evidence[0].origin=="unknown"
    assert proposal.evidence[0].origin_confidence=="UNKNOWN"


def test_public_rest_and_mcp_reads_expose_ella_not_legacy_truth(tmp_path,monkeypatch):
    import json as _json
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP

    db=EmberDB.connect(str(tmp_path/'public-ella'))
    agent,token=AgentRegistry(db).register('public-ella-reader')
    proto=MemoryProtocol(db,default_namespace='public-ella')
    rid=proto.remember({'content':'public ELLA surface claim','verify_status':'verified'},
        written_by=agent.agent_id,agent_id=agent.agent_id)
    proto.verify(rid,status='verified',note='legacy audit marker only')

    monkeypatch.setattr(api,'_get_db',lambda:db)
    client=TestClient(api.app)
    headers={'X-Ember-Agent-Id':agent.agent_id,'X-Ember-Token':token}
    rest=client.get(f'/v1/memory/read/{rid}',headers=headers)
    assert rest.status_code==200,rest.text
    rest_payload=rest.json()
    assert 'verify_status' not in rest_payload['data'] and '_status' not in rest_payload['data']
    assert rest_payload['epistemic']['available'] is True
    assert rest_payload['epistemic']['base_epistemic_verdict']=='PROVISIONAL'
    assert rest_payload['epistemic']['public_epistemic_state']=='PROVISIONAL'
    assert rest_payload['epistemic']['conflict_overlay']=='none'

    mcp=EmberMCP(db=db)
    auth={'agent_id':agent.agent_id,'token':token}
    def mcp_json(name,args):
        result=mcp.call_tool(name,{**args,**auth})
        assert not result['isError'],result
        return _json.loads(result['content'][0]['text'])

    read=mcp_json('ember_read',{'record_id':rid})
    assert 'verify_status' not in read['data'] and '_status' not in read['data']
    assert read['epistemic']['base_epistemic_verdict']=='PROVISIONAL'
    legacy=[a for a in read['annotations'] if a.get('context')=='verification']
    assert legacy and legacy[0]['epistemic_authority'] is False
    assert legacy[0]['legacy_verification_audit'] is True

    searched=mcp_json('ember_search',{'query':'public ELLA surface claim','namespace':'public-ella'})
    row=next(x for x in searched if x['id']==rid)
    assert 'verify_status' not in row['data'] and row['epistemic']['public_epistemic_state']=='PROVISIONAL'

    queried=mcp_json('ember_query',{'namespace':'public-ella'})
    row=next(x for x in queried['records'] if x['id']==rid)
    assert 'verify_status' not in row['data'] and row['epistemic']['conflict_overlay']=='none'
    query_legacy=[a for a in row['annotations'] if a.get('context')=='verification']
    assert query_legacy and query_legacy[0]['epistemic_authority'] is False
    assert query_legacy[0]['legacy_verification_audit'] is True


def test_public_record_summary_failure_cannot_hide_memory(rig):
    db,_,rid,_=rig
    from embers.core.domain import public_epistemic_summary
    rec=db._reader.get(rid,track_access=False)
    rec._epistemic_provider=lambda: (_ for _ in ()).throw(ValueError('synthetic projection failure'))
    summary=public_epistemic_summary(rec)
    assert summary=={'available':False,'error':'epistemic_projection_unavailable'}
    assert rec.data['content']=='exact claim'
