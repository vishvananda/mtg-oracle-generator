"""Rejudge all frozen arms with displayed names, without altering original scores."""
import argparse
import json
from pathlib import Path
from data_utils import digest, write_jsonl
from judge_display import judge_display
from rl_teacher import faithful, prompt
from custom_sft_experiment import panel_report


def calibration():
    card = {'name': 'Mistwing Adept', 'mana_cost': '{2}{U}', 'colors': ['U'],
            'type_line': 'Creature — Bird Wizard', 'power': '2', 'toughness': '2',
            'rarity': 'uncommon', 'oracle_text': 'Flying\nWhen CARDNAME enters, draw a card.'}
    request = 'A 2/2 blue creature with flying that draws me one card when it enters.'
    rows = []
    for index, (text, verdict) in enumerate([
        (card['oracle_text'], 'pass'),
        (card['oracle_text'].replace('CARDNAME', card['name']), 'pass'),
        ('Flying\nWhen CARDNAME enters, draw two cards.', 'fail'),
        ('Flying\nWhen CARDNAME enters, target opponent draws a card.', 'fail'),
        ('When CARDNAME enters, draw a card.', 'fail'),
        ('Flying\nWhen CARDNAME enters, you lose 1 life.', 'fail'),
    ]):
        rows.append({'id': str(index), 'request': request, 'explicit_metadata': {},
                     'candidate': {**card, 'oracle_text': text}, 'expected': verdict})
    return rows


def run(cases_path, scores_path, output, workers):
    output.mkdir(parents=True, exist_ok=True)
    identity = {'cases_sha256': digest(cases_path.read_bytes()),
                'original_scores_sha256': digest(scores_path.read_bytes()),
                'rubric_sha256': digest(prompt('reward-judge-v3.txt')),
                'normalization': 'CARDNAME replaced with that face name, only in oracle_text',
                'deduplication': 'identical displayed request/metadata/card judged once across arms',
                'judge_model': 'gpt-6.1-sol', 'effort': 'medium'}
    pin = output / 'identity.json'
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise ValueError('Audit inputs changed')
    pin.write_text(json.dumps(identity, indent=2) + '\n')
    controls = calibration()
    labels = judge_display(controls, output / 'calibration', workers)
    results = [{**c, 'judgment': labels[c['id']]} for c in controls]
    write_jsonl(output / 'calibration.jsonl', results)
    if any(r['judgment']['verdict'] != r['expected'] or r['judgment']['confidence'] != 'high'
           or (r['expected'] == 'pass' and not faithful(r['judgment'])) for r in results):
        raise ValueError('Calibration failed; inspect before judging the evaluation')
    cases = {c['id']: c for c in map(json.loads, cases_path.read_text().splitlines())}
    original = list(map(json.loads, scores_path.read_text().splitlines()))
    pending = []
    for row in original:
        if row['checks']['schema_valid']:
            case = cases[row['case_id']]
            pending.append({'id': row['arm'] + ':' + row['case_id'],
                            'request': case['request'], 'explicit_metadata': case['explicit_metadata'],
                            'candidate': json.loads(row['raw'])})
    judgments = judge_display(pending, output / 'judge', workers)
    rescored = []
    for row in original:
        judgment = judgments.get(row['arm'] + ':' + row['case_id'])
        good = (row['checks']['gate'] == 'needs_intent_judge' and judgment is not None
                and judgment['verdict'] == 'pass' and judgment['confidence'] == 'high'
                and not judgment['violations'])
        rescored.append({**row, 'original_judgment': row['judgment'], 'judgment': judgment,
                         'intent_faithful': good, 'faithful_clean_oracle': good and faithful(judgment)})
    write_jsonl(output / 'cases.jsonl', rescored)
    report = panel_report(cases_path, output / 'cases.jsonl')
    report['audit'] = identity
    (output / 'panels.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--scores', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', type=int, default=3)
    a = p.parse_args()
    run(a.cases, a.scores, a.output, a.workers)
