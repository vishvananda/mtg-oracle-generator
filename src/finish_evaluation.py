"""Watch an already submitted development job and finish its local report.

No GPU jobs are submitted here. Bucket writes can become visible after the job
enters a terminal state, so collection waits for both verified result manifests.
"""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

from data_utils import digest
from hf_jobs import collect, save, TERMINAL
from paths import PROJECT
from post_training import paired_identity


def complete_pair(directory,split='validation'):
    receipts={}
    for arm in ('base','adapter'):
        predictions=directory/f'{arm}-{split}.jsonl'
        receipts[arm]=json.loads(predictions.with_suffix('.manifest.json').read_text())
        if receipts[arm]['predictions_sha256']!=digest(predictions.read_bytes()):
            raise ValueError(arm+' predictions do not match the completion manifest')
    paired_identity(receipts['base'],receipts['adapter'])
    return receipts


def collect_settled(record,output,attempts=19,delay=10,split='validation'):
    """Refresh an owned download until late bucket commits are visible."""
    import huggingface_hub as hf
    receipt=collect(record)
    if receipt['state'] not in TERMINAL: raise ValueError('Job is still running')
    if output.exists():
        saved=json.loads((output/'job-result.json').read_text())
        for key in ('job_id','package_hash','dataset_manifest_sha256','output_prefix'):
            if saved.get(key)!=receipt[key]: raise ValueError('Download belongs to another job')
    else:
        collect(record,output)
    job=json.loads(record.read_text())
    last_error=None
    for attempt in range(attempts):
        try:
            complete_pair(output,split)
            return receipt
        except (OSError,ValueError,KeyError) as error:
            last_error=error
        if attempt+1<attempts:
            time.sleep(delay)
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(output),
                           ignore_times=True,quiet=True)
    raise RuntimeError(f'Paired evaluation remains incomplete after collection settled: {last_error}')


def run(record,dataset,adapter,output,report,status,cpu_python,poll=30):
    job=json.loads(record.read_text())
    if job['stage'] not in ('development','test'): raise ValueError('Expected development or final-test job')
    split='test' if job['stage']=='test' else 'validation'
    with record.with_suffix('.watch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            current=collect(record)
            save(status,{'phase':job['stage'],**current,'resumed_from':job.get('resumed_from'),
                         'automatic_retry':False,'automatic_continuation_after_review':False})
            print(json.dumps(current),flush=True)
            if current['state'] in TERMINAL: break
            time.sleep(poll)
        save(status,{'phase':'collecting_'+job['stage'],**current,'automatic_retry':False})
        collect_settled(record,output,split=split)
        save(status,{'phase':'scoring_'+job['stage'],**current,'report':str(report),'automatic_retry':False})
        subprocess.run([str(cpu_python),str(PROJECT/'src/post_training.py'),'--dataset',str(dataset),
            '--adapter',str(adapter),'--generations',str(output),'--split',split,
            '--output',str(report)],check=True)
        save(status,{'phase':'awaiting_quality_review','job_id':job['job_id'],'report':str(report),
                     'generations':str(output),'budget_ledger':job.get('budget_ledger'),
                     'automatic_continuation_after_review':False,'automatic_promotion':False,
                     'final_test_evaluated':split=='test'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('record','dataset','adapter','output','report','status','cpu-python'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try: run(a.record,a.dataset,a.adapter,a.output,a.report,a.status,a.cpu_python)
    except BlockingIOError:
        # A second watcher must not overwrite the active watcher's status.
        raise
    except Exception as error:
        save(a.status,{'phase':'stopped_for_error','error':str(error),'record':str(a.record),
                       'automatic_retry':False})
        raise
