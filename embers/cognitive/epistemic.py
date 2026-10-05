"""ELLA persistence/authentication. All epistemic reduction is native Rust.

Exact memory ID + immutable content hash is the claim version. Observation never
writes this journal. Evidence is referenced by its stored record ID.
"""
from copy import deepcopy
import time
import uuid
from ..core.domain import call
from ..core.record import EmberRecord
from ..core.types import RecordType
from .usefulness import canonical, digest, identifier

KIND='ember.epistemic.v1'

class EpistemicLedger:
    def __init__(self, db, namespace):
        self.db,self.namespace=db,namespace
        self.prefix='epistemic-'+uuid.uuid5(uuid.NAMESPACE_URL,KIND+namespace).hex+'-'

    def load(self):
        events=[]
        for rid in sorted(x for x in self.db._store.all_ids() if x.startswith(self.prefix)):
            record=self.db._store.read(rid)
            if (rid!=self.prefix+f'{len(events)+1:012d}' or record is None or
                record.namespace!=self.namespace or record.record_type!=RecordType.RAW or
                record.retrieval_candidate or not record.verify_integrity()):
                raise ValueError('epistemic journal integrity failure')
            event=record.data
            if event['previous']!=(events[-1]['seal'] if events else None) or digest({k:v for k,v in event.items() if k!='seal'})!=event['seal']:
                raise ValueError('epistemic journal chain failure')
            events.append(event)
        state=dict(assessments={},evidence_overrides={},hard_groups={},policy=call('ella_policy_default',None),carried={})
        for event in events:
            state=call('ella_reduce',{'state':state,'event':event})
        return state,events

    def target(self,rid):
        rec=self.db._store.read(rid)
        if rec is None or rec.namespace!=self.namespace or rec.record_type not in self.db._DURABLE_MEMORY_TYPES:
            raise ValueError('exact target must be a durable memory in this namespace')
        if not rec.verify_integrity():raise ValueError('target integrity failure')
        return rec

    def project(self,rid,state=None,revision=None):
        target=self.target(rid)
        if state is None:
            state,events=self.load();revision=len(events)
        evidence=[]
        records={r.id:r for r in self.db.evidence_for(rid)}
        for eid in state.get('carried',{}).get(rid,[]):
            record=self.db._store.read(eid)
            if record is None or record.namespace!=self.namespace:raise ValueError('carried evidence unavailable')
            records[eid]=record
        for record in sorted(records.values(),key=lambda item:item.id):
            ev=self.db.get_evidence(record.id)
            if ev is None or not ev.verify_integrity():raise ValueError('evidence integrity failure')
            evidence.append({**ev.to_dict(),'id':record.id,**state['evidence_overrides'].get(record.id,{})})
        conflicts=self.db.conflicts_for(rid)
        active=[c.status.value for c in conflicts if c.status.value in ('open','investigating')]
        result=call('ella_project',dict(policy=state['policy'],evidence=evidence,
            assessments=[state['assessments'][key] for key in sorted(state['assessments'])],hard_groups=[sorted(state['hard_groups'][key]) for key in sorted(state['hard_groups'])],
            target_memory_id=rid,target_memory_version=target.content_hash,revision=revision,
            conflict_overlay='open' if 'open' in active else 'investigating' if active else 'none'))
        pending=sorted((a for a in state['assessments'].values() if a['target_memory_id']==rid and a['target_memory_version']==target.content_hash and a['status']=='confirmation_required'),key=lambda a:a['assessment_id'])
        result.update(confirmation_required=bool(pending),confirmation_assessment_ids=[a['assessment_id'] for a in pending],
            target_memory_id=rid,target_memory_version=target.content_hash,
            lifecycle=dict(deprecated=self.db._writer.is_deprecated(rid),superseded_by=self.db._writer.get_superseded_by(rid)))
        return result

    def read(self,rid,actor):
        self.db.require_namespace_access(self.namespace,actor,'read')
        with self.db._writer.lock:return self.project(rid)

    def apply(self,action,payload,*,actor,request_id,expected_revision):
        self.db.require_namespace_access(self.namespace,actor,'write')
        identifier(request_id,'request_id')
        if not isinstance(payload,dict):raise ValueError('payload required')
        if type(expected_revision)!=int or expected_revision<0:raise ValueError('expected_revision required')
        fingerprint=digest([actor,action,payload,expected_revision])
        with self.db._writer.lock:
            state,events=self.load()
            for event in events:
                if event['actor']==actor and event['request_id']==request_id:
                    if event['fingerprint']!=fingerprint:raise ValueError('request id reused with different content')
                    return deepcopy(event['response'])
            if expected_revision!=len(events):raise ValueError('stale epistemic revision')
            rid=payload.get('target_memory_id');target=self.target(rid)
            if payload.get('target_memory_version')!=target.content_hash:raise ValueError('exact target content hash required')
            admin=actor in getattr(self.db,'_usefulness_admins',())
            next_state=deepcopy(state);revision=len(events)+1;event_id=self.prefix+f'{revision:012d}'
            reason=payload.get('assessment_note') or payload.get('reason')
            identifier(reason,'audit note')
            allowed_common={'target_memory_id','target_memory_version','reason','session_id'}
            session=payload.get('session_id')
            if session:
                s=self.db.get_session(session)
                if s is None or s.agent_id!=actor or s.status.value!='active':raise PermissionError('active own session required')
            if action in ('report','revise','confirm','resolve'):
                action_fields={
                    'report':{'evidence_id','polarity','strength','assessment_note'},
                    'revise':{'evidence_id','polarity','strength','assessment_note','assessment_id'},
                    'confirm':{'evidence_id','polarity','strength','assessment_note','confirmation_of'},
                    'resolve':{'evidence_id','polarity','strength','assessment_note','assessment_ids'},
                }
                if set(payload)-(allowed_common|action_fields[action]):
                    raise ValueError('unsupported assessment fields; numeric likelihoods are forbidden')
                attached={r.id for r in self.db.evidence_for(rid)}|set(state.get('carried',{}).get(rid,[]))
                if payload.get('evidence_id') not in attached:raise ValueError('evidence must be attached to this exact memory version')
                evidence=self.db.get_evidence(payload['evidence_id'])
                if evidence is None or not evidence.verify_integrity():raise ValueError('evidence integrity failure')
                if next_state['evidence_overrides'].get(payload['evidence_id'],{}).get('invalidated'):raise ValueError('evidence invalidated')
                if action=='resolve':
                    if not admin:raise PermissionError('epistemic decision authority required')
                    chosen=payload.get('assessment_ids')
                    if not isinstance(chosen,list) or not chosen or len(set(chosen))!=len(chosen):raise ValueError('unique assessment_ids required')
                    for aid in chosen:
                        prior=next_state['assessments'].get(aid);self._owned(prior,rid,actor,True)
                        if prior['status'] not in ('accepted','confirmation_required'):
                            raise ValueError('only active assessments may be resolved')
                        prior['status']='resolved'
                if action=='revise':
                    prior=next_state['assessments'].get(payload.get('assessment_id'))
                    self._owned(prior,rid,actor,admin)
                    if prior['status'] not in ('accepted','confirmation_required'):
                        raise ValueError('only active assessments may be revised')
                    prior['status']='superseded'
                assessment=dict(assessment_id=event_id+':assessment',target_memory_id=rid,target_memory_version=target.content_hash,
                    evidence_id=payload['evidence_id'],assessor_id=actor,session_id=session,
                    polarity=payload.get('polarity'),strength=payload.get('strength'),assessment_note=reason,
                    request_id=request_id,revision=revision,created_at=time.time(),status='accepted')
                call('ella_assessment',assessment)
                if action=='confirm':
                    prior=next_state['assessments'].get(payload.get('confirmation_of'))
                    if (prior is None or prior['status']!='confirmation_required'
                            or prior['target_memory_id']!=rid
                            or prior['evidence_id']!=assessment['evidence_id']
                            or prior.get('requires_confirmation') is not True
                            or prior.get('confirmation_of') is not None):
                        raise ValueError('original pending threshold-crossing assessment required; disagreement needs explicit resolution')
                    if prior['assessor_id']==actor:raise PermissionError('distinct confirmation assessor required')
                    if any(a.get('confirmation_of')==prior['assessment_id'] and a['status'] not in ('withdrawn','superseded','resolved') for a in next_state['assessments'].values()):raise ValueError('confirmation already submitted; explicit resolution required')
                    assessment['confirmation_of']=prior['assessment_id']
                    if prior['polarity']==assessment['polarity']:prior['status']='accepted'
                    else:assessment['status']='confirmation_required'
                next_state['assessments'][assessment['assessment_id']]=assessment
                if action!='confirm':
                    before=self.project(rid,state,len(events));after=self.project(rid,next_state,revision)
                    decision=call('ella_crossing',{'before':before,'after':after})
                    if decision['confirmation_required']:
                        assessment['status']='confirmation_required'
                        assessment['requires_confirmation']=True
            elif action=='withdraw':
                if set(payload)-(allowed_common|{'assessment_id'}):raise ValueError('unsupported withdrawal fields')
                a=next_state['assessments'].get(payload.get('assessment_id'));self._owned(a,rid,actor,admin)
                if a['status'] not in ('accepted','confirmation_required'):raise ValueError('only active assessments may be withdrawn')
                a['status']='withdrawn'
            elif action=='invalidate_evidence':
                if set(payload)-(allowed_common|{'evidence_id'}):raise ValueError('unsupported evidence invalidation fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                if payload.get('evidence_id') not in {r.id for r in self.db.evidence_for(rid)}:raise ValueError('attached evidence required')
                next_state['evidence_overrides'].setdefault(payload['evidence_id'],{})['invalidated']=True
            elif action=='correct_evidence':
                if set(payload)-(allowed_common|{'evidence_id','lineage'}):raise ValueError('unsupported evidence correction fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                eid=payload.get('evidence_id')
                if eid not in {r.id for r in self.db.evidence_for(rid)}:raise ValueError('attached evidence required')
                changes=payload.get('lineage')
                if not isinstance(changes,dict) or set(changes)-{'reference','event_id','origin','derived_from','origin_confidence'}:
                    raise ValueError('explicit lineage correction required')
                for field in ('reference','event_id','origin'):
                    if field in changes and changes[field] is not None:identifier(changes[field],field)
                if 'derived_from' in changes:
                    if not isinstance(changes['derived_from'],list):raise ValueError('dependency list required')
                    for dep in changes['derived_from']:identifier(dep,'dependency')
                confidence=changes.get('origin_confidence')
                if confidence not in (None,'UNKNOWN','AGENT_DECLARED','SYSTEM_CONFIRMED'):raise ValueError('invalid origin confidence')
                if confidence=='SYSTEM_CONFIRMED':raise PermissionError('SYSTEM_CONFIRMED origin is reserved for system-captured provenance')
                if changes.get('origin')=='unknown' and confidence not in (None,'UNKNOWN'):
                    raise ValueError('unknown origin cannot carry declared identity confidence')
                next_state['evidence_overrides'].setdefault(eid,{}).update(changes)
            elif action=='carry_forward':
                if not admin:raise PermissionError('epistemic decision authority required')
                if set(payload)-(allowed_common|{'source_memory_id','source_memory_version','evidence_ids'}):
                    raise ValueError('unsupported carry-forward fields')
                source=self.target(payload.get('source_memory_id'))
                if payload.get('source_memory_version')!=source.content_hash:raise ValueError('exact source version required')
                lineage=set(self.db._writer.get_supersession_chain(source.id))
                if rid not in lineage:
                    raise ValueError('carry-forward is limited to later versions in the same memory history')
                ids=payload.get('evidence_ids');available={r.id for r in self.db.evidence_for(source.id)}|set(state.get('carried',{}).get(source.id,[]))
                if not isinstance(ids,list) or not ids or any(eid not in available for eid in ids):raise ValueError('source evidence required')
                next_state.setdefault('carried',{})[rid]=sorted(set(next_state.get('carried',{}).get(rid,[]))|set(ids))
            elif action=='configure':
                if set(payload)-(allowed_common|{'policy'}):raise ValueError('unsupported policy fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                next_state['policy']=call('ella_policy',payload.get('policy'))
            elif action in ('merge','split'):
                allowed=allowed_common|({'evidence_ids'} if action=='merge' else {'group_id'})
                if set(payload)-allowed:raise ValueError('unsupported dependency resolution fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                if action=='merge':
                    ids=payload.get('evidence_ids');attached={r.id for r in self.db.evidence_for(rid)}
                    if not isinstance(ids,list) or len(ids)<2 or any(x not in attached for x in ids):raise ValueError('attached evidence ids required')
                    next_state['hard_groups'][event_id]=ids
                else:
                    group=payload.get('group_id')
                    if group not in next_state['hard_groups']:raise ValueError('explicit merge group required; correct raw lineage separately')
                    del next_state['hard_groups'][group]
            else:raise ValueError('unsupported epistemic action')
            next_state=call('ella_state',next_state)
            response=self.project(rid,next_state,revision)
            response.update(event_id=event_id)
            event=dict(kind=KIND,id=event_id,namespace=self.namespace,revision=revision,actor=actor,request_id=request_id,
                action=action,payload=deepcopy(payload),created_at=time.time(),fingerprint=fingerprint,
                observation=dict(operation='epistemic_'+action,memory_ids=[rid],session_id=session,epistemic=response),
                previous=events[-1]['seal'] if events else None,state=next_state,response=response)
            event['seal']=digest(event)
            self.db._writer.write(EmberRecord(id=event_id,namespace=self.namespace,record_type=RecordType.RAW,
                data=event,written_by=actor,agent_id=actor,retrieval_candidate=False,training_candidate=False))
            # Durable source first; delivery is recoverable from this source at startup.
            from ..integration.observation_journal import initialize,append
            from ..integration.observation_stream import committed
            try:
                initialize(self.db)
                append(self.db,event,'ella:'+event_id,source_revision=revision)
                committed(self.db,self.namespace)
            except Exception:
                import logging
                logging.getLogger('embers').exception('ELLA committed; observation pending recovery')
            return deepcopy(response)

    @staticmethod
    def _owned(a,rid,actor,admin):
        if a is None or a['target_memory_id']!=rid:raise ValueError('target assessment not found')
        if a['assessor_id']!=actor and not admin:raise PermissionError('assessment owner or decision authority required')
