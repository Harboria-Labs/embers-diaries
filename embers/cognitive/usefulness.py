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
from ..core.domain import call, MODEL_VERSION

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
        call('fur_policy', asdict(self))


def target_key(target, context):
    return canonical([target, context])


def derive(experiences, policy):
    return call('fur_derive', {'experiences': experiences, 'policy': policy})


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
        result = call('fur_reduce', {'state': state, 'event': event})
        state.clear()
        state.update(result)

    def _target(self, value):
        target = call('fur_target', value)
        for rid in target['memory_ids']:
            record = self.db._reader.get(rid, track_access=False)
            if record is None or record.namespace != self.namespace or record.record_type not in self.db._DURABLE_MEMORY_TYPES:
                raise ValueError('target must be current durable memory in this namespace')
        return target

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
        fingerprint = digest([actor, action, payload, expected_revision])
        with self.db._writer.lock:
            state = self._load()
            now = float(self.clock())
            event_id = self.prefix + f'{len(self._events) + 1:012d}'
            result = call('fur_apply', {'state':state, 'events':[e if e['actor']==actor and e['request_id']==request_id else {k:e[k] for k in ('actor','request_id','fingerprint')} for e in self._events],
                'action':action, 'payload':payload, 'actor':actor, 'admin':actor in self.admins,
                'request_id':request_id, 'expected_revision':expected_revision,
                'revision':len(self._events), 'fingerprint':fingerprint, 'now':now, 'event_id':event_id})
            if 'retry' in result:
                return result['retry']
            report = result['report']
            if report is not None:
                self._target(report['target'])
                session = report['session_id']
                if session is not None:
                    actual = self.db.get_session(session)
                    if actual is None or actual.agent_id != actor or actual.status.value != 'active':
                        raise PermissionError('report session must be active and belong to caller')
            changed, transitions = result['experiences'], result['transitions']
            ids = {rid for exp in changed for rid in exp['target']['memory_ids']}
            truth = self._truth(ids)
            event = {'kind': KIND, 'id': event_id, 'revision': len(self._events) + 1,
                'namespace': self.namespace, 'actor': actor, 'request_id': request_id,
                'action': action, 'fingerprint': fingerprint, 'created_at': now,
                'previous': self._events[-1]['seal'] if self._events else None,
                'policy': result['policy'], 'experiences': changed, 'model_version': MODEL_VERSION,
                'transitions': transitions, 'truth_before': truth, 'truth_after': truth,
                'truth_firewall': 'append-only relevance ledger; source records not written',
                'reason': payload.get('reason'), 'report_state': result.get('report_state')}
            if report is not None: event['report'] = report
            if result.get('research_config') is not None:
                event['research_config'] = result['research_config']
                event['configuration_revision'] = result['configuration_revision']
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
