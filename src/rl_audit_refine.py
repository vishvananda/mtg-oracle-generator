"""Apply documented spot-check repairs, then blindly recheck all audit pairs."""
import argparse,json,shutil
from collections import Counter
from pathlib import Path

from data_utils import write_jsonl
from paths import PROJECT
from rl_data import sha_file
from rl_parser_cache import ParserCache
from rl_rewards import inspect
from rl_teacher import batches,prompt,REPAIR,REVIEW,faithful,wrong


def refine(review,corrections,output,workers=4):
    output.mkdir(parents=True,exist_ok=True)
    previous=json.loads((review/'review.json').read_text())
    if sha_file(review/'preferences.jsonl')!=previous['approved_pairs_sha256']:raise ValueError('Audit changed')
    items=json.loads(corrections.read_text());excluded=items['exclude'];repair=items['repair']
    rows=list(map(json.loads,(review/'preferences.jsonl').read_text().splitlines()))
    metadata=[r for r in map(json.loads,(review/'audit.jsonl').read_text().splitlines()) if r['approved']]
    cases={r['id']:r for r in map(json.loads,(review/'requirements.jsonl').read_text().splitlines())}
    if len(rows)!=len(metadata):raise ValueError('Audit rows not aligned')
    kept=[(m,r) for m,r in zip(metadata,rows) if m['case_id'] not in excluded]
    pending=[]
    for meta,row in kept:
        key=meta['case_id']
        if key in repair:
            pending.append({'id':key,'request':cases[key]['request'],'explicit_metadata':cases[key]['explicit_metadata'],
                'candidate':json.loads(row['chosen'][0]['content']),'feedback':repair[key]})
    repaired=batches(pending,prompt('intent-repair-v1.txt'),REPAIR,output/'repairs',workers)
    checks={};judge_cases=[];schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text());schema['required']+=['name','rarity']
    with ParserCache(review/'parser-cache') as parser:
        for meta,row in kept:
            key=meta['case_id']
            if key in repaired:
                row['chosen'][0]['content']=repaired[key]['draft_json']
                meta['previous_chosen']=meta['chosen'];meta['chosen']=key+'-spotcheck-repair';meta['chosen_origin']='sol_repair'
            for role in ('chosen','rejected'):
                raw=row[role][0]['content'];cid=meta[role]
                checks[cid]=inspect(cases[key],raw,schema,parser.check)
                if checks[cid]['schema_valid']:
                    judge_cases.append({'id':cid,'request':cases[key]['request'],
                        'explicit_metadata':cases[key]['explicit_metadata'],'candidate':json.loads(raw)})
        provenance=parser.provenance
    judgments=batches(judge_cases,prompt('reward-judge-v3.txt'),REVIEW,output/'final-blind-review',workers,effort='high')
    pairs=[];evidence=[]
    for meta,row in kept:
        good=judgments.get(meta['chosen'],{'verdict':'uncertain'});bad=judgments.get(meta['rejected'],{'verdict':'uncertain'})
        eligible=(checks[meta['chosen']]['gate']=='needs_intent_judge' and faithful(good)
            and (wrong(bad) or bool(checks[meta['rejected']]['deterministic_violations'])))
        evidence.append({**meta,'approved':eligible,'final_chosen':good,'final_rejected':bad,
            'chosen_checks':checks[meta['chosen']],'rejected_checks':checks[meta['rejected']]})
        if eligible:pairs.append(row)
    file=write_jsonl(output/'preferences.jsonl',pairs);write_jsonl(output/'audit.jsonl',evidence)
    for name in ('requirements.jsonl','candidates-reviewed.jsonl'):shutil.copyfile(review/name,output/name)
    # Retain repaired outputs and their actual checks, in addition to the original audit.
    with (output/'candidates-reviewed.jsonl').open('a') as stream:
        for meta,row in kept:
            if 'previous_chosen' in meta:
                stream.write(json.dumps({'case_id':meta['case_id'],'candidate_id':meta['chosen'],'raw':row['chosen'][0]['content'],
                    'origin':'sol_repair','assessment':checks[meta['chosen']],'judgment':judgments.get(meta['chosen'])})+'\n')
    result={**previous,'version':'sol-repair-preferences-v3','approved_pairs':len(pairs),'approved_pairs_sha256':file['sha256'],
        'chosen_origins':dict(Counter(m['chosen_origin'] for m in evidence if m['approved'])),
        'spotcheck_exclusions':excluded,'spotcheck_repairs':len(repaired),'source_review_sha256':sha_file(review/'review.json'),
        'corrections_sha256':sha_file(corrections),'rubric_sha256':sha_file(PROJECT/'prompts/reward-judge-v3.txt'),
        'final_review_effort':'high','parser':provenance,'status':'Refined audit ready for quality review'}
    (output/'review.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('review','corrections','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();print(json.dumps(refine(a.review,a.corrections,a.output),indent=2))
