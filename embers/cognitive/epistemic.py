"""ELLA persistence/authentication. All epistemic reduction is native Rust.

Exact memory ID + immutable content hash is the claim version. Observation never
writes this journal. Evidence is referenced by its stored record ID.
"""
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import time
import uuid
from ..core.domain import call
from ..core.record import EmberRecord
from ..core.types import RecordType
from .usefulness import canonical, digest, identifier
from ..storage.format import decode_index

KIND='ember.epistemic.v1'
_CACHE_LIMIT=16
_CACHE_LOCK=threading.RLock()
_VERIFIED: OrderedDict[tuple[str,str], '_VerifiedState']=OrderedDict()
_METRICS={'cold_replays':0,'warm_reuses':0,'incremental_events':0,
          'head_reads':0,'invalidations':0}

@dataclass
class _VerifiedState:
    state: dict
    revision: int
    seal: str | None
    head_hash: str | None
    epoch: int
    retries: dict
    store_identity: tuple[int,int]
    audit_boundary: bytes

def clear_verified_state(store_path=None, namespace=None):
    """Discard disposable projections, including after external diagnostics."""
    with _CACHE_LOCK:
        if store_path is None:
            _METRICS['invalidations']+=len(_VERIFIED)
            _VERIFIED.clear()
            return
        path=os.path.normcase(str(Path(store_path).resolve()))
        for key in list(_VERIFIED):
            if key[0]==path and (namespace is None or key[1]==namespace):
                _METRICS['invalidations']+=1
                del _VERIFIED[key]

def verified_state_stats():
    """Small diagnostic view; no retained journal events or responses."""
    with _CACHE_LOCK:
        return {'projections':len(_VERIFIED), 'limit':_CACHE_LIMIT,
                'revisions':{str(key):value.revision for key,value in _VERIFIED.items()},
                'retry_identities':sum(len(value.retries) for value in _VERIFIED.values()),
                'state_bytes':sum(len(canonical(value.state).encode()) for value in _VERIFIED.values()),
                **_METRICS}

def enable_epistemic(db, admins):
    """Configure ELLA decision authority independently of usefulness/FUR."""
    db._epistemic_admins=frozenset(admins)

class EpistemicLedger:
    def __init__(self, db, namespace):
        self.db,self.namespace=db,namespace
        self.prefix='epistemic-'+uuid.uuid5(uuid.NAMESPACE_URL,KIND+namespace).hex+'-'

    def _cache_key(self):
        return os.path.normcase(str(self.db._store.root.resolve())),self.namespace

    def _source_identity(self):
        stat=self.db._store.root.stat()
        return stat.st_dev,stat.st_ino

    def _audit_boundary(self):
        path=self.db._store.meta_dir/'integrity_audits'/'active.json'
        if not path.exists():return b''
        raw=path.read_bytes()
        marker=decode_index(raw)
        if not isinstance(marker,dict) or 'run_id' not in marker:
            raise ValueError('integrity audit boundary is invalid')
        return raw

    def _journal_event(self,rid,revision,previous):
        try:
            record=self.db._store.read_strict(rid)
        except Exception as exc:
            raise ValueError('epistemic journal integrity failure') from exc
        if (rid!=self.prefix+f'{revision:012d}' or record.namespace!=self.namespace or
                record.record_type!=RecordType.RAW or record.retrieval_candidate or
                record.content_hash is None):
            raise ValueError('epistemic journal integrity failure')
        event=record.data
        if (not isinstance(event,dict) or event.get('id')!=rid or
                event.get('revision')!=revision or event.get('previous')!=previous or
                digest({k:v for k,v in event.items() if k!='seal'})!=event.get('seal')):
            raise ValueError('epistemic journal chain failure')
        return event,record.content_hash

    def _current(self):
        """Verified in-process state; the journal remains the replay authority."""
        with self.db._writer.lock:
            key=self._cache_key()
            identity=self._source_identity()
            boundary=self._audit_boundary()
            epoch=self.db._store.index_epoch()
            with _CACHE_LOCK:
                cached=_VERIFIED.get(key)
            if (cached is not None and cached.store_identity==identity and
                    cached.audit_boundary==boundary and
                    not (boundary and decode_index(boundary).get('run_id'))):
                try:
                    if cached.revision:
                        head_id=self.prefix+f'{cached.revision:012d}'
                        head=self.db._store.read_strict(head_id)
                        with _CACHE_LOCK:_METRICS['head_reads']+=1
                        if (head.namespace!=self.namespace or head.record_type!=RecordType.RAW or
                                head.retrieval_candidate or head.content_hash!=cached.head_hash or
                                head.data.get('seal')!=cached.seal or
                                head.data.get('state')!=cached.state):
                            raise ValueError('epistemic cached head differs from source')
                    if epoch!=cached.epoch:
                        ids=sorted(rid for rid in self.db._store.all_ids() if rid.startswith(self.prefix))
                        if len(ids)<cached.revision or any(
                                rid!=self.prefix+f'{i:012d}' for i,rid in enumerate(ids,1)):
                            raise ValueError('epistemic journal sequence changed')
                        state=cached.state;seal=cached.seal;head_hash=cached.head_hash
                        retries=dict(cached.retries)
                        for revision,rid in enumerate(ids[cached.revision:],cached.revision+1):
                            event,head_hash=self._journal_event(rid,revision,seal)
                            state=call('ella_reduce',{'state':state,'event':event})
                            seal=event['seal']
                            retries[(event['actor'],event['request_id'])]=(event['fingerprint'],rid)
                            with _CACHE_LOCK:_METRICS['incremental_events']+=1
                        cached=_VerifiedState(state,len(ids),seal,head_hash,epoch,retries,identity,boundary)
                    with _CACHE_LOCK:
                        _VERIFIED[key]=cached;_VERIFIED.move_to_end(key)
                        _METRICS['warm_reuses']+=1
                    return cached
                except Exception:
                    clear_verified_state(self.db._store.root,self.namespace)
                    # Replay gives the authoritative result or raises on corrupt source.
            state,events=self.load()
            with _CACHE_LOCK:_METRICS['cold_replays']+=1
            retries={(event['actor'],event['request_id']):(event['fingerprint'],event['id'])
                     for event in events}
            revision=len(events)
            head_hash=(self.db._store.read_strict(events[-1]['id']).content_hash
                       if events else None)
            cached=_VerifiedState(state,revision,events[-1]['seal'] if events else None,
                                  head_hash,epoch,retries,identity,boundary)
            if not (boundary and decode_index(boundary).get('run_id')):
                with _CACHE_LOCK:
                    _VERIFIED[key]=cached;_VERIFIED.move_to_end(key)
                    while len(_VERIFIED)>_CACHE_LIMIT:_VERIFIED.popitem(last=False)
            return cached

    def _publish(self,state,event):
        """Advance only after the immutable journal write has returned."""
        key=self._cache_key()
        with _CACHE_LOCK:
            prior=_VERIFIED.get(key)
        if prior is None:return
        retries=dict(prior.retries)
        retries[(event['actor'],event['request_id'])]=(event['fingerprint'],event['id'])
        head=self.db._store.read_strict(event['id'])
        updated=_VerifiedState(state,event['revision'],event['seal'],head.content_hash,
                               self.db._store.index_epoch(),retries,
                               self._source_identity(),self._audit_boundary())
        with _CACHE_LOCK:
            _VERIFIED[key]=updated;_VERIFIED.move_to_end(key)
            while len(_VERIFIED)>_CACHE_LIMIT:_VERIFIED.popitem(last=False)

    def load(self):
        """Authoritative full replay, retained for recovery and verification."""
        with self.db._writer.lock:
            try:
                events=[]
                for rid in sorted(x for x in self.db._store.all_ids() if x.startswith(self.prefix)):
                    event,_=self._journal_event(rid,len(events)+1,
                                                events[-1]['seal'] if events else None)
                    events.append(event)
                state=dict(assessments={},evidence_overrides={},hard_groups={},
                           policy=call('ella_policy_default',None),carried={})
                for event in events:
                    state=call('ella_reduce',{'state':state,'event':event})
                with _CACHE_LOCK:
                    cached=_VERIFIED.get(self._cache_key())
                if cached is not None and (cached.revision!=len(events) or cached.state!=state):
                    clear_verified_state(self.db._store.root,self.namespace)
                return state,events
            except Exception:
                clear_verified_state(self.db._store.root,self.namespace)
                raise

    def target(self,rid):
        rec=self.db._store.read(rid)
        if rec is None or rec.namespace!=self.namespace or rec.record_type not in self.db._DURABLE_MEMORY_TYPES:
            raise ValueError('exact target must be a durable memory in this namespace')
        if not rec.verify_integrity():raise ValueError('target integrity failure')
        return rec

    def project(self,rid,state=None,revision=None):
        target=self.target(rid)
        if state is None:
            current=self._current();state=current.state;revision=current.revision
        evidence=[]
        records={r.id:r for r in self.db.evidence_for(rid)}
        for eid in state.get('carried',{}).get(rid,[]):
            record=self.db._store.read(eid)
            if record is None or record.namespace!=self.namespace:raise ValueError('carried evidence unavailable')
            records[eid]=record
        for record in sorted(records.values(),key=lambda item:item.id):
            ev=self.db.get_evidence(record.id)
            if ev is None or not ev.verify_integrity():raise ValueError('evidence integrity failure')
            projected=ev.to_dict()
            if projected.get('origin') is None:
                projected['origin']='unknown'
                projected['origin_confidence']='UNKNOWN'
            evidence.append({**projected,'id':record.id,**state['evidence_overrides'].get(record.id,{})})
        conflicts=self.db.conflicts_for(rid)
        active=[c.status.value for c in conflicts if c.status.value in ('open','investigating')]
        result=call('ella_project',dict(policy=state['policy'],evidence=evidence,
            assessments=[state['assessments'][key] for key in sorted(state['assessments'])],hard_groups=[sorted(state['hard_groups'][key]) for key in sorted(state['hard_groups'])],
            target_memory_id=rid,target_memory_version=target.content_hash,revision=revision,
            conflict_overlay='open' if 'open' in active else 'investigating' if active else 'none'))
        active=[a for a in state['assessments'].values()
                if a['target_memory_id']==rid and a['target_memory_version']==target.content_hash
                and a['status']=='confirmation_required']
        by_id={a['assessment_id']:a for a in active}
        disagreement_groups=[]
        for child in active:
            parent=by_id.get(child.get('confirmation_of'))
            if parent is not None and parent['evidence_id']==child['evidence_id'] and parent['polarity']!=child['polarity']:
                disagreement_groups.append(sorted([parent['assessment_id'],child['assessment_id']]))
        disagreement_groups=sorted({tuple(group) for group in disagreement_groups})
        disputed_ids={aid for group in disagreement_groups for aid in group}
        pending=sorted((a for a in active if a.get('confirmation_of') is None and a['assessment_id'] not in disputed_ids),
                       key=lambda a:a['assessment_id'])
        result.update(confirmation_required=bool(pending),confirmation_assessment_ids=[a['assessment_id'] for a in pending],
            resolution_required=bool(disagreement_groups),resolution_assessment_groups=[list(group) for group in disagreement_groups],
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
            head=self._current();state=head.state
            retry=head.retries.get((actor,request_id))
            if retry is not None:
                if retry[0]!=fingerprint:raise ValueError('request id reused with different content')
                retry_revision=int(retry[1][-12:])
                previous=(None if retry_revision==1 else
                          self.db._store.read_strict(self.prefix+f'{retry_revision-1:012d}').data['seal'])
                event,_=self._journal_event(retry[1],retry_revision,previous)
                if event['fingerprint']!=fingerprint:
                    raise ValueError('epistemic retry source differs from verified identity')
                return deepcopy(event['response'])
            if expected_revision!=head.revision:raise ValueError('stale epistemic revision')
            rid=payload.get('target_memory_id');target=self.target(rid)
            if payload.get('target_memory_version')!=target.content_hash:raise ValueError('exact target content hash required')
            admin=actor in getattr(self.db,'_epistemic_admins',())
            available_evidence={r.id for r in self.db.evidence_for(rid)}|set(state.get('carried',{}).get(rid,[]))
            next_state=deepcopy(state);revision=head.revision+1;event_id=self.prefix+f'{revision:012d}'
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
                if payload.get('evidence_id') not in available_evidence:raise ValueError('evidence must be attached or explicitly carried to this exact memory version')
                evidence=self.db.get_evidence(payload['evidence_id'])
                if evidence is None or not evidence.verify_integrity():raise ValueError('evidence integrity failure')
                if next_state['evidence_overrides'].get(payload['evidence_id'],{}).get('invalidated'):raise ValueError('evidence invalidated')
                if action=='resolve':
                    if not admin:raise PermissionError('epistemic decision authority required')
                    chosen=payload.get('assessment_ids')
                    if not isinstance(chosen,list) or not chosen or len(set(chosen))!=len(chosen):raise ValueError('unique assessment_ids required')
                    priors=[]
                    for aid in chosen:
                        prior=next_state['assessments'].get(aid);self._owned(prior,rid,actor,True)
                        if prior['status'] not in ('accepted','confirmation_required'):
                            raise ValueError('only active assessments may be resolved')
                        if prior['target_memory_version']!=target.content_hash:
                            raise ValueError('resolution must stay on the exact claim version')
                        priors.append(prior)
                    if payload.get('evidence_id') not in {a['evidence_id'] for a in priors}:
                        raise ValueError('resolution evidence must belong to the resolved epistemic unit')
                    accepted=[a for a in priors if a['status']=='accepted']
                    if accepted:
                        current=self.project(rid,state,revision-1)
                        chosen_ids=set(chosen)
                        matched=any(u.get('status')=='unresolved' and set(u.get('assessment_ids',[]))==chosen_ids
                                    for u in current.get('units',[]))
                        if not matched:
                            raise ValueError('assessment resolution must cover one complete unresolved epistemic unit')
                    else:
                        # Threshold-confirmation disagreement is pending rather
                        # than score-bearing. Resolution must cover the complete
                        # opposed confirmation pair; no third-vote shortcut.
                        current=self.project(rid,state,revision-1)
                        chosen_ids=set(chosen)
                        if not any(chosen_ids==set(group) for group in current.get('resolution_assessment_groups',[])):
                            raise ValueError('pending resolution must cover one complete confirmation disagreement')
                    for prior in priors:prior['status']='resolved'
                if action=='revise':
                    prior=next_state['assessments'].get(payload.get('assessment_id'))
                    self._owned(prior,rid,actor,admin)
                    if prior['status'] not in ('accepted','confirmation_required'):
                        raise ValueError('only active assessments may be revised')
                    if prior['target_memory_version']!=target.content_hash or prior['evidence_id']!=payload.get('evidence_id'):
                        raise ValueError('revision must preserve exact claim version and evidence record')
                    linked=prior.get('confirmation_of') is not None or any(
                        a.get('confirmation_of')==prior['assessment_id']
                        and a['status'] not in ('withdrawn','superseded','resolved')
                        for a in next_state['assessments'].values())
                    if linked:
                        raise ValueError('confirmation-linked assessments require withdrawal/resolution, not revision')
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
                    before=self.project(rid,state,revision-1);after=self.project(rid,next_state,revision)
                    decision=call('ella_crossing',{'before':before,'after':after})
                    if decision['confirmation_required']:
                        assessment['status']='confirmation_required'
                        assessment['requires_confirmation']=True
            elif action=='withdraw':
                if set(payload)-(allowed_common|{'assessment_id'}):raise ValueError('unsupported withdrawal fields')
                a=next_state['assessments'].get(payload.get('assessment_id'));self._owned(a,rid,actor,admin)
                if a['status'] not in ('accepted','confirmation_required'):raise ValueError('only active assessments may be withdrawn')
                if any(child.get('confirmation_of')==a['assessment_id']
                       and child['status'] not in ('withdrawn','superseded','resolved')
                       for child in next_state['assessments'].values()):
                    raise ValueError('withdraw active confirmation child before withdrawing its parent')
                parent_id=a.get('confirmation_of')
                a['status']='withdrawn'
                if parent_id:
                    parent=next_state['assessments'].get(parent_id)
                    if (parent is not None and parent.get('requires_confirmation') is True
                            and parent['status']=='accepted'):
                        parent['status']='confirmation_required'
            elif action=='invalidate_evidence':
                if set(payload)-(allowed_common|{'evidence_id'}):raise ValueError('unsupported evidence invalidation fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                if payload.get('evidence_id') not in available_evidence:raise ValueError('available evidence required')
                next_state['evidence_overrides'].setdefault(payload['evidence_id'],{})['invalidated']=True
            elif action=='correct_evidence':
                if set(payload)-(allowed_common|{'evidence_id','lineage'}):raise ValueError('unsupported evidence correction fields')
                if not admin:raise PermissionError('epistemic decision authority required')
                eid=payload.get('evidence_id')
                if eid not in available_evidence:raise ValueError('available evidence required')
                changes=payload.get('lineage')
                if not isinstance(changes,dict) or set(changes)-{'reference','event_id','request_id','origin','derived_from','origin_confidence'}:
                    raise ValueError('explicit lineage correction required')
                for field in ('reference','event_id','request_id','origin'):
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
                    ids=payload.get('evidence_ids')
                    if not isinstance(ids,list) or len(ids)<2 or any(x not in available_evidence for x in ids):raise ValueError('available evidence ids required')
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
                previous=head.seal,state=next_state,response=response)
            # The Rust reducer owns journal transition validation both at commit
            # time and replay time. Python supplies authenticated IO context but
            # cannot persist a state mutation the native reducer rejects.
            reduced=call('ella_reduce',{'state':state,'event':event})
            if reduced!=next_state:raise RuntimeError('native ELLA reducer returned a different state')
            event['seal']=digest(event)
            self.db._writer.write(EmberRecord(id=event_id,namespace=self.namespace,record_type=RecordType.RAW,
                data=event,written_by=actor,agent_id=actor,retrieval_candidate=False,training_candidate=False))
            try:self._publish(next_state,event)
            except Exception:
                clear_verified_state(self.db._store.root,self.namespace)
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
