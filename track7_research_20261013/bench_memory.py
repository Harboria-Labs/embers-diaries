"""Bounded, diskless ELLA load characterization; does not open EmberStore."""
import argparse
import cProfile
import csv
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import pstats
import statistics
import time
import tracemalloc

from embers.cognitive.epistemic import EpistemicLedger, KIND
from embers.cognitive.usefulness import digest
from embers.core.domain import call
from embers.core.record import EmberRecord
from embers.core.types import RecordType
from embers.storage.format import encode, decode


class MemoryStore:
    def __init__(self, blobs):
        self.blobs = blobs
        self.reads = 0
        self.bytes_read = 0

    def all_ids(self):
        return list(self.blobs)

    def read(self, rid):
        self.reads += 1
        raw = self.blobs.get(rid)
        if raw is None:
            return None
        self.bytes_read += len(raw)
        record = EmberRecord.from_dict(decode(raw))
        if record.content_hash is not None:
            record.verify_integrity()
        return record


class DB:
    def __init__(self, store):
        self._store = store


def fixture(n):
    namespace = 'track7-diskless'
    ledger = EpistemicLedger(DB(MemoryStore({})), namespace)
    policy = call('ella_policy_default', None)
    state = {'assessments': {}, 'evidence_overrides': {}, 'hard_groups': {}, 'policy': policy, 'carried': {}}
    blobs = {}
    previous = None
    start = time.perf_counter()
    for i in range(1, n + 1):
        rid = ledger.prefix + f'{i:012d}'
        next_state = deepcopy(state)
        assessment = {'assessment_id': rid + ':assessment', 'target_memory_id': 'target',
                      'target_memory_version': 'hash', 'evidence_id': f'evidence-{i}',
                      'assessor_id': 'actor', 'assessment_note': 'measured event',
                      'polarity': 'SUPPORTS', 'strength': 'WEAK', 'status': 'accepted',
                      'request_id': f'request-{i}'}
        next_state['assessments'][assessment['assessment_id']] = assessment
        event = {'kind': KIND, 'id': rid, 'namespace': namespace, 'revision': i,
                 'actor': 'actor', 'request_id': f'request-{i}', 'action': 'report',
                 'payload': {'target_memory_id': 'target', 'target_memory_version': 'hash',
                             'evidence_id': f'evidence-{i}', 'polarity': 'SUPPORTS',
                             'strength': 'WEAK'}, 'previous': previous,
                 'state': next_state, 'response': {}}
        assert call('ella_reduce', {'state': state, 'event': event}) == next_state
        event['seal'] = digest(event)
        record = EmberRecord(id=rid, namespace=namespace, record_type=RecordType.RAW,
                             data=event, written_by='actor', agent_id='actor',
                             retrieval_candidate=False, training_candidate=False)
        record.seal()
        blobs[rid] = encode(record.to_dict())
        previous, state = event['seal'], next_state
    return ledger.prefix, blobs, (time.perf_counter() - start) * 1000


def timed(fn):
    c0 = time.process_time_ns()
    w0 = time.perf_counter_ns()
    value = fn()
    return value, (time.perf_counter_ns() - w0) / 1e6, (time.process_time_ns() - c0) / 1e6


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--counts', nargs='+', type=int, default=[10, 20, 30, 50])
    parser.add_argument('--reps', type=int, default=5)
    args = parser.parse_args()
    out = Path(__file__).resolve().parent
    rows = []
    summary = {'kind': 'diskless synthetic ELLA report journal using production load/reducer',
               'counts': args.counts, 'reps': args.reps, 'python': platform.python_version(),
               'os': platform.platform(), 'processor': platform.processor(), 'cases': {}}
    for n in args.counts:
        if n > 50 or n < 1:
            raise ValueError('bounded to 1..50 events')
        (prefix, blobs, fixture_ms), _, fixture_cpu = timed(lambda: fixture(n))
        summary['cases'][str(n)] = {'fixture_ms': fixture_ms, 'encoded_bytes': sum(map(len, blobs.values())),
                                    'event_count': len(blobs), 'rows': {}}
        store = MemoryStore(blobs)
        ledger = EpistemicLedger(DB(store), 'track7-diskless')
        assert ledger.prefix == prefix
        encoded_size = sum(map(len, blobs.values()))
        rows.append({'count': n, 'rep': 1, 'operation': 'fixture_create', 'wall_ms': fixture_ms,
                     'cpu_ms': fixture_cpu, 'records_read': 0, 'bytes_read': 0,
                     'encoded_store_bytes': encoded_size, 'error': ''})
        store.reads = store.bytes_read = 0
        (_, first_wall, first_cpu) = timed(ledger.load)
        rows.append({'count': n, 'rep': 1, 'operation': 'first_load', 'wall_ms': first_wall,
                     'cpu_ms': first_cpu, 'records_read': store.reads, 'bytes_read': store.bytes_read,
                     'encoded_store_bytes': encoded_size, 'error': ''})
        for rep in range(args.reps):
            for name, obj in [('same_object', ledger), ('new_object', EpistemicLedger(DB(store), 'track7-diskless'))]:
                store.reads = store.bytes_read = 0
                (state, events), wall, cpu = timed(obj.load)
                assert len(events) == n and len(state['assessments']) == n
                rows.append({'count': n, 'rep': rep + 1, 'operation': name, 'wall_ms': wall,
                             'cpu_ms': cpu, 'records_read': store.reads, 'bytes_read': store.bytes_read,
                             'encoded_store_bytes': sum(map(len, blobs.values())), 'error': ''})
        # Same bytes; manual pass times independent stages without changing source.
        for rep in range(args.reps):
            ids, enum_ms, enum_cpu = timed(lambda: sorted(x for x in store.all_ids() if x.startswith(prefix)))
            records, read_ms, read_cpu = timed(lambda: [store.read(rid) for rid in ids])
            _, explicit_ms, explicit_cpu = timed(lambda: [r.verify_integrity() for r in records])
            events = [r.data for r in records]
            _, chain_ms, chain_cpu = timed(lambda: [digest({k:v for k,v in e.items() if k != 'seal'}) for e in events])
            def replay():
                state = {'assessments': {}, 'evidence_overrides': {}, 'hard_groups': {},
                         'policy': call('ella_policy_default', None), 'carried': {}}
                for event in events:
                    state = call('ella_reduce', {'state': state, 'event': event})
                return state
            _, reduce_ms, reduce_cpu = timed(replay)
            for operation, wall, cpu in [('enumerate', enum_ms, enum_cpu), ('read_decode_verify', read_ms, read_cpu),
                                         ('explicit_verify', explicit_ms, explicit_cpu), ('chain_digest', chain_ms, chain_cpu),
                                         ('native_replay_boundary', reduce_ms, reduce_cpu)]:
                rows.append({'count': n, 'rep': rep + 1, 'operation': operation, 'wall_ms': wall,
                             'cpu_ms': cpu, 'records_read': n if operation == 'read_decode_verify' else 0,
                             'bytes_read': sum(map(len, blobs.values())) if operation == 'read_decode_verify' else 0,
                             'encoded_store_bytes': sum(map(len, blobs.values())), 'error': ''})
        print(n, 'events', sum(map(len, blobs.values())), 'bytes')
    with (out / '03_BENCHMARK_RESULTS.csv').open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    for n in args.counts:
        subset = [r for r in rows if r['count'] == n]
        for op in sorted({r['operation'] for r in subset}):
            values = [r['wall_ms'] for r in subset if r['operation'] == op]
            summary['cases'][str(n)]['rows'][op] = {'median_wall_ms': statistics.median(values),
                                                      'min_wall_ms': min(values), 'max_wall_ms': max(values)}
    prefix, blobs, _ = fixture(max(args.counts))
    ledger = EpistemicLedger(DB(MemoryStore(blobs)), 'track7-diskless')
    profiler = cProfile.Profile(); profiler.enable(); ledger.load(); profiler.disable()
    with (out / 'profile_ella_50.txt').open('w', encoding='utf-8') as fh:
        import io
        stream = io.StringIO(); pstats.Stats(profiler, stream=stream).sort_stats('cumtime').print_stats(45)
        fh.write(stream.getvalue())
    tracemalloc.start(); ledger.load(); _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    summary['peak_tracemalloc_bytes_50'] = peak
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({n: summary['cases'][str(n)]['rows']['same_object'] for n in args.counts}, indent=2))

if __name__ == '__main__':
    main()
