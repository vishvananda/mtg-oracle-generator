"""Independent blinded review of preference pairs and generation diagnostics."""
import argparse
from collections import Counter
import json
from pathlib import Path

from judge_transport import invoke_judge
from data_utils import digest,write_jsonl
from rl_data import sha_file,checked_file
from rl_rewards import judge_payload,judge_schema,align_judgments


def judge(cases,output,model,effort='medium'):
    output.mkdir(parents=True,exist_ok=True)
    ordered=sorted(cases,key=lambda c:digest(['independent-review',c['id']]))
    results={};receipts=[]
    for start in range(0,len(ordered),9):
        batch=ordered[start:start+9];folder=output/f'batch-{start//9:04d}'
        value,receipt=invoke_judge(model,batch,folder,prefix_root=output/'prefixes',reasoning_effort=effort)
        results.update(align_judgments(value,batch));receipts.append(receipt)
        print(json.dumps({'reviewed':len(results),'scheduled':len(cases),'model':model}),flush=True)
    return results,receipts


def review_pairs(pilot,pairs,output,model,effort):
    manifest=json.loads((pairs/'manifest.json').read_text())
    pm=json.loads((pilot/'manifest.json').read_text())
    if manifest['pilot_manifest_sha256']!=sha_file(pilot/'manifest.json'):raise ValueError('Pilot identity mismatch')
    rows=[json.loads(l) for l in checked_file(pairs,'preferences.jsonl',manifest).open()]
    audit=[json.loads(l) for l in checked_file(pairs,'audit.jsonl',manifest).open()]
    if len(rows)!=len(audit) or len(rows)!=manifest['pairs']:raise ValueError('Preference audit row count differs')
    cases={c['id']:c for c in map(json.loads,checked_file(pilot,'cases.jsonl',pm).open())}
    checks=[]
    for row,meta in zip(rows,audit):
        case=cases[meta['case_id']]
        for key in ('chosen','rejected'):
            checks.append({'id':meta[key],'request':case['request'],'explicit_metadata':case['explicit_metadata'],
                           'candidate':json.loads(row[key][0]['content'])})
    judgments,receipts=judge(checks,output/'judge',model,effort)
    approved=[];evidence=[];rejected=[]
    for row,meta in zip(rows,audit):
        chosen=judgments[meta['chosen']];bad=judgments[meta['rejected']]
        usable=(chosen['verdict']=='pass' and chosen['confidence']=='high' and chosen['oracle_style']==2
                and not chosen['violations'] and bad['verdict']=='fail' and bad['confidence']=='high')
        evidence.append({**meta,'approved':usable,'independent_chosen':chosen,'independent_rejected':bad})
        if usable:approved.append(row)
        else:rejected.append(meta['case_id'])
    file=write_jsonl(output/'preferences.jsonl',approved);write_jsonl(output/'audit.jsonl',evidence)
    result={'independent_review':True,'judge_model':model,'judge_effort':effort,'input_pairs':len(rows),
        'approved_pairs':len(approved),'approved_pairs_sha256':file['sha256'],'rejected_case_ids':rejected,
        'input_manifest_sha256':sha_file(pairs/'manifest.json'),'pilot_manifest_sha256':sha_file(pilot/'manifest.json'),
        'judge_usage':[r['usage'] for r in receipts],'final_test_used':False,
        'note':'Independent stronger-model review, not human expert labels'}
    (output/'review.json').write_text(json.dumps(result,indent=2)+'\n');return result


def review_cases(cases_path,output,model,effort):
    cases=[json.loads(l) for l in cases_path.open()]
    judgments,receipts=judge(cases,output/'judge',model,effort)
    rows=[{**c,'judgment':judgments[c['id']]} for c in cases]
    write_jsonl(output/'reviews.jsonl',rows)
    labeled=[c for c in cases if 'expected' in c]
    result={'cases':len(cases),'judge_model':model,'judge_effort':effort,'cases_sha256':sha_file(cases_path),
        'verdicts':dict(Counter(j['verdict'] for j in judgments.values())),
        'agreement':sum(judgments[c['id']]['verdict']==c['expected'] for c in labeled),
        'labeled_cases':len(labeled),'false_accepts':[c['id'] for c in labeled if c['expected']=='fail'
            and judgments[c['id']]['verdict']=='pass' and judgments[c['id']]['confidence']=='high'],
        'disagreements':[c['id'] for c in labeled if judgments[c['id']]['verdict']!=c['expected']],
        'judge_usage':[r['usage'] for r in receipts],'final_test_used':False}
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',type=Path)
    p.add_argument('--pilot',type=Path);p.add_argument('--pairs',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default='gpt-6.1-sol')
    p.add_argument('--effort',default='medium');a=p.parse_args()
    if a.cases:result=review_cases(a.cases,a.output,a.model,a.effort)
    elif a.pilot and a.pairs:result=review_pairs(a.pilot,a.pairs,a.output,a.model,a.effort)
    else:p.error('Supply --cases or --pilot and --pairs')
    print(json.dumps(result,indent=2))
