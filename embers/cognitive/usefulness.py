"""Evidence-derived contextual usefulness. Append-only RAW ledger; no truth/heat writes."""
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import hashlib
import json
import math
import time
import uuid

from ..core.record import EmberRecord
from ..core.types import RecordType

KIND = 'ember.usefulness-evidence.v1'
TYPES = {'memory': {'CONTRIBUTED', 'IRRELEVANT', 'MISLEADING', 'UNUSED'},
         'pair': {'PAIR_HELPED', 'PAIR_IRRELEVANT', 'UNUSED'},
         'group': {'GROUP_SUCCESS', 'GROUP_FAILURE', 'UNUSED'}}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value, name):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 512:
        raise ValueError(f'{name} must be nonempty text of at most 512 bytes')
    return value


@dataclass(frozen=True)
class UsefulnessPolicy:
    kappa_u: float = 4.0
    u0: float = .5
    epsilon: float = 0.0
    u_max: float = 1.0
    kappa_w: float = 4.0
    w0: float = .5
    severity: dict = field(default_factory=lambda: {'contributed': 1., 'irrelevant': 1., 'misleading': 2.})
    cluster: dict = field(default_factory=lambda: {'session_window': 1800., 'time_window': 60.})

    def __post_init__(self):
        if set(self.severity) != {'contributed', 'irrelevant', 'misleading'} or set(self.cluster) != {'session_window', 'time_window'}:
            raise ValueError('invalid severity/cluster keys')
        for value in [self.kappa_u, self.u0, self.epsilon, self.u_max, self.kappa_w, self.w0, *self.severity.values(), *self.cluster.values()]:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('policy values must be finite numbers')
        if not 0 <= self.u0 <= 1 or not 0 <= self.w0 <= 1 or not 0 <= self.epsilon < self.u_max <= 1:
            raise ValueError('invalid prior or floor/ceiling')
        if min(self.kappa_u, self.kappa_w, *self.severity.values()) <= 0:
            raise ValueError('prior strengths and severities must be positive')
        if self.severity['misleading'] <= self.severity['irrelevant']:
            raise ValueError('misleading severity must exceed irrelevant severity')
        if any(not 0 <= v <= 86400 for v in self.cluster.values()):
            raise ValueError('cluster windows are seconds in [0,86400]')


def target_key(target, context):
    return canonical([target, context])


def derive(experiences, policy):
    p = UsefulnessPolicy(**policy)
    states = {}
    for exp in experiences.values():
        if not exp['active'] or exp['target']['kind'] == 'group':
            continue
        key = target_key(exp['target'], exp['context'])
        state = states.setdefault(key, {'target': exp['target'], 'context': exp['context'],
                                       'positive_mass': 0., 'N_eff': 0., 'experiences': []})
        typ = exp.get('resolved_feedback_type')
        weight = 0.
        if exp['resolution_status'] == 'accepted' and typ != 'UNUSED':
            positive = typ in ('CONTRIBUTED', 'PAIR_HELPED')
            weight = p.severity['contributed' if positive else 'misleading' if typ == 'MISLEADING' else 'irrelevant']
            state['positive_mass'] += weight if positive else 0.
            state['N_eff'] += weight
        state['experiences'].append({'id': exp['id'], 'effective_weight': weight,
                                    'resolution_status': exp['resolution_status'], 'feedback_type': typ})
    for state in states.values():
        pair = state['target']['kind'] == 'pair'
        kappa, prior = (p.kappa_w, p.w0) if pair else (p.kappa_u, p.u0)
        if not math.isfinite(kappa + state['N_eff']) or not math.isfinite(state['positive_mass']):
            raise ValueError('evidence mass exceeds finite numeric range')
        fraction = (kappa * prior + state['positive_mass']) / (kappa + state['N_eff'])
        state['value'] = fraction if pair else p.epsilon + (p.u_max - p.epsilon) * fraction
        state['metric'] = 'W' if pair else 'U'
    return states


def consensus(exp, reports):
    types = {reports[r]['feedback_type'] for r in exp['reports']} - {'UNUSED'}
    if len(types) > 1:
        exp.update(resolution_status='unresolved', resolved_feedback_type=None)
    else:
        exp.update(resolution_status='accepted', resolved_feedback_type=next(iter(types), 'UNUSED'))


class UsefulnessLedger:
    """Namespace journal with immutable operations, replay and exact context keys.

    Callers pass authenticated actors. Runtime admins can attest identities and
    resolve/correct/merge/split/configure; ordinary writers can submit reports.
    """
    def __init__(self, db, namespace, *, admins=(), policy=None, clock=None):
        self.db, self.namespace = db, identifier(namespace, 'namespace')
        self.admins = frozenset(admins)
        self.initial_policy = asdict(policy or UsefulnessPolicy())
        self.clock = clock or time.time
        self.prefix = 'usefulness-' + uuid.uuid5(uuid.NAMESPACE_URL, KIND + namespace).hex + '-'
        self._events = []
        self._state = {'reports': {}, 'experiences': {}, 'policy': self.initial_policy}

    def _load(self):
        ids = sorted(rid for rid in self.db._store.all_ids() if rid.startswith(self.prefix))
        if len(ids) < len(self._events):
            raise ValueError('usefulness history was removed')
        for index, rid in enumerate(ids):
            if rid != self.prefix + f'{index + 1:012d}':
                raise ValueError('usefulness history sequence is broken')
        for rid in ids[len(self._events):]:
            record = self.db._store.read(rid)
            if (record is None or record.namespace != self.namespace or record.record_type != RecordType.RAW
                    or not record.verify_integrity() or record.retrieval_candidate):
                raise ValueError('usefulness journal integrity failure')
            event = record.data
            previous = self._events[-1]['seal'] if self._events else None
            if event['previous'] != previous or digest({k:v for k,v in event.items() if k != 'seal'}) != event['seal']:
                raise ValueError('usefulness event chain is broken')
            self._reduce(self._state, event)
            self._events.append(event)
        return self._state

    @staticmethod
    def _reduce(state, event):
        # Events contain validated replacements, not an executable user command.
        if 'policy' in event:
            state['policy'] = event['policy']
        if 'report' in event:
            state['reports'][event['report']['id']] = event['report']
        for exp in event.get('experiences', []):
            state['experiences'][exp['id']] = exp

    def _target(self, value):
        if not isinstance(value, dict) or set(value) - {'kind', 'memory_ids', 'relation'}:
            raise ValueError('target requires kind, memory_ids and optional pair relation')
        kind, ids = value.get('kind'), value.get('memory_ids')
        if kind not in TYPES or not isinstance(ids, list) or not ids or len(ids) > 32 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('invalid target')
        if (kind == 'memory' and len(ids) != 1) or (kind == 'pair' and len(ids) != 2):
            raise ValueError('invalid target cardinality')
        for rid in ids:
            identifier(rid, 'memory ID')
            record = self.db._reader.get(rid, track_access=False)
            if record is None or record.namespace != self.namespace or record.record_type not in self.db._DURABLE_MEMORY_TYPES:
                raise ValueError('target must be current durable memory in this namespace')
        relation = value.get('relation')
        if kind == 'pair' and relation not in ('explains', 'requires', 'warns_about', 'alternative'):
            raise ValueError('pair requires a supported directional type')
        if kind != 'pair' and relation is not None:
            raise ValueError('only pair targets carry a relation')
        return {'kind': kind, 'memory_ids': sorted(ids) if kind == 'group' else list(ids), 'relation': relation}

    def _truth(self, ids):
        result = {}
        for rid in ids:
            rec = self.db._reader.get(rid, track_access=False)
            if rec is not None and rec.namespace == self.namespace:
                data = rec.data if isinstance(rec.data, dict) else {}
                result[rid] = {'content_hash': rec.content_hash,
                    'verify_status': data.get('verify_status'),
                    'epistemic_digest': digest({k:data.get(k) for k in ('verify_status','epistemic_state','correctness','truth')}),
                    'annotations_digest': digest([a.to_dict() for a in rec.annotations])}
        return result

    def apply(self, action, payload, *, actor, request_id, expected_revision=None):
        self.db.require_namespace_access(self.namespace, actor, 'write')
        if action not in ('report','resolve','merge','split','configure'):
            raise ValueError('unsupported usefulness action')
        identifier(request_id, 'request_id')
        if not isinstance(payload, dict) or len(canonical(payload).encode()) > 16384:
            raise ValueError('payload must be an object of at most 16 KiB')
        if action != 'report' and actor not in self.admins:
            raise PermissionError('feedback decision requires a configured authorized agent')
        fingerprint = digest([actor, action, payload, expected_revision])
        with self.db._writer.lock:
            state = self._load()
            for event in self._events:
                if event['actor'] == actor and event['request_id'] == request_id:
                    if event['fingerprint'] != fingerprint:
                        raise ValueError('request_id reused with different input')
                    return deepcopy(event)
            if action != 'report' and (type(expected_revision) is not int or expected_revision != len(self._events)):
                raise ValueError('stale expected_revision')
            working = deepcopy(state)
            now = float(self.clock())
            changed, report = [], None
            event_id = self.prefix + f'{len(self._events) + 1:012d}'
            if action == 'report':
                allowed = {'target','context','feedback_type','session_id','query_request_id','identity','identity_verified','note','experience_id'}
                if set(payload) - allowed:
                    raise ValueError('unsupported report fields')
                target = self._target(payload.get('target'))
                context = payload.get('context')
                if context is not None:
                    identifier(context, 'context')
                typ = payload.get('feedback_type')
                if typ not in TYPES[target['kind']]:
                    raise ValueError('feedback type does not match target')
                session = payload.get('session_id')
                if session is not None:
                    identifier(session, 'session_id')
                    actual = self.db.get_session(session)
                    if actual is None or actual.agent_id != actor or actual.status.value != 'active':
                        raise PermissionError('report session must be active and belong to caller')
                identity = payload.get('identity')
                verified = payload.get('identity_verified', False)
                if type(verified) is not bool:
                    raise ValueError('identity_verified must be boolean')
                if identity is not None:
                    if not isinstance(identity, dict) or set(identity) != {'value','source','provenance'}:
                        raise ValueError('identity needs value, source and provenance')
                    for key, val in identity.items(): identifier(val, key)
                if verified and (actor not in self.admins or identity is None):
                    raise PermissionError('only configured decision agents may explicitly attest identity')
                report = {'id': event_id + ':report', 'target': target, 'context': context,
                          'feedback_type': typ, 'actor': actor, 'session_id': session,
                          'query_request_id': payload.get('query_request_id'), 'identity': identity,
                          'identity_verified_by': actor if verified else None,
                          'note': payload.get('note',''), 'created_at': now}
                candidates = [e for e in working['experiences'].values() if e['active'] and e['target'] == target and e['context'] == context]
                exp = None
                if payload.get('experience_id'):
                    exp = working['experiences'].get(payload['experience_id'])
                    if exp not in candidates:
                        raise ValueError('experience does not match target/context')
                elif verified:
                    matches = [e for e in candidates if [identity['source'], identity['value']] in e.get('identity_aliases', [e.get('identity_key')])]
                    if len(matches) > 1:
                        raise ValueError('identity was split; explicit experience_id required')
                    exp = matches[0] if matches else None
                else:
                    window = working['policy']['cluster']['session_window' if session else 'time_window']
                    exp = next((e for e in reversed(candidates) if e['identity_key'] is None
                        and e['cluster_actor'] == actor and e['cluster_session'] == session
                        and 0 <= now - e['created_at'] <= window and window > 0), None)
                if exp is None:
                    exp = {'id': event_id + ':experience', 'target': target, 'context': context,
                           'reports': [], 'active': True, 'created_at': now, 'updated_at': now,
                           'identity_key': [identity['source'], identity['value']] if verified else None,
                           'identity_provenance': identity if verified else None,
                           'identity_verified_by': actor if verified else None,
                           'cluster_actor': actor, 'cluster_session': session,
                           'identity_mode': 'attested' if verified else 'structural', 'manual_resolution': False}
                exp['reports'].append(report['id'])
                exp['updated_at'] = now
                working['reports'][report['id']] = report
                if typ == 'UNUSED' and exp.get('manual_resolution'):
                    pass  # Telemetry cannot undo an explicit correction.
                elif exp.get('manual_resolution'):
                    exp.update(resolution_status='unresolved', resolved_feedback_type=None, manual_resolution=False)
                else:
                    consensus(exp, working['reports'])
                changed.append(exp)
            elif action == 'configure':
                if set(payload) != {'policy', 'reason'}:
                    raise ValueError('configure requires policy and reason')
                identifier(payload['reason'], 'reason')
                working['policy'] = asdict(UsefulnessPolicy(**payload['policy']))
            else:
                identifier(payload.get('reason'), 'reason')
                if action == 'resolve':
                    if set(payload) != {'experience_id','feedback_type','status','reason'}:
                        raise ValueError('resolve requires experience_id, feedback_type, status, reason')
                    exp = working['experiences'].get(payload['experience_id'])
                    if exp is None or not exp['active']:
                        raise ValueError('active experience required')
                    if payload['status'] not in ('accepted','unresolved'):
                        raise ValueError('invalid resolution status')
                    typ = payload['feedback_type']
                    if (payload['status'] == 'accepted' and typ not in TYPES[exp['target']['kind']]) or (payload['status'] == 'unresolved' and typ is not None):
                        raise ValueError('invalid resolved type')
                    exp.update(resolution_status=payload['status'], resolved_feedback_type=typ,
                               manual_resolution=True, updated_at=now, resolution_reason=payload['reason'])
                    changed.append(exp)
                else:
                    if set(payload) != ({'experience_ids','reason'} if action == 'merge' else {'experience_id','partitions','reason'}):
                        raise ValueError('invalid merge/split fields')
                    old_ids = payload.get('experience_ids') if action == 'merge' else [payload['experience_id']]
                    if not isinstance(old_ids, list) or not old_ids or len(old_ids) > 100 or len(set(old_ids)) != len(old_ids):
                        raise ValueError('invalid experience list')
                    old = [working['experiences'].get(eid) for eid in old_ids]
                    if any(e is None or not e['active'] for e in old) or (action == 'merge' and len(old) < 2):
                        raise ValueError('active experiences required')
                    template = old[0]
                    if any(e['context'] != template['context'] or e['target'] != template['target'] for e in old):
                        raise ValueError('merge cannot cross target/context')
                    all_reports = [rid for e in old for rid in e['reports']]
                    partitions = [all_reports] if action == 'merge' else payload['partitions']
                    if not isinstance(partitions, list) or (action == 'split' and len(partitions) < 2) or any(not isinstance(p,list) or not p for p in partitions):
                        raise ValueError('nonempty report partitions required')
                    flattened = [r for partition in partitions for r in partition]
                    if sorted(flattened) != sorted(all_reports) or len(set(flattened)) != len(flattened):
                        raise ValueError('partitions must conserve every report exactly once')
                    for e in old:
                        e.update(active=False, updated_at=now, replaced_by=event_id)
                        changed.append(e)
                    for number, partition in enumerate(partitions):
                        exp = deepcopy(template)
                        exp.update(id=event_id + ':experience:' + str(number), reports=partition,
                                   active=True, created_at=now, updated_at=now, manual_resolution=False,
                                   identity_key=None, identity_aliases=[list(k) for k in sorted({tuple(k) for e in old for k in e.get('identity_aliases', [e.get('identity_key')]) if k})],
                                   identity_provenance={'decision': action, 'reason': payload['reason']},
                                   identity_verified_by=actor, identity_mode='manual_partition',
                                   cluster_actor=None, cluster_session=None, parents=old_ids)
                        exp.pop('replaced_by', None)
                        consensus(exp, working['reports'])
                        changed.append(exp)
            for exp in changed: working['experiences'][exp['id']] = exp
            ids = {rid for exp in changed for rid in exp['target']['memory_ids']}
            before, after = derive(state['experiences'], state['policy']), derive(working['experiences'], working['policy'])
            changed_keys = set(before) | set(after)
            transitions = []
            for key in sorted(changed_keys):
                if before.get(key) != after.get(key) or (report is not None and key == target_key(report['target'], report['context'])):
                    old = before.get(key)
                    if old is None:
                        template = after[key]
                        pol = UsefulnessPolicy(**state['policy'])
                        old = {'target':template['target'],'context':template['context'], 'metric':template['metric'],
                               'N_eff':0.,'positive_mass':0.,'experiences':[],
                               'value':pol.w0 if template['metric']=='W' else pol.epsilon+(pol.u_max-pol.epsilon)*pol.u0}
                    transitions.append({'key': json.loads(key), 'before': old, 'after': after.get(key)})
            truth = self._truth(ids)
            event = {'kind': KIND, 'id': event_id, 'revision': len(self._events) + 1,
                'namespace': self.namespace, 'actor': actor, 'request_id': request_id,
                'action': action, 'fingerprint': fingerprint, 'created_at': now,
                'previous': self._events[-1]['seal'] if self._events else None,
                'policy': working['policy'], 'experiences': changed,
                'transitions': transitions, 'truth_before': truth, 'truth_after': truth,
                'truth_firewall': 'append-only relevance ledger; source records not written',
                'reason': payload.get('reason')}
            if report is not None: event['report'] = report
            self._append(event)
            if self._truth(ids) != truth:
                raise RuntimeError('SEVERE: epistemic/source state changed during relevance operation')
            return deepcopy(event)

    def _append(self, event):
        event['seal'] = digest(event)
        # The only persistence operation in this module constructs a NEW RAW
        # record. No source memory objects/IDs are ever passed to writer methods.
        self.db._writer.write(EmberRecord(id=event['id'], namespace=self.namespace,
            record_type=RecordType.RAW, data=event, written_by=event['actor'],
            agent_id=event['actor'], retrieval_candidate=False, training_candidate=False))
        self._reduce(self._state, event)
        self._events.append(event)
        from ..integration.observation_stream import committed
        committed(self.db, self.namespace)

    def observe(self, observation, *, actor, request_id):
        self.db.require_namespace_access(self.namespace, actor, 'read')
        with self.db._writer.lock:
            self._load()
            event = {'kind': KIND, 'id': self.prefix + f'{len(self._events)+1:012d}',
                     'namespace': self.namespace, 'revision': len(self._events)+1,
                     'actor': actor, 'request_id': request_id, 'fingerprint': digest(observation),
                     'created_at': float(self.clock()), 'action': 'observation',
                     'previous': self._events[-1]['seal'] if self._events else None,
                     'observation': observation, 'policy': self._state['policy']}
            self._append(event)
            return event['id']

    def read(self, *, actor):
        self.db.require_namespace_access(self.namespace, actor, 'read')
        with self.db._writer.lock:
            state = self._load()
            return {'revision': len(self._events), 'policy': deepcopy(state['policy']),
                    'states': list(deepcopy(derive(state['experiences'], state['policy'])).values()),
                    'experiences': deepcopy(list(state['experiences'].values())),
                    'reports': deepcopy(list(state['reports'].values()))}

    def events(self, *, actor, after=0, limit=100, request_id=None, session_id=None):
        self.db.require_namespace_access(self.namespace, actor, 'read')
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('invalid event page')
        with self.db._writer.lock:
            self._load()
            for name,value in (('request_id',request_id),('session_id',session_id)):
                if value is not None: identifier(value,name)
            selected = []
            for event in self._events[after:]:
                if request_id is not None and request_id not in (event['request_id'],event.get('report',{}).get('query_request_id')):
                    continue
                if session_id is not None and session_id not in (event.get('report',{}).get('session_id'),event.get('observation',{}).get('session_id')):
                    continue
                selected.append(event)
                if len(selected)==limit: break
            return deepcopy(selected)
