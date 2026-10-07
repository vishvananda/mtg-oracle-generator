"""Rescore saved development generations; makes no model calls."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
from data_utils import digest,write_jsonl
from evaluate import score,summarize
from validate_mtgish import MtgishValidator

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,default=Path('reports/pilot-16k/development-predictions.jsonl'))
    p.add_argument('--schema',type=Path,default=Path('schemas/design-draft-v1.json'))
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Output must be new')
    groups=defaultdict(list)
    for line in a.input.read_text().splitlines():
        row=json.loads(line);groups[row['condition']].append(row)
    schema=json.loads(a.schema.read_text())
    report={'scope':'12 known development prompts per condition; not held-out validation or final test',
            'final_test':False,'measured_at':datetime.now(timezone.utc).isoformat(),
            'predictions_sha256':digest(a.input.read_bytes()),'conditions':{}}
    cases=[]
    with MtgishValidator() as parser:
        for condition,rows in groups.items():
            predictions=[]
            for row in rows:
                prediction=dict(row)
                # The historical Luna product prompt explicitly requested this
                # wrapper. Extract its card payload without changing any text.
                if row.get('response_format')=='draft_and_image_prompt':
                    prediction['raw_text']=json.dumps(json.loads(row['raw_text'])['draft'],ensure_ascii=False)
                predictions.append(prediction)
            results=score(rows,predictions,schema,parser.check)
            report['conditions'][condition]=summarize(results)
            cases.extend({'condition':condition,**r} for r in results)
            print(condition,report['conditions'][condition]['mtgish_accepted'],flush=True)
        report['parser']=parser.provenance
    a.output.mkdir(parents=True)
    write_jsonl(a.output/'cases.jsonl',cases)
    (a.output/'metrics.json').write_text(json.dumps(report,indent=2)+'\n')
