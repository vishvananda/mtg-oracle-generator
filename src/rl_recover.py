"""Recover preference pairs with blinded Sol review and training-only references.

No candidate generation, GPU submission, or final-test access occurs here.
Previously approved pairs stay intact. Additional source positives are judged
like every other candidate, with their origin hidden from the judge.
"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from data_utils import digest,write_jsonl
from paths import PROJECT
from rl_data import checked_file,sha_file
from rl_review import judge,review_pairs


def faithful(j):
    return j['verdict']=='pass' and j['confidence']=='high' and j['oracle_style']==2 and not j['violations']


def wrong(j):
    return j['verdict']=='fail' and j['confidence']=='high' and bool(j['violations'])


def select_pair(rows,judgments):
    states=defaultdict(set)
    for row in rows:
        j=judgments[row['candidate_id']]
        states[digest(json.loads(row['raw']))].add('good' if faithful(j) else 'bad' if wrong(j) else 'unknown')
    if any({'good','bad'}<=v for v in states.values()):return None
    good=[r for r in rows if faithful(judgments[r['candidate_id']])]
    bad=[r for r in rows if r['origin']=='sampled_sft' and wrong(judgments[r['candidate_id']])]
    if not good or not bad:return None
    # Prefer a faithful model sample over a source reference when both exist.
    chosen=min(good,key=lambda r:(r['origin']!='sampled_sft',r['candidate_id']))
    rejected=min(bad,key=lambda r:r['candidate_id'])
    return chosen,rejected


def run(pilot,scored,pairs,initial_review,output,model='gpt-6.1-sol'):
    identity={'pilot_sha256':sha_file(pilot/'manifest.json'),'scored_sha256':sha_file(scored/'manifest.json'),
        'pairs_sha256':sha_file(pairs/'manifest.json'),'initial_review_sha256':sha_file(initial_review/'review.json'),
        'model':model,'code_sha256':sha_file(Path(__file__)),'final_test_used':False}
    output.mkdir(parents=True,exist_ok=True);pin=output/'request.json'
    if pin.exists() and json.loads(pin.read_text())!=identity:raise ValueError('Recovery inputs changed')
    pin.write_text(json.dumps(identity,indent=2)+'\n')
    pm=json.loads((pilot/'manifest.json').read_text())
    if pm['source_split']!='train' or pm['test_or_reserve_used']:raise ValueError('Training-only pilot required')
    cases={c['id']:c for c in map(json.loads,checked_file(pilot,'cases.jsonl',pm).read_text().splitlines())}
    prompts={c['id']:c['prompt'] for c in map(json.loads,checked_file(pilot,'prompts.jsonl',pm).read_text().splitlines())}
    sm=json.loads((scored/'manifest.json').read_text());source=scored/'scored.jsonl'
    if sm['pilot_manifest_sha256']!=identity['pilot_sha256'] or sha_file(source)!=sm['file']['sha256']:
        raise ValueError('Scored candidates changed')
    groups=defaultdict(list)
    for line in source.read_text().splitlines():
        row=json.loads(line)
        if row['case_id'] not in cases or row['assessment']['raw_sha256']!=digest(row['raw']):
            raise ValueError('Unknown or changed candidate')
        groups[row['case_id']].append({**row,'origin':'sampled_sft'})
    # Replays only hash-verified existing batches; no old judgments are guessed.
    before=json.loads((initial_review/'review.json').read_text())
    if before['judge_model']!=model:raise ValueError('Independent reviewer changed')
    if review_pairs(pilot,pairs,initial_review,model,'medium')!=before:raise ValueError('Initial review differs on replay')
    audit=[json.loads(l) for l in (initial_review/'audit.jsonl').read_text().splitlines()]
    original=json.loads((pairs/'manifest.json').read_text())
    original_pairs=[json.loads(l) for l in checked_file(pairs,'preferences.jsonl',original).read_text().splitlines()]
    kept=[];evidence=[];judgments={};recover=set()
    for meta,row in zip(audit,original_pairs):
        for role in ('chosen','rejected'):judgments[meta[role]]=meta['independent_'+role]
        if meta['approved']:
            kept.append(row);evidence.append({**meta,'chosen_origin':'sampled_sft','rejected_origin':'sampled_sft'})
        else:recover.add(meta['case_id'])
    for case_id,rows in groups.items():
        gates={r['assessment']['gate'] for r in rows}
        if 'intent_failure' in gates and 'faithful_candidate' not in gates:recover.add(case_id)
    schema=json.loads((PROJECT/'schemas/design-draft-v1.json').read_text());schema['required']+=['name','rarity']
    validator=Draft202012Validator(schema);pending=[];skipped=Counter()
    for case_id in sorted(recover):
        case=cases[case_id];rows=groups[case_id]
        if len(rows)!=pm['candidates_per_prompt']:raise ValueError('Incomplete candidate group')
        # A previously contradictory identical-card group stays excluded.
        if any(judgments.get(r['candidate_id'],{}).get('verdict')=='pass' and
               judgments[r['candidate_id']].get('violations') for r in rows):
            skipped['inconsistent_previous_judgment']+=1;continue
        reference=case['reference']
        if not list(validator.iter_errors(reference)):
            rows.append({'case_id':case_id,'candidate_id':case_id+'-source-reference',
                'raw':json.dumps(reference,ensure_ascii=False,separators=(',',':')),'origin':'training_reference'})
        for row in rows:
            if row['candidate_id'] not in judgments:
                pending.append({'id':row['candidate_id'],'request':case['request'],
                    'explicit_metadata':case['explicit_metadata'],'candidate':json.loads(row['raw'])})
    write_jsonl(output/'review-cases.jsonl',pending)
    print(json.dumps({'recovery_families':len(recover),'retained_pairs':len(kept),'additional_reviews':len(pending)}),flush=True)
    new,receipts=judge(pending,output/'judge',model,'medium');judgments.update(new)
    for case_id in sorted(recover):
        rows=groups[case_id]
        if any(r['candidate_id'] not in judgments for r in rows):continue
        pair=select_pair(rows,judgments)
        if pair is None:skipped['no_consistent_faithful_vs_wrong_pair']+=1;continue
        good,bad=pair
        kept.append({'prompt':prompts[case_id],'chosen':[{'role':'assistant','content':good['raw']}],
                     'rejected':[{'role':'assistant','content':bad['raw']}]})
        evidence.append({'case_id':case_id,'source_group_id':cases[case_id]['source_group_id'],
            'chosen':good['candidate_id'],'rejected':bad['candidate_id'],'chosen_origin':good['origin'],
            'rejected_origin':bad['origin'],'approved':True,'independent_chosen':judgments[good['candidate_id']],
            'independent_rejected':judgments[bad['candidate_id']]})
    if len({r['source_group_id'] for r in evidence})!=len(evidence):raise ValueError('Repeated source family')
    file=write_jsonl(output/'preferences.jsonl',kept);audit_file=write_jsonl(output/'audit.jsonl',evidence)
    result={'independent_review':True,'judge_model':model,'judge_effort':'medium','approved_pairs':len(kept),
        'approved_pairs_sha256':file['sha256'],'audit_sha256':audit_file['sha256'],
        'retained_pairs':before['approved_pairs'],'recovery_families':len(recover),'additional_reviews':len(pending),
        'chosen_origins':dict(Counter(r['chosen_origin'] for r in evidence)),'skipped':dict(skipped),
        'pilot_manifest_sha256':identity['pilot_sha256'],'recovery_request_sha256':sha_file(pin),
        'initial_review_sha256':identity['initial_review_sha256'],'judge_usage':[r['usage'] for r in receipts],
        'final_test_used':False,'source_positives':'Training-only Oracle targets, independently judged; never assumed correct for a request',
        'note':'Sol independently reviews Qwen outputs and source cards. No human-expert label claim.'}
    (output/'review.json').write_text(json.dumps(result,indent=2)+'\n');return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('pilot','scored','pairs','initial-review','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();print(json.dumps(run(a.pilot,a.scored,a.pairs,a.initial_review,a.output),indent=2))
