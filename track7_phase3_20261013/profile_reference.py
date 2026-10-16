"""Representative healthy no-conflict profile in a fresh disposable store."""
import cProfile
import io
from pathlib import Path
import pstats
import shutil

from embers.db import EmberDB
from embers.core.record import EmberRecord
from embers.core.types import RecordType
from bench_conflicts import type_candidate

ROOT=Path(__file__).resolve().parent
STORE=ROOT/'store_run_20261013_04'
if STORE.exists():raise RuntimeError('fresh store required')
if shutil.disk_usage(ROOT).free<500_000_000:raise RuntimeError('disk headroom too low')
db=EmberDB.connect(STORE)
ids=[]
for i in range(101):
    ids.append(db.write(EmberRecord(namespace='profile',record_type=RecordType.DOCUMENT,
                       data={'content':f'unrelated-{i}'},written_by='research')))
target=ids[0]
for name,fn in [('reference',lambda:db.conflicts_for(target)),
                ('type_candidate',lambda:type_candidate(db,target))]:
    p=cProfile.Profile();p.enable();result=fn();p.disable();assert result==[]
    text=io.StringIO();pstats.Stats(p,stream=text).sort_stats('cumtime').print_stats(35)
    (ROOT/f'profile_{name}_101.txt').write_text(text.getvalue(),encoding='utf-8')
