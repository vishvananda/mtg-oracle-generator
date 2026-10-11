"""Freeze, launch and collect one bounded Oracle repair SFT experiment."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import time

from custom_training_data import save
from hf_jobs import collect, cost_cap, sync_allowlist, TERMINAL
from job_budget import reserve, settle_terminal
from job_image import verify_job_image
from paths import PROJECT
from repair_sft_gpu import verify
from rl_gpu import sha


def package(root, run):
    destination = root/'package'
    if (destination/'package-manifest.json').exists():
        verify(destination, Path(run['parent']))
        return destination
    if destination.exists():
        raise ValueError('Partial package requires inspection')
    subprocess.run([run['cpu_python'], str(PROJECT/'src/prepared_training.py'),
        '--dataset', str(root/'dataset'), '--config', str(root/'config.json'),
        '--output', str(root/'prepared'), '--workers', '4'], check=True)
    destination.mkdir()
    for name in ('repair_sft_gpu.py', 'repair_sft_gpu.py.lock', 'custom_sft_gpu.py',
                 'rl_gpu.py', 'export_gguf.py', 'prepared_training.py', 'train_qlora.py'):
        shutil.copyfile(PROJECT/'src'/name, destination/name)
    for name in ('train.sft.jsonl', 'manifest.json'):
        shutil.copyfile(root/'dataset'/name, destination/name)
    for path in (root/'prepared').iterdir():
        shutil.copyfile(path, destination/path.name)
    shutil.copyfile(root/'config.json', destination/'config.json')
    shutil.copyfile(root/'system-prompt.txt', destination/'serving-system.txt')
    parent = Path(run['parent'])
    pins = {name: sha(parent/name) for name in ('identity.json', 'training-summary.json',
        'phase-complete.json', 'adapter/adapter_config.json', 'adapter/adapter_model.safetensors')}
    save(destination/'package-manifest.json', {
        'files': {p.name: sha(p) for p in destination.iterdir()}, 'parent_files': pins,
        'dataset_manifest_sha256': sha(root/'dataset/manifest.json'),
        'run_sha256': sha(root/'run.json')})
    verify(destination, parent)
    return destination


def launch(root, frozen, run):
    import huggingface_hub as hf
    record = root/'job.json'
    if record.exists():
        old = json.loads(record.read_text())
        if not old.get('job_id'):
            raise ValueError('Ambiguous earlier submission; inspect HF before any retry')
        if old['package_hash'] != sha(frozen/'package-manifest.json'):
            raise ValueError('Submitted package changed')
        return old
    token = hf.get_token()
    account = hf.whoami(token=token)['name']
    parent = json.loads(Path(run['parent_record']).read_text())
    image = run['image']
    live = hf.inspect_job(job_id=parent['job_id'], namespace=parent['namespace'], token=token)
    if live.status.stage != 'COMPLETED':
        raise ValueError('Parent DPO training has not completed')
    bucket = parent['bucket']
    if not bucket.startswith(account+'/') or not hf.bucket_info(bucket, token=token).private:
        raise ValueError('An owned private bucket is required')
    hardware = next(h for h in hf.list_jobs_hardware(token=token) if h.name == 'a100-large')
    cap = cost_cap({'timeout_minutes': run['timeout_minutes']}, hardware)
    if cap > run['maximum_compute_usd'] or run['maximum_paid_jobs'] != 1:
        raise ValueError('GPU recipe exceeds this experiment authorization')
    verify_job_image(image)
    verify(frozen, Path(run['parent']))
    manifest = json.loads((frozen/'package-manifest.json').read_text())
    if manifest['run_sha256'] != sha(root/'run.json'):
        raise ValueError('Experiment specification changed')
    pin = sha(frozen/'package-manifest.json')
    prefix = 'repair-packages/'+pin
    sync_allowlist(hf, frozen, [*manifest['files'], 'package-manifest.json'],
                   f'hf://buckets/{bucket}/{prefix}', token)
    name = 'oracle-repair-sft-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    outputs = 'runs/'+name
    receipt = {'stage': 'repair_sft', 'namespace': account, 'bucket': bucket,
        'output_prefix': outputs, 'package_hash': pin,
        'dataset_manifest_sha256': manifest['dataset_manifest_sha256'],
        'hardware_cost_cap_usd': cap, 'timeout_minutes': run['timeout_minutes'],
        'flavor': hardware.name, 'unit_cost_usd': hardware.unit_cost_usd,
        'unit_label': hardware.unit_label, 'image': image, 'state': 'submitting',
        'automatic_retry': False, 'automatic_promotion': False}
    reserve(root/'budget.json', run['maximum_compute_usd'], record, cap)
    save(record, receipt)
    job = hf.run_job(image=image, command=['uv', 'run', '--frozen', '--python', '3.12',
        '/package/repair_sft_gpu.py', '--package', '/package', '--parent', '/parent', '--output', '/outputs'],
        flavor=hardware.name, timeout=str(run['timeout_minutes'])+'m', attempts=1,
        name=name, secrets={'HF_TOKEN': token},
        volumes=[hf.Volume(type='bucket', source=bucket, path=prefix, mount_path='/package', read_only=True),
                 hf.Volume(type='bucket', source=bucket, path=parent['output_prefix'], mount_path='/parent', read_only=True),
                 hf.Volume(type='bucket', source=bucket, path=outputs, mount_path='/outputs')],
        env={'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '8',
             'UV_NO_PROGRESS': '1', 'PYTHONDONTWRITEBYTECODE': '1',
             'PYTORCH_CUDA_ALLOC_CONF': 'expandable_segments:True'}, token=token)
    receipt.update(job_id=job.id, url=job.url, state=job.status.stage)
    save(record, receipt)
    return receipt


def run(root):
    spec = json.loads((root/'run.json').read_text())
    with (root/'experiment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        frozen = package(root, spec)
        job = launch(root, frozen, spec)
        while True:
            status = collect(root/'job.json')
            save(root/'status.json', status)
            print(json.dumps(status), flush=True)
            if status['state'] in TERMINAL:
                break
            time.sleep(30)
        import huggingface_hub as hf
        live = hf.inspect_job(job_id=job['job_id'], namespace=job['namespace'])
        if live.started_at and live.finished_at:
            settle_terminal(root/'budget.json', root/'job.json', {
                'job_id': live.id, 'state': live.status.stage,
                'started_at': live.started_at.isoformat(), 'finished_at': live.finished_at.isoformat(),
                'observed_at': datetime.now(timezone.utc).isoformat()})
        if status['state'] != 'COMPLETED':
            if not (root/'gpu-failed').exists():
                collect(root/'job.json', root/'gpu-failed')
            raise ValueError('GPU job failed; no automatic paid retry')
        from export_gguf import check_custom_export
        for attempt in range(12):
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",
                           str(root/'gpu'), quiet=True, ignore_times=True)
            try:
                phase = json.loads((root/'gpu/phase-complete.json').read_text())
                summary = json.loads((root/'gpu/training-summary.json').read_text())
                identity = json.loads((root/'gpu/identity.json').read_text())
                check_custom_export(summary, identity, phase)
                if phase['package_sha256'] != sha(frozen/'package-manifest.json'):
                    raise ValueError('Wrong output package')
                for name, pin in phase['adapter_files'].items():
                    if sha(root/'gpu/adapter'/name) != pin:
                        raise ValueError('Final adapter changed')
                break
            except (OSError, KeyError, ValueError):
                if attempt == 11:
                    raise
                time.sleep(10)
        save(root/'status.json', {**status, 'phase': 'ready_for_q4_evaluation', 'automatic_promotion': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    run(parser.parse_args().root)
