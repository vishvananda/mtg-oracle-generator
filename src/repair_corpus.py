"""Prepare a training-only, parser-checked pilot for the browser's repair task.

Synthetic corruptions are candidate training data, not independent evidence of
real-user repair quality. No model calls, training, or uploads occur here.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import re
import subprocess

from data_utils import digest, write_jsonl
from paths import PROJECT
from validate_mtgish import MtgishValidator


def corruptions(card):
    text = card['oracle_text']
    rules = (
        ('trigger_comma', r'\b((?:When|Whenever)[^\n,.]+), ', r'\1 '),
        ('activation_colon', r'(\}): ', r'\1 '),
        ('flying_typo', r'\bFlying\b', 'Flyng'),
        ('vigilance_typo', r'\bVigilance\b', 'Vigilence'),
        ('draw_grammar', r'\b[Dd]raw a card\b', 'draws one card'),
        ('old_enters_wording', r'\b(CARDNAME) enters\b', r'\1 comes into play'),
        ('tap_words', r'\{T\}', 'Tap'),
    )
    for kind, pattern, replacement in rules:
        changed, count = re.subn(pattern, replacement, text, count=1)
        if count:
            yield kind, {**copy.deepcopy(card), 'oracle_text': changed}


def select(rows, heldout, parser, limit):
    seen = set()
    counts = Counter()
    accepted = []
    provenance = []
    for row in rows:
        if row.get('split') != 'train':
            raise ValueError('Only explicit training-split records may produce repairs')
        group = row.get('source_group_id')
        if not group:
            raise ValueError('Missing source family')
        if group in heldout:
            counts['heldout_family_skipped'] += 1
            continue
        if group in seen:
            continue
        seen.add(group)
        card = row['target']
        if card.get('card_faces') or card.get('layout') not in (None, 'normal', 'single'):
            counts['outside_browser_scope'] += 1
            continue
        good = parser.check(card)
        if good.get('status') == 'parser_error':
            raise RuntimeError('Parser infrastructure failure')
        if not good.get('parse_complete'):
            counts['target_not_supported'] += 1
            continue
        for kind, broken in corruptions(card):
            bad = parser.check(broken)
            if bad.get('status') == 'parser_error':
                raise RuntimeError('Parser infrastructure failure')
            if bad.get('status') != 'rejected':
                counts['corruption_still_parsed_or_unsupported'] += 1
                continue
            assert {k:v for k,v in broken.items() if k != 'oracle_text'} == {k:v for k,v in card.items() if k != 'oracle_text'}
            key = digest([group, kind, broken, card])[:24]
            accepted.append({'id': key, 'current_card': broken, 'diagnostic': bad, 'target': card})
            provenance.append({'id': key, 'source_id': row['id'], 'source_group_id': group,
                'source_split': 'train', 'mutation': kind, 'original_request': row['request'],
                'synthetic': True, 'target_parse_sha256': digest(good),
                'source_target_sha256': digest(card)})
            counts[kind] += 1
            if len(accepted) >= limit:
                return accepted, provenance, dict(counts)
    return accepted, provenance, dict(counts)


def export(source, holdouts, system, output, limit):
    if limit < 1:
        raise ValueError('Positive limit required')
    if output.exists():
        raise ValueError('Choose a new output directory; preserve previous receipts')
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    reserved = {row['source_group_id'] for file in holdouts
        for row in map(json.loads, file.read_text().splitlines())}
    with MtgishValidator() as parser:
        cases, provenance, counts = select(rows, reserved, parser, limit)
        parser_identity = parser.provenance
    # Use the browser implementation verbatim, including its parser-feedback hints.
    bridge = """import {revisionMessages,designOnly} from './prompts/browser-repair.mjs';
let input='';for await (const part of process.stdin) input+=part;
const {system,cases}=JSON.parse(input);
console.log(JSON.stringify(cases.map(c=>({
  prompt:revisionMessages(system,c.current_card,'Fix the Oracle wording while preserving its meaning.',c.diagnostic),
  completion:[{role:'assistant',content:JSON.stringify(designOnly(c.target))}]
}))));"""
    examples = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', bridge],
        input=json.dumps({'system':system.read_text(),'cases':cases}), text=True, cwd=PROJECT))
    output.mkdir(parents=True)
    files = {name:write_jsonl(output/name, records) for name,records in
        [('train.sft.jsonl',examples),('provenance.jsonl',provenance),('cases.jsonl',cases)]}
    manifest = {'status':'synthetic_pilot_prepared_not_trained', 'examples':len(examples),
        'families':len({r['source_group_id'] for r in provenance}), 'counts':counts,
        'source_sha256':digest(source.read_bytes()),
        'holdout_sha256':[digest(file.read_bytes()) for file in holdouts],
        'system_sha256':digest(system.read_bytes()),
        'browser_prompt_sha256':digest((PROJECT/'prompts/browser-repair.mjs').read_bytes()),
        'parser':parser_identity, 'files':files, 'automatic_training':False,
        'limitation':'Synthetic inverse corruptions only; requires semantic review and separate real-error evaluation before training.'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--holdout',type=Path,action='append',required=True)
    p.add_argument('--system',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--limit',type=int,default=128)
    a=p.parse_args()
    print(json.dumps(export(a.source,a.holdout,a.system,a.output,a.limit),indent=2))
