"""Paired greedy SFT/DPO development diagnostic with an independent intent judge."""
import argparse
from collections import Counter
import json
from pathlib import Path

from data_utils import write_jsonl
from paths import PROJECT
from rl_data import sha_file
from rl_review import judge
from rl_rewards import inspect
from validate_mtgish import MtgishValidator


def run(cases_path,sft,dpo,output,model='gpt-6.1-sol'):
    cases={r['id']:r for r in map(json.loads,cases_path.read_text().splitlines())}
    predictions={arm:{r['case_id']:r for r in map(json.loads,path.read_text().splitlines())}
                 for arm,path in [('sft',sft),('dpo',dpo)]}
    for arm,path in [('sft',sft),('dpo',dpo)]:
        receipt=json.loads(path.with_suffix('.manifest.json').read_text())
        if not receipt['complete'] or receipt['sha256']!=sha_file(path) or set(predictions[arm])!=set(cases):
            raise ValueError('Incomplete or changed development generations')
    before=json.loads(sft.with_suffix('.manifest.json').read_text())
    after=json.loads(dpo.with_suffix('.manifest.json').read_text())
    if before['decoding']!=after['decoding'] or before['backend']!=after['backend']:
        raise ValueError('Development generation budgets/backends differ')
    output.mkdir(parents=True,exist_ok=True)
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text());schema['required']+=['name','rarity']
    checks={};pending=[]
    with MtgishValidator() as parser:
        for arm,rows in predictions.items():
            for case_id,row in rows.items():
                case=cases[case_id];key=arm+'-'+case_id
                checks[key]=inspect(case,row['raw'],schema,parser.check,row['hit_token_limit'])
                if checks[key]['gate']=='needs_intent_judge':
                    pending.append({**case,'id':key,'candidate':json.loads(row['raw'])})
        parser_provenance=parser.provenance
    judgments,receipts=judge(pending,output/'judge',model)
    results=[];metrics={}
    for arm,rows in predictions.items():
        counts=Counter()
        for case_id,row in rows.items():
            key=arm+'-'+case_id;check=checks[key];j=judgments.get(key)
            valid=check['schema_valid'];parsed=check.get('mtgish',{}).get('parse_complete') is True
            faithful=bool(j and j['verdict']=='pass' and j['confidence']=='high' and not j['violations'])
            counts.update(cases=1,schema_valid=int(valid),mtgish_accepted=int(parsed),intent_faithful=int(faithful),
                          faithful_and_parsed=int(faithful and parsed),abstentions=int(j is not None and (j['verdict']=='uncertain' or j['confidence']!='high')))
            results.append({'arm':arm,'case_id':case_id,'request':cases[case_id]['request'],'raw':row['raw'],
                            'checks':check,'judgment':j,'intent_faithful':faithful})
        metrics[arm]=dict(counts)
    write_jsonl(output/'cases.jsonl',results)
    result={'scope':'64 correlated hand-authored development requests; not the locked SFT test or a representative accuracy estimate',
        'cases_sha256':sha_file(cases_path),'sft_sha256':sha_file(sft),'dpo_sha256':sha_file(dpo),
        'metrics':metrics,'judge_model':model,'judge_effort':'medium','parser':parser_provenance,
        'decoding':before['decoding'],'judge_usage':[r['usage'] for r in receipts],
        'automatic_promotion':False,'final_test_used':False}
    (output/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('cases','sft','dpo','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--model',default='gpt-6.1-sol');a=p.parse_args()
    print(json.dumps(run(a.cases,a.sft,a.dpo,a.output,a.model),indent=2))
