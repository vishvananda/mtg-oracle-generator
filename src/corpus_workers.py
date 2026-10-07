#!/usr/bin/env python3
"""Durable full-Oracle description queue with independent model fidelity review."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import re
import threading
import time

from paths import PROJECT
from full_corpus import ROOT
from data_utils import digest
from codex_batch import invoke
from codex_cached_batch import invoke as invoke_cached
from corpus_protocol import VERSION as COMPACT_PROTOCOL, teacher_request, teacher_reply

TEACHER='gpt-6.1-sol'
REVIEWER='gpt-6-luna'
WORKER_SHA=digest(Path(__file__).read_bytes())


def schema(key, fields):
    return {'type':'object','additionalProperties':False,'required':[key],
            'properties':{key:{'type':'array','items':{'type':'object','additionalProperties':False,
                 'required':list(fields),'properties':fields}}}}


def deterministic_checks(projection, description):
    issues=[]
    def check_fields(target):
        costs=re.findall(r'(?:\{[0-9A-Z/]+\})+',description)
        if target.get('mana_cost') and target['mana_cost'] not in costs:
            issues.append('missing_or_changed_mana_cost:'+target['mana_cost'])
        if target.get('power') is not None and f"{target['power']}/{target['toughness']}" not in description.replace(' ',''):
            issues.append('missing_or_changed_stats')
        typ=target.get('type_line','')
        subtype_words=typ.split(' — ',1)[1].split() if ' — ' in typ else []
        creature_implied=bool(re.search(r'\b\d+\s*/\s*\d+\b',description)) or (
            'Creature' in typ and bool(subtype_words) and all(re.search(r'\b'+re.escape(w)+r'\b',description,re.I) for w in subtype_words))
        for word in re.split(r'\s+|—|//',typ):
            if word=='Creature' and creature_implied: continue
            if word=='Artifact' and 'Equipment' in subtype_words and re.search(r'\bequipment\b',description,re.I): continue
            if word and not re.search(r'\b'+re.escape(word)+r'\b',description,re.I):
                issues.append('missing_type_word:'+word)
        for face in target.get('card_faces',[]): check_fields(face)
    check_fields(projection['target'])
    if projection['style'] in ('minimal','concept'):
        target=projection['target']
        if 'mana_cost' not in target and re.search(r'\{[^}]+\}',description): issues.append('detail_level_leaked_mana_cost')
        if 'power' not in target and re.search(r'\b\d+\s*/\s*\d+\b',description): issues.append('detail_level_leaked_exact_stats')
    if len(description)>12000 or not description.strip(): issues.append('invalid_description_length')
    return issues


def prompt_config(policy='exact_constraints'):
    """Freeze shared terminology in the config; resumed jobs never read live prompts."""
    if policy not in ('exact_constraints','plausible_completion'):
        raise ValueError('Unknown description policy')
    terminology=(PROJECT/'prompts/player-language-v1.txt').read_text()
    if policy=='plausible_completion':
        return {'instructions':(PROJECT/'prompts/teacher-broad-descriptions-v1.txt').read_text()+'\n'+terminology,
                'reviewer_terminology':terminology,
                'reviewer_completion_guidance':(PROJECT/'prompts/review-broad-descriptions-v1.txt').read_text(),
                'description_policy':policy,
                'terminology_revision':'player-language-v1','terminology_sha256':digest(terminology.encode())}
    instructions=(PROJECT/'prompts/teacher-descriptions-v1.txt').read_text()+'''
For exact mana costs use braced notation and for exact stats use P/T notation.
For linked faces, describe each face and the supplied layout/relationship. Names
such as Draft Face 1 are editable face labels, not historical source names.
Do not state technical metadata like "normal layout"; ordinary single-face cards
need no such wording. Preserve special gameplay layouts where they matter.
'''+ '\n' + terminology
    return {'instructions':instructions,'reviewer_terminology':terminology,
            'terminology_revision':'player-language-v1','terminology_sha256':digest(terminology.encode())}


def prepare(root, batch_size=12):
    queue=root/'queue'
    if queue.exists(): raise ValueError('Queue already exists')
    queue.mkdir()
    cards=[json.loads(line) for line in (root/'sources.jsonl').read_text().splitlines()]
    # Freeze prompts/config before any model generation. The canary uses training cards only.
    config={'teacher':TEACHER,'reviewer':REVIEWER,'reasoning_effort':'medium','batch_cards':batch_size,
            'workers':2,'max_attempts':1,'per_invocation_timeout_seconds':900,
            **prompt_config(),'student_prompt_sha256':digest((root/'student-prompt.txt').read_bytes()),
            'completion_policy':'Unspecified characteristics may be suggested; teacher descriptions must still honor their assigned detail level.',
            'human_reviewed':False,'automatic_training':False,'publication':False}
    (root/'generation-config.json').write_text(json.dumps(config,indent=2)+'\n')
    number=0
    for split in ('train','validation','test'):
        selected=[c for c in cards if c['split']==split]
        for start in range(0,len(selected),batch_size):
            number+=1
            batch=selected[start:start+batch_size]
            job={'id':f'{number:05d}','split':split,'cards':batch,'state':'pending'}
            (queue/(job['id']+'.json')).write_text(json.dumps(job,ensure_ascii=False)+'\n')
    report={'jobs':number,'cards':len(cards),'descriptions':sum(len(c['projections']) for c in cards),
            'max_model_calls':number*2,'workers':2,'status':'prepared','automatic_training':False}
    (root/'queue-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


def align_rows(rows, identifiers):
    if len(rows)!=len(identifiers) or len({r['id'] for r in rows})!=len(rows) or {r['id'] for r in rows}!=set(identifiers):
        raise ValueError('Missing, duplicate or unknown IDs')
    by_id={r['id']:r for r in rows}
    return [by_id[key] for key in identifiers]


def run_job(path, config, retry_review=False):
    root=path.parent.parent
    job=json.loads(path.read_text())
    if retry_review:
        if job['state']!='needs_review' or job.get('review_retry_count',0): raise ValueError('Only one explicit review recovery is allowed')
        job['prior_error']=job.pop('error',''); job['review_retry_count']=1
    elif job['state']!='pending': return
    def save():
        tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(job,ensure_ascii=False)+'\n'); tmp.replace(path)
    job.update(state='running',started_at=time.time(),worker_source_sha256=WORKER_SHA,generation_config_sha256=digest(config)); save()
    run=root/'runs'/job['id']
    items=[p for c in job['cards'] for p in c['projections']]
    try:
        teacher_input={'instructions':config['instructions'],'projections':[
            {k:p[k] for k in ('id','style','target','rules_coverage','omitted_fields',
                              'description_policy','reference_draft') if k in p} for p in items]}
        broad=any(p.get('description_policy')=='plausible_completion' for p in items)
        if broad and not config.get('reviewer_completion_guidance'):
            raise ValueError('Broad descriptions require an explicit plausible-completion review policy')
        teacher_schema=schema('descriptions',{'id':{'type':'string','enum':[p['id'] for p in items]},'description':{'type':'string'},'coverage_notes':{'type':'string'}})
        compact=config.get('wire_protocol')==COMPACT_PROTOCOL
        mapping=None
        if compact:
            teacher_input,teacher_schema,mapping=teacher_request(teacher_input)
        def call(model,payload,output_schema,output):
            if compact:
                return invoke_cached(model,payload,output_schema,output,prefix_root=root/'codex-prefixes',
                                     reasoning_effort=config.get('reasoning_effort','medium'))
            return invoke(model,payload,output_schema,output)
        if retry_review:
            if json.loads((run/'teacher/input.json').read_text())!=teacher_input: raise ValueError('Cannot reuse teacher with changed input')
            teacher_receipt=json.loads((run/'teacher/receipt.json').read_text())
            if digest((run/'teacher/output.json').read_bytes())!=teacher_receipt['output_sha256']: raise ValueError('Teacher output changed')
            reply=json.loads((run/'teacher/output.json').read_text())
        else:
            reply,teacher_receipt=call(config['teacher'],teacher_input,teacher_schema,run/'teacher')
        if compact:
            reply=teacher_reply(reply,mapping)
        rows=align_rows(reply['descriptions'],[p['id'] for p in items])
        review_input={'instructions':'''Audit each candidate training INPUT against only the supplied expressed constraints.
Do not generate or fix descriptions. Return exactly one review for every case label. Use pass only
when every expressed characteristic and mechanic is preserved and no omitted
detail is introduced into the description. A downstream draft MAY fill omitted
characteristics; that does not permit the input description to claim those values.
Check repeated mana symbols, exact stats, type/supertype/subtypes, colors,
quantities, target vs each, player/controller, zones, duration scope across ALL
linked clauses, optionality, costs, restrictions, replacements, and face links.
For rules_coverage=unspecified, the input must not say no abilities or invent
abilities. For explicit_none it must communicate no abilities. Qualitative size
does not supply exact stats. Ordinary player shorthand is allowed if unambiguous.
Use reject for concrete errors; uncertain for any unclear linkage or unresolved
meaning. Do not assume a recognizable published card supplies missing details.
Return a reviews object keyed by case label. Each value has verdict
(pass/reject/uncertain) and issues (array of concise specific strings, empty for
pass). Include every label, even when cases seem similar. No tools or other outputs.''',
            'cases':[{'id':p['id'],'expressed_constraints':p['target'],'style':p['style'],
                      'rules_coverage':p['rules_coverage'],'description':r['description'],
                      **{k:p[k] for k in ('description_policy','reference_draft') if k in p}}
                     for p,r in zip(items,rows)]}
        if config.get('reviewer_terminology'):
            review_input['instructions']+='\n\n'+config['reviewer_terminology']
        if config.get('reviewer_completion_guidance'):
            review_input['instructions']+='\n\n'+config['reviewer_completion_guidance']
        controls=[
            ('scope',{'type_line':'Sorcery','oracle_text':'Each opponent loses 3 life.'},'A sorcery that makes me lose 3 life.'),
            ('optional',{'type_line':'Creature','oracle_text':'When this creature enters, you may gain 4 life.'},'A creature. Its ETB forces me to gain 4 life; I cannot decline.'),
            ('duration',{'type_line':'Sorcery','oracle_text':'Until end of turn, you may play cards from the top of your library.'},'A sorcery letting me play cards from the top of my library for the rest of the game.'),
        ]
        control_ids=[]
        for key,target,description in controls:
            identifier=digest([job['id'],'fidelity-control',key])[:24]
            control_ids.append(identifier)
            review_input['cases'].append({'id':identifier,'expressed_constraints':target,'style':'mechanics',
                                         'rules_coverage':'all','description':description})
        completion_controls={}
        if broad:
            walker={'type_line':'Legendary Planeswalker','loyalty':'3',
                    'oracle_text':'+1: Create a 0/1 green Plant creature token.\n−7: Draw seven cards.'}
            rummage={'type_line':'Instant','colors':['R'],
                     'oracle_text':'As an additional cost to cast this spell, discard a card.\nDraw two cards.'}
            for key,reference,description,expected in [
                ('omitted_loyalty',walker,'A planeswalker that makes plants and has a big card-draw ultimate.','pass'),
                ('changed_ultimate',walker,'A planeswalker whose ultimate deals seven damage to each opponent.','reject'),
                ('omitted_rummage_counts',rummage,'A red instant for rummaging.','pass'),
                ('reversed_rummage',rummage,'A red instant that lets me draw first and then discard.','reject'),
            ]:
                identifier=digest([job['id'],'completion-control',key])[:24]
                completion_controls[identifier]=expected
                review_input['cases'].append({'id':identifier,'expressed_constraints':{},'style':'broad_plain',
                    'rules_coverage':'summary','description_policy':'plausible_completion',
                    'reference_draft':reference,'description':description})
        review_input['cases'].sort(key=lambda c:digest(c['id']))
        # Short required object keys avoid transcription errors and skipped
        # members in long arrays. Stable corpus IDs remain outside model output.
        labels={f'case_{i+1:03d}':case['id'] for i,case in enumerate(review_input['cases'])}
        review_input['cases']=[{**case,'id':label} for label,case in zip(labels,review_input['cases'])]
        review_value={'type':'object','additionalProperties':False,'required':['verdict','issues'],
                      'properties':{'verdict':{'enum':['pass','reject','uncertain']},'issues':{'type':'array','items':{'type':'string'}}}}
        review_schema={'type':'object','additionalProperties':False,'required':['reviews'],
                       'properties':{'reviews':{'type':'object','additionalProperties':False,'required':list(labels),
                                               'properties':{label:review_value for label in labels}}}}
        if compact:
            from corpus_protocol import review_schema as compact_review_schema
            review_schema=compact_review_schema(labels)
        review,review_receipt=call(config['reviewer'],review_input,review_schema,run/('reviewer-recovery-1' if retry_review else 'reviewer'))
        if not isinstance(review.get('reviews'),dict): raise ValueError('Missing batch reviews')
        if set(review['reviews'])!=set(labels): raise ValueError('Missing or unknown review labels')
        reviews={labels[label]:{'id':labels[label],**value} for label,value in review['reviews'].items()}
        if any(reviews[key]['verdict']!='reject' for key in control_ids):
            raise ValueError('Fidelity reviewer failed a wrong-player, optionality or duration control; no rows admitted')
        if any(reviews[key]['verdict']!=expected or (expected=='pass' and reviews[key]['issues'])
               for key,expected in completion_controls.items()):
            raise ValueError('Reviewer failed a broad-completion omission or contradiction control; no rows admitted')
        job['fidelity_controls_rejected']=len(control_ids)
        if completion_controls: job['completion_controls_checked']=len(completion_controls)
        result=[]
        by_id={p['id']:c for c in job['cards'] for p in c['projections']}
        for projection,row in zip(items,rows):
            verdict=reviews[row['id']]
            issues=deterministic_checks(projection,row['description'])
            card=by_id[row['id']]
            # Original names are provenance only; typed subtype names and real other-card references remain allowed.
            if card['name'].casefold() in row['description'].casefold() and len(card['name'])>4:
                if card['name'] not in json.dumps(projection['target']): issues.append('source_name_leak')
            passed=not issues and verdict['verdict']=='pass' and not verdict['issues']
            result.append({'example_id':row['id'],'split':card['split'],'source_group_id':card['source_group_id'],
                'source':{k:card[k] for k in ('oracle_id','source_sha256','layout','source_updated_at')},
                'style':projection['style'],'task':'propose_card_draft','description':row['description'],
                'target':projection['draft_target'],'expressed_constraints':projection['target'],
                **({'description_policy':'plausible_completion','field_annotation':'not_extracted',
                    'completion_controls_checked':len(completion_controls),'coverage_notes':row['coverage_notes']}
                   if projection.get('description_policy')=='plausible_completion' else {}),
                'specified_fields':projection['specified_fields'],'proposed_fields':projection['proposed_fields'],
                'rules_coverage':projection['rules_coverage'],'teacher':config['teacher'],'reviewer':config['reviewer'],
                'generation_config_sha256':digest(config),
                'teacher_prompt_sha256':teacher_receipt['prompt_sha256'],'teacher_output_sha256':teacher_receipt['output_sha256'],
                'reviewer_output_sha256':review_receipt['output_sha256'],
                'fidelity_controls_rejected':job['fidelity_controls_rejected'],
                'fidelity_review':verdict,'deterministic_issues':issues,'status':'accepted' if passed else 'quarantined',
                'human_reviewed':False,'oracle_source':'Scryfall complete Oracle snapshot; source-derived target',
                'engine_validation':'not_run; unsupported engine mechanics are not rejected'})
        (run/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in result))
        job.update(state='completed',accepted=sum(r['status']=='accepted' for r in result),quarantined=sum(r['status']=='quarantined' for r in result))
    except Exception as e:
        job.update(state='needs_review',error=str(e))
    job['finished_at']=time.time(); save()
    print(json.dumps({k:job[k] for k in ('id','state','accepted','quarantined','error') if k in job}),flush=True)


def run(root,limit,config_path=None):
    with (root/'coordinator.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=json.loads((config_path or root/'generation-config.json').read_text())
        workers=config.get('workers',2)
        if type(workers) is not int or workers<1:
            raise ValueError('workers must be a positive integer')
        records={p:{k:v for k,v in json.loads(p.read_text()).items() if k!='cards'} for p in sorted((root/'queue').glob('*.json'))}
        paths=[p for p,job in records.items() if job['state']=='pending'][:limit]
        stop=threading.Event()
        progress_lock=threading.Lock()
        def save_progress(changed=None):
            with progress_lock:
                if changed:
                    records[changed]={k:v for k,v in json.loads(changed.read_text()).items() if k!='cards'}
                states=list(records.values())
                report={'states':dict(Counter(s['state'] for s in states)),
                        'accepted':sum(s.get('accepted',0) for s in states),'quarantined':sum(s.get('quarantined',0) for s in states),
                        'generated_descriptions':sum(s.get('accepted',0)+s.get('quarantined',0) for s in states),
                        'workers':workers,
                        'automatic_training':False,'public_uploads':False,'updated_at':time.time()}
                temporary=root/'progress.tmp'
                temporary.write_text(json.dumps(report,indent=2)+'\n'); temporary.replace(root/'progress.json')
                return report
        def checked(path):
            if stop.is_set() or (root/'drain.request.json').exists(): return
            run_job(path,config)
            if json.loads(path.read_text())['state']=='needs_review': stop.set()
            save_progress(path)
        save_progress()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(checked,paths))
        report=save_progress()
        print(json.dumps(report),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=('prepare','run','retry-review'))
    p.add_argument('--root',type=Path,default=ROOT); p.add_argument('--limit',type=int,default=2)
    p.add_argument('--job-id')
    p.add_argument('--export-output',type=Path)
    p.add_argument('--config',type=Path)
    a=p.parse_args()
    if a.action=='prepare': prepare(a.root)
    elif a.action=='retry-review':
        if not a.job_id or not re.fullmatch(r'\d{5}',a.job_id): p.error('Supply --job-id NNNNN')
        with (a.root/'coordinator.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run_job(a.root/'queue'/(a.job_id+'.json'),json.loads((a.config or a.root/'generation-config.json').read_text()),retry_review=True)
        run(a.root,0,a.config)
    else:
        if a.export_output and a.export_output.exists(): p.error('Export output must be new')
        run(a.root,a.limit,a.config)
        if a.export_output:
            from export_full_corpus import export
            export(a.root,a.export_output)
