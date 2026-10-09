"""Cached, blinded Sol requirement extraction, repair and preference review."""
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path

from codex_cached_batch import invoke
from data_utils import digest,write_jsonl
from paths import PROJECT
from rl_data import sha_file,checked_file
from rl_rewards import inspect
from rl_parser_cache import ParserCache


def obj(properties):
    return {'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}


STRING={'type':'string'}
STRINGS={'type':'array','items':STRING}
REVIEW=obj({'id':STRING,'verdict':{'enum':['pass','fail','uncertain']},
    'confidence':{'enum':['high','medium','low']},'oracle_style':{'type':'integer','enum':[0,1,2]},'violations':STRINGS})
CHECKLIST=obj({'id':STRING,'requirements':{'type':'array','items':obj({'requirement':STRING,'evidence':STRING})},
    'editable_defaults':STRINGS,'ambiguities':STRINGS,
    'additional_exact_metadata':{'type':'array','items':obj({'field':{'enum':['name','rarity','power','toughness','loyalty','defense']},'value':STRING,'evidence':STRING})}})
REPAIR=obj({'id':STRING,'draft_json':STRING,'notes':STRINGS})


def batch_call(cases,instructions,item,output,prefix_root,model='gpt-6.1-sol',effort='medium',contract_retries=2):
    if not 1<=len(cases)<=9 or len({c['id'] for c in cases})!=len(cases):raise ValueError('Invalid batch')
    labels=[f'c{i}' for i in range(9)]
    item=json.loads(json.dumps(item));item['properties']['id']={'type':'string','enum':labels}
    schema=obj({'results':{'anyOf':[{'type':'null'},{'type':'array','items':item}]}})
    payload={'instructions':instructions,'cases':[{**c,'id':label} for c,label in zip(cases,labels)]}
    identity={'cases_sha256':digest(cases),'model':model,'effort':effort,'schema_sha256':digest(schema),
              'instructions_sha256':digest(instructions)}
    if output.exists():
        if json.loads((output/'identity.json').read_text())!=identity:raise ValueError('Batch input changed')
        receipt=json.loads((output/'receipt.json').read_text())
        if receipt.get('error') or receipt.get('output_sha256')!=digest((output/'output.json').read_bytes()):
            raise ValueError('Incomplete or corrupt cached batch: '+str(output))
        value=json.loads((output/'output.json').read_text())
    else:
        # Store identity even on a failed invocation, preserving replay diagnostics.
        try:value,receipt=invoke(model,payload,schema,output,prefix_root=prefix_root,reasoning_effort=effort)
        finally:
            if output.exists():(output/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    rows=value['results']
    expected=set(labels[:len(cases)])
    if not isinstance(rows,list) or len(rows)!=len(cases) or {r['id'] for r in rows}!=expected:
        if not contract_retries:raise ValueError('Missing, repeated or unknown response label after bounded retries')
        retried,retry_receipt=batch_call(cases,instructions,item,output.with_name(output.name+'-retry'),prefix_root,
            model,effort,contract_retries-1)
        return retried,{**retry_receipt,'usage':receipt['usage']+retry_receipt['usage'],
            'contract_retry_history':[str(output)]+retry_receipt.get('contract_retry_history',[])}
    mapped={r['id']:r for r in rows}
    return [{**mapped[label],'id':case['id']} for case,label in zip(cases,labels)],receipt


def batches(cases,prompt,item,output,workers=4,model='gpt-6.1-sol',effort='medium'):
    output.mkdir(parents=True,exist_ok=True)
    ordered=sorted(cases,key=lambda c:digest(['blind-order-v2',c['id']]))
    groups=[ordered[i:i+9] for i in range(0,len(ordered),9)]
    results={};receipts=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(batch_call,g,prompt,item,output/('batch-'+digest(g)[:20]),
            output.parent/'prefixes',model,effort):g for g in groups}
        for future in as_completed(futures):
            try:rows,receipt=future.result()
            except BaseException:
                for pending in futures:pending.cancel()
                raise
            results.update((r['id'],r) for r in rows);receipts.append(receipt)
            progress={'stage':output.name,'model':model,'completed':len(results),'scheduled':len(cases)}
            (output/'progress.json').write_text(json.dumps(progress)+'\n');print(json.dumps(progress),flush=True)
    write_jsonl(output/'results.jsonl',[results[c['id']] for c in ordered])
    usage=Counter()
    for receipt in receipts:
        for record in receipt['usage']:usage.update(record)
    (output/'usage.json').write_text(json.dumps(dict(usage),indent=2)+'\n')
    return results


def prompt(name):return (PROJECT/'prompts'/name).read_text()


def faithful(j):
    return j['verdict']=='pass' and j['confidence']=='high' and j['oracle_style']==2 and not j['violations']


def wrong(j):return j['verdict']=='fail' and j['confidence']=='high' and bool(j['violations'])


def add_constraints(case,checklist):
    case={**case,'explicit_metadata':dict(case.get('explicit_metadata',{}))}
    multiface=bool(case.get('reference',{}).get('card_faces'))
    multiface=multiface or any(word in case['request'].casefold() for word in ('double-faced','transforming','split card','adventure'))
    for requirement in checklist['requirements']:
        if not requirement['evidence'] or requirement['evidence'].casefold() not in case['request'].casefold():
            raise ValueError('Checklist evidence is not in request: '+case['id'])
    for row in checklist['additional_exact_metadata']:
        key=row['field'];value=row['value'];evidence=row['evidence']
        if not evidence or evidence.casefold() not in case['request'].casefold():raise ValueError('Exact-field evidence missing')
        # Only literal values become extra hard gates. Other semantics remain judge checks.
        if value.casefold() not in evidence.casefold():continue
        if key=='name' and value not in case['request']:continue
        # A front/back face's name or loyalty is not a root-card scalar. The
        # blinded reviewer checks these face-specific requirements from the request.
        if multiface and key!='rarity':continue
        previous=case['explicit_metadata'].get(key)
        if previous is not None and previous!=value:raise ValueError('Extracted metadata conflicts with source constraints')
        case['explicit_metadata'][key]=value
    return case


def reviewer_case(case,row):
    # Deliberately omit source answer, family IDs, generator, parser and repair rationale.
    return {'id':row['candidate_id'],'request':case['request'],'explicit_metadata':case['explicit_metadata'],
            'candidate':json.loads(row['raw'])}


def select_pair(rows,assessments,judgments):
    states=defaultdict(set)
    for r in rows:
        a=assessments[r['candidate_id']];j=judgments.get(r['candidate_id'])
        if not a['schema_valid'] or j is None:continue
        states[digest(json.loads(r['raw']))].add('good' if faithful(j) else 'bad' if wrong(j) else 'unknown')
    if any({'good','bad'}<=values for values in states.values()):return None
    good=[r for r in rows if assessments[r['candidate_id']]['gate']=='needs_intent_judge'
          and faithful(judgments.get(r['candidate_id'],{'verdict':'uncertain'}))]
    bad=[r for r in rows if r['origin']=='sampled_sft' and assessments[r['candidate_id']]['schema_valid']
         and (assessments[r['candidate_id']]['deterministic_violations'] or wrong(judgments.get(r['candidate_id'],{'verdict':'uncertain'})))]
    if not good or not bad:return None
    good.sort(key=lambda r:(r['origin']!='sampled_sft',r['candidate_id']))
    for chosen in good:
        for rejected in sorted(bad,key=lambda r:r['candidate_id']):
            if digest(json.loads(chosen['raw']))!=digest(json.loads(rejected['raw'])):return chosen,rejected
    return None


def run(pilot,candidates,output,workers=4,resume_code_change=False,parser_seed=None):
    output.mkdir(parents=True,exist_ok=True)
    identity={'pilot_sha256':sha_file(pilot/'manifest.json'),'candidates_sha256':sha_file(candidates),
        'code_sha256':sha_file(Path(__file__)),'prompts':{n:sha_file(PROJECT/'prompts'/n) for n in
        ('intent-checklist-v1.txt','intent-repair-v1.txt','reward-judge-v3.txt')},'model':'gpt-6.1-sol','effort':'medium'}
    pin=output/'identity.json'
    if pin.exists() and json.loads(pin.read_text())!=identity:
        previous=json.loads(pin.read_text())
        if not resume_code_change or {k:v for k,v in previous.items() if k!='code_sha256'}!={k:v for k,v in identity.items() if k!='code_sha256'}:
            raise ValueError('Teacher run inputs changed')
        with (output/'implementation-history.jsonl').open('a') as history:
            history.write(json.dumps({'previous':previous,'resumed':identity,'operator_explicit_resume':True})+'\n')
    pin.write_text(json.dumps(identity,indent=2)+'\n')
    manifest=json.loads((pilot/'manifest.json').read_text())
    if manifest['source_split']!='train' or manifest['test_or_reserve_used']:raise ValueError('Training-only input required')
    cases={c['id']:c for c in map(json.loads,checked_file(pilot,'cases.jsonl',manifest).read_text().splitlines())}
    prompts={r['id']:r['prompt'] for r in map(json.loads,checked_file(pilot,'prompts.jsonl',manifest).read_text().splitlines())}
    extraction=[{'id':c['id'],'request':c['request'],'exact_metadata':c['explicit_metadata']} for c in cases.values()]
    checklists=batches(extraction,prompt('intent-checklist-v1.txt'),CHECKLIST,output/'checklists',workers)
    scheduled_requests=len(cases)
    for attempt in range(3):
        valid={};invalid=[]
        for key,case in cases.items():
            try:valid[key]=add_constraints(case,checklists[key])
            except ValueError as error:
                invalid.append({'id':key,'request':case['request'],'exact_metadata':case['explicit_metadata'],
                    'previous_extraction':checklists[key],'validation_error':str(error)})
        if not invalid:break
        if attempt==2:
            write_jsonl(output/'excluded-checklists.jsonl',invalid);break
        corrected=batches(invalid,prompt('intent-checklist-v1.txt')+'\nCorrect the previous extraction using the validation error. Preserve literal request evidence; do not flatten different card faces into one field.',
            CHECKLIST,output/f'checklist-corrections-{attempt+1}',workers)
        checklists.update(corrected)
    cases=valid
    write_jsonl(output/'requirements.jsonl',[{**c,'checklist':checklists[c['id']]} for c in cases.values()])
    groups=defaultdict(list)
    for row in map(json.loads,candidates.read_text().splitlines()):
        if row['case_id'] in cases:groups[row['case_id']].append({**row,'origin':'sampled_sft'})
    if set(groups)!=set(cases) or any(len(g)!=manifest['candidates_per_prompt'] for g in groups.values()):
        raise ValueError('Incomplete candidate groups')
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text());schema['required']+=['name','rarity']
    checks={};pending=[]
    with ParserCache(output/'parser-cache') as parser:
        if parser_seed:
            print(json.dumps({'reused_exact_parser_results':parser.seed(parser_seed)}),flush=True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(inspect,cases[key],row['raw'],schema,parser.check,row.get('hit_token_limit',False)):(key,row)
                     for key,rows in groups.items() for row in rows}
            for future in as_completed(futures):
                key,row=futures[future];cid=row['candidate_id'];checks[cid]=future.result()
                if checks[cid]['schema_valid']:pending.append(reviewer_case(cases[key],row))
                if len(checks)%64==0:print(json.dumps({'stage':'deterministic_checks','completed':len(checks),'scheduled':len(futures)}),flush=True)
        judgments=batches(pending,prompt('reward-judge-v3.txt'),REVIEW,output/'original-review',workers)
        repair_cases=[]
        for key,rows in groups.items():
            if select_pair(rows,checks,judgments):continue
            bad=[r for r in rows if checks[r['candidate_id']]['schema_valid'] and
                 (checks[r['candidate_id']]['deterministic_violations'] or wrong(judgments[r['candidate_id']]))]
            if not bad:continue
            row=sorted(bad,key=lambda r:r['candidate_id'])[0];case=cases[key]
            repair_cases.append({'id':key,'request':case['request'],'explicit_metadata':case['explicit_metadata'],
                'requirements':checklists[key]['requirements'],'candidate':json.loads(row['raw']),
                'feedback':{'intent':judgments[row['candidate_id']],'schema_and_parser':checks[row['candidate_id']]}})
        repairs=batches(repair_cases,prompt('intent-repair-v1.txt'),REPAIR,output/'repairs',workers)
        pending=[]
        for key,repair in repairs.items():
            row={'case_id':key,'candidate_id':key+'-teacher','raw':repair['draft_json'],'origin':'sol_repair'}
            checks[row['candidate_id']]=inspect(cases[key],row['raw'],schema,parser.check)
            groups[key].append(row)
            if checks[row['candidate_id']]['schema_valid']:pending.append(reviewer_case(cases[key],row))
        judgments.update(batches(pending,prompt('reward-judge-v3.txt'),REVIEW,output/'repair-review',workers))
        parser_provenance=parser.provenance
    proposed=[];audit=[];critic=[]
    for key,rows in sorted(groups.items()):
        pair=select_pair(rows,checks,judgments)
        if pair is None:continue
        chosen,rejected=pair
        proposed.append({'prompt':prompts[key],'chosen':[{'role':'assistant','content':chosen['raw']}],
                         'rejected':[{'role':'assistant','content':rejected['raw']}]})
        audit.append({'case_id':key,'source_group_id':cases[key]['source_group_id'],'chosen':chosen['candidate_id'],
            'rejected':rejected['candidate_id'],'chosen_origin':chosen['origin'],'rejected_origin':rejected['origin'],
            'chosen_review':judgments[chosen['candidate_id']],'rejected_review':judgments[rejected['candidate_id']],
            'chosen_checks':checks[chosen['candidate_id']],'rejected_checks':checks[rejected['candidate_id']]})
        critic.extend(reviewer_case(cases[key],r) for r in pair)
    luna=batches(critic,prompt('reward-judge-v3.txt'),REVIEW,output/'luna-critic',workers,model='gpt-6-luna')
    disagreements=0
    for meta in audit:
        if not faithful(luna[meta['chosen']]) or not wrong(luna[meta['rejected']]):
            disagreements+=1
    # The natural-error audit found weaknesses even where critics agreed.
    # Recheck every proposed pair at high effort, blind to all previous labels.
    adjudicated=batches(critic,prompt('reward-judge-v3.txt'),REVIEW,output/'final-blind-review',workers,effort='high')
    pairs=[];evidence=[]
    for row,meta in zip(proposed,audit):
        chosen=adjudicated.get(meta['chosen'],meta['chosen_review']);bad=adjudicated.get(meta['rejected'],meta['rejected_review'])
        accepted=faithful(chosen) and (wrong(bad) or bool(meta['rejected_checks']['deterministic_violations']))
        evidence.append({**meta,'approved':accepted,'luna_chosen':luna[meta['chosen']],
            'luna_rejected':luna[meta['rejected']],'adjudicated':meta['chosen'] in adjudicated,
            'final_chosen':chosen,'final_rejected':bad})
        if accepted:pairs.append(row)
    file=write_jsonl(output/'preferences.jsonl',pairs);write_jsonl(output/'audit.jsonl',evidence)
    write_jsonl(output/'candidates-reviewed.jsonl',[{**row,'assessment':checks[row['candidate_id']],
        'judgment':judgments.get(row['candidate_id'])} for rows in groups.values() for row in rows])
    result={'version':'sol-repair-preferences-v3','independent_review':True,
        'independence_note':'Separate blinded Sol sessions; same-model correlated judgments. Luna critic and disagreement adjudication; not human labels.',
        'judge_model':'gpt-6.1-sol','judge_effort':'medium screening; high final review for every proposed pair','input_requests':len(cases),
        'scheduled_requests':scheduled_requests,'excluded_checklists':len(invalid),
        'proposed_pairs':len(proposed),'approved_pairs':len(pairs),'approved_pairs_sha256':file['sha256'],
        'pilot_manifest_sha256':identity['pilot_sha256'],'repair_requests':len(repairs),
        'chosen_origins':dict(Counter(r['chosen_origin'] for r in evidence if r['approved'])),
        'critic_disagreement_pairs':disagreements,'parser':parser_provenance,'final_test_used':False,
        'status':'Audit complete; inspect label quality before scaling','identity':identity}
    (output/'review.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('pilot','candidates','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--resume-code-change',action='store_true')
    p.add_argument('--parser-seed',type=Path,help='Verified earlier scoring directory to reuse exact-card parse results');a=p.parse_args()
    print(json.dumps(run(a.pilot,a.candidates,a.output,a.workers,a.resume_code_change,a.parser_seed),indent=2))
