"""Watch an approved epoch completion job, evaluate once, and stop for review.

Only the development evaluation is submitted here; it reuses a verified base
arm and reserves its full timeout cost in the existing shared budget ledger.
Failed jobs never trigger an automatic paid retry or model promotion.
"""
import argparse
import fcntl
import json
from pathlib import Path
import time

from finish_evaluation import run as finish_evaluation
from data_utils import digest
from hf_jobs import collect,launch,plan,save,TERMINAL
from run_package import verify_package


def collect_training(record,output,package,attempts=19):
    import huggingface_hub as hf
    job=json.loads(record.read_text());manifest=verify_package(package)
    if not output.exists():collect(record,output)
    else:
        saved=json.loads((output/'job-result.json').read_text())
        if saved['job_id']!=job['job_id']:raise ValueError('Training download belongs to another job')
    for attempt in range(attempts):
        try:
            complete=json.loads((output/'phase-complete.json').read_text())
            summary=json.loads((output/'adapter/run-summary.json').read_text())
            if complete['stage']!='train' or complete['package_manifest_sha256']!=job['package_hash']:
                raise ValueError('Training completion identity differs')
            if summary['training_identity']['dataset_manifest_sha256']!=manifest['dataset_manifest_sha256']:
                raise ValueError('Training dataset differs')
            if (summary['stopped_for_training_budget'] or summary['max_steps']!=-1
                    or summary['optimizer_steps']!=manifest['optimizer_steps']
                    or summary['metrics']['epoch']<summary['config']['epochs']):
                raise ValueError('Training stopped before completing the configured epoch')
            return summary
        except (FileNotFoundError,json.JSONDecodeError):
            if attempt+1==attempts:raise
            time.sleep(10)
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(output),ignore_times=True,quiet=True)


def run(package,record,dataset,baseline,output,cpu_python,poll=30):
    job=json.loads(record.read_text());status=output/'status.json'
    verify_package(package)
    if digest((package/'package-manifest.json').read_bytes())!=job['package_hash']:
        raise ValueError('Watcher package differs from submitted training job')
    if job['stage']!='train' or not job.get('resumed_from'):raise ValueError('Expected an approved resumed training job')
    if not job.get('budget_ledger'):raise ValueError('Shared budget ledger required')
    output.mkdir(parents=True,exist_ok=True)
    with (output/'watch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            current=collect(record)
            save(status,{'phase':'finishing_epoch',**current,'automatic_retry':False,'automatic_promotion':False})
            print(json.dumps(current),flush=True)
            if current['state'] in TERMINAL:break
            time.sleep(poll)
        if current['state']!='COMPLETED':raise RuntimeError('Epoch job failed; inspect saved checkpoints before another paid job')
        save(status,{'phase':'collecting_training',**current,'automatic_retry':False})
        collect_training(record,output/'train',package)
        evaluation=output/'development-job.json'
        if not evaluation.exists():
            launch(package,plan(package,'development'),evaluation,1.26,dataset=dataset,
                   adapter_record=record,ledger=Path(job['budget_ledger']),reuse_base=baseline)
        finish_evaluation(evaluation,dataset,output/'train/adapter',output/'development',
                          output/'development-report',status,cpu_python,poll)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('package','record','dataset','baseline','output','cpu-python'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try:run(a.package,a.record,a.dataset,a.baseline,a.output,a.cpu_python)
    except BlockingIOError:raise
    except Exception as error:
        save(a.output/'status.json',{'phase':'stopped_for_error','error':str(error),
                                    'record':str(a.record),'automatic_retry':False,'automatic_promotion':False})
        raise
