"""Run an authorized fixed-recipe smoke -> fresh epoch -> locked final test.

No test access during smoke/training, no checkpoint selection, no paid retries,
and no automatic serving promotion. Every job uses the same shared cost ledger.
"""
import argparse
import fcntl
import json
from pathlib import Path
import time

from data_utils import digest
from hf_jobs import collect, launch, plan, save, TERMINAL
from hub_dataset import verify
from run_package import verify_package
from watch_epoch import collect_training
from finish_evaluation import run as finish_evaluation


def await_job(record,status,phase,poll):
    while True:
        current=collect(record)
        save(status,{'phase':phase,**current,'automatic_retry':False,'automatic_promotion':False})
        print(json.dumps(current),flush=True)
        if current['state'] in TERMINAL:
            if current['state']!='COMPLETED':raise RuntimeError(phase+' did not complete; no paid retry submitted')
            return current
        time.sleep(poll)


def collect_smoke(record,output,attempts=19):
    import huggingface_hub as hf
    job=json.loads(record.read_text())
    if not output.exists():collect(record,output)
    elif json.loads((output/'job-result.json').read_text())['job_id']!=job['job_id']:
        raise ValueError('Smoke collection belongs to a different job')
    for attempt in range(attempts):
        try:
            summary=json.loads((output/'adapter/run-summary.json').read_text())
            phase=json.loads((output/'phase-complete.json').read_text())
            if phase['stage']!='smoke' or phase['package_manifest_sha256']!=job['package_hash']:
                raise ValueError('Smoke completion identity changed')
            return summary
        except (FileNotFoundError,json.JSONDecodeError):
            if attempt+1==attempts:raise
            time.sleep(10)
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(output),ignore_times=True,quiet=True)


def run(package,dataset,output,ledger,cpu_python,poll=30):
    manifest=verify_package(package);data=verify(dataset)
    recipe=json.loads((package/'recipe.json').read_text())
    config=json.loads((package/'train-config.json').read_text())
    if (recipe.get('purpose')!='fixed_epoch_two_way' or data['counts'].get('validation',0)!=0
            or config.get('monitoring_split')!='train' or set(recipe['hf']['stages'])!={'smoke','train','test'}):
        raise ValueError('Expected a two-way fixed-epoch recipe with training-only diagnostics')
    if digest((dataset/'manifest.json').read_bytes())!=manifest['dataset_manifest_sha256']:
        raise ValueError('Training and test dataset identities differ')
    output.mkdir(parents=True,exist_ok=True);status=output/'status.json'
    with (output/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Explicit limits match the approved recipe; the shared ledger also
        # accounts for cancelled/earlier jobs. API price verification happens
        # immediately before every submission.
        smoke=output/'smoke-job.json'
        if not smoke.exists():launch(package,plan(package,'smoke'),smoke,1.26,ledger=ledger)
        await_job(smoke,status,'smoke',poll);collect_smoke(smoke,output/'smoke')
        train=output/'train-job.json'
        if not train.exists():
            launch(package,plan(package,'train',output/'smoke'),train,48.96,ledger=ledger)
        job=json.loads(train.read_text())
        if job.get('resumed_from'):raise ValueError('Revised split must start from the base, not an old adapter')
        await_job(train,status,'training',poll)
        save(status,{'phase':'collecting_training','automatic_retry':False})
        # A time-limited partial epoch remains a checkpoint, not a completed
        # run. It cannot trigger final test; an explicit continuation is needed.
        collect_training(train,output/'train',package)
        test=output/'test-job.json'
        if not test.exists():
            launch(package,plan(package,'test'),test,2.51,dataset=dataset,adapter_record=train,ledger=ledger)
        finish_evaluation(test,dataset,output/'train/adapter',output/'test',output/'test-report',status,cpu_python,poll)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('package','dataset','output','ledger','cpu-python'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try:run(a.package,a.dataset,a.output,a.ledger,a.cpu_python)
    except BlockingIOError:raise
    except Exception as error:
        save(a.output/'status.json',{'phase':'stopped_for_review','error':str(error),
             'automatic_retry':False,'automatic_promotion':False,'final_test_evaluated':False})
        raise
