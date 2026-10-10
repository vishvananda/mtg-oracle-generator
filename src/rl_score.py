"""Score pre-generated pilot candidates on the host; no GPU job or training."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time

from judge_transport import invoke_judge
from data_utils import digest,write_jsonl
from paths import PROJECT
from rl_data import checked_file,sha_file
from rl_rewards import inspect,combine,judge_payload,judge_schema,align_judgments
from validate_mtgish import MtgishValidator


def bind_candidates(cases,rows):
    bound=[];seen=set()
    for row in rows:
        if row['case_id'] not in cases or row['candidate_id'] in seen or not isinstance(row['raw'],str):
            raise ValueError('Unknown case, duplicate candidate ID or non-string raw output')
        if type(row.get('hit_token_limit',False)) is not bool:raise ValueError('Invalid truncation flag')
        seen.add(row['candidate_id']);bound.append((cases[row['case_id']],row))
    return bound


def score(pilot,candidates,output,model,effort='medium'):
    started=time.monotonic()
    request={'pilot_manifest_sha256':sha_file(pilot/'manifest.json'),'candidates_sha256':sha_file(candidates),
        'judge_model':model,'judge_effort':effort,'rubric_sha256':sha_file(PROJECT/'prompts/reward-judge-v1.txt'),
        'code_files':{n:sha_file(PROJECT/'src'/n) for n in ('rl_score.py','rl_rewards.py','judge_transport.py','validate_mtgish.py')}}
    if output.exists():
        if not (output/'request.json').exists() or json.loads((output/'request.json').read_text())!=request:
            raise ValueError('Existing scoring directory belongs to different inputs')
    else:
        output.mkdir(parents=True);(output/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    manifest=json.loads((pilot/'manifest.json').read_text())
    if manifest['source_split']!='train' or manifest['test_or_reserve_used']:
        raise ValueError('Scoring pilot must contain training data only')
    with checked_file(pilot,'cases.jsonl',manifest).open() as stream:cases={c['id']:c for c in map(json.loads,stream)}
    with candidates.open() as stream:bound=bind_candidates(cases,list(map(json.loads,stream)))
    counts=Counter(row['case_id'] for _,row in bound)
    if any(n>manifest['candidates_per_prompt'] for n in counts.values()):
        raise ValueError('Candidate count exceeds prepared budget')
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    schema['required']+=['name','rarity']
    reports={};references={};pending=[];cache_file=output/'deterministic.json'
    if cache_file.exists():
        cached=json.loads(cache_file.read_text());reports=cached['reports'];references=cached['references'];provenance=cached['parser']
        if sha_file(cache_file)!=(output/'deterministic.sha256').read_text().strip():raise ValueError('Cached checks changed')
    else:
        with MtgishValidator() as parser:
            cache={}
            def check(card):
                key=digest(card)
                if key not in cache:cache[key]=parser.check(card)
                return cache[key]
            for index,(case,row) in enumerate(bound):
                cid=row['candidate_id']
                reports[cid]=inspect(case,row['raw'],schema,check,row.get('hit_token_limit',False))
                if case['id'] not in references:references[case['id']]=check(case['reference'])
                if (index+1)%32==0:print(json.dumps({'parsed':index+1,'scheduled':len(bound)}),flush=True)
            provenance=parser.provenance
        cache_file.write_text(json.dumps({'reports':reports,'references':references,'parser':provenance})+'\n')
        (output/'deterministic.sha256').write_text(sha_file(cache_file)+'\n')
    for case,row in bound:
        if reports[row['candidate_id']]['gate']=='needs_intent_judge':
            pending.append({**case,'id':row['candidate_id'],'candidate':json.loads(row['raw'])})
    # Input order cannot disclose which candidate was the greedy or best completion.
    pending.sort(key=lambda c:digest(c['id']));judgments={};receipts=[]
    for start in range(0,len(pending),9):
        batch=pending[start:start+9]
        value,receipt=invoke_judge(model,batch,output/f'judge-{start//9:04d}',
                             prefix_root=output/'prefixes',reasoning_effort=effort)
        judgments.update(align_judgments(value,batch));receipts.append(receipt)
        print(json.dumps({'judged':len(judgments),'scheduled':len(pending)}),flush=True)
    scored=[{**row,'assessment':combine(reports[row['candidate_id']],judgments.get(row['candidate_id']),references[case['id']]),
             'reference_parser':references[case['id']]} for case,row in bound]
    file=write_jsonl(output/'scored.jsonl',scored)
    result={'rows':len(scored),'cases':len(counts),'judged':len(judgments),
            'eligible':sum(r['assessment']['eligible'] for r in scored),
            'gates':dict(Counter(r['assessment']['gate'] for r in scored)),
            'pilot_manifest_sha256':sha_file(pilot/'manifest.json'),'candidates_sha256':sha_file(candidates),
            'rubric_sha256':sha_file(PROJECT/'prompts/reward-judge-v1.txt'),
            'reward_code_sha256':sha_file(PROJECT/'src/rl_rewards.py'),'score_code_sha256':sha_file(Path(__file__)),
            'judge_model':model,'judge_effort':effort,'parser':provenance,
            'file':file,'seconds':time.monotonic()-started,'judge_batch_usage':[r['usage'] for r in receipts],
            'initialization_receipts':sorted({r['base_initialization_receipt'] for r in receipts}),
            'automatic_training':False,'independently_reviewed':False,'test_set_used':False}
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('pilot','candidates','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--judge-model',required=True);p.add_argument('--effort',default='medium')
    a=p.parse_args();print(json.dumps(score(a.pilot,a.candidates,a.output,a.judge_model,a.effort),indent=2))
