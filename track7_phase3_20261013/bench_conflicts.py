"""Reference-vs-candidate conflict lookup on one fresh disposable store."""
import csv
import json
from pathlib import Path
import shutil
import statistics
import time
import tracemalloc

from embers.db import EmberDB
from embers.core.record import EmberRecord
from embers.core.conflict import Conflict
from embers.core.types import RecordType, ConflictStatus, EdgeType
from embers.storage.format import decode, encode
import embers.storage.store as store_module

ROOT=Path(__file__).resolve().parent
STORE=ROOT/'store_run_20261013_01'
MIN_FREE=500_000_000
MAX_BYTES=25_000_000
EQ=[]; PERF=[]
EQ_FIELDS=['case','candidate','equal','reference_kind','candidate_kind','reference_result','candidate_result','record_count','graph_degree']
PERF_FIELDS=['case','candidate','rep','wall_ms','cpu_ms','reads','read_bytes','verify_calls','verify_ms','decode_calls','decode_ms','enumerate_ms','graph_ms','peak_python_bytes','record_count','graph_degree','free_bytes']

def disk_free():return shutil.disk_usage(ROOT).free
def size():return sum(p.stat().st_size for p in STORE.rglob('*') if p.is_file())
def budget():
    if disk_free()<MIN_FREE or size()>MAX_BYTES:raise RuntimeError('phase3 disk budget reached')

def mem(db,ns='phase3',label='memory'):
    budget()
    return db.write(EmberRecord(namespace=ns,record_type=RecordType.DOCUMENT,
                    data={'content':label},written_by='research',agent_id='research',tags=['memory']))

def type_candidate(db,memory_id,include_closed=False,caller=None):
    memory=db._reader.get(memory_id,include_deprecated=True,include_superseded=True)
    if memory is None:
        if caller is not None:raise KeyError(f'Memory {memory_id} not found.')
        return []
    if caller is not None:db._ns_manager.require_read(memory.namespace,caller)
    ns=memory.namespace
    if caller is not None:db._ns_manager.require_read(ns,caller)
    ids=db._master_index.get_namespace_ids(ns,include_deprecated=True,include_superseded=False)
    ids=[rid for rid in ids if (db._master_index.get_meta(rid) or {}).get('record_type')==RecordType.CONFLICT.value]
    records=db._reader.get_many(ids,include_deprecated=True,include_superseded=False)
    closed={ConflictStatus.RESOLVED,ConflictStatus.ACCEPTED_BOTH,ConflictStatus.SUPERSEDED}
    out=[];seen=set()
    for rec in records:
        if rec.record_type!=RecordType.CONFLICT:continue
        current=db._reader.get_current(rec.id) or rec
        if current.id in seen:continue
        seen.add(current.id)
        c=db._conflict_from_record(current)
        if memory_id not in (c.memory_a,c.memory_b):continue
        if not include_closed and c.status in closed:continue
        out.append(c)
    return out

def tag_candidate(db,memory_id,include_closed=False,caller=None):
    # External prefilter only: temporarily limit index namespace IDs, then run the reference.
    memory=db._reader.get(memory_id,True,True)
    if memory is None:
        if caller is not None:raise KeyError(f'Memory {memory_id} not found.')
        return []
    tagged=db._master_index.get_by_tag('conflict')
    original=db._master_index.get_namespace_ids
    def limited(namespace,include_deprecated=False,include_superseded=False):
        return [rid for rid in original(namespace,include_deprecated,include_superseded) if rid in tagged]
    db._master_index.get_namespace_ids=limited
    try:return db.conflicts_for(memory_id,include_closed,caller)
    finally:db._master_index.get_namespace_ids=original

def graph_gate_candidate(db,memory_id,include_closed=False,caller=None):
    memory=db._reader.get(memory_id,True,True)
    if memory is None:
        if caller is not None:raise KeyError(f'Memory {memory_id} not found.')
        return []
    edges=db._graph_index.get_edges(memory_id,direction='both')
    if not any(e['edge_type']==EdgeType.CONTRADICTS.value for e in edges):
        if caller is not None:db._ns_manager.require_read(memory.namespace,caller)
        return []
    return type_candidate(db,memory_id,include_closed,caller)

def capture(fn):
    try:return {'kind':'value','value':[x.to_dict() for x in fn()]}
    except Exception as exc:return {'kind':type(exc).__name__,'value':str(exc)}

class Meter:
    def __init__(self,db):
        self.db=db;self.current={}
        original_read=db._store.read
        def read(rid):
            self.current['reads']=self.current.get('reads',0)+1
            p=db._store.records_dir/(rid+'.ember')
            if p.exists():self.current['read_bytes']=self.current.get('read_bytes',0)+p.stat().st_size
            return original_read(rid)
        db._store.read=read
        original_verify=EmberRecord.verify_integrity
        def verify(rec):
            start=time.perf_counter_ns()
            try:return original_verify(rec)
            finally:
                self.current['verify_calls']=self.current.get('verify_calls',0)+1
                self.current['verify_ns']=self.current.get('verify_ns',0)+time.perf_counter_ns()-start
        EmberRecord.verify_integrity=verify
        original_decode=store_module.decode
        def decoding(raw):
            start=time.perf_counter_ns()
            try:return original_decode(raw)
            finally:
                self.current['decode_calls']=self.current.get('decode_calls',0)+1
                self.current['decode_ns']=self.current.get('decode_ns',0)+time.perf_counter_ns()-start
        store_module.decode=decoding
        original_enum=db._master_index.get_namespace_ids
        def enumeration(*args,**kwargs):
            start=time.perf_counter_ns()
            try:return original_enum(*args,**kwargs)
            finally:self.current['enumerate_ns']=self.current.get('enumerate_ns',0)+time.perf_counter_ns()-start
        db._master_index.get_namespace_ids=enumeration
        original_graph=db._graph_index.get_edges
        def graph(*args,**kwargs):
            start=time.perf_counter_ns()
            try:return original_graph(*args,**kwargs)
            finally:self.current['graph_ns']=self.current.get('graph_ns',0)+time.perf_counter_ns()-start
        db._graph_index.get_edges=graph

    def call(self,fn):
        self.current={}
        tracemalloc.start()
        c0=time.process_time_ns();w0=time.perf_counter_ns()
        result=capture(fn)
        wall=(time.perf_counter_ns()-w0)/1e6;cpu=(time.process_time_ns()-c0)/1e6
        _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        return result,wall,cpu,peak,dict(self.current)

def measure(db,meter,case,target,include_closed=False,caller=None,reps=3):
    funcs={'reference':lambda:db.conflicts_for(target,include_closed,caller),
           'type_metadata':lambda:type_candidate(db,target,include_closed,caller),
           'tag_metadata':lambda:tag_candidate(db,target,include_closed,caller),
           'graph_gate':lambda:graph_gate_candidate(db,target,include_closed,caller)}
    degree=len(db._graph_index.get_edges(target,direction='both')) if db._store.exists(target) else 0
    reference=None
    for name,fn in funcs.items():
        results=[]
        for rep in range(1,reps+1):
            result,wall,cpu,peak,c=meter.call(fn)
            results.append(result)
            PERF.append(dict(case=case,candidate=name,rep=rep,wall_ms=wall,cpu_ms=cpu,
                reads=c.get('reads',0),read_bytes=c.get('read_bytes',0),verify_calls=c.get('verify_calls',0),
                verify_ms=c.get('verify_ns',0)/1e6,decode_calls=c.get('decode_calls',0),
                decode_ms=c.get('decode_ns',0)/1e6,enumerate_ms=c.get('enumerate_ns',0)/1e6,
                graph_ms=c.get('graph_ns',0)/1e6,peak_python_bytes=peak,
                record_count=db._store.record_count(),graph_degree=degree,free_bytes=disk_free()))
        # Preserve each run's ordering and exception text; flag unstable ordering too.
        stable=all(x==results[0] for x in results)
        if name=='reference':reference=results[0]
        EQ.append(dict(case=case,candidate=name,equal=(stable and results[0]==reference),
            reference_kind=reference['kind'],candidate_kind=results[0]['kind'],
            reference_result=json.dumps(reference,sort_keys=True),
            candidate_result=json.dumps(results[0],sort_keys=True),
            record_count=db._store.record_count(),graph_degree=degree))
    print(case,'records',db._store.record_count(),'reference',reference['kind'],
          'matches',{x['candidate']:x['equal'] for x in EQ if x['case']==case})

def save():
    for filename,rows,fields in [('03_EQUIVALENCE_MATRIX.csv',EQ,EQ_FIELDS),
                                 ('04_PERFORMANCE_RESULTS.csv',PERF,PERF_FIELDS)]:
        with (ROOT/filename).open('w',newline='',encoding='utf-8') as fh:
            writer=csv.DictWriter(fh,fieldnames=fields);writer.writeheader();writer.writerows(rows)

def main():
    if STORE.exists():raise RuntimeError('fresh store required')
    if disk_free()<MIN_FREE+MAX_BYTES:raise RuntimeError('insufficient disk headroom')
    start_free=disk_free();db=EmberDB.connect(STORE)
    a=mem(db,label='target');b=mem(db,label='other-b');c=mem(db,label='other-c');d=mem(db,label='other-d')
    cross=mem(db,'phase3-other','cross-target');cross2=mem(db,'phase3-other','cross-peer')
    meter=Meter(db)
    measure(db,meter,'no_conflicts',a)
    cid=db.map_conflict(a,b,detected_by='research')
    measure(db,meter,'one_active',a)
    assert db.map_conflict(b,a,detected_by='research')==cid
    measure(db,meter,'duplicate_map_idempotent',a)
    cid2=db.map_conflict(a,c,detected_by='research')
    measure(db,meter,'multiple_active',a)
    db.resolve_conflict(cid,'resolved once',changed_by='research')
    measure(db,meter,'one_closed_default',a)
    measure(db,meter,'one_closed_included',a,True)
    db.update_conflict_status(cid2,ConflictStatus.SUPERSEDED,'claim replaced','research')
    measure(db,meter,'superseded_default',a)
    measure(db,meter,'superseded_included',a,True)
    db.map_conflict(cross,cross2,detected_by='research')
    measure(db,meter,'cross_namespace',a,True)
    measure(db,meter,'missing_target','does-not-exist',False,'research')
    stale=Conflict(namespace='phase3',memory_a=a,memory_b='does-not-exist',detected_by='research')
    db.write(EmberRecord(namespace='phase3',record_type=RecordType.CONFLICT,
             data=stale.to_record_payload(),written_by='research',tags=['conflict']))
    measure(db,meter,'stale_endpoint',a,True)
    untagged=Conflict(namespace='phase3',memory_a=a,memory_b=d,detected_by='research')
    untagged_id=db.write(EmberRecord(namespace='phase3',record_type=RecordType.CONFLICT,
             data=untagged.to_record_payload(),written_by='research',tags=[]))
    measure(db,meter,'untagged_conflict',a,True)
    # Growing unrelated namespace records, then independent graph degree.
    growth=mem(db,label='growth-target')
    measure(db,meter,'growth_0',growth)
    peers=[]
    for n in range(1,201):
        peers.append(mem(db,label=f'unrelated-{n}'))
        if n in (25,100,200):
            budget();measure(db,meter,f'growth_{n}',growth)
    for n in range(1,31):
        db.link(growth,peers[n-1],EdgeType.RELATES_TO.value)
        if n in (10,30):measure(db,meter,f'graph_degree_{n}',growth)
    # Memory-only stale metadata: reference reads actual CONFLICT, type candidate misses it.
    meta=db._master_index._records[untagged_id]
    prior=meta['record_type'];meta['record_type']=RecordType.DOCUMENT.value
    try:measure(db,meter,'stale_type_metadata',a,True)
    finally:meta['record_type']=prior
    # A validly sealed but semantically malformed unrelated conflict must raise in reference.
    malformed=db.write(EmberRecord(namespace='phase3',record_type=RecordType.CONFLICT,
        data={'memory_a':growth,'memory_b':growth,'status':'open'},written_by='research',tags=['conflict']))
    measure(db,meter,'malformed_conflict_reference',a,True)
    # Restore only the in-memory index type for restart check; no existing stores touched.
    save()
    summary={'commit':'f183346547cd210f58ca0a6b23df91a56be22b66',
      'store':str(STORE.resolve()),'start_free':start_free,'end_free':disk_free(),
      'store_bytes':size(),'record_count':db._store.record_count(),
      'cases':len({r['case'] for r in EQ}),'equivalence_rows':len(EQ),
      'performance_rows':len(PERF),'mismatches':[{'case':r['case'],'candidate':r['candidate']}
          for r in EQ if not r['equal']]}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
