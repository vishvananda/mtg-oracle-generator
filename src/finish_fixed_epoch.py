"""Finish one explicitly authorized epoch across time-limited HF jobs.

The authorization binds the existing run, dataset, code/config and final step.
It allows recorded budget increases only for completing that epoch and one
paired final evaluation. No extra epochs, hyperparameter changes, RL, model
promotion, or automatic recovery from non-timeout failures are authorized.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import time

from data_utils import digest
from finish_evaluation import run as finish_evaluation
from hf_jobs import collect, cost_cap, launch, plan, save, TERMINAL
from hub_dataset import verify
from job_budget import amend_limit, settle_terminal
from run_package import continue_package, verify_continuation, verify_package


CHECKPOINT_FILES=('adapter_model.safetensors','adapter_config.json','optimizer.pt',
                  'scheduler.pt','rng_state.pth','trainer_state.json','training-identity.json')


def authorization(path,expected_hash):
    if digest(path.read_bytes())!=expected_hash:raise ValueError('Completion authorization changed')
    value=json.loads(path.read_text())
    if value['scope']!='finish_existing_epoch_and_final_test' or not value['user_instruction'].strip():
        raise ValueError('Explicit epoch-completion authorization required')
    return value


def expected_identity(package):
    manifest=verify_package(package)
    config=json.loads((package/'train-config.json').read_text())
    data=json.loads((package/'data/manifest.json').read_text())
    return {'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
        'config_sha256':digest(json.dumps(config,sort_keys=True)),
        'trainer_sha256':digest((package/'train_qlora.py').read_bytes()),
        'train_sha256':data['files']['train.sft.jsonl']['sha256'],
        'validation_sha256':data['files'].get('validation.sft.jsonl',{}).get('sha256'),
        **({'monitoring_split':'train'} if config.get('monitoring_split')=='train' else {}),
        'max_steps':-1}


def inspect_segment(output,package,job,terminal,previous_steps):
    manifest=verify_package(package);identity=expected_identity(package)
    target=manifest['optimizer_steps'];adapter=output/'adapter'
    summary=None
    if terminal['state']=='COMPLETED':
        phase=json.loads((output/'phase-complete.json').read_text())
        if phase!={'stage':'train','package_manifest_sha256':job['package_hash']}:
            raise ValueError('Training completion identity differs')
        summary=json.loads((adapter/'run-summary.json').read_text())
        if summary['training_identity']!=identity:raise ValueError('Training identity differs')
        if summary['config']!=json.loads((package/'train-config.json').read_text()):raise ValueError('Training config differs')
        step=summary['optimizer_steps']
        if not previous_steps<step<=target:raise ValueError('No forward progress or epoch exceeded')
        if not all(math.isfinite(summary[key]['eval_loss']) for key in ('base_eval','adapter_eval')):
            raise ValueError('Nonfinite diagnostic loss')
        if not math.isfinite(summary['metrics']['train_loss']):raise ValueError('Nonfinite training loss')
        if step==target and summary['metrics']['epoch']>=summary['config']['epochs'] and not summary['stopped_for_training_budget']:
            return {'complete':True,'step':step,'summary':summary,'checkpoint':None}
        if not summary['stopped_for_training_budget']:
            raise ValueError('Unexpected incomplete epoch; not a time-limit stop')
        candidates=[adapter/f'checkpoint-{step}']
    elif terminal['state']=='ERROR' and 'timeout' in (terminal.get('message') or '').casefold():
        candidates=sorted((p for p in adapter.glob('checkpoint-*') if p.name.removeprefix('checkpoint-').isdigit()),
                          key=lambda p:int(p.name.split('-')[-1]),reverse=True)
    else:raise ValueError('Only normal budget stops and HF timeouts may continue automatically')
    for checkpoint in candidates:
        if not all((checkpoint/name).is_file() and (checkpoint/name).stat().st_size for name in CHECKPOINT_FILES):continue
        if json.loads((checkpoint/'training-identity.json').read_text())!=identity:
            raise ValueError('Checkpoint belongs to another dataset/config/trainer')
        state=json.loads((checkpoint/'trainer_state.json').read_text());step=state['global_step']
        if state['max_steps']!=target or checkpoint.name!=f'checkpoint-{step}':
            raise ValueError('Checkpoint schedule or step differs')
        if not previous_steps<step<target:raise ValueError('No resumable forward progress inside the authorized epoch')
        return {'complete':False,'step':step,'checkpoint':checkpoint.name,'summary':summary}
    raise FileNotFoundError('Complete resumable optimizer checkpoint not yet available')


def collect_segment(record,output,package,terminal,previous_steps,attempts=19):
    import huggingface_hub as hf
    job=json.loads(record.read_text())
    if output.exists():
        receipt=json.loads((output/'job-result.json').read_text())
        if receipt['job_id']!=job['job_id']:raise ValueError('Collection belongs to another job')
    else:collect(record,output)
    for attempt in range(attempts):
        try:return inspect_segment(output,package,job,terminal,previous_steps)
        except (FileNotFoundError,json.JSONDecodeError):
            if attempt+1==attempts:raise
            time.sleep(10)
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(output),ignore_times=True,quiet=True)


def ensure_room(ledger,amount,authorization_path,authorization_hash):
    auth=authorization(authorization_path,authorization_hash)
    current=json.loads(ledger.read_text())
    needed=math.ceil((sum(r['maximum_usd'] for r in current['reservations'])+amount)*100)/100
    if needed>current['limit_usd']:
        current=amend_limit(ledger,current['limit_usd'],needed,
            'User authorized completion of the existing epoch and final test, superseding the $60 ceiling. '
            f'Authorization {authorization_hash}; final optimizer step {auth["target_optimizer_steps"]}.')
    return current['limit_usd']


def segment_limits(result,target):
    remaining=target-result['step']
    if remaining<1:raise ValueError('No authorized training steps remain')
    speed=(result.get('summary') or {}).get('seconds_per_optimizer_step',4.)
    if not math.isfinite(speed) or speed<=0:raise ValueError('Invalid measured training speed')
    # At most 3.5 training hours per continuation, with 15 minutes to load/save.
    # Repeated continuations require a strictly newer verified checkpoint.
    seconds=max(1800,min(12600,math.ceil(remaining*speed*1.5)))
    return seconds,math.ceil((seconds+900)/60)


def package_for_phase(parent,destination,seconds,minutes,budget):
    if not destination.exists():continue_package(parent,destination,seconds,minutes,budget)
    verify_continuation(destination,parent)
    recipe=json.loads((destination/'recipe.json').read_text())
    if recipe['budget_limit_usd']!=budget:raise ValueError('Prepared continuation budget changed')
    phase=recipe['hf']['stages']['train']
    if phase['training_seconds']!=seconds or phase['timeout_minutes']!=minutes:
        raise ValueError('Prepared continuation time limits changed')
    return destination


def run(auth_path,auth_hash,poll=30):
    import huggingface_hub as hf
    auth=authorization(auth_path,auth_hash)
    original=Path(auth['package']);dataset=Path(auth['dataset']);record=Path(auth['record'])
    output=Path(auth['output']);ledger=Path(auth['ledger']);cpu_python=Path(auth['cpu_python'])
    manifest=verify_package(original);config=json.loads((original/'train-config.json').read_text())
    if (digest((original/'package-manifest.json').read_bytes())!=auth['package_hash']
            or digest(record.read_bytes())!=auth['initial_record_sha256']
            or manifest['dataset_manifest_sha256']!=auth['dataset_manifest_sha256']
            or manifest['optimizer_steps']!=auth['target_optimizer_steps'] or config['epochs']!=1):
        raise ValueError('Authorization does not match the existing epoch')
    verify(dataset)
    if digest((dataset/'manifest.json').read_bytes())!=auth['dataset_manifest_sha256']:
        raise ValueError('Evaluation dataset changed')
    status=output/'status.json';folder=output/'completion';folder.mkdir(exist_ok=True)
    target=manifest['optimizer_steps'];package=original;previous_steps=0;index=0;download=output/'train'
    with (output/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            authorization(auth_path,auth_hash)
            job=json.loads(record.read_text())
            if job['package_hash']!=digest((package/'package-manifest.json').read_bytes()):
                raise ValueError('Training record and package differ')
            while True:
                current=collect(record)
                save(status,{'phase':'training',**current,'finish_epoch_authorized':True,
                    'authorization_sha256':auth_hash,'automatic_time_limit_resume':True,
                    'target_optimizer_steps':target,'segment':index,'previous_optimizer_steps':previous_steps,
                    'automatic_retry_other_failures':False,'automatic_promotion':False})
                if current['state'] in TERMINAL:break
                time.sleep(poll)
            if current['state']!='COMPLETED' and not (current['state']=='ERROR' and 'timeout' in (current.get('message') or '').casefold()):
                raise RuntimeError('Training failed or was canceled; no automatic paid retry')
            save(status,{'phase':'collecting_training','job_id':job['job_id'],'finish_epoch_authorized':True})
            result=collect_segment(record,download,package,current,previous_steps)
            live=hf.inspect_job(job_id=job['job_id'],namespace=job['namespace'])
            settle_terminal(ledger,record,{'job_id':live.id,'state':live.status.stage,
                'started_at':live.started_at.isoformat(),'finished_at':live.finished_at.isoformat() if live.finished_at else None,
                'observed_at':datetime.now(timezone.utc).isoformat()})
            save(folder/f'segment-{index:03d}.json',{'record':str(record),'package':str(package),
                'step':result['step'],'complete':result['complete'],'checkpoint':result['checkpoint'],
                'authorization_sha256':auth_hash})
            recipe=json.loads((package/'recipe.json').read_text())
            hardware=next(h for h in hf.list_jobs_hardware() if h.name==recipe['hf']['flavor'])
            test_cap=cost_cap({'timeout_minutes':recipe['hf']['stages']['test']['timeout_minutes']},hardware)
            if result['complete']:
                test=output/'test-job.json'
                if not test.exists():
                    budget=ensure_room(ledger,test_cap,auth_path,auth_hash)
                    limits=recipe['hf']['stages']['train']
                    test_package=package_for_phase(package,folder/'test-package',limits['training_seconds'],limits['timeout_minutes'],budget)
                    launch(test_package,plan(test_package,'test',compatible_package=package),test,test_cap+.01,
                        dataset=dataset,adapter_record=record,ledger=ledger,compatible_package=package)
                finish_evaluation(test,dataset,download/'adapter',output/'test',output/'test-report',status,cpu_python,poll)
                return
            seconds,minutes=segment_limits(result,target)
            train_cap=cost_cap({'timeout_minutes':minutes},hardware)
            index+=1;next_root=folder/f'continuation-{index:03d}';next_record=next_root/'train-job.json'
            if next_record.exists():
                # Recover a submitted job after a local watcher interruption.
                next_package=next_root/'package';verify_continuation(next_package,package)
                saved=json.loads(next_record.read_text())
                if saved.get('resumed_from')!=str(record):raise ValueError('Continuation belongs to another parent')
            else:
                budget=ensure_room(ledger,train_cap+test_cap,auth_path,auth_hash)
                next_package=package_for_phase(package,next_root/'package',seconds,minutes,budget)
                proposed=plan(next_package,'train',output/'smoke',compatible_package=package,smoke_package=original)
                authorization(auth_path,auth_hash)
                launch(next_package,proposed,next_record,train_cap+.01,resume_record=record,
                    checkpoint=result['checkpoint'],ledger=ledger,compatible_package=package)
            previous_steps=result['step'];package=next_package;record=next_record;download=next_root/'train'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--authorization',type=Path,required=True);p.add_argument('--authorization-sha256',required=True)
    a=p.parse_args()
    try:run(a.authorization,a.authorization_sha256)
    except BlockingIOError:raise
    except Exception as error:
        auth=authorization(a.authorization,a.authorization_sha256)
        save(Path(auth['output'])/'status.json',{'phase':'stopped_for_review','error':str(error),
            'automatic_retry':False,'automatic_promotion':False,'final_test_evaluated':False})
        raise
