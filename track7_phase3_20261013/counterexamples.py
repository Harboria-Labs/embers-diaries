"""Fresh-store negative controls; all mutations stay in this disposable fixture."""
import csv
import json
from pathlib import Path
import shutil

from embers.db import EmberDB
from embers.core.record import EmberRecord
from embers.core.types import RecordType, EdgeType
from embers.storage.format import encode, decode
from bench_conflicts import type_candidate, graph_gate_candidate, tag_candidate, capture

ROOT=Path(__file__).resolve().parent
STORE=ROOT/'store_run_20261013_02'
if STORE.exists():raise RuntimeError('fresh counterexample store required')
if shutil.disk_usage(ROOT).free<500_000_000:raise RuntimeError('disk headroom too low')
db=EmberDB.connect(STORE)
def memory(text,ns='counter'):
    return db.write(EmberRecord(namespace=ns,record_type=RecordType.DOCUMENT,
             data={'content':text},written_by='research',agent_id='research'))
a=memory('a');b=memory('b');unrelated=memory('unrelated')
cid=db.map_conflict(a,b,detected_by='research')
rows=[]
def compare(case,target,closed=False,caller=None):
    ref=capture(lambda:db.conflicts_for(target,closed,caller))
    for name,fn in [('type_metadata',type_candidate),('tag_metadata',tag_candidate),('graph_gate',graph_gate_candidate)]:
        got=capture(lambda:fn(db,target,closed,caller))
        rows.append(dict(case=case,candidate=name,equal=got==ref,
                         reference_kind=ref['kind'],candidate_kind=got['kind'],
                         reference_result=json.dumps(ref,sort_keys=True),
                         candidate_result=json.dumps(got,sort_keys=True)))
    print(case,ref['kind'],[(r['candidate'],r['equal']) for r in rows if r['case']==case])

compare('mapped_before_edge_loss',a)
db._graph_index.remove_edge(a,b,EdgeType.CONTRADICTS.value)
compare('mapped_record_missing_graph_edge',a)
# Reopen tests recovery and index rebuild while the graph edge remains absent.
db=EmberDB.connect(STORE)
compare('restart_missing_graph_edge',a)

db.create_namespace('private-counter',owner='owner')
p=memory('private-a','private-counter');q=memory('private-b','private-counter')
db.map_conflict(p,q,detected_by='owner')
compare('unauthorized_private_namespace',p,caller='outsider')
compare('authorized_private_namespace',p,caller='owner')

# Tamper an unrelated DOCUMENT in the disposable store after all valid-state checks.
path=db._store.records_dir/(unrelated+'.ember')
raw=decode(path.read_bytes());raw['data']['content']='tampered after seal';path.write_bytes(encode(raw))
compare('unrelated_record_integrity_failure',a)

with (ROOT/'03_EQUIVALENCE_MATRIX_EXTRA.csv').open('w',newline='',encoding='utf-8') as fh:
    writer=csv.DictWriter(fh,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
(ROOT/'counterexamples.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
