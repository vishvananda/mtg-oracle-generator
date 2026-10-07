"""Score every frozen evaluation case, retaining all failures in the denominator."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from data_utils import digest,write_jsonl
from hub_dataset import verify
from validate_mtgish import MtgishValidator


def score(cases,predictions,schema,check):
    identifiers=[r['example_id'] for r in cases]
    if len(set(identifiers))!=len(identifiers): raise ValueError('Duplicate evaluation ID')
    by_id={}
    for row in predictions:
        key=row['example_id']
        if key in by_id: raise ValueError('Duplicate prediction ID')
        by_id[key]=row
    if set(by_id)-set(identifiers): raise ValueError('Unknown prediction ID')
    validator=Draft202012Validator(schema)
    results=[]
    for case in cases:
        item={'example_id':case['example_id'],'source_group_id':case.get('source_group_id'),
              'style':case.get('style','unknown'),'reference_type':case.get('target',{}).get('type_line','unknown'),
              'json_object':False,'schema_valid':False,'mtgish_accepted':False,
              'prediction_status':'missing','mtgish':{'status':'not_attempted','parse_complete':False}}
        prediction=by_id.get(case['example_id'])
        if prediction is not None:
            item['prediction_status']='generated'
            item['hit_token_limit']=bool(prediction.get('hit_token_limit',False))
            try:
                actual=json.loads(prediction['raw_text'])
                item['json_object']=isinstance(actual,dict)
                errors=sorted(e.message for e in validator.iter_errors(actual))
                item['schema_valid']=not errors
                item['schema_errors']=errors
                if item['schema_valid']:
                    result=check(actual)
                    item['mtgish']=result
                    item['mtgish_accepted']=result.get('status')=='parsed' and result.get('parse_complete') is True
                    item['has_oracle_text']=any(f.get('oracle_text','').strip() for f in actual.get('card_faces') or [actual])
            except (ValueError,TypeError,KeyError) as error:
                item['prediction_status']='invalid_json' if 'raw_text' in prediction else 'generation_error'
                item['error']=str(error)
        results.append(item)
    return results


def aggregate(rows):
    total=len(rows)
    accepted=sum(r['mtgish_accepted'] for r in rows)
    attempted=sum(r['mtgish']['status'] in ('parsed','rejected') for r in rows)
    return {'cases':total,'json_objects':sum(r['json_object'] for r in rows),
            'schema_valid':sum(r['schema_valid'] for r in rows),'mtgish_accepted':accepted,
            'mtgish_acceptance_all_cases':accepted/total if total else None,
            'mtgish_attempted':attempted,'mtgish_acceptance_attempted':accepted/attempted if attempted else None,
            'prediction_status':dict(Counter(r['prediction_status'] for r in rows)),
            'mtgish_status':dict(Counter(r['mtgish']['status'] for r in rows)),
            'accepted_with_rewrites':sum(r['mtgish_accepted'] and bool(r['mtgish'].get('applied_rewrites')) for r in rows),
            'accepted_without_rewrites':sum(r['mtgish_accepted'] and not r['mtgish'].get('applied_rewrites') for r in rows),
            'hit_token_limit':sum(r.get('hit_token_limit',False) for r in rows)}


def summarize(results):
    summary=aggregate(results)
    for field in ('style','reference_type'):
        groups=defaultdict(list)
        for row in results: groups[row[field]].append(row)
        summary['by_'+field]={key:aggregate(rows) for key,rows in sorted(groups.items())}
    summary['with_generated_rules_text']=aggregate([r for r in results if r.get('has_oracle_text')])
    summary['interpretation']='Whole-card parser recognition, not intent fidelity, balance, official legality, or exact Oracle correctness.'
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--predictions',type=Path,required=True)
    p.add_argument('--split',choices=['validation','test'],default='validation')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists(): p.error('Evaluation output must be new')
    manifest=verify(a.dataset)
    if a.split=='test' and manifest['status']!='complete': p.error('Final test requires a complete frozen release')
    name=a.split+'.jsonl'
    if name not in manifest['files']: p.error('Split missing from dataset manifest')
    cases=[json.loads(line) for line in (a.dataset/name).read_text().splitlines()]
    predictions=[json.loads(line) for line in a.predictions.read_text().splitlines()]
    receipt_path=a.predictions.with_suffix('.manifest.json')
    generation=json.loads(receipt_path.read_text())
    if generation['dataset_manifest_sha256']!=digest((a.dataset/'manifest.json').read_bytes()):
        p.error('Predictions belong to another dataset')
    if generation['split']!=a.split or generation['predictions_sha256']!=digest(a.predictions.read_bytes()):
        p.error('Prediction split/hash mismatch')
    schema=json.loads((a.dataset/'draft-schema.json').read_text())
    with MtgishValidator() as parser:
        results=score(cases,predictions,schema,parser.check)
        summary=summarize(results)
        summary.update(split=a.split,final_test=a.split=='test',measured_at=datetime.now(timezone.utc).isoformat(),
            dataset_manifest_sha256=digest((a.dataset/'manifest.json').read_bytes()),
            predictions_sha256=digest(a.predictions.read_bytes()),parser=parser.provenance,generation=generation)
    a.output.mkdir(parents=True)
    write_jsonl(a.output/'cases.jsonl',results)
    (a.output/'metrics.json').write_text(json.dumps(summary,indent=2)+'\n')
    (a.output/'README.md').write_text(
        f'# {a.split.title()} generation evaluation\n\n'
        f'Cases: {summary["cases"]:,}; JSON objects: {summary["json_objects"]:,}; schema valid: {summary["schema_valid"]:,}.\n\n'
        f'mtgish accepted **{summary["mtgish_accepted"]:,}/{summary["cases"]:,}** '
        f'({100*summary["mtgish_acceptance_all_cases"]:.2f}%) across **all** scheduled cases. '
        f'There were {summary["mtgish_attempted"]:,} grammar attempts. Missing generations, schema failures, '
        'ignored cards, out-of-scope inputs and parser errors are not passes.\n\n'
        'See `metrics.json` for conditional rates, rewrite counts, detail-level/type breakdowns and revision/hash provenance; '
        '`cases.jsonl` retains diagnostics. Parser recognition does not prove intent fidelity or official rules correctness.\n')
    print(json.dumps({k:summary[k] for k in ('cases','schema_valid','mtgish_accepted','mtgish_acceptance_all_cases')},indent=2))
