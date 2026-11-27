"""Summarize retained raw runs without discarding failures or outliers."""
import csv
import json
from pathlib import Path
from statistics import median

root=Path(__file__).resolve().parent
run30=json.loads((root/'summary_30.json').read_text(encoding='utf-8'))
run50=json.loads((root/'summary_50_retry.json').read_text(encoding='utf-8'))
rows=list(csv.DictReader((root/'03_RAW_RESULTS_50_RETRY.csv').open(encoding='utf-8')))
failed=list(csv.DictReader((root/'03_RAW_RESULTS_50.csv').open(encoding='utf-8')))
checkpoints={}
for n in [5,10,20,30,50]:
    append=next(row for row in rows if row['operation']=='append' and int(row['index'])==n)
    def vals(operation,key='wall_ms'):
        return [float(row[key]) for row in rows if row['operation']==operation and int(row['case'])==n]
    recent=[float(row['wall_ms']) for row in rows if row['operation']=='append' and n-4<=int(row['index'])<=n]
    checkpoints[str(n)]={'append_ms':float(append['wall_ms']),
        'last_5_append_median_ms':median(recent),'load_same_median_ms':median(vals('load_same')),
        'load_new_median_ms':median(vals('load_new')),'project_median_ms':median(vals('project')),
        'load_same_range_ms':[min(vals('load_same')),max(vals('load_same'))],
        'project_range_ms':[min(vals('project')),max(vals('project'))],
        'journal_bytes':int(append['journal_bytes']),'wal_bytes':int(append['wal_bytes']),
        'observation_bytes':int(append['observation_bytes']),
        'store_bytes':int(append['store_bytes']),'free_after':int(append['free_after']),
        'append_nested_load_ms':float(append['load_ms']),
        'append_store_write_ms':float(append['store_write_ms']),
        'append_observation_append_ms':float(append['observation_append_ms'])}
summary={'run30':run30,'run50':run50,'checkpoints':checkpoints,
         'failed_run02':{'failed_rows':[row for row in failed if row['error']],
                         'result':'first append failed with PermissionError; isolated partial store retained'},
         'measurement_note':'timed components overlap; no arithmetic decomposition of wall time'}
(root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(checkpoints,indent=2))
