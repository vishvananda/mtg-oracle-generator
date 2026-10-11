"""Compare separate adapters against merged quants with shared blind judgments."""
import argparse
import json
from pathlib import Path
import time

from rl_gpu import save, sha
from rl_serving_eval import evaluate


def run(merged, adapters, output):
    if sha(merged/'cases.jsonl') != sha(adapters/'cases.jsonl'):
        raise ValueError('Joint comparison requires identical frozen cases')
    output.mkdir(exist_ok=True)
    while True:
        statuses = [json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
                    for root in (merged, adapters)]
        if statuses[0].get('phase') == 'complete' and statuses[1].get('phase') == 'ready_for_joint_evaluation':
            break
        save(output/'status.json', {'phase': 'waiting_for_comparisons'})
        time.sleep(30)
    predictions = {}
    for root in (merged, adapters):
        for arm in json.loads((root/'experiment.json').read_text())['arms']:
            if arm in predictions:
                raise ValueError('Duplicate comparison arm')
            predictions[arm] = root/'predictions'/(arm+'.jsonl')
    save(output/'status.json', {'phase': 'evaluating', 'arms': list(predictions)})
    result = evaluate(merged/'cases.jsonl', predictions, output/'evaluation', workers=3,
        display_names=True, reuse_evaluation=merged/'evaluation')
    save(output/'status.json', {'phase': 'complete', 'metrics': result['metrics'], 'automatic_promotion': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('merged', 'adapters', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    run(**vars(parser.parse_args()))
