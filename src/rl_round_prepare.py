"""Freeze training/evaluation family boundaries and a reused 128-request audit."""
import argparse,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

from coverage_split import features
from data_utils import digest,write_jsonl
from rl_data import checked_file,select,sha_file


def write_pilot(output,cases,system,dataset_hash,**extra):
    output.mkdir(parents=True,exist_ok=False)
    prompts=[{'id':c['id'],'prompt':[{'role':'system','content':system},{'role':'user','content':c['request']}]} for c in cases]
    files={n:write_jsonl(output/n,rows) for n,rows in [('cases.jsonl',cases),('prompts.jsonl',prompts)]}
    coverage=Counter(f for c in cases for f in features(c['reference'])|{'style:'+c['style']})
    value={'version':'oracle-preference-round2','dataset_manifest_sha256':dataset_hash,'source_split':'train',
        'unique_families':len({c['source_group_id'] for c in cases}),'prompts':len(cases),'candidates_per_prompt':4,
        'planned_completions':len(cases)*4,'coverage':dict(coverage),'files':files,'test_or_reserve_used':False,**extra}
    (output/'manifest.json').write_text(json.dumps(value,indent=2)+'\n');return value


def prepare(dataset,old_pilot,old_scored,old_candidates,output):
    if output.exists():raise ValueError('Fresh round directory required')
    dm=json.loads((dataset/'manifest.json').read_text());pm=json.loads((old_pilot/'manifest.json').read_text())
    if pm['dataset_manifest_sha256']!=sha_file(dataset/'manifest.json'):raise ValueError('Dataset mismatch')
    all_old=list(map(json.loads,checked_file(old_pilot,'cases.jsonl',pm).read_text().splitlines()))
    old_groups={c['source_group_id'] for c in all_old}
    system=checked_file(dataset,'system-prompt.txt',dm).read_text()
    source=checked_file(dataset,'train.jsonl',dm)
    with source.open() as stream:
        reserved,coverage=select((r for r in map(json.loads,stream) if r['source_group_id'] not in old_groups),128,'rl-round2-eval-families-v1')
    reserved.sort(key=lambda c:digest(['eval-partition-v1',c['source_group_id']]))
    heldout={c['source_group_id'] for c in reserved}
    output.mkdir(parents=True)
    write_jsonl(output/'evaluation-source-cases.jsonl',[{**c,'evaluation_split':'development' if i%2==0 else 'release',
        'evaluation_origin':'sft_training_card_new_rl_holdout'} for i,c in enumerate(reserved)])
    (output/'reserved-families.json').write_text(json.dumps({'source_group_ids':sorted(heldout),
        'source_oracle_ids':sorted({c['oracle_id'] for c in reserved}),'source_coverage':coverage,
        'sft_test_used':False,'note':'Disjoint from all prior preference-pilot families. Cards were seen in SFT; new-request evaluation, not unseen-card knowledge.'},indent=2)+'\n')
    sm=json.loads((old_scored/'manifest.json').read_text())
    if sha_file(old_scored/'scored.jsonl')!=sm['file']['sha256']:raise ValueError('Prior scores changed')
    priorities=Counter()
    for row in map(json.loads,(old_scored/'scored.jsonl').read_text().splitlines()):
        if row['assessment']['gate'] in ('intent_failure','explicit_metadata_or_required_characteristic'):
            priorities[row['case_id']]+=1
    hard=sorted((c for c in all_old if priorities[c['id']]),key=lambda c:(-priorities[c['id']],digest(c['id'])))[:100]
    selected={c['id'] for c in hard}
    controls=sorted((c for c in all_old if c['id'] not in selected),key=lambda c:digest(['audit-controls',c['id']]))[:128-len(hard)]
    audit=hard+controls
    write_pilot(output/'audit-pilot',audit,system,sha_file(dataset/'manifest.json'),
        selection={'error_enriched_requests':len(hard),'other_requests':len(controls),
                   'prior_scores_for_selection_only':True,'new_blind_judgments_required':True},
        reserved_families_sha256=sha_file(output/'reserved-families.json'))
    source_receipt=json.loads(old_candidates.with_suffix('.manifest.json').read_text())
    if not source_receipt['complete'] or source_receipt['sha256']!=sha_file(old_candidates):raise ValueError('Prior generations changed')
    ids={c['id'] for c in audit}
    rows=[r for r in map(json.loads,old_candidates.read_text().splitlines()) if r['case_id'] in ids]
    if len(rows)!=512:raise ValueError('Audit needs four generations per request')
    f=write_jsonl(output/'audit-candidates.jsonl',rows)
    (output/'audit-candidates.manifest.json').write_text(json.dumps({'complete':True,**f,'cases':128,'completions':512,
        'source_sha256':sha_file(old_candidates),'source_receipt':source_receipt},indent=2)+'\n')
    authorization={'scope':'one_preference_round','user_instruction':'ok do it',
        'accepted_plan':'128-request Sol audit, scale toward 1000–2000 reviewed pairs, 125–250 DPO updates and evaluation',
        'authorized_at':datetime.now(timezone.utc).isoformat(),'ceiling_usd':20,
        'ceiling_source':'Conservative operator-selected bound for candidate sampling, training and evaluation; not a user-specified limit',
        'automatic_retries':False,'automatic_full_sft_epoch':False,'automatic_promotion':False}
    (output/'authorization.json').write_text(json.dumps(authorization,indent=2)+'\n')
    (output/'status.json').write_text(json.dumps({'phase':'audit_prepared','requests':128,'reused_candidates':512,
        'evaluation_source_families_reserved':128,'source_dataset_sha256':sha_file(dataset/'manifest.json')},indent=2)+'\n')
    print(json.dumps({'audit_requests':128,'error_enriched':len(hard),'reused_generations':512,'reserved_families':128}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('dataset','old-pilot','old-scored','old-candidates','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();prepare(a.dataset,a.old_pilot,a.old_scored,a.old_candidates,a.output)
