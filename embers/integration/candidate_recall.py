"""Opt-in Candidate 04 recall with explicit agent cues and model-token counting.

The agent supplies direct relevance scores. This policy never treats exposure as
learning, never infers truth and never recursively propagates neighbor heat.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import uuid

from ..cognitive.feedback_dynamics import activation_step
from ..core.record import EmberRecord
from ..core.types import RecordType


@dataclass(frozen=True)
class CandidatePolicy:
    epsilon: float = .02
    mu: float = .5
    delta: float = .05
    rho: float = 1.0
    beta: float = .25
    threshold: float = .4
    strong_direct: float = .8
    max_seeds: int = 32
    max_edges: int = 128
    max_state_memories: int = 2048
    max_results: int = 10
    token_budget: int = 2048
    memory_token_cap: int = 512
    neighborhood_token_cap: int = 1024

    def __post_init__(self):
        activation_step(self.epsilon, direct=0, cue=0, bias=0, elapsed=0,
                        epsilon=self.epsilon, mu=self.mu, delta=self.delta, rho=self.rho)
        for name in ('beta', 'strong_direct', 'threshold'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'invalid {name}')
        u = self.strong_direct * (1 - self.mu)
        v = (1 - self.strong_direct) * (1 + self.mu) + self.delta
        lower_equilibrium = self.epsilon + (1 - 2 * self.epsilon) * u / (u + v)
        if not self.epsilon < self.threshold < lower_equilibrium:
            raise ValueError('threshold conflicts with strong-cue recovery/floors')
        for name in ('max_seeds', 'max_edges', 'max_state_memories', 'max_results',
                     'token_budget', 'memory_token_cap', 'neighborhood_token_cap'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        if max(self.memory_token_cap, self.neighborhood_token_cap) > self.token_budget:
            raise ValueError('item/neighborhood cap exceeds total attention budget')
        if self.max_state_memories < self.max_seeds + self.max_edges:
            raise ValueError('state capacity must hold a complete bounded expansion')


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


class CandidateRecall:
    """Server-configured, durable query receipts and bounded transient activation.

    The token counter must count the configured model's tokens in the rendered
    context string. Transport envelopes outside that string are not its budget.
    Complete feedback/query history remains immutable; only transient activation
    cache entries can return to their floor when its explicit capacity is reached.
    """
    VERSION = 'candidate-04-recall-v1'

    def __init__(self, journal, *, token_counter, tokenizer_id, policy=None):
        if not callable(token_counter) or not isinstance(tokenizer_id, str) or not tokenizer_id:
            raise ValueError('configure a tokenizer identity and callable token counter')
        self.journal = journal
        self.db = journal._db
        self.namespace = journal._namespace
        self.context_id = journal._context_id
        self.policy = policy or CandidatePolicy()
        self.counter = token_counter
        self.root_id = _id(_json([self.VERSION, self.namespace, self.context_id]))
        self.settings = {'kind': self.VERSION, 'entry': 'policy', 'root': self.root_id,
                         'policy': asdict(self.policy), 'tokenizer_id': tokenizer_id}
        with self.db._writer.lock:
            prior = self.db._store.read(self.root_id)
            if prior is None:
                if self.db._store.exists(self.root_id):
                    raise ValueError('unreadable candidate policy')
                self._write(self.root_id, self.settings, 'system')
            elif prior.data != self.settings or not prior.verify_integrity():
                raise ValueError('candidate policy changed; explicit migration required')
        if not hasattr(self.db, '_candidate_services'):
            self.db._candidate_services = {}
        self.db._candidate_services[(self.namespace, self.context_id)] = self

    def _write(self, rid, data, actor):
        self.db._writer.write(EmberRecord(
            id=rid, namespace=self.namespace, record_type=RecordType.RAW, data=data,
            written_by=actor, agent_id=actor, retrieval_candidate=False,
        ))

    def _count(self, text):
        count = self.counter(text)
        if type(count) is not int or count < 0:
            raise ValueError('token counter must return a nonnegative integer')
        return count

    def _render(self, rows, format):
        if format == 'structured':
            return _json(rows)
        text = '\n\n'.join(
            f"[{r['id']}; truth={r['truth_status']}; source={r['written_by']}]\n{_json(r['data'])}"
            for r in rows)
        if format == 'messages':
            return _json([{'role': 'system', 'content': text}])
        return text

    def _record(self, rid):
        record = self.db._reader.get(rid, track_access=False)
        if (record is None or record.namespace != self.namespace or
                record.record_type not in self.db._DURABLE_MEMORY_TYPES or
                not record.retrieval_candidate):
            return None
        return record

    def _row(self, record, active, direct, cue):
        known = {'verified', 'hypothesis', 'unverified', 'contested', 'deprecated', 'incorrect'}
        status = record.data.get('verify_status', 'hypothesis') if isinstance(record.data, dict) else 'hypothesis'
        if status not in known:
            status = 'hypothesis'
        for annotation in record.annotations:
            if annotation.annotation_type == 'validation' and annotation.context == 'verification':
                statuses = [tag for tag in annotation.tags if tag in known]
                if len(statuses) == 1:
                    status = statuses[0]
        if any(c.status.value == 'open' for c in self.db.conflicts_for(record.id)):
            status = 'contested'
        return {'id': record.id, 'data': record.data, 'truth_status': status,
                'written_by': record.written_by, 'content_hash': record.content_hash,
                'activation': active, 'direct': direct, 'cue': cue}

    def recall(self, *, actor, query_id, direct_scores, elapsed, format='structured'):
        self.db.require_namespace_access(self.namespace, actor, 'write')
        if not isinstance(query_id, str) or not query_id or len(query_id) > 128:
            raise ValueError('query_id must be 1..128 characters')
        if format not in ('text', 'messages', 'structured'):
            raise ValueError('unsupported context format')
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('elapsed must be nonnegative finite model time')
        p = self.policy
        if not isinstance(direct_scores, dict) or len(direct_scores) > p.max_seeds:
            raise ValueError('direct scores exceed the configured seed budget')
        for rid, score in direct_scores.items():
            if (not isinstance(rid, str) or type(score) not in (int, float) or
                    not math.isfinite(score) or not 0 <= score <= 1):
                raise ValueError('direct scores require record IDs and finite [0,1] values')
        request = _json({'direct_scores': direct_scores, 'elapsed': elapsed, 'format': format})
        fingerprint = hashlib.sha256(request.encode()).hexdigest()
        receipt_id = _id(_json([self.root_id, actor, query_id]))
        with self.db._writer.lock:
            receipt = self.db._store.read(receipt_id)
            if receipt is not None:
                if receipt.data.get('fingerprint') != fingerprint:
                    raise ValueError('query_id reused with different input')
                if not receipt.verify_integrity():
                    raise ValueError('query receipt integrity failure')
                return receipt.data['response']
            history = []
            for rid in self.db._store.all_ids():
                record = self.db._store.read(rid)
                if record is None:
                    raise ValueError('cannot recall with unreadable durable state')
                data = record.data
                if isinstance(data, dict) and data.get('root') == self.root_id and data.get('entry') == 'query':
                    if (not record.verify_integrity() or record.namespace != self.namespace or
                            record.record_type != RecordType.RAW or record.retrieval_candidate):
                        raise ValueError('query history integrity failure')
                    history.append(record)
            history.sort(key=lambda record: record.data['sequence'])
            previous = self.db._store.read(self.root_id)
            if previous is None or not previous.verify_integrity():
                raise ValueError('candidate policy missing or damaged')
            for sequence, record in enumerate(history, 1):
                if record.data['sequence'] != sequence or record.data['previous_hash'] != previous.content_hash:
                    raise ValueError('query history chain is broken')
                previous = record
            sequence = len(history) + 1
            prior = history[-1].data['state'] if history else {}
            learned = self.journal.project()
            records = {}
            direct = {}
            groups = {}
            for rid, score in direct_scores.items():
                record = self._record(rid)
                if record is None:
                    raise ValueError('direct target is unavailable in this namespace')
                records[rid], direct[rid], groups[rid] = record, score, {rid}
            adjacency = {}
            for (source, target, relation), value in learned.pair_state.items():
                if value > 0:
                    adjacency.setdefault(source, []).append((target, relation, value))
            neighbor = {}
            traversed = 0
            for source in sorted(direct, key=lambda rid: (-direct[rid], rid)):
                edges = adjacency.get(source, [])
                denominator = max(1.0, math.fsum(edge[2] for edge in edges))
                for target, relation, weight in sorted(edges, key=lambda edge: (-edge[2], edge[0], edge[1])):
                    if traversed >= p.max_edges:
                        break
                    traversed += 1
                    record = self._record(target)
                    if record is None:
                        continue
                    records[target] = record
                    groups.setdefault(target, set()).add(source)
                    neighbor[target] = neighbor.get(target, 0) + direct[source] * weight / denominator
            cues = {rid: direct.get(rid, 0) + p.beta * (1 - direct.get(rid, 0)) * min(1, neighbor.get(rid, 0))
                    for rid in records}
            state = {}
            for rid in set(prior) | set(records):
                old = prior.get(rid, {'active': p.epsilon, 'last_seen': 0})
                active, _ = activation_step(
                    old['active'], direct=direct.get(rid, 0), cue=cues.get(rid, 0),
                    bias=learned.memory_bias.get(rid, 0), elapsed=elapsed,
                    epsilon=p.epsilon, mu=p.mu, delta=p.delta, rho=p.rho,
                )
                state[rid] = {'active': active, 'last_seen': sequence if rid in records else old['last_seen']}
            rows = {rid: self._row(record, state[rid]['active'], direct.get(rid, 0), cues[rid])
                    for rid, record in records.items() if cues[rid] > 0}
            latent = sorted(rid for rid in rows if prior.get(rid, {}).get('active', p.epsilon) < p.threshold)
            reserved = latent[(sequence - 1) % len(latent)] if latent else None
            ranked = sorted(rows, key=lambda rid: (-cues[rid] * state[rid]['active'], rid))
            order = ([reserved] if reserved else []) + [rid for rid in ranked if rid != reserved and state[rid]['active'] >= p.threshold]
            selected = []
            selected_ids = []
            for rid in order:
                if len(selected) == p.max_results:
                    break
                row = rows[rid]
                if self._count(self._render([row], format)) > p.memory_token_cap:
                    continue
                proposed = selected + [row]
                if self._count(self._render(proposed, format)) > p.token_budget:
                    continue
                if any(self._count(self._render([r for r in proposed if group in groups[r['id']]], format)) > p.neighborhood_token_cap
                       for group in groups[rid]):
                    continue
                selected, selected_ids = proposed, selected_ids + [rid]
            rendered = self._render(selected, format)
            count = self._count(rendered)
            if count > p.token_budget:
                raise ValueError('context wrapper alone exceeds attention budget')
            keep = sorted(state, key=lambda rid: (-state[rid]['last_seen'], rid))[:p.max_state_memories]
            state = {rid: state[rid] for rid in keep}
            response = {'context': rendered, 'format': format, 'token_count': count,
                        'selected_ids': selected_ids, 'query_id': query_id,
                        'feedback_generation': learned.generation, 'model_version': self.VERSION,
                        'edges_examined': traversed, 'latent_inspected': reserved}
            self._write(receipt_id, {'kind': self.VERSION, 'root': self.root_id, 'entry': 'query',
                        'sequence': sequence, 'previous_hash': previous.content_hash,
                        'fingerprint': fingerprint, 'state': state, 'response': response}, actor)
            return response
