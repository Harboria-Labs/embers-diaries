"""Persisted-index counterexample in a new disposable store only."""
import json
from pathlib import Path
import shutil
from embers.db import EmberDB
from embers.core.record import EmberRecord
from embers.core.types import RecordType
from bench_conflicts import type_candidate,capture

ROOT=Path(__file__).resolve().parent
STORE=ROOT/'store_run_20261013_03'
if STORE.exists():raise RuntimeError('fresh store required')
if shutil.disk_usage(ROOT).free<500_000_000:raise RuntimeError('disk headroom too low')
db=EmberDB.connect(STORE)
def memory(name):return db.write(EmberRecord(namespace='restart',record_type=RecordType.DOCUMENT,
                            data={'content':name},written_by='research'))
a=memory('a');b=memory('b');cid=db.map_conflict(a,b,detected_by='research')
db.checkpoint()
index=STORE/'indexes'/'master.json'
document=json.loads(index.read_text(encoding='utf-8'))
assert document['records'][cid]['record_type']==RecordType.CONFLICT.value
document['records'][cid]['record_type']=RecordType.DOCUMENT.value
index.write_text(json.dumps(document),encoding='utf-8')
db=EmberDB.connect(STORE)
ref=capture(lambda:db.conflicts_for(a))
got=capture(lambda:type_candidate(db,a))
assert ref['kind']=='value' and len(ref['value'])==1 and got['kind']=='value' and not got['value']
out={'case':'persisted_stale_type_metadata_after_restart','reference':ref,'candidate':got,
     'equal':ref==got,'index_path':str(index.resolve())}
(ROOT/'restart_metadata.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'case':out['case'],'equal':out['equal'],'reference_count':len(ref['value']),
                  'candidate_count':len(got['value'])}))
