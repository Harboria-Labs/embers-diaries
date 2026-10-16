"""Bounded persisted ELLA benchmark. Writes only to a new store beside this file."""
import argparse
import cProfile
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import shutil
import statistics
import time
import traceback

from embers.db import EmberDB
from embers.core.evidence import Evidence
from embers.core.record import EmberRecord
from embers.identity.registry import AgentRegistry
from embers.integration import MemoryProtocol
from embers.cognitive.epistemic import EpistemicLedger, enable_epistemic
import embers.cognitive.epistemic as epistemic_module
import embers.integration.observation_journal as observation_journal

ROOT = Path(__file__).resolve().parent
CSV = ROOT / '03_RAW_RESULTS.csv'
STORE = ROOT / 'store_run_20261013_01'
MIN_FREE = 300_000_000
MAX_EXPERIMENT_BYTES = 20_000_000
FIELDS = ['case','event_count','operation','index','wall_ms','cpu_ms','free_before','free_after',
          'store_bytes','journal_bytes','wal_bytes','observation_bytes','record_count',
          'rss_bytes','load_ms','read_ms','verify_ms','native_ms','native_reduce_ms',
          'store_write_ms','observation_append_ms','reads','verifications','native_reduces',
          'error']

try:
    import psutil
    PROCESS = psutil.Process()
except ImportError:
    PROCESS = None


def disk_free():
    return shutil.disk_usage(ROOT).free


def file_bytes(path):
    return path.stat().st_size if path.exists() else 0


def store_bytes():
    return sum(p.stat().st_size for p in STORE.rglob('*') if p.is_file()) + file_bytes(observation_journal.path(DB))


def state_size(ledger):
    prefix = ledger.prefix
    return sum(p.stat().st_size for p in DB._store.records_dir.iterdir()
               if p.suffix == '.ember' and p.stem.startswith(prefix))


def snapshot(ledger):
    return dict(store_bytes=store_bytes(), journal_bytes=state_size(ledger),
                wal_bytes=file_bytes(STORE / 'wal.jsonl'),
                observation_bytes=file_bytes(observation_journal.path(DB)),
                record_count=DB._store.record_count(),
                rss_bytes=PROCESS.memory_info().rss if PROCESS else '')


def budget():
    if disk_free() < MIN_FREE or store_bytes() >= MAX_EXPERIMENT_BYTES:
        raise RuntimeError('experiment disk budget reached')


METRICS = {}


def wrap(owner, name, metric, predicate=None):
    original = getattr(owner, name)
    def measured(*args, **kwargs):
        started = time.perf_counter_ns()
        try:
            return original(*args, **kwargs)
        finally:
            if predicate is None or predicate(args, kwargs):
                METRICS[metric + '_ns'] = METRICS.get(metric + '_ns', 0) + time.perf_counter_ns() - started
                METRICS[metric + '_calls'] = METRICS.get(metric + '_calls', 0) + 1
    setattr(owner, name, measured)


def wrap_native():
    original = epistemic_module.call
    def measured(operation, value):
        started = time.perf_counter_ns()
        try:
            return original(operation, value)
        finally:
            elapsed = time.perf_counter_ns() - started
            METRICS['native_ns'] = METRICS.get('native_ns', 0) + elapsed
            if operation == 'ella_reduce':
                METRICS['native_reduce_ns'] = METRICS.get('native_reduce_ns', 0) + elapsed
                METRICS['native_reduce_calls'] = METRICS.get('native_reduce_calls', 0) + 1
    epistemic_module.call = measured


def run(case, operation, index, ledger, fn):
    METRICS.clear()
    budget()
    before = disk_free()
    cpu0 = time.process_time_ns(); wall0 = time.perf_counter_ns()
    error = ''
    try:
        result = fn()
    except Exception as exc:
        result = None
        error = type(exc).__name__ + ': ' + str(exc)
        (ROOT / ('error_' + STORE.name + '.txt')).write_text(traceback.format_exc(), encoding='utf-8')
    wall = (time.perf_counter_ns() - wall0) / 1e6
    cpu = (time.process_time_ns() - cpu0) / 1e6
    sizes = snapshot(ledger)
    row = dict(case=case, event_count=index if operation == 'append' else case,
               operation=operation, index=index, wall_ms=wall, cpu_ms=cpu,
               free_before=before, free_after=disk_free(), **sizes,
               load_ms=METRICS.get('load_ns', 0)/1e6,
               read_ms=METRICS.get('read_ns', 0)/1e6,
               verify_ms=METRICS.get('verify_ns', 0)/1e6,
               native_ms=METRICS.get('native_ns', 0)/1e6,
               native_reduce_ms=METRICS.get('native_reduce_ns', 0)/1e6,
               store_write_ms=METRICS.get('store_write_ns', 0)/1e6,
               observation_append_ms=METRICS.get('observation_append_ns', 0)/1e6,
               reads=METRICS.get('read_calls', 0),
               verifications=METRICS.get('verify_calls', 0),
               native_reduces=METRICS.get('native_reduce_calls', 0), error=error)
    with CSV.open('a', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if fh.tell() == 0: writer.writeheader()
        writer.writerow(row)
    if error: raise RuntimeError(error)
    if wall > 10_000: raise RuntimeError('operation exceeded 10 second runtime limit')
    budget()
    return result


def main():
    global DB, STORE, CSV
    parser = argparse.ArgumentParser()
    parser.add_argument('--up-to', type=int, choices=[30,50], default=30)
    parser.add_argument('--run-id', choices=['01','02','03'], default='01')
    args = parser.parse_args()
    if args.run_id == '02':
        STORE = ROOT / 'store_run_20261013_02'
        CSV = ROOT / '03_RAW_RESULTS_50.csv'
    elif args.run_id == '03':
        STORE = ROOT / 'store_run_20261013_03'
        CSV = ROOT / '03_RAW_RESULTS_50_RETRY.csv'
    if STORE.exists() or CSV.exists():
        raise RuntimeError('fresh run required: store/CSV already exists')
    start_free = disk_free()
    if start_free < MIN_FREE + MAX_EXPERIMENT_BYTES:
        raise RuntimeError(f'insufficient headroom: {start_free} bytes free')
    DB = EmberDB.connect(STORE)
    registry = AgentRegistry(DB)
    identity, token = registry.register('track7-phase2', provider='research', model='benchmark')
    assert registry.authenticate(identity.agent_id, token).agent_id == identity.agent_id
    DB.create_namespace('track7-phase2', owner=identity.agent_id)
    enable_epistemic(DB, {identity.agent_id})
    ledger = EpistemicLedger(DB, 'track7-phase2')
    wrap(DB._store, 'read', 'read')
    wrap(DB._store, 'write', 'store_write')
    wrap(ledger, 'load', 'load')
    wrap(EmberRecord, 'verify_integrity', 'verify')
    wrap(observation_journal, 'append', 'observation_append')
    wrap_native()
    def auth():
        assert registry.authenticate(identity.agent_id, token).agent_id == identity.agent_id
    proto = MemoryProtocol(DB, default_namespace='track7-phase2')
    def claim():
        auth()
        return proto.remember({'content':'Track 7 isolated claim','verify_status':'hypothesis'},
                              written_by=identity.agent_id, agent_id=identity.agent_id)
    rid = run(0, 'claim_create', 0, ledger, claim)
    target_hash = DB._store.read(rid).content_hash
    evidence_ids = []
    for i in range(5):
        def attach(i=i):
            auth()
            DB.require_namespace_access('track7-phase2', identity.agent_id, 'write')
            return DB.attach_evidence(rid, Evidence(reference=f'independent-source-{i}',
                      source=f'source-{i}', agent_id=identity.agent_id))
        evidence_ids.append(run(0, 'evidence_attach', i+1, ledger, attach))
    checkpoints = [5,10,20,30] + ([50] if args.up_to == 50 else [])
    projections = {}
    for i in range(1, args.up_to + 1):
        def append(i=i):
            auth()
            return ledger.apply('report', dict(target_memory_id=rid,
                target_memory_version=target_hash, evidence_id=evidence_ids[(i-1)%5],
                polarity='SUPPORTS', strength='WEAK', assessment_note=f'bounded assessment {i}'),
                actor=identity.agent_id, request_id=f'track7-report-{i}', expected_revision=i-1)
        result = run(i, 'append', i, ledger, append)
        if i in checkpoints:
            for rep in range(3):
                run(i, 'load_same', rep+1, ledger, ledger.load)
                run(i, 'load_new', rep+1, ledger,
                    EpistemicLedger(DB, 'track7-phase2').load)
                projected = run(i, 'project', rep+1, ledger,
                                lambda: ledger.project(rid))
                projections[i] = projected
            state, events = ledger.load()
            assert len(events) == i and len(state['assessments']) == i
            assert projections[i]['epistemic_revision'] == i
            print(f'checkpoint {i}: journal={state_size(ledger)} store={store_bytes()} free={disk_free()}')
    # Bounded correction of lineage metadata; retained raw evidence remains immutable.
    before = DB.get_evidence(evidence_ids[0]).to_dict()
    corrected = run(args.up_to+1, 'correct_evidence', args.up_to+1, ledger,
        lambda: ledger.apply('correct_evidence', dict(target_memory_id=rid,
            target_memory_version=target_hash, evidence_id=evidence_ids[0],
            lineage={'reference':'corrected-source-0'}, reason='bounded correction'),
            actor=identity.agent_id, request_id='track7-correct-1',
            expected_revision=args.up_to))
    assert DB.get_evidence(evidence_ids[0]).to_dict() == before
    assert corrected['epistemic_revision'] == args.up_to + 1
    state, events = ledger.load()
    assert len(events) == args.up_to+1
    assert state['evidence_overrides'][evidence_ids[0]]['reference'] == 'corrected-source-0'
    reopened = EmberDB.connect(STORE)
    enable_epistemic(reopened, {identity.agent_id})
    assert AgentRegistry(reopened).authenticate(identity.agent_id, token).agent_id == identity.agent_id
    new_state, new_events = EpistemicLedger(reopened, 'track7-phase2').load()
    assert new_state == state and len(new_events) == len(events)
    projection = EpistemicLedger(reopened, 'track7-phase2').project(rid)
    assert projection['epistemic_revision'] == args.up_to + 1
    summary = {'commit':'f183346547cd210f58ca0a6b23df91a56be22b66',
               'native_sha256':hashlib.sha256((Path(__file__).resolve().parent.parent/'embers'/'_native.cp313-win_amd64.pyd').read_bytes()).hexdigest(),
               'python':platform.python_version(),'os':platform.platform(),
               'start_free':start_free,'end_free':disk_free(),'store':str(STORE.resolve()),
               'journal_events':len(events),'journal_bytes':state_size(ledger),
               'wal_bytes':file_bytes(STORE/'wal.jsonl'),
               'observation_bytes':file_bytes(observation_journal.path(DB)),
               'store_bytes':store_bytes(),'record_count':DB._store.record_count(),
               'reopen_state_equal':new_state==state,'correction_verified':True,
               'final_revision':projection['epistemic_revision'],'checkpoints':checkpoints,
               'budget':{'min_free':MIN_FREE,'max_experiment_bytes':MAX_EXPERIMENT_BYTES}}
    summary_path = ROOT / ('summary_50_retry.json' if args.run_id == '03' else
                           'summary_50.json' if args.run_id == '02' else 'summary.json')
    summary_path.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__ == '__main__':
    main()
