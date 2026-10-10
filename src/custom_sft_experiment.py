"""Durable custom-card experiment: reviewed corpus -> one GPU job -> blind report.

One explicitly invoked experiment; no paid retry or automatic model deployment.
The run file binds source paths, model identities, policy and a compute ceiling.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import random
import shutil
import subprocess
import time

from data_utils import digest
from custom_training_data import save, read, status
from export_custom_training import export
from hf_jobs import collect, sync_allowlist, cost_cap, TERMINAL
from job_budget import reserve, settle_terminal
from job_image import verify_job_image
from paths import PROJECT
from rl_gpu import sha
from rl_serving_eval import evaluate


def build_package(root, config, cpu_python):
    destination=root/'package'
    if (destination/'package-manifest.json').exists():return destination
    if destination.exists():raise ValueError('Partial package requires inspection')
    prepared=root/'prepared'
    subprocess.run([str(cpu_python),str(PROJECT/'src/prepared_training.py'),
        '--dataset',str(root/'dataset'),'--config',str(root/'config.json'),
        '--output',str(prepared),'--workers','4'],check=True)
    destination.mkdir()
    for name in ('custom_sft_gpu.py','custom_sft_gpu.py.lock','rl_gpu.py','prepared_training.py','train_qlora.py'):
        shutil.copyfile(PROJECT/'src'/name,destination/name)
    for name in ('train.sft.jsonl','manifest.json','evaluation-prompts.jsonl'):
        shutil.copyfile(root/'dataset'/name,destination/name)
    for path in prepared.iterdir():shutil.copyfile(path,destination/path.name)
    shutil.copyfile(root/'config.json',destination/'config.json')
    return destination


def launch(root, package, run):
    import huggingface_hub as hf
    record=root/'job.json'
    if record.exists():
        old=json.loads(record.read_text())
        if not old.get('job_id'):raise ValueError('Ambiguous earlier submission; inspect HF before any retry')
        if old['package_hash']!=sha(package/'package-manifest.json'):raise ValueError('Job package changed')
        return old
    token=hf.get_token(); account=hf.whoami(token=token)['name']
    sft=json.loads(Path(run['sft_record']).read_text());dpo=json.loads(Path(run['dpo_record']).read_text())
    for source in (sft,dpo):
        job=hf.inspect_job(job_id=source['job_id'],namespace=source['namespace'],token=token)
        if job.status.stage!='COMPLETED':raise ValueError('Parent adapter job is not complete')
    bucket=sft['bucket']
    if bucket!=dpo['bucket'] or not bucket.startswith(account+'/') or not hf.bucket_info(bucket,token=token).private:
        raise ValueError('Owned private training bucket required')
    hardware=next(h for h in hf.list_jobs_hardware(token=token) if h.name=='a100-large')
    minutes=run['timeout_minutes']
    cap=cost_cap({'timeout_minutes':minutes},hardware)
    if cap>run['maximum_compute_usd']:raise ValueError('Live GPU price exceeds the experiment ceiling')
    verify_job_image(sft['image'])
    manifest=json.loads((package/'package-manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if sha(package/name)!=pin:raise ValueError('Frozen package changed')
    pin=sha(package/'package-manifest.json');prefix='custom-packages/'+pin
    sync_allowlist(hf,package,[*manifest['files'],'package-manifest.json'],f'hf://buckets/{bucket}/{prefix}',token)
    name='oracle-custom-sft-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    outputs='runs/'+name
    receipt={'stage':'custom_sft','namespace':account,'bucket':bucket,'output_prefix':outputs,
        'package_hash':pin,'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
        'hardware_cost_cap_usd':cap,'timeout_minutes':minutes,
        'flavor':hardware.name,'unit_cost_usd':hardware.unit_cost_usd,'unit_label':hardware.unit_label,
        'state':'submitting','automatic_retry':False,'image':sft['image']}
    reserve(Path(run.get('budget_ledger',root/'budget.json')),run['maximum_compute_usd'],record,cap)
    save(record,receipt)
    job=hf.run_job(image=sft['image'],command=['uv','run','--frozen','--python','3.12',
        '/package/custom_sft_gpu.py','--package','/package','--sft','/sft','--dpo','/dpo','--output','/outputs'],
        flavor=hardware.name,timeout=str(minutes)+'m',attempts=1,name=name,secrets={'HF_TOKEN':token},
        volumes=[hf.Volume(type='bucket',source=bucket,path=prefix,mount_path='/package',read_only=True),
                 hf.Volume(type='bucket',source=bucket,path=sft['output_prefix']+'/adapter',mount_path='/sft',read_only=True),
                 hf.Volume(type='bucket',source=bucket,path=dpo['output_prefix']+'/adapter',mount_path='/dpo',read_only=True),
                 hf.Volume(type='bucket',source=bucket,path=outputs,mount_path='/outputs')],
        env={'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'8',
             'UV_NO_PROGRESS':'1','PYTHONDONTWRITEBYTECODE':'1','PYTORCH_CUDA_ALLOC_CONF':'expandable_segments:True'},token=token)
    receipt.update(job_id=job.id,url=job.url,state=job.status.stage);save(record,receipt)
    return receipt


def verify_outputs(root):
    destination=root/'gpu';package=root/'package'
    phase=json.loads((destination/'phase-complete.json').read_text())
    if phase['package_sha256']!=sha(package/'package-manifest.json'):raise ValueError('Wrong completed job package')
    expected={r['id'] for r in read(root/'dataset/evaluation-cases.jsonl')}
    decoding=None;backend=None
    for arm in ('sft','dpo','custom'):
        path=destination/(arm+'.jsonl');receipt=json.loads(path.with_suffix('.manifest.json').read_text())
        rows=read(path)
        if (not receipt['complete'] or receipt['sha256']!=sha(path) or receipt!=phase['generation'][arm]
            or len(rows)!=len(expected) or {r['case_id'] for r in rows}!=expected):
            raise ValueError('Incomplete/changed generations: '+arm)
        if decoding is None:decoding=receipt['decoding'];backend=receipt['backend']
        elif receipt['decoding']!=decoding or receipt['backend']!=backend:
            raise ValueError('Comparison arms use different generation protocols')
    for name,pin in phase['adapter_files'].items():
        if sha(destination/'adapter'/name)!=pin:raise ValueError('Final adapter changed')
    return phase


def panel_report(cases_path, scored_path):
    cases={r['id']:r for r in read(cases_path)};scored=read(scored_path)
    by_panel=defaultdict(lambda:defaultdict(dict))
    for row in scored:by_panel[cases[row['case_id']]['panel']][row['arm']][row['case_id']]=row
    fields={
        'schema_valid':lambda r:r['checks']['schema_valid'],
        'mtgish_accepted':lambda r:r['checks'].get('mtgish',{}).get('parse_complete') is True,
        'intent_faithful':lambda r:r['intent_faithful'],
        'faithful_clean_oracle':lambda r:r['faithful_clean_oracle'],
        'faithful_and_parsed':lambda r:r['intent_faithful'] and r['checks'].get('mtgish',{}).get('parse_complete') is True}
    result={}
    for panel,arms in by_panel.items():
        keys=set(arms['custom'])
        if any(set(rows)!=keys for rows in arms.values()):raise ValueError('Unpaired panel')
        counts={arm:{field:sum(bool(fn(r)) for r in rows.values()) for field,fn in fields.items()} for arm,rows in arms.items()}
        paired={}
        for baseline in ('sft','dpo'):
            paired[baseline]={}
            for field,fn in fields.items():
                groups=defaultdict(list)
                for key in sorted(keys):
                    groups[cases[key]['source_group_id']].append(int(bool(fn(arms['custom'][key])))-int(bool(fn(arms[baseline][key]))))
                groups=list(groups.values());rng=random.Random(43);samples=[]
                for _ in range(5000):
                    sample=[groups[rng.randrange(len(groups))] for _ in groups]
                    samples.append(sum(sum(g) for g in sample)/sum(len(g) for g in sample))
                samples.sort()
                paired[baseline][field]={'difference_percentage_points':100*(counts['custom'][field]-counts[baseline][field])/len(keys),
                    'family_bootstrap_95_percent_interval_pp':[100*samples[124],100*samples[4874]]}
        result[panel]={'cases':len(keys),'families':len({cases[k]['source_group_id'] for k in keys}),
            'counts':counts,'custom_minus_baseline':paired}
    return {'panels':result,'inference':'NF4; quantized deployment confirmation is a separate next gate',
        'caveats':['Custom holdout is parser-filtered and limited to supported single-face designs.',
                   'Descriptions and intent labels are correlated Sol judgments, not human labels.',
                   'Intervals are descriptive, family-clustered, and not corrected for multiple metrics.',
                   'Existing development cards may have been seen by the original SFT model.'],
        'automatic_promotion':False}


def run(root, run_file):
    run=json.loads(run_file.read_text());run_pin=sha(run_file)
    root.mkdir(parents=True,exist_ok=True)
    def update(phase,**values):
        if sha(run_file)!=run_pin:raise ValueError('Experiment specification changed')
        value={'phase':phase,'updated_at':datetime.now(timezone.utc).isoformat(),
               'run_sha256':run_pin,'automatic_promotion':False,**values}
        save(root/'status.json',value);print(json.dumps(value),flush=True)
    with (root/'experiment.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            progress=status(root/'corpus');update('reviewing_custom_cards',**progress)
            if progress['jobs'].get('failed'):raise ValueError('Description worker failure; inspect before continuing')
            if not progress['jobs'].get('pending') and not progress['jobs'].get('running'):break
            time.sleep(30)
        dataset=root/'dataset'
        if not (dataset/'manifest.json').exists():
            update('freezing_dataset')
            export(root/'corpus',dataset,Path(run['development']),Path(run['reserved']),Path(run['serving_system']).read_text())
        update('preparing_training')
        package=build_package(root,json.loads((root/'config.json').read_text()),Path(run['cpu_python']))
        if not (package/'package-manifest.json').exists():
            adapters={}
            for key in ('sft','dpo'):
                adapter=Path(run[key+'_adapter'])
                adapters[key]={n:sha(adapter/n) for n in ('adapter_config.json','adapter_model.safetensors')}
            adapters['sft']['run-summary.json']=sha(Path(run['sft_adapter'])/'run-summary.json')
            save(package/'package-manifest.json',{'files':{p.name:sha(p) for p in package.iterdir()},
                'adapters':adapters,'run_sha256':run_pin,'dataset_manifest_sha256':sha(dataset/'manifest.json')})
        from custom_sft_gpu import verify
        verify(package,Path(run['sft_adapter']),Path(run['dpo_adapter']))
        update('submitting_gpu')
        job=launch(root,package,run)
        while True:
            result=collect(root/'job.json');update('gpu_job',**result)
            if result['state'] in TERMINAL:break
            time.sleep(30)
        import huggingface_hub as hf
        live=hf.inspect_job(job_id=job['job_id'],namespace=job['namespace'])
        if live.started_at and live.finished_at:
            settle_terminal(Path(run.get('budget_ledger',root/'budget.json')),root/'job.json',{'job_id':live.id,'state':live.status.stage,
                'started_at':live.started_at.isoformat(),'finished_at':live.finished_at.isoformat(),
                'observed_at':datetime.now(timezone.utc).isoformat()})
        if result['state']!='COMPLETED':
            if not (root/'gpu-failed').exists():collect(root/'job.json',root/'gpu-failed')
            raise ValueError('GPU job failed; partial outputs collected; no automatic paid retry')
        for attempt in range(12):
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(root/'gpu'),quiet=True,ignore_times=True)
            try:phase=verify_outputs(root);break
            except (OSError,KeyError,ValueError):
                if attempt==11:raise
                time.sleep(10)
        update('blind_evaluation')
        comparison=root/'comparison'
        if not (comparison/'comparison.json').exists():
            evaluate(dataset/'evaluation-cases.jsonl',{arm:root/'gpu'/(arm+'.jsonl') for arm in ('sft','dpo','custom')},comparison,workers=3)
        save(comparison/'panels.json',panel_report(dataset/'evaluation-cases.jsonl',comparison/'cases.jsonl'))
        subprocess.run([run['cpu_python'],str(PROJECT/'src/plot_training.py'),
            '--summary',str(root/'gpu/training-summary.json'),'--state',str(root/'gpu/checkpoints/trainer_state.json'),
            '--output',str(root/'plots')],check=True)
        update('complete_for_review',report=str(comparison/'panels.json'),adapter=str(root/'gpu/adapter'),
               optimizer_steps=phase['training']['optimizer_steps'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    a=p.parse_args()
    try:run(a.root,a.run)
    except BlockingIOError:raise
    except Exception as error:
        save(a.root/'status.json',{'phase':'stopped_for_review','error':str(error),
            'automatic_retry':False,'automatic_promotion':False});raise
