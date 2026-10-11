"""Display-only name binding for blinded evaluation; raw predictions stay intact."""
import copy
import json
from data_utils import digest
from rl_teacher import batches, REVIEW, prompt


def display_card(card):
    result = copy.deepcopy(card)
    for face in [result, *(result.get('card_faces') or [])]:
        if isinstance(face.get('oracle_text'), str) and isinstance(face.get('name'), str):
            face['oracle_text'] = face['oracle_text'].replace('CARDNAME', face['name'])
    return result


def reusable_judgments(evaluation):
    report = json.loads((evaluation/'comparison.json').read_text())
    if (report.get('judge_model') != 'gpt-6.1-sol' or report.get('judge_effort') != 'medium'
            or report.get('rubric_sha256') != digest(prompt('reward-judge-v3.txt'))
            or report.get('judge_display_names') is not True):
        raise ValueError('Prior judge protocol differs')
    # The keys are hashes of the complete displayed request/card/metadata input.
    # Load only complete prior judgments from the existing content-addressed set.
    rows = [json.loads(line) for line in (evaluation/'judge/results.jsonl').read_text().splitlines()]
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate prior judgment keys')
    return {r['id']: r for r in rows}


def judge_display(cases, output, workers=3, reuse_evaluation=None):
    # Identical displayed submissions receive one judgment, regardless of arm.
    unique = {}
    keys = {}
    for case in cases:
        value = {k: case[k] for k in ('request', 'explicit_metadata')}
        value['candidate'] = display_card(case['candidate'])
        key = digest(value)
        keys[case['id']] = key
        unique[key] = {'id': key, **value}
    prior = reusable_judgments(reuse_evaluation) if reuse_evaluation else {}
    reused = {key: prior[key] for key in unique if key in prior}
    pending = [value for key, value in unique.items() if key not in reused]
    judgments = {**reused, **batches(pending, prompt('reward-judge-v3.txt'), REVIEW, output, workers)}
    if reuse_evaluation:
        from data_utils import write_jsonl
        write_jsonl(output/'results.jsonl', [judgments[key] for key in sorted(judgments)])
        (output/'reuse.json').write_text(json.dumps({'source': str(reuse_evaluation),
            'source_sha256': digest((reuse_evaluation/'judge/results.jsonl').read_bytes()),
            'reused': len(reused), 'new': len(pending)}, indent=2)+'\n')
    return {key: {**judgments[group], 'id': key} for key, group in keys.items()}
