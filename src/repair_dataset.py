"""Freeze reviewed repairs plus generation replay with browser-exact messages."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from custom_training_data import read, save
from data_utils import digest, write_jsonl
from paths import PROJECT
from repair_expansion import design


def browser_examples(system, cases):
    bridge = """import {revisionMessages,designOnly} from './prompts/browser-repair.mjs';
let s='';for await(const p of process.stdin)s+=p;
const {system,cases}=JSON.parse(s);
console.log(JSON.stringify(cases.map(c=>({prompt:revisionMessages(system,c.current_card,
c.requested_change,c.diagnostic),completion:[{role:'assistant',content:JSON.stringify(designOnly(c.target))}]}))));"""
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', bridge],
        input=json.dumps({'system': system, 'cases': cases}), text=True, cwd=PROJECT))


def select_replay(rows, excluded, count):
    unique = {}
    for row in rows:
        if row.get('split') != 'train':
            raise ValueError('Replay must come from the explicit training split')
        group = row['source_group_id']; card = row['target']
        if group in excluded or group in unique or card.get('card_faces') or card.get('layout', 'normal') not in ('normal', 'single'):
            continue
        if not card.get('name') or not card.get('rarity') or len(card['oracle_text']) > 1800:
            continue
        unique[group] = row
    ordered = sorted(unique.values(), key=lambda r: digest(['repair-replay', r['example_id']]))
    walkers = [r for r in ordered if 'Planeswalker' in r['target']['type_line']][:count//10]
    selected = {r['source_group_id'] for r in walkers}
    replay = walkers + [r for r in ordered if r['source_group_id'] not in selected][:count-len(walkers)]
    if len(replay) != count:
        raise ValueError('Insufficient replay data')
    return replay


def export(root, official, development):
    output = root/'dataset'
    if output.exists():
        raise ValueError('Dataset is immutable; choose a new run')
    receipt = json.loads((root/'review-complete.json').read_text())
    if receipt['file_sha256'] != digest((root/'reviewed.jsonl').read_bytes()):
        raise ValueError('Reviewed data changed')
    reviewed = read(root/'reviewed.jsonl')
    cases = [r for r in reviewed if r['eligible']]
    train = [r for r in cases if r['split'] == 'train']
    evaluation = [r for r in cases if r['split'] == 'evaluation']
    split = json.loads((root/'split.json').read_text())
    excluded = set(split['excluded_groups']) | set(split['repair_holdout_groups'])
    if {r['source_group_id'] for r in train} & excluded:
        raise ValueError('Repair training leaked a held-out family')
    if len(train) < 500 or sum(r['kind'] == 'real_model_error' for r in train) < 32:
        raise ValueError('Insufficient reviewed real-error/training coverage')
    if sum('Planeswalker' in r['target']['type_line'] for r in train) < 80:
        raise ValueError('Insufficient planeswalker examples')
    replay = select_replay((json.loads(l) for l in (official/'train.jsonl').open()), excluded, len(train))
    system = (root/'system-prompt.txt').read_text()
    examples = browser_examples(system, train)
    meta = [{k: r[k] for k in ('id', 'source_group_id', 'kind', 'origin')} for r in train]
    for row in replay:
        examples.append({'prompt': [{'role': 'system', 'content': system},
            {'role': 'user', 'content': row['description']}],
            'completion': [{'role': 'assistant', 'content': json.dumps(design(row['target']), ensure_ascii=False)}]})
        meta.append({'id': row['example_id'], 'source_group_id': row['source_group_id'],
                     'kind': 'generation_replay', 'origin': 'official_training'})
    prompts = {}
    for example in examples:
        key = digest(example['prompt'])
        if key in prompts and prompts[key] != example['completion']:
            raise ValueError('Conflicting supervised targets')
        prompts[key] = example['completion']
    # A bounded, family-disjoint evaluation subset is frozen before training.
    buckets = {}
    for row in evaluation:
        category = ('real' if row['kind'] == 'real_model_error' else
                    'noop' if row['kind'] == 'no_op' else 'edit' if row['diagnostic'] is None else 'synthetic')
        buckets.setdefault(category, []).append(row)
    selected = []
    for category, cap in [('real', 48), ('synthetic', 80), ('edit', 24), ('noop', 12)]:
        values = sorted(buckets.get(category, []), key=lambda r: digest(['repair-eval', r['id']]))
        chosen = []; groups = set()
        # Include all held-out walkers before other families.
        values.sort(key=lambda r: 0 if 'Planeswalker' in r['current_card']['type_line'] else 1)
        for row in values:
            if row['source_group_id'] in groups:
                continue
            groups.add(row['source_group_id']); chosen.append({**row, 'panel': category})
            if len(chosen) == cap:
                break
        selected.extend(chosen)
    evaluation_examples = browser_examples(system, selected)
    eval_rows = [{**row, 'messages': ex['prompt']} for row, ex in zip(selected, evaluation_examples)]
    dev = sorted(read(development), key=lambda r: digest(['repair-generation-check', r['id']]))
    dev.sort(key=lambda r: 0 if 'Planeswalker' in r.get('reference', {}).get('type_line', '') else 1)
    for row in dev[:64]:
        eval_rows.append({**row, 'id': 'generation-'+row['id'], 'panel': 'generation',
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': row['request']}]})
    training_groups = {r['source_group_id'] for r in meta}
    if training_groups & {r['source_group_id'] for r in eval_rows}:
        raise ValueError('Evaluation family present in repair or replay training')
    if set(prompts) & {digest(row['messages']) for row in eval_rows}:
        raise ValueError('Evaluation prompt present in training')
    output.mkdir()
    files = {'train.sft.jsonl': write_jsonl(output/'train.sft.jsonl', examples),
             'training-provenance.jsonl': write_jsonl(output/'training-provenance.jsonl', meta),
             'evaluation-cases.jsonl': write_jsonl(output/'evaluation-cases.jsonl', eval_rows)}
    manifest = {'version': 'repair-sft-v1', 'files': files, 'source_review_sha256': digest(receipt),
        'repair_examples': len(train), 'replay_examples': len(replay), 'group_overlap': 0,
        'checks': {'group_overlap': 0, 'description_overlap': 0},
        'counts': dict(Counter(r['kind'] for r in meta)),
        'evaluation_counts': dict(Counter(r['panel'] for r in eval_rows)),
        'planeswalker_training': sum('Planeswalker' in r['target']['type_line'] for r in train),
        'browser_prompt_sha256': digest((PROJECT/'prompts/browser-repair.mjs').read_bytes()),
        'automatic_promotion': False,
        'evaluation_scope': 'Repair-family holdout plus existing DPO2 real errors and development regression cases; not an untouched final test.'}
    save(output/'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'official', 'development'):
        p.add_argument('--'+name, type=Path, required=True)
    print(json.dumps(export(**vars(p.parse_args())), indent=2))
