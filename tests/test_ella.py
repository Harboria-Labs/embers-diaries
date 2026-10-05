"""ELLA V1: native projection, exact-version journal and epistemic firewalls."""
from copy import deepcopy
from dataclasses import replace
import hashlib
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
    assert result['confirmation_required'] and result['base_epistemic_verdict']=='PROVISIONAL'


def test_same_reference_many_assessors_one_unit(rig):
    for i in range(10):result=report(rig,ev(rig,'same-artifact'),actor='assessor-'+str(i))
    assert result['raw_evidence_count']==10 and result['accepted_unit_count']==1 and result['hard_collapsed_count']==9
    assert result['score']==pytest.approx(call('ella_policy_default',None)['strengths']['STRONG'])


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
