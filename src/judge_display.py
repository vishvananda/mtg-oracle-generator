"""Display-only name binding for blinded evaluation; raw predictions stay intact."""
import copy
from data_utils import digest
from rl_teacher import batches, REVIEW, prompt


def display_card(card):
    result = copy.deepcopy(card)
    for face in [result, *(result.get('card_faces') or [])]:
        if isinstance(face.get('oracle_text'), str) and isinstance(face.get('name'), str):
            face['oracle_text'] = face['oracle_text'].replace('CARDNAME', face['name'])
    return result


def judge_display(cases, output, workers=3):
    # Identical displayed submissions receive one judgment, regardless of arm.
    unique = {}
    keys = {}
    for case in cases:
        value = {k: case[k] for k in ('request', 'explicit_metadata')}
        value['candidate'] = display_card(case['candidate'])
        key = digest(value)
        keys[case['id']] = key
        unique[key] = {'id': key, **value}
    judgments = batches(list(unique.values()), prompt('reward-judge-v3.txt'), REVIEW,
                        output, workers)
    return {key: {**judgments[group], 'id': key} for key, group in keys.items()}
