"""Build family-disjoint repair/edit candidates; never modify source corpora."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re

from custom_training_data import read, save
from data_utils import digest, write_jsonl
from repair_corpus import corruptions
from rl_parser_cache import ParserCache

FIELDS = ('name', 'rarity', 'mana_cost', 'type_line', 'colors', 'power', 'toughness',
          'loyalty', 'defense', 'oracle_text')
REPAIR = 'Fix the Oracle wording while preserving its meaning.'


def design(card):
    return {k: copy.deepcopy(card[k]) for k in FIELDS if card.get(k) is not None}


def mutations(card):
    yield from corruptions(card)
    text = card['oracle_text']
    for kind, pattern, replacement in (
        ('loyalty_colon', r'(?m)^([+−-]?\d+): ', r'\1 '),
        ('loyalty_words', r'(?m)^\+(\d+): ', r'plus \1: '),
        ('scry_grammar', r'\b[Ss]cry (\d+)\b', r'look at the top \1 cards and scry them'),
        ('counter_grammar', r'\ba \+1/\+1 counter\b', 'one +1/+1 counters'),
        ('create_grammar', r'\b[Cc]reate (a|two)\b', r'make \1'),
    ):
        changed, count = re.subn(pattern, replacement, text, count=1)
        if count:
            yield kind, {**card, 'oracle_text': changed}
    if 'CARDNAME' in text and ' ' in card['name'] and ',' not in card['name']:
        yield 'short_self_reference', {**card, 'oracle_text': text.replace('CARDNAME', card['name'].split()[0])}


def edits(card):
    if 'Creature' in card['type_line']:
        if re.fullmatch(r'\d+', card.get('power', '')):
            value = str(int(card['power']) + 1)
            yield 'power_edit', f'Change only the power to {value}.', {**card, 'power': value}
        if not re.search(r'\bflying\b', card['oracle_text'], re.I):
            yield 'keyword_edit', 'Give this creature flying. Keep its other abilities and characteristics.', {
                **card, 'oracle_text': 'Flying' + ('\n' + card['oracle_text'] if card['oracle_text'] else '')}
    if 'Planeswalker' in card['type_line'] and re.fullmatch(r'\d+', card.get('loyalty', '')):
        value = str(int(card['loyalty']) + 1)
        yield 'loyalty_edit', f'Change only the starting loyalty to {value}.', {**card, 'loyalty': value}


def checked(parser, card):
    result = parser.check(card)
    if result.get('status') == 'parser_error':
        raise RuntimeError('Parser infrastructure failure: ' + str(result.get('error')))
    return result


def reserved_groups(official, custom, reserved):
    groups = set(json.loads(reserved.read_text())['source_group_ids'])
    for path in (official/'test.jsonl', official/'heldout-reserve.jsonl', custom/'evaluation-cases.jsonl'):
        groups.update(row['source_group_id'] for row in read(path))
    return groups


def prepare(root, official, custom, reserved, round2, system, count=640):
    if root.exists():
        raise ValueError('Use a new experiment directory')
    root.mkdir(parents=True)
    excluded = reserved_groups(official, custom, reserved)
    rows = {}
    source_files = [official/'train.jsonl', custom/'train.jsonl']
    for path in source_files:
        for line in path.open():
            row = json.loads(line)
            if row.get('split') != 'train':
                raise ValueError('Non-training source record')
            group = row['source_group_id']; card = row['target']
            if (group in excluded or group in rows or card.get('card_faces')
                    or card.get('layout', 'normal') not in ('normal', 'single')
                    or not card.get('name') or not card.get('rarity')
                    or len(card.get('oracle_text', '')) > 1600):
                continue
            rows[group] = {'source_group_id': group, 'target': design(card),
                'request': row.get('request', row.get('description')),
                'id': row.get('id', row.get('example_id')), 'source': str(path)}
    ordered = sorted(rows.values(), key=lambda r: digest(['repair-v1', r['source_group_id']]))
    walkers = [r for r in ordered if 'Planeswalker' in r['target']['type_line']]
    others = [r for r in ordered if 'Planeswalker' not in r['target']['type_line']]
    # Parse all walkers first so a shuffled broad corpus cannot bury them.
    pool = walkers + others[:count * 3]
    accepted = []; rejected = Counter()
    with ParserCache(root/'parser-cache') as parser:
        with ThreadPoolExecutor(max_workers=6) as workers:
            for row, result in zip(pool, workers.map(lambda r: checked(parser, r['target']), pool)):
                if result['parse_complete']:
                    accepted.append(row)
                else:
                    rejected['unsupported_source'] += 1
        pw = [r for r in accepted if 'Planeswalker' in r['target']['type_line']]
        nonpw = [r for r in accepted if 'Planeswalker' not in r['target']['type_line']]
        if len(pw) < 20 or len(nonpw) < count:
            raise ValueError('Insufficient supported family coverage')
        # Freeze repair-specific holdout families before proposing any examples.
        held = pw[:16] + nonpw[:64]
        train = pw[16:96] + nonpw[64:64 + count - min(80, len(pw)-16)]
        hold_ids = {r['source_group_id'] for r in held}
        def examples(row, split):
            card = row['target']; group = row['source_group_id']; result = []
            def add(kind, current, target, diagnostic, change=REPAIR):
                result.append({'id': digest([group, kind, current, target])[:24],
                    'source_group_id': group, 'split': split, 'kind': kind,
                    'current_card': current, 'target': target, 'diagnostic': diagnostic,
                    'requested_change': change, 'origin': 'synthetic' if kind not in ('no_op',) else 'control',
                    'source_id': row['id'], 'source_target_sha256': digest(card)})
            choices = sorted(mutations(card), key=lambda p: digest([group, p[0]]))
            # Include loyalty syntax explicitly; at most two corruptions per family.
            choices.sort(key=lambda p: 0 if p[0].startswith('loyalty_') else 1)
            for kind, broken in choices:
                check = checked(parser, broken)
                if check.get('status') == 'rejected':
                    add(kind, broken, card, check)
                    if len(result) == 2:
                        break
            if int(digest(['noop', group])[:4], 16) % 4 == 0:
                add('no_op', card, card, checked(parser, card))
            for kind, change, changed in list(edits(card))[:1]:
                if checked(parser, changed)['parse_complete']:
                    add(kind, card, changed, None, change)
            return result
        with ThreadPoolExecutor(max_workers=6) as workers:
            tasks = [(r, s) for s, values in [('train', train), ('evaluation', held)] for r in values]
            samples = [v for batch in workers.map(lambda pair: examples(*pair), tasks) for v in batch]
        parser_pin = parser.provenance
    # Real sampled failures from the earlier SFT policy, never held-out families.
    real = []; seen = set()
    for batch in sorted(round2.glob('batch-*')):
        cases_file = batch/'pilot/cases.jsonl'; candidates_file = batch/'review/candidates-reviewed.jsonl'
        if not cases_file.exists() or not candidates_file.exists():
            continue
        cases = {r['id']: r for r in read(cases_file)}
        candidates = sorted(read(candidates_file), key=lambda r: digest(['repair-real', r['candidate_id']]))
        for row in candidates:
            case = cases[row['case_id']]; group = case['source_group_id']
            if group in excluded or group in hold_ids or group in seen:
                continue
            assessment = row['assessment']
            if not assessment.get('schema_valid') or assessment.get('mtgish', {}).get('status') != 'rejected':
                continue
            card = json.loads(row['raw'])
            if card.get('card_faces') or not card.get('name') or not card.get('rarity'):
                continue
            seen.add(group)
            real.append({'id': 'real-' + row['candidate_id'], 'source_group_id': group,
                'split': 'train', 'kind': 'real_model_error', 'origin': 'sampled_sft',
                'current_card': design(card), 'diagnostic': assessment['mtgish'],
                'requested_change': REPAIR, 'original_request': case['request'],
                'source_file': str(candidates_file), 'raw_sha256': digest(row['raw'])})
            if len(real) >= 192:
                break
        if len(real) >= 192:
            break
    # Current serving-policy errors are evaluation only, including valid-but-unsupported controls.
    eval_real = []
    q4 = custom.parent/'review-gates-20261011/q4'
    scored = {r['case_id']: r for r in read(q4/'comparison/cases.jsonl') if r['arm'] == 'dpo'}
    eval_cases = {r['id']: r for r in read(custom/'evaluation-cases.jsonl')}
    for row in sorted(read(q4/'dpo.jsonl'), key=lambda r: digest(['real-eval', r['case_id']])):
        a = scored[row['case_id']]['checks']; case = eval_cases[row['case_id']]
        if not a.get('schema_valid') or a.get('mtgish', {}).get('status') != 'rejected':
            continue
        card = json.loads(row['raw'])
        if card.get('card_faces') or len(card.get('oracle_text', '')) > 1800:
            continue
        if any(r['source_group_id'] == case['source_group_id'] for r in eval_real):
            continue
        eval_real.append({'id': 'live-' + row['case_id'], 'source_group_id': case['source_group_id'],
            'split': 'evaluation', 'kind': 'real_model_error', 'origin': 'deployed_dpo2_q4',
            'current_card': design(card), 'diagnostic': a['mtgish'], 'requested_change': REPAIR,
            'original_request': case['request'], 'source_file': str(q4/'dpo.jsonl'), 'raw_sha256': digest(row['raw'])})
        if len(eval_real) >= 48:
            break
    source_pin = {str(p): digest(p.read_bytes()) for p in source_files + [reserved, system]}
    files = {'synthetic.jsonl': write_jsonl(root/'synthetic.jsonl', samples),
             'real-proposals.jsonl': write_jsonl(root/'real-proposals.jsonl', real + eval_real),
             'family-pool.jsonl': write_jsonl(root/'family-pool.jsonl', train + held)}
    save(root/'split.json', {'excluded_groups': sorted(excluded), 'repair_holdout_groups': sorted(hold_ids),
        'policy': 'All repair variants stay together. Synthetic holdout families may have appeared in original SFT; excluded from this continuation.'})
    (root/'system-prompt.txt').write_text(system.read_text())
    summary = {'version': 'repair-expansion-v1', 'files': files, 'source_hashes': source_pin,
        'parser': parser_pin, 'train_families': len(train), 'holdout_families': len(held),
        'planeswalker_families': {'train': sum('Planeswalker' in r['target']['type_line'] for r in train), 'evaluation': 16},
        'synthetic_counts': dict(Counter(r['split']+':'+r['kind'] for r in samples)),
        'real_training_proposals': len(real), 'real_evaluation_proposals': len(eval_real),
        'rejected': dict(rejected), 'automatic_promotion': False}
    save(root/'preparation.json', summary)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'official', 'custom', 'reserved', 'round2', 'system'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--count', type=int, default=640)
    print(json.dumps(prepare(**vars(p.parse_args())), indent=2))
