"""Package, submit once, and observe a bounded second-epoch SFT segment."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import time

from hf_jobs import collect, cost_cap, sync_allowlist, TERMINAL
from job_budget import reserve, settle_terminal
from job_image import verify_job_image
from paths import PROJECT
from rl_gpu import save, sha
from sft_continuation_gpu import verify


def package(root, spec):
    destination = root/'package'
    if (destination/'package-manifest.json').is_file():
        verify(destination, Path(spec['parent']))
        return destination
    if destination.exists():
        raise ValueError('Partial package requires inspection')
    destination.mkdir()
    sources = ('sft_continuation_gpu.py', 'sft_continuation_gpu.py.lock',
               'custom_sft_gpu.py', 'rl_gpu.py', 'prepared_training.py', 'train_qlora.py')
    for name in sources:
        shutil.copyfile(PROJECT/'src'/name, destination/name)
    for name in ('prepared-training.json', 'prepared-training.tar.gz'):
        shutil.copyfile(Path(spec['prepared'])/name, destination/name)
    shutil.copyfile(Path(spec['dataset'])/'manifest.json', destination/'manifest.json')
    shutil.copyfile(root/'config.json', destination/'config.json')
    # This checks the cached tokenizer, dataset, preprocessing code and masks in
    # the same pinned CPU environment before any GPU allocation.
    subprocess.run([spec['cpu_python'], '-B', '-c',
        'import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
        'from prepared_training import inspect_prepared;'
        'p=Path(sys.argv[1]);m=inspect_prepared(p,p,json.loads((p/"config.json").read_text()));'
        'print(json.dumps({"verified_prepared_counts":m["counts"]}))', str(destination)], check=True)
    parent = Path(spec['parent'])
    save(destination/'package-manifest.json', {
        'files': {p.name: sha(p) for p in destination.iterdir()},
        'parent_files': {n: sha(parent/n) for n in ('adapter_config.json', 'adapter_model.safetensors', 'run-summary.json')},
        'dataset_manifest_sha256': sha(destination/'manifest.json'), 'run_sha256': sha(root/'run.json')})
    verify(destination, parent)
    return destination


def launch(root, frozen, spec):
    import huggingface_hub as hf
    record = root/'job.json'
    if record.exists():
        old = json.loads(record.read_text())
        if not old.get('job_id') or old['package_hash'] != sha(frozen/'package-manifest.json'):
            raise ValueError('Earlier submission requires inspection; no duplicate job')
        return old
    token = hf.get_token(); account = hf.whoami(token=token)['name']
    parent = json.loads(Path(spec['parent_record']).read_text())
    live = hf.inspect_job(job_id=parent['job_id'], namespace=parent['namespace'], token=token)
    if live.status.stage != 'COMPLETED':
        raise ValueError('Parent training has not completed')
    bucket = parent['bucket']
    if not bucket.startswith(account+'/') or not hf.bucket_info(bucket, token=token).private:
        raise ValueError('An owned private bucket is required')
    hardware = next(h for h in hf.list_jobs_hardware(token=token) if h.name == 'a100-large')
    cap = cost_cap({'timeout_minutes': spec['timeout_minutes']}, hardware)
    if cap > spec['maximum_compute_usd'] or spec['maximum_paid_training_jobs'] != 1:
        raise ValueError('Training exceeds this experiment authorization')
    image_check = verify_job_image(spec['image'])
    manifest, _, config = verify(frozen, Path(spec['parent']))
    if manifest['run_sha256'] != sha(root/'run.json'):
        raise ValueError('Experiment specification changed')
    pin = sha(frozen/'package-manifest.json'); prefix = 'sft-continuations/'+pin
    sync_allowlist(hf, frozen, [*manifest['files'], 'package-manifest.json'],
                   f'hf://buckets/{bucket}/{prefix}', token)
    name = 'oracle-sft-epoch2-quarter-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    outputs = 'runs/'+name
    receipt = {'stage': 'sft_continuation', 'namespace': account, 'bucket': bucket,
        'output_prefix': outputs, 'package_hash': pin, 'dataset_manifest_sha256': manifest['dataset_manifest_sha256'],
        'hardware_cost_cap_usd': cap, 'timeout_minutes': spec['timeout_minutes'],
        'flavor': hardware.name, 'unit_cost_usd': hardware.unit_cost_usd, 'unit_label': hardware.unit_label,
        'image': spec['image'], 'image_check': image_check, 'state': 'submitting',
        'stop_after_steps': config['stop_after_steps'], 'schedule_steps': config['schedule_steps'],
        'automatic_retry': False, 'automatic_epoch_completion': False, 'automatic_promotion': False}
    reserve(root/'budget.json', spec['maximum_compute_usd'], record, cap)
    save(record, receipt)
    job = hf.run_job(image=spec['image'], command=['uv', 'run', '--frozen', '--python', '3.12',
        '/package/sft_continuation_gpu.py', '--package', '/package', '--parent', '/parent', '--output', '/outputs'],
        flavor=hardware.name, timeout=str(spec['timeout_minutes'])+'m', attempts=1, name=name,
        secrets={'HF_TOKEN': token}, volumes=[
            hf.Volume(type='bucket', source=bucket, path=prefix, mount_path='/package', read_only=True),
            hf.Volume(type='bucket', source=bucket, path=parent['output_prefix']+'/adapter', mount_path='/parent', read_only=True),
            hf.Volume(type='bucket', source=bucket, path=outputs, mount_path='/outputs')],
        env={'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '8',
             'UV_NO_PROGRESS': '1', 'PYTHONDONTWRITEBYTECODE': '1',
             'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True'}, token=token)
    receipt.update(job_id=job.id, url=job.url, state=job.status.stage)
    save(record, receipt)
    return receipt


def watch(root):
    import huggingface_hub as hf
    job = json.loads((root/'job.json').read_text())
    while True:
        status = collect(root/'job.json')
        save(root/'status.json', status)
        print(json.dumps(status), flush=True)
        if status['state'] in TERMINAL:
            break
        time.sleep(30)
    live = hf.inspect_job(job_id=job['job_id'], namespace=job['namespace'])
    if live.started_at and live.finished_at:
        settle_terminal(root/'budget.json', root/'job.json', {'job_id': live.id, 'state': live.status.stage,
            'started_at': live.started_at.isoformat(), 'finished_at': live.finished_at.isoformat(),
            'observed_at': datetime.now(timezone.utc).isoformat()})
    destination = root/('gpu' if status['state'] == 'COMPLETED' else 'gpu-failed')
    for attempt in range(12):
        hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}", str(destination), quiet=True, ignore_times=True)
        if status['state'] != 'COMPLETED':
            raise ValueError('GPU job failed; no automatic paid retry')
        try:
            phase = json.loads((destination/'phase-complete.json').read_text())
            if phase['package_sha256'] != job['package_hash']:
                raise ValueError('Output package differs')
            for name, pin in phase['adapter_files'].items():
                if sha(destination/'adapter'/name) != pin:
                    raise ValueError('Saved adapter differs')
            for name, pin in phase['checkpoint_files'].items():
                if sha(destination/phase['training']['resume_checkpoint']/name) != pin:
                    raise ValueError('Resumable checkpoint differs')
            break
        except (OSError, json.JSONDecodeError):
            if attempt == 11:
                raise
            time.sleep(10)
    save(root/'status.json', {**status, 'phase': 'ready_for_evaluation',
        'automatic_epoch_completion': False, 'automatic_promotion': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--action', choices=('prepare', 'launch', 'watch'), required=True)
    args = parser.parse_args()
    with (args.root/'experiment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        spec = json.loads((args.root/'run.json').read_text())
        if args.action == 'watch':
            watch(args.root)
        else:
            frozen = package(args.root, spec)
            if args.action == 'launch':
                print(json.dumps(launch(args.root, frozen, spec), indent=2))
