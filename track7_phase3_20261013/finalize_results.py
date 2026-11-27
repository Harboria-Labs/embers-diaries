"""Combine already captured equivalence rows; does not open any store."""
import csv
import json
from pathlib import Path
import shutil

root=Path(__file__).resolve().parent
main=list(csv.DictReader((root/'03_EQUIVALENCE_MATRIX_MAIN.csv').open(encoding='utf-8')))
extra=list(csv.DictReader((root/'03_EQUIVALENCE_MATRIX_EXTRA.csv').open(encoding='utf-8')))
fields=list(main[0])
rows=main+[{key:row.get(key,'') for key in fields} for row in extra]
restart=json.loads((root/'restart_metadata.json').read_text(encoding='utf-8'))
rows.append(dict(case=restart['case'],candidate='type_metadata',equal=False,
    reference_kind=restart['reference']['kind'],candidate_kind=restart['candidate']['kind'],
    reference_result=json.dumps(restart['reference'],sort_keys=True),
    candidate_result=json.dumps(restart['candidate'],sort_keys=True),
    record_count=3,graph_degree=1))
with (root/'03_EQUIVALENCE_MATRIX.csv').open('w',newline='',encoding='utf-8') as fh:
    writer=csv.DictWriter(fh,fieldnames=fields);writer.writeheader();writer.writerows(rows)
prior=json.loads((root/'summary.json').read_text(encoding='utf-8'))
prior['all_equivalence_rows']=len(rows)
prior['all_cases']=len(set(row['case'] for row in rows))
prior['all_mismatches']=[{'case':row['case'],'candidate':row['candidate']}
                         for row in rows if str(row['equal']).lower()=='false']
prior['free_at_reporting']=shutil.disk_usage(root).free
prior['disk_alert']='free space fell externally from about 543 MB to about 101 MB; optional profile preflight stopped'
(root/'summary.json').write_text(json.dumps(prior,indent=2),encoding='utf-8')
print(len(rows),len(prior['all_mismatches']),prior['free_at_reporting'])
