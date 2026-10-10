"""Prepare and independently review a family-disjoint custom-card SFT pilot.

Artifacts stay outside Git. This module neither submits GPU jobs nor publishes
third-party source records. Source targets are never rewritten by the teacher.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import fcntl
import heapq
import json
from pathlib import Path
import time

from jsonschema import Draft202012Validator
from codex_cached_batch import invoke
from corpus_protocol import keyed_schema
from data_utils import digest, write_jsonl
from full_corpus import normalized_group, rules
from paths import PROJECT

STYLES = ('broad', 'mechanics', 'detailed')
VERSION = 'custom-sft-pilot-v1'


def save(path, value):
    tmp = path.with_suffix('.pending.json')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
    tmp.replace(path)


def read(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def family(draft):
    return normalized_group({**draft, 'layout': draft.get('layout', 'normal')})


def target(draft):
    allowed = json.loads((PROJECT/'schemas/design-draft-v1.json').read_text())
    result = {k: v for k, v in draft.items() if k in allowed['properties']}
    result['oracle_text'] = rules(draft['oracle_text'], draft['name'], keep_reminder=True)
    Draft202012Validator(allowed).validate(result)
    if not result.get('name') or not result.get('rarity'):
        raise ValueError('Missing target name/rarity')
    return result


def coverage(row):
    d = row['draft']; colors = d.get('colors', [])
    return set(['color:'+c for c in colors] +
               ['color:colorless']*(not colors) + ['color:multicolor']*(len(colors)>1) +
               ['type:'+t for t in d['type_line'].split('—')[0].split()
                if t in ('Creature', 'Instant', 'Sorcery', 'Artifact', 'Enchantment', 'Land', 'Planeswalker', 'Battle')])


def select_covered(rows, count, salt):
    ranked = sorted(rows, key=lambda r: digest([salt, r['family_id']]))
    available = set().union(*(coverage(r) for r in ranked))
    chosen = []; covered = set()
    # Greedy feature coverage first, stable hash order for ties.
    while available-covered:
        row = max(ranked, key=lambda r: len(coverage(r)-covered))
        chosen.append(row); covered |= coverage(row); ranked.remove(row)
    if len(chosen)>count or len(ranked)<count-len(chosen):
        raise ValueError('Insufficient independent families for requested coverage/count')
    return chosen + ranked[:count-len(chosen)]


def prepare(candidates, official, output, train_count=512, test_count=64):
    if output.exists(): raise ValueError('Use a new immutable corpus directory')
    source = json.loads(candidates.with_name('summary.json').read_text())
    if source['candidates']['sha256'] != digest(candidates.read_bytes()):
        raise ValueError('Custom source checksum mismatch')
    manifest = json.loads((official/'manifest.json').read_text())
    heldout = set()
    for name in ('test.jsonl', 'heldout-reserve.jsonl'):
        raw = (official/name).read_bytes()
        if digest(raw) != manifest['files'][name]['sha256']: raise ValueError('Official holdout changed')
        heldout |= {json.loads(l)['source_group_id'] for l in raw.splitlines()}
    official_train = set(); replay_heap = []; pw_heap = []
    def keep(heap, row, maximum):
        key = int(digest([VERSION, 'replay', row['example_id']]), 16)
        item = (-key, row['example_id'], row)
        if len(heap)<maximum: heapq.heappush(heap, item)
        elif item>heap[0]: heapq.heapreplace(heap, item)
    import hashlib
    sha = hashlib.sha256()
    with (official/'train.jsonl').open('rb') as stream:
        for raw in stream:
            sha.update(raw); row = json.loads(raw)
            if row['split'] != 'train' or row['source_group_id'] in heldout:
                raise ValueError('Official training split contamination')
            official_train.add(row['source_group_id'])
            keep(replay_heap, row, train_count*8)
            if 'Planeswalker' in row['target'].get('type_line', ''):
                keep(pw_heap, row, 256)
    if sha.hexdigest()!=manifest['files']['train.jsonl']['sha256']:
        raise ValueError('Official train checksum mismatch')
    unique = {}; excluded = Counter()
    for source_row in read(candidates):
        if source_row['validation'].get('parse_complete') is not True:
            raise ValueError('Expected complete parsed custom inputs')
        row = {**source_row, 'family_id': family(source_row['draft'])}
        if row['family_id'] in heldout:
            excluded['official_heldout_family'] += 1; continue
        target(row['draft'])
        if row['family_id'] in unique: excluded['near_duplicate_custom_family'] += 1
        else: unique[row['family_id']] = row
    pool = list(unique.values())
    # A custom test family must be absent from the ORIGINAL SFT training too.
    test = select_covered([r for r in pool if r['family_id'] not in official_train], test_count, VERSION+'test')
    test_ids = {r['family_id'] for r in test}
    train = select_covered([r for r in pool if r['family_id'] not in test_ids], train_count, VERSION+'train')
    output.mkdir(parents=True); (output/'queue').mkdir()
    config = {'version': VERSION, 'teacher': 'gpt-6.1-sol', 'reviewer': 'gpt-6.1-sol',
              'effort': 'medium', 'batch_cards': 6,
              'teacher_instructions': (PROJECT/'prompts/custom-card-descriptions-v1.txt').read_text(),
              'review_instructions': (PROJECT/'prompts/custom-card-review-v1.txt').read_text()}
    save(output/'config.json', config)
    files = {}
    selected = [{**r, 'split': split} for split, rows in [('train', train), ('test', test)] for r in rows]
    files['selected.jsonl'] = write_jsonl(output/'selected.jsonl', selected)
    replay = {v[1]: v[2] for v in replay_heap+pw_heap}
    files['replay-pool.jsonl'] = write_jsonl(output/'replay-pool.jsonl', list(replay.values()))
    system = (official/'system-prompt.txt').read_bytes()
    if digest(system)!=manifest['files']['system-prompt.txt']['sha256']: raise ValueError('Prompt changed')
    (output/'system-prompt.txt').write_bytes(system)
    n = 0
    for split in ('train', 'test'):
        selected_rows = [r for r in selected if r['split']==split]
        for start in range(0, len(selected_rows), config['batch_cards']):
            n += 1
            save(output/'queue'/f'{n:04}.json', {'id': f'{n:04}', 'state': 'pending', 'split': split,
                'cards': selected_rows[start:start+config['batch_cards']]})
    result = {'version': VERSION, 'source_sha256': digest(candidates.read_bytes()),
        'official_manifest_sha256': digest((official/'manifest.json').read_bytes()),
        'config_sha256': digest(config), 'files': files, 'counts': {'train':len(train), 'test':len(test)},
        'jobs': n, 'excluded': dict(excluded), 'group_overlap':0,
        'test_family_overlap_with_original_sft':0,
        'coverage': {s:dict(Counter(f for r in rows for f in coverage(r))) for s,rows in [('train',train),('test',test)]},
        'training_family_ids':sorted(r['family_id'] for r in train),
        'test_family_ids':sorted(test_ids), 'human_reviewed':False, 'public_uploads':False}
    save(output/'preparation.json', result)
    return {k:result[k] for k in ('counts','jobs','excluded','coverage')}


def verdict_schema():
    return {'type':'object','additionalProperties':False,'required':['verdict','issues'],
            'properties':{'verdict':{'enum':['pass','reject','uncertain']},
                          'issues':{'type':'array','items':{'type':'string'}}}}


def run_batch(path):
    root = path.parent.parent
    with path.with_suffix('.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        job = json.loads(path.read_text())
        if job['state']!='pending': return
        job.update(state='running', started_at=time.time()); save(path, job)
        try:
            config = json.loads((root/'config.json').read_text())
            if digest(config)!=json.loads((root/'preparation.json').read_text())['config_sha256']:
                raise ValueError('Frozen generation policy changed')
            labels = [f'c{i}' for i in range(config['batch_cards'])]
            cards = {label: target(row['draft']) for label,row in zip(labels,job['cards'])}
            descriptions_schema = {'type':'object','additionalProperties':False,'required':list(STYLES),
                                   'properties':{s:{'type':'string'} for s in STYLES}}
            # Fixed schema even for short last batches; unused slots use empty strings.
            instructions = config['teacher_instructions']+'\nFor unused labels return empty strings in all three fields.'
            value, teacher = invoke(config['teacher'], {'instructions':instructions,'cards':cards},
                keyed_schema('descriptions',labels,descriptions_schema),root/'runs'/job['id']/'teacher',
                prefix_root=root/'prefixes', reasoning_effort=config['effort'])
            proposals = value['descriptions']
            for label in cards:
                if any(not proposals[label][s].strip() for s in STYLES):raise ValueError('Empty description')
            controls = {'bad': {'target':{'name':'Control','rarity':'common','type_line':'Sorcery',
                'colors':['B'],'mana_cost':'{B}','oracle_text':'Each opponent loses 2 life.'},
                'descriptions':dict.fromkeys(STYLES,'A common black sorcery named Control costing {B} that makes me gain 2 life.')}}
            review_cases = {k:{'target':v,'descriptions':proposals[k]} for k,v in cards.items()}
            review_cases.update(controls)
            review_fields = ['target',*STYLES]
            review_schema = {'type':'object','additionalProperties':False,'required':review_fields,
                             'properties':{s:verdict_schema() for s in review_fields}}
            reply, reviewer = invoke(config['reviewer'], {'instructions':config['review_instructions']+
                '\nFor unused labels use uncertain with issue "unused" in every field.', 'cases':review_cases},
                keyed_schema('reviews',labels+['bad'],review_schema),root/'runs'/job['id']/'reviewer',
                prefix_root=root/'prefixes', reasoning_effort=config['effort'])
            reviews = reply['reviews']
            if any(reviews['bad'][s]['verdict']!='reject' for s in STYLES):
                raise ValueError('Reviewer failed the player/effect control')
            results = []
            for label,row in zip(labels,job['cards']):
                verdicts = reviews[label]
                for v in verdicts.values():
                    if v['verdict']=='pass' and v['issues']:raise ValueError('Inconsistent review pass')
                results.append({'source_id':row['id'],'family_id':row['family_id'],'split':job['split'],
                    'target':cards[label],'descriptions':proposals[label],'review':verdicts,
                    'teacher_output_sha256':teacher['output_sha256'],
                    'reviewer_output_sha256':reviewer['output_sha256']})
            job.update(state='reviewed', results=results, finished_at=time.time())
        except Exception as error:
            job.update(state='failed', error=str(error), finished_at=time.time())
        save(path,job)
        print(json.dumps({'batch':job['id'],'state':job['state'],
                         'seconds':round(job['finished_at']-job['started_at'],1),
                         **({'error':job['error']} if 'error' in job else {})}),flush=True)


def run(root, workers, limit):
    paths = [p for p in sorted((root/'queue').glob('*.json')) if json.loads(p.read_text())['state']=='pending']
    if limit: paths = paths[:limit]
    with ThreadPoolExecutor(max_workers=workers) as pool:list(pool.map(run_batch,paths))
    return status(root)


def status(root):
    jobs = [json.loads(p.read_text()) for p in (root/'queue').glob('*.json')]
    counts = Counter(j['state'] for j in jobs)
    rows = [r for j in jobs for r in j.get('results',[])]
    return {'jobs':dict(counts),'reviewed_cards':len(rows),
            'accepted_targets':sum(r['review']['target']['verdict']=='pass' for r in rows),
            'accepted_descriptions':sum(r['review']['target']['verdict']=='pass' and r['review'][s]['verdict']=='pass'
                                        for r in rows for s in STYLES)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare')
    for key in ('candidates','official','output'):prep.add_argument('--'+key,type=Path,required=True)
    prep.add_argument('--train-count',type=int,default=512);prep.add_argument('--test-count',type=int,default=64)
    work=sub.add_parser('run');work.add_argument('--root',type=Path,required=True)
    work.add_argument('--workers',type=int,default=3);work.add_argument('--limit',type=int)
    check=sub.add_parser('status');check.add_argument('--root',type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':value=prepare(a.candidates,a.official,a.output,a.train_count,a.test_count)
    elif a.command=='run':value=run(a.root,a.workers,a.limit)
    else:value=status(a.root)
    print(json.dumps(value,indent=2))
