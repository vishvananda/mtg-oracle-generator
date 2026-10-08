"""Prepare a train-only preference pilot and export reviewed pairs. Never submits jobs."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path

from coverage_split import features
from data_utils import digest,write_jsonl
from oracle_retrieval import allowed_ids
from rl_rewards import VERSION,group_is_usable

EXACT_FIELDS={'name','rarity','mana_cost','colors','power','toughness','loyalty','defense',
              'hand_modifier','life_modifier'}


def sha_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def checked_file(root,name,manifest):
    path=root/name
    if sha_file(path)!=manifest['files'][name]['sha256']:
        raise ValueError('Artifact hash mismatch: '+name)
    return path


def select(rows,count,seed):
    """One description per family; cover types/colors, then balance detail styles."""
    representatives={}
    for row in rows:
        if row['split']!='train':raise ValueError('Non-training row supplied')
        group=row['source_group_id'];rank=digest([seed,row['example_id']])
        if group not in representatives or rank<representatives[group][0]:
            case={'id':row['example_id'],'source_group_id':group,'oracle_id':row['source']['oracle_id'],
                  'style':row['style'],'request':row['description'],'reference':row['target'],
                  'explicit_metadata':{k:v for k,v in row.get('expressed_constraints',{}).items()
                                       if k in EXACT_FIELDS and k in row.get('specified_fields',[])}}
            representatives[group]=(rank,case,features(row['target'])|{'style:'+row['style']})
    pool=sorted(representatives.values(),key=lambda r:r[0])
    if not 1<=count<=len(pool):raise ValueError('Insufficient unique training families')
    totals=Counter(f for _,_,fs in pool for f in fs)
    needed={f for f in totals if f.startswith(('type:','color_combination:','style:'))}
    selected=[];used=set();coverage=Counter();styles=Counter()
    def add(item):
        _,case,fs=item
        if case['source_group_id'] in used:return
        if len(selected)==count:raise ValueError('Requested count cannot cover all strata')
        selected.append(case);used.add(case['source_group_id']);coverage.update(fs);styles[case['style']]+=1
    for feature in sorted(needed,key=lambda f:(totals[f],f)):
        if coverage[feature]:continue
        add(next(item for item in pool if feature in item[2] and item[1]['source_group_id'] not in used))
    # Deliberately oversample the user's difficult planeswalker case, without duplicating families.
    walker_quota=min(count//16,totals['type:planeswalker'])
    for item in pool:
        if coverage['type:planeswalker']>=walker_quota:break
        if 'type:planeswalker' in item[2]:add(item)
    queues=defaultdict(list)
    for item in reversed(pool):
        if item[1]['source_group_id'] not in used:queues[item[1]['style']].append(item)
    while len(selected)<count:
        style=min((s for s in queues if queues[s]),key=lambda s:(styles[s],s))
        add(queues[style].pop())
    return selected,dict(sorted(coverage.items()))


def prepare(dataset,output,count=512,seed='oracle-rl-pilot-v1',candidates=4):
    if output.exists():raise ValueError('Choose a new immutable pilot directory')
    if candidates<2:raise ValueError('Preference pilot requires multiple candidates')
    manifest=json.loads((dataset/'manifest.json').read_text())
    ids,_=allowed_ids(dataset,'train-reward')
    train=checked_file(dataset,'train.jsonl',manifest)
    system=checked_file(dataset,'system-prompt.txt',manifest).read_text()
    with train.open() as stream:
        cases,coverage=select((json.loads(line) for line in stream),count,seed)
    if any(c['oracle_id'] not in ids for c in cases):raise ValueError('Held-out source in pilot')
    prompts=[{'id':c['id'],'prompt':[{'role':'system','content':system},{'role':'user','content':c['request']}]} for c in cases]
    output.mkdir(parents=True)
    files={name:write_jsonl(output/name,values) for name,values in [('cases.jsonl',cases),('prompts.jsonl',prompts)]}
    result={'version':'oracle-rl-pilot-v1','dataset_manifest_sha256':sha_file(dataset/'manifest.json'),
            'source_split':'train','unique_families':len(cases),'prompts':len(cases),'candidates_per_prompt':candidates,
            'planned_completions':len(cases)*candidates,'seed':seed,'coverage':coverage,'files':files,
            'test_or_reserve_used':False,'automatic_training':False,'ready_for_training':False,
            'status':'Prompts prepared; final SFT adapter, candidate generation, judge calibration and reviewed pairs pending',
            'code_sha256':sha_file(Path(__file__))}
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


def export_pairs(pilot,scored,output):
    """Only high-confidence faithful-vs-wrong pairs; no parser-only preferences.

    Input JSONL: case_id, candidate_id, raw (exact generated JSON string), assessment
    (rl_rewards.inspect + combine). Preserve judge/parser receipts alongside input.
    """
    if output.exists():raise ValueError('Choose a new immutable pair directory')
    manifest=json.loads((pilot/'manifest.json').read_text())
    if manifest['source_split']!='train' or manifest['test_or_reserve_used']:
        raise ValueError('Pilot is not training-only')
    with checked_file(pilot,'cases.jsonl',manifest).open() as stream:
        cases={c['id']:c for c in map(json.loads,stream)}
    with checked_file(pilot,'prompts.jsonl',manifest).open() as stream:
        prompts={c['id']:c['prompt'] for c in map(json.loads,stream)}
    groups=defaultdict(list);seen=set()
    with scored.open() as stream:
        for line in stream:
            row=json.loads(line);case_id=row['case_id'];candidate_id=row['candidate_id'];a=row['assessment']
            if case_id not in cases or candidate_id in seen:raise ValueError('Unknown case or duplicate candidate')
            if a['case_id']!=case_id or a['reward_version']!=VERSION or a['raw_sha256']!=digest(row['raw']):
                raise ValueError('Assessment does not match candidate')
            seen.add(candidate_id);groups[case_id].append(row)
    pairs=[];audit=[];skipped=Counter()
    for case_id,rows in sorted(groups.items()):
        if len(rows)!=manifest['candidates_per_prompt']:
            skipped['incomplete_group']+=1;continue
        if not group_is_usable([r['assessment'] for r in rows]):
            skipped['uncertain_all_bad_or_tied']+=1;continue
        good=[r for r in rows if r['assessment']['gate']=='faithful_candidate'
              and r['assessment'].get('judgment',{}).get('oracle_style')==2]
        bad=[r for r in rows if r['assessment']['gate'] in ('intent_failure','explicit_metadata_or_required_characteristic')
             and r['assessment']['schema_valid']]
        if not good or not bad:
            skipped['no_clear_semantic_preference']+=1;continue
        chosen=min(good,key=lambda r:(-r['assessment']['reward'],r['candidate_id']))
        rejected=min(bad,key=lambda r:r['candidate_id'])
        if chosen['raw']==rejected['raw']:raise ValueError('Contradictory judgments for identical candidates')
        pairs.append({'prompt':prompts[case_id],'chosen':[{'role':'assistant','content':chosen['raw']}],
                      'rejected':[{'role':'assistant','content':rejected['raw']}]})
        audit.append({'row':len(pairs)-1,'case_id':case_id,'source_group_id':cases[case_id]['source_group_id'],
                      'chosen':chosen['candidate_id'],'rejected':rejected['candidate_id'],
                      'chosen_assessment':chosen['assessment'],'rejected_assessment':rejected['assessment']})
    output.mkdir(parents=True)
    files={name:write_jsonl(output/name,values) for name,values in [('preferences.jsonl',pairs),('audit.jsonl',audit)]}
    result={'pairs':len(pairs),'input_cases':len(groups),'missing_cases':len(cases)-len(groups),'skipped':dict(skipped),
            'pilot_manifest_sha256':sha_file(pilot/'manifest.json'),'scored_sha256':sha_file(scored),
            'reward_version':VERSION,'code_sha256':sha_file(Path(__file__)),'files':files,
            'format':'TRL conversational prompt/chosen/rejected','automatic_training':False,
            'ready_for_training':False,'required_next_step':'Independent spot-check of pairs and judge disagreements'}
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--dataset',type=Path,required=True)
    prep.add_argument('--count',type=int,default=512);prep.add_argument('--seed',default='oracle-rl-pilot-v1')
    prep.add_argument('--candidates',type=int,default=4);prep.add_argument('--output',type=Path,required=True)
    exp=sub.add_parser('export-pairs')
    for name in ('pilot','scored','output'):exp.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    result=prepare(a.dataset,a.output,a.count,a.seed,a.candidates) if a.action=='prepare' else export_pairs(a.pilot,a.scored,a.output)
    print(json.dumps(result,indent=2))
