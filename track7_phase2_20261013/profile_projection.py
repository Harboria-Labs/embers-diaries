"""Read-side timing on the two completed isolated stores."""
import csv
import cProfile
import io
from pathlib import Path
import pstats
import time

from embers.db import EmberDB
from embers.cognitive.epistemic import EpistemicLedger
from embers.integration.observation_journal import read as read_observations

ROOT = Path(__file__).resolve().parent
FIELDS = ['history','operation','rep','wall_ms','store_reads','conflict_scan_ms','conflict_scan_calls',
          'namespace_read_ms','namespace_read_calls','observation_events']
ROWS=[]

def measure(n, operation, rep, db, fn, counters):
    counters.clear()
    start=time.perf_counter_ns()
    result=fn()
    wall=(time.perf_counter_ns()-start)/1e6
    ROWS.append(dict(history=n,operation=operation,rep=rep,wall_ms=wall,
                     store_reads=counters.get('read_count',0),
                     conflict_scan_ms=counters.get('conflict_ns',0)/1e6,
                     conflict_scan_calls=counters.get('conflict_count',0),
                     namespace_read_ms=counters.get('namespace_ns',0)/1e6,
                     namespace_read_calls=counters.get('namespace_count',0),
                     observation_events=len(result) if operation=='observation_read' else ''))
    return result

for n, store in [(31,ROOT/'store_run_20261013_01'),(51,ROOT/'store_run_20261013_03')]:
    db=EmberDB.connect(store)
    ledger=EpistemicLedger(db,'track7-phase2')
    ids=[x for x in db._store.all_ids() if x.startswith(ledger.prefix)]
    assert len(ids)==n
    target=next(x for x in db._store.all_ids() if x not in ids and
                (lambda r:r and r.namespace=='track7-phase2' and r.record_type.value=='document')(db._store.read(x)))
    counters={}
    def wrap(owner,name,key):
        original=getattr(owner,name)
        def timed(*args,**kwargs):
            start=time.perf_counter_ns()
            try:return original(*args,**kwargs)
            finally:
                counters[key+'_ns']=counters.get(key+'_ns',0)+time.perf_counter_ns()-start
                counters[key+'_count']=counters.get(key+'_count',0)+1
        setattr(owner,name,timed)
    wrap(db._store,'read','read')
    wrap(db,'conflict_records','conflict')
    wrap(db._reader,'get_namespace','namespace')
    state,events=ledger.load()
    for rep in range(1,4):
        measure(n,'load',rep,db,ledger.load,counters)
        measure(n,'project_cached_state',rep,db,lambda:ledger.project(target,state,len(events)),counters)
        measure(n,'project_full',rep,db,lambda:ledger.project(target),counters)
    obs=measure(n,'observation_read',1,db,lambda:read_observations(db,'track7-phase2'),counters)
    assert len(obs)==n
with (ROOT/'projection_profile.csv').open('w',newline='',encoding='utf-8') as fh:
    writer=csv.DictWriter(fh,fieldnames=FIELDS);writer.writeheader();writer.writerows(ROWS)
for row in ROWS:print(row)
profile=cProfile.Profile();profile.enable();ledger.project(target);profile.disable()
stream=io.StringIO();pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(50)
(ROOT/'profile_project_51.txt').write_text(stream.getvalue(),encoding='utf-8')
