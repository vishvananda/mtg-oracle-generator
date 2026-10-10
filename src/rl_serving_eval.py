"""Reproducible production-prompt generation and blind evaluation of local GGUFs."""
import argparse,json,time,urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

from data_utils import digest,write_jsonl
from paths import PROJECT
from rl_data import sha_file
from rl_rewards import inspect
from rl_teacher import batches,REVIEW,prompt,faithful
from rl_parser_cache import ParserCache


def generate(cases_path,system,model_sha,endpoint,output):
    cases=list(map(json.loads,cases_path.read_text().splitlines()))
    identity={'cases_sha256':sha_file(cases_path),'system_sha256':digest(system.encode()),'model_sha256':model_sha,
        'decoding':{'temperature':0,'seed':17,'max_tokens':2048,'enable_thinking':False},'backend':'llama.cpp Q4_0 CPU',
        'scope':'production prompt with terminology guide; no schema-constrained decoding'}
    output.parent.mkdir(parents=True,exist_ok=True)
    receipt_path=output.with_suffix('.manifest.json')
    if receipt_path.exists():
        old=json.loads(receipt_path.read_text())
        if any(old[k]!=v for k,v in identity.items()):raise ValueError('Serving evaluation inputs changed')
        if old.get('complete'):
            if old['sha256']!=sha_file(output):raise ValueError('Serving evaluation output changed')
            return old
    else:receipt_path.write_text(json.dumps({**identity,'complete':False},indent=2)+'\n')
    done={}
    if output.exists():
        for line in output.read_text().splitlines():
            row=json.loads(line)
            if row['case_id'] in done:raise ValueError('Duplicate saved output')
            done[row['case_id']]=row
    if not set(done)<={c['id'] for c in cases}:raise ValueError('Unknown saved case')
    with output.open('a') as stream:
        for case in sorted(cases,key=lambda c:(len(c['request']),c['id'])):
            if case['id'] in done:continue
            body={'messages':[{'role':'system','content':system},{'role':'user','content':case['request']}],
                'temperature':0,'seed':17,'max_tokens':2048,'chat_template_kwargs':{'enable_thinking':False}}
            req=urllib.request.Request(endpoint+'/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            before=time.monotonic()
            with urllib.request.urlopen(req,timeout=300) as f:value=json.load(f)
            choice=value['choices'][0]
            row={'case_id':case['id'],'candidate_id':case['id']+'-00','raw':choice['message']['content'],
                'hit_token_limit':choice['finish_reason']=='length','output_tokens':value.get('usage',{}).get('completion_tokens'),
                'seconds':time.monotonic()-before}
            stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();done[case['id']]=row
            print(json.dumps({'generation':output.name,'completed':len(done),'scheduled':len(cases)}),flush=True)
    receipt={**identity,'complete':True,'sha256':sha_file(output),'cases':len(cases),'completions':len(done)}
    receipt_path.write_text(json.dumps(receipt,indent=2)+'\n');return receipt


def evaluate(cases_path,predictions,output,workers=4):
    cases={c['id']:c for c in map(json.loads,cases_path.read_text().splitlines())}
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text());schema['required']+=['name','rarity']
    output.mkdir(parents=True,exist_ok=True)
    pending=[];checks={};allrows={};identities={}
    with ParserCache(output/'parser-cache') as parser:
        for arm,path in predictions.items():
            receipt=json.loads(path.with_suffix('.manifest.json').read_text())
            if not receipt['complete'] or receipt['sha256']!=sha_file(path):raise ValueError('Incomplete/changed prediction')
            rows=list(map(json.loads,path.read_text().splitlines()))
            if len(rows)!=len(cases) or {r['case_id'] for r in rows}!=set(cases):raise ValueError('Prediction cases differ')
            identities[arm]=receipt
            for row in rows:
                key=arm+'-'+row['case_id'];case=cases[row['case_id']]
                allrows[key]=(arm,row)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(inspect,cases[row['case_id']],row['raw'],schema,parser.check,row['hit_token_limit']):key
                     for key,(_,row) in allrows.items()}
            for future in as_completed(futures):
                key=futures[future];checks[key]=future.result()
                _,row=allrows[key];case=cases[row['case_id']]
                if checks[key]['schema_valid']:
                    pending.append({'id':key,'request':case['request'],'explicit_metadata':case['explicit_metadata'],
                        'candidate':json.loads(row['raw'])})
        parser_provenance=parser.provenance
    judgments=batches(pending,prompt('reward-judge-v3.txt'),REVIEW,output/'judge',workers)
    counts={arm:Counter() for arm in predictions};results=[]
    for key,(arm,row) in allrows.items():
        c=cases[row['case_id']];a=checks[key];j=judgments.get(key)
        good=(a['gate']=='needs_intent_judge' and bool(j) and j['verdict']=='pass'
              and j['confidence']=='high' and not j['violations'])
        clean=good and faithful(j)
        parsed=a.get('mtgish',{}).get('parse_complete') is True
        counts[arm].update(cases=1,schema_valid=int(a['schema_valid']),intent_faithful=int(good),
            mtgish_accepted=int(parsed),faithful_and_parsed=int(good and parsed),
            faithful_clean_oracle=int(clean),parser_infrastructure_error=int(a.get('mtgish',{}).get('status')=='parser_error'),
            exact_metadata_failure=int(bool(a['deterministic_violations'])),
            abstentions=int(j is not None and (j['verdict']=='uncertain' or j['confidence']!='high')))
        results.append({'arm':arm,'case_id':c['id'],'source_group_id':c['source_group_id'],'request':c['request'],
            'raw':row['raw'],'checks':a,'judgment':j,'intent_faithful':good,'faithful_clean_oracle':clean})
    write_jsonl(output/'cases.jsonl',results)
    result={'scope':f'{len(cases)} requests grouped into {len({c["source_group_id"] for c in cases.values()})} families; new-request diagnostic, not unseen-SFT-card accuracy',
        'cases_sha256':sha_file(cases_path),'metrics':{k:dict(v) for k,v in counts.items()},'predictions':identities,
        'judge_model':'gpt-6.1-sol','judge_effort':'medium','rubric_sha256':digest(prompt('reward-judge-v3.txt')),
        'metric_definitions':{'intent_faithful':'Schema/explicit-field gates and high-confidence pass without violations; style reported separately',
            'faithful_clean_oracle':'intent_faithful plus highest Oracle style score (2)'},
        'parser':parser_provenance,'automatic_promotion':False}
    (output/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    gen=sub.add_parser('generate');gen.add_argument('--cases',type=Path,required=True)
    gen.add_argument('--system',type=Path,required=True);gen.add_argument('--model-sha',required=True)
    gen.add_argument('--endpoint',required=True);gen.add_argument('--output',type=Path,required=True)
    ev=sub.add_parser('evaluate');ev.add_argument('--cases',type=Path,required=True);ev.add_argument('--output',type=Path,required=True)
    ev.add_argument('--prediction',action='append',required=True,help='arm=/path/to/generations.jsonl');ev.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if a.action=='generate':result=generate(a.cases,a.system.read_text(),a.model_sha,a.endpoint,a.output)
    else:result=evaluate(a.cases,{k:Path(v) for k,v in (s.split('=',1) for s in a.prediction)},a.output,a.workers)
    print(json.dumps(result,indent=2))
