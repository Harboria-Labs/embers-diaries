"""Authorized adapters and bounded read-only snapshots for usefulness research."""
import json
import logging
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from ..cognitive.usefulness import UsefulnessLedger, UsefulnessPolicy, canonical, digest


def enable(db, admins):
    path = os.environ.get('EMBER_USEFULNESS_CONFIG')
    policy = UsefulnessPolicy(**json.loads(Path(path).read_text())) if path else UsefulnessPolicy()
    db._usefulness_policy = policy
    db._usefulness_admins = frozenset(admins)
    db._usefulness_enabled = True


def service(db, namespace):
    if not hasattr(db, '_usefulness_services'):
        db._usefulness_services = {}
    if namespace not in db._usefulness_services:
        db._usefulness_services[namespace] = UsefulnessLedger(db, namespace,
            admins=getattr(db, '_usefulness_admins', ()),
            policy=getattr(db, '_usefulness_policy', UsefulnessPolicy()))
    return db._usefulness_services[namespace]


def update(db, namespace, actor, body, session_id=None):
    if not isinstance(body, dict) or set(body) - {'action','payload','request_id','expected_revision'}:
        raise ValueError('invalid usefulness command')
    if session_id and body.get('action') == 'report' and isinstance(body.get('payload'), dict):
        body = {**body, 'payload': {'session_id': session_id, **body['payload']}}
    return service(db, namespace).apply(body.get('action'), body.get('payload'), actor=actor,
        request_id=body.get('request_id'), expected_revision=body.get('expected_revision'))


def record_request(db, namespace, actor, operation, args, result, started):
    if not getattr(db, '_usefulness_enabled', False):
        return
    try:
        fields = {k: args[k] for k in ('query','clues','primary_context','context_id','hints','session_id','query_id','subject') if k in args}
        # Metadata is bounded; credentials and arbitrary tool bodies are never copied.
        query = str(fields.get('query', fields.get('clues', '')))[:2048]
        candidates, returned, reasons, heat = [], [], {}, {}
        if operation == 'ember_orient':
            candidates, returned = result.get('candidate_ids', []), result.get('returned_ids', [])
            for subject in result.get('territory', []):
                for context in subject['contexts']:
                    for row in context['memories']:
                        reasons[row['id']] = {'signals': row['signals'], 'relationships': row['relationships']}
        elif operation == 'ember_candidate_recall':
            returned = result.get('selected_ids', [])
            if result.get('format') == 'structured':
                rows = json.loads(result['context'])
                heat = {r['id']: r.get('activation') for r in rows}
            # Candidate IDs not returned by this older surface are not guessed.
        elif isinstance(result, dict):
            candidates = [r['id'] for r in result.get('candidates', [])]
            returned = [r['id'] for r in result.get('results', [])]
        elif isinstance(result, list):
            returned = [r['id'] for r in result if isinstance(r,dict) and 'id' in r]
        session = db.get_session(fields['session_id']) if fields.get('session_id') else None
        task = session.task if session and session.agent_id == actor and session.namespace == namespace else None
        observation = {'operation': operation, 'query': query,
            'context': fields.get('primary_context', fields.get('context_id')),
            'session_id': fields.get('session_id'), 'hints': fields.get('hints'), 'subject': fields.get('subject'),
            'task': task, 'goal': None, 'constraints': None,
            'candidate_ids': candidates[:100], 'returned_ids': returned[:100],
            'reasons': {k: v for k,v in list(reasons.items())[:100]},
            'observed_heat': heat, 'heat_source': 'legacy Candidate 04 receipt' if heat else None,
            'budget': {k: result[k] for k in ('bounds','token_count','response_bytes','edges_examined') if isinstance(result,dict) and k in result},
            'latency_ms': round((time.monotonic()-started)*1000,3),
            'stage_timing': 'completion snapshot; internal stage timestamps unavailable'}
        if len(canonical(observation).encode()) > 32768:
            observation['reasons'] = {}
            observation['reasons_truncated'] = True
        service(db, namespace).observe(observation, actor=actor,
            request_id=str(fields.get('query_id') or uuid.uuid4()))
    except Exception as error:
        logging.getLogger('embers').warning('Request succeeded but observability recording failed: %s', error)


def snapshot(db, namespace, actor, *, after=0, limit=100, request_id=None, session_id=None):
    # A snapshot and its event cursor describe one committed journal prefix.
    with db._writer.lock:
        return _snapshot(db, namespace, actor, after=after, limit=limit,
                         request_id=request_id, session_id=session_id)


def _snapshot(db, namespace, actor, *, after=0, limit=100, request_id=None, session_id=None):
    db.require_namespace_access(namespace, actor, 'read')
    ledger = service(db, namespace)
    state = ledger.read(actor=actor)
    events = ledger.events(actor=actor, after=after, limit=limit, request_id=request_id, session_id=session_id)
    experiences = [e for e in state['experiences'] if e['active']]
    # Include only namespace records and edges whose endpoints were authorized.
    ids = []
    for exp in experiences:
        ids.extend(exp['target']['memory_ids'])
    for event in events:
        ids.extend(event.get('observation', {}).get('candidate_ids', []))
        ids.extend(event.get('observation', {}).get('returned_ids', []))
    ids = list(dict.fromkeys(ids))[:100]
    nodes = []
    for rid in ids:
        rec = db._reader.get(rid, track_access=False)
        if rec is None or rec.namespace != namespace or rec.record_type not in db._DURABLE_MEMORY_TYPES:
            continue
        data = rec.data if isinstance(rec.data, dict) else {}
        observed = next((e['observation'] for e in reversed(events) if rid in e.get('observation',{}).get('returned_ids',[])
                         or rid in e.get('observation',{}).get('candidate_ids',[])), {})
        reports = [r for r in state['reports'] if r['target']['kind'] == 'memory' and r['target']['memory_ids'] == [rid]]
        nodes.append({'id': rid, 'subject': data.get('subject'), 'primary_context': data.get('primary_context'),
            'preview': str(data.get('content',''))[:500], 'verify_status': data.get('verify_status'),
            'usefulness': [s for s in state['states'] if s['target']['kind']=='memory' and s['target']['memory_ids']==[rid]][:30],
            'heat': observed.get('observed_heat',{}).get(rid), 'heat_source': observed.get('heat_source'),
            'active': None, 'latent': None, 'reactivated': None,
            'candidate': rid in observed.get('candidate_ids',[]), 'returned': rid in observed.get('returned_ids',[]),
            'candidate_position': observed.get('candidate_ids',[]).index(rid) if rid in observed.get('candidate_ids',[]) else None,
            'used': any(r['feedback_type']=='CONTRIBUTED' for r in reports),
            'unused': any(r['feedback_type']=='UNUSED' for r in reports),
            'observation_scope': 'candidate/return from latest matching displayed request; used/unused are historical individual reports',
            'why_retrieved': observed.get('reasons',{}).get(rid)})
    visible = {n['id'] for n in nodes}
    edges = []
    for rid in visible:
        if len(edges) >= 100: break
        for edge in db._graph_index.get_edges(rid, direction='outgoing'):
            if len(edges) >= 100: break
            if edge['target'] in visible:
                edges.append({'from':rid, 'to':edge['target'], 'type':str(edge.get('edge_type'))[:128],
                              'kind':'stored_relation', 'traversed':None, 'used':None})
    for s in state['states']:
        t = s['target']
        if t['kind']=='pair' and all(r in visible for r in t['memory_ids']) and len(edges)<200:
            edges.append({'from':t['memory_ids'][0], 'to':t['memory_ids'][1], 'type':t['relation'],
                          'kind':'pair_evidence', 'context':s['context'], 'W':s['value'], 'N_eff':s['N_eff'],
                          'received_feedback':True, 'traversed':None, 'used':None})
    reports = state['reports']
    unresolved = sum(e['resolution_status']=='unresolved' for e in experiences)
    duplicate_count = sum(max(0,len(e['reports'])-1) for e in experiences)
    flags = []
    if unresolved: flags.append({'kind':'unresolved_pileup', 'count':unresolved, 'assessment':'count only; no calibrated threshold'})
    if duplicate_count: flags.append({'kind':'repetitive_reports', 'count':duplicate_count, 'assessment':'collapsed; not evidence of extra reinforcement'})
    structural = [e for e in experiences if e['identity_mode']=='structural']
    if structural: flags.append({'kind':'identity_observability_limit', 'count':len(structural)})
    unused = sum(r['feedback_type']=='UNUSED' for r in reports)
    if unused: flags.append({'kind':'retrieval_waste_signal', 'unused_reports':unused, 'assessment':'telemetry only; no penalty'})
    keys = [(canonical(e['target']),e['context'],canonical(key)) for e in experiences for key in e.get('identity_aliases',[e.get('identity_key')]) if key]
    if len(keys)!=len(set(keys)): flags.append({'kind':'identity_fragmentation','severity':'warning'})
    for event in events:
        if event.get('truth_before') != event.get('truth_after'):
            flags.append({'kind':'truth_leakage','severity':'SEVERE','event':event['id']})
        if event.get('action') == 'report':
            for delta in event.get('transitions', []):
                if delta['key'][1] != event['report']['context']:
                    flags.append({'kind':'context_leakage','severity':'SEVERE','event':event['id']})
                before, after_state = delta['before'], delta['after']
                if before and after_state and after_state['value'] > before['value'] and after_state['N_eff'] <= before['N_eff']:
                    flags.append({'kind':'reinforcement_without_added_mass','event':event['id'],
                                  'assessment':'inspect correction/conflict resolution; not automatically an error'})
    repetitive = {canonical([e['target'],e['context']]) for e in experiences if len(e['reports'])>1}
    growth = {}
    for event in events:
        for delta in event.get('transitions', []):
            key = canonical(delta['key'])
            b,a = delta['before'],delta['after']
            if key in repetitive and b and a:
                row = growth.setdefault(key,{'gain':0.,'start':event['created_at'],'end':event['created_at']})
                row['gain'] += a['value']-b['value']; row['end']=event['created_at']
    for key,row in growth.items():
        if row['gain']>0:
            flags.append({'kind':'repetitive_growth_review','target_context':json.loads(key),
                          'gain':row['gain'],'elapsed_seconds':max(0,row['end']-row['start']),
                          'assessment':'raw growth over this page with repeated reports; no calibrated rapid-growth threshold'})
    # Full audit events remain in the ledger. Bound nested lists in the UI page,
    # retaining counts and IDs so large duplicate clusters never block pagination.
    def compact_experience(exp):
        return {**exp, 'report_count':len(exp['reports']), 'reports':exp['reports'][:20],
                'reports_truncated':len(exp['reports'])>20}
    for event in events:
        event['experiences'] = [compact_experience(e) for e in event.get('experiences', [])][:20]
        event['transitions_count'] = len(event.get('transitions', []))
        event['transitions'] = event.get('transitions', [])[:20]
        for delta in event['transitions']:
            for side in ('before','after'):
                if delta[side]:
                    delta[side]['experience_count'] = len(delta[side]['experiences'])
                    delta[side]['experiences'] = delta[side]['experiences'][:20]
    output = {'namespace':namespace, 'revision':state['revision'], 'policy':state['policy'],
        'nodes':nodes, 'edges':edges, 'states':state['states'][:100],
        'experiences':[compact_experience(e) for e in experiences[:100]], 'reports':reports[:200], 'events':events,
        'next_after': after+len(events), 'diagnostics':{
            'active_memories':None,'latent_memories':None,'reactivations':None,
            'candidate_count':sum(len(e.get('observation',{}).get('candidate_ids',[])) for e in events),
            'returned_count':sum(len(e.get('observation',{}).get('returned_ids',[])) for e in events),
            'experience_count':len(experiences), 'resolved':len(experiences)-unresolved,'unresolved':unresolved,
            'duplicate_reports_collapsed':duplicate_count,
            'unclustered_reports':sum(len(e['reports'])==1 and e['identity_mode']=='structural' for e in experiences),
            'group_outcomes':sum(e['target']['kind']=='group' for e in experiences),
            'pair_edges':sum(s['metric']=='W' for s in state['states']),
            'pair_expansions':None, 'context_budgets':[e['observation']['budget'] for e in events if 'observation' in e],
            'latency_ms':[e['observation']['latency_ms'] for e in events if 'observation' in e],
            'pair_feedback_events':sum(r['target']['kind']=='pair' for r in reports),
            'U_distribution':[s['value'] for s in state['states'] if s['metric']=='U'][:100],
            'N_eff_distribution':[s['N_eff'] for s in state['states']][:100],
            'storage':{'ledger_canonical_json_bytes':sum(len(canonical(e).encode()) for e in ledger._events),
                       'physical_bytes':None,'global_quota':'enforced by native writer; global values withheld'},
            'scope':'counts namespace-wide except candidate/returned counts from this event page'},
        'flags':flags, 'pipeline':{
            'query':'observed when recorded', 'context':'exact agent supplied',
            'direct_candidates':'observed when exposed', 'LADC_reactivation':'NOT ACTIVE in new usefulness model',
            'pair_expansion':'legacy orientation/recall only; no new W coupling',
            'judgment':'NOT ACTIVE', 'budget':'see request observations', 'return':'observed',
            'agent_feedback':'active', 'evidence_structure':'active', 'state_update':'derived U/W; no heat/truth write'},
        'limits':{'nodes':100,'edges':200,'experiences':100,'reports':200,'events':limit,'bytes':262144},
        'truncated': len(ids)>=100 or len(experiences)>100 or len(reports)>200 or len(state['states'])>100}
    while len(canonical(output).encode())>258048:
        output['truncated']=True
        for key in ('events','reports','experiences','states','nodes','edges'):
            if output[key]: output[key].pop();break
        else: raise ValueError('snapshot metadata exceeds budget')
    output['next_after'] = output['events'][-1]['revision'] if output['events'] else state['revision']
    if events and not output['events']:
        event = events[0]
        output['events'] = [{k:event[k] for k in ('id','action','revision','created_at','request_id','namespace','actor')}]
        output['events'][0]['details_omitted'] = 'event exceeded snapshot budget; use ledger events SDK'
        output['next_after'] = event['revision']
    return output
