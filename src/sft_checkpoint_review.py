"""Wait for one bounded SFT segment, then export and evaluate locally.

This never submits another paid job, finishes the epoch, or promotes a model.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from paths import PROJECT
from rl_gpu import save, sha
from serving_quantization import run_arm
from rl_serving_eval import evaluate
from train_qlora import checkpoint_release, publish_checkpoint
from export_lora_gguf import export_adapter
from validate_mtgish import MtgishValidator


def save_status(review, value):
    value = {**value, 'updated_at': datetime.now(timezone.utc).isoformat()}
    pending = review/'status.pending.json'
    save(pending, value)
    pending.replace(review/'status.json')


def run(training, review):
    try:
        _run(training, review)
    except Exception as error:
        path = review/'status.json'
        previous = json.loads(path.read_text()) if path.exists() else {}
        save_status(review, {**previous, 'phase': 'failed', 'failed_phase': previous.get('phase'),
            'error_type': type(error).__name__, 'errno': getattr(error, 'errno', None),
            'completed_outputs_preserved': True, 'automatic_promotion': False})
        raise


def _run(training, review):
    spec = json.loads((review/'review.json').read_text())
    pin = sha(review/'review.json')
    while True:
        status = json.loads((training/'status.json').read_text()) if (training/'status.json').exists() else {}
        if status.get('phase') == 'ready_for_evaluation':
            break
        if status.get('state') in ('ERROR', 'CANCELED'):
            raise ValueError('Training failed; no paid retry or evaluation')
        save_status(review, {'phase': 'waiting_for_training', 'spec_sha256': pin})
        time.sleep(30)
    source = training/'gpu'
    if sha(review/'review.json') != pin or sha(review/'cases.jsonl') != spec['cases_sha256'] or sha(review/'experiment.json') != spec['experiment_sha256']:
        raise ValueError('Frozen review configuration changed while waiting')
    save_status(review, {'phase': 'checking_evaluation_setup', 'spec_sha256': pin})
    # Catch a missing artifact root or changed grammar before lengthy generation.
    with MtgishValidator():
        pass
    receipt = json.loads((source/'phase-complete.json').read_text())
    for name, value in receipt['adapter_files'].items():
        if sha(source/'adapter'/name) != value:
            raise ValueError('Downloaded adapter differs from training receipt')
    save_status(review, {'phase': 'exporting', 'spec_sha256': pin})
    serving = training/'serving'
    if not (serving/'candidate.json').exists():
        subprocess.run([spec['cpu_python'], str(PROJECT/'src/export_gguf.py'),
            '--adapter', str(source/'adapter'), '--continuation-run', str(source), '--output', str(serving),
            '--converter', spec['converter'], '--quantizer', spec['quantizer']], check=True)
    if spec.get('separate_adapter'):
        separate = spec['separate_adapter']
        save_status(review, {'phase': 'exporting_separate_adapter', 'spec_sha256': pin})
        export_adapter(source/'adapter', Path(separate['base']), Path(separate['base_gguf']),
                       Path(separate['base_receipt']), Path(separate['converter']),
                       serving/'separate', python=spec['cpu_python'])
    if spec.get('publication') and not (training/'public-adapter/publication.json').exists():
        publication = spec['publication']; summary = receipt['training']
        release = checkpoint_release(source/'adapter', training/'public-adapter', {
            'phase': 'second-epoch review checkpoint', 'step': 19774+summary['optimizer_steps'],
            'epoch': 1+summary['metrics']['epoch'], 'tag': publication['tag'], 'config': summary['config'],
            'training_identity': json.loads((source/'identity.json').read_text()),
            'dataset_reference': publication['dataset_reference'], 'final_test_evaluated': False},
            (source/'adapter/system-prompt.txt').read_text())
        try:
            save(release/'publication.json', publish_checkpoint(release, publication['repo'], publication['tag']))
        except Exception as error:
            save(release/'publication-error.json', {'error_type': type(error).__name__, 'retry_required': True})
    experiment = json.loads((review/'experiment.json').read_text())
    for arm in experiment['arms']:
        save_status(review, {'phase': 'generating', 'arm': arm, 'spec_sha256': pin})
        run_arm(review, arm, port=0)
    save_status(review, {'phase': 'evaluating', 'spec_sha256': pin})
    result = evaluate(review/'cases.jsonl', {arm: review/'predictions'/(arm+'.jsonl') for arm in experiment['arms']},
                      review/'evaluation', workers=3, display_names=True)
    save_status(review, {'phase': 'complete', 'metrics': result['metrics'],
        'automatic_promotion': False, 'automatic_epoch_completion': False, 'spec_sha256': pin})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--training', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    args = parser.parse_args()
    run(args.training, args.review)
