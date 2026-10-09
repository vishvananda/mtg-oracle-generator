"""Package and launch explicitly requested preference-pilot phases on HF Jobs."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil

from data_utils import digest
from hf_jobs import cost_cap,save,sync_allowlist
from job_budget import reserve
from job_image import verify_job_image
from rl_gpu import sha,read_prompts

CONFIG={'seed':29,'candidates_per_prompt':4,'generation_batch_size':24,'max_new_tokens':2048,
    'max_length':4096,'minimum_pairs':32,'max_steps':100,'effective_batch_size':8,
    'learning_rate':5e-6,'beta':.1,'training_seconds':1800}


def package(output,adapter,pilot,development,preferences=None,review=None,config=None):
    if output.exists():raise ValueError('Use a new immutable package')
    root=Path(__file__).resolve().parents[1]
    m=json.loads((pilot/'manifest.json').read_text())
    if m['source_split']!='train' or m['test_or_reserve_used']:raise ValueError('Training-only pilot required')
    if sha(pilot/'prompts.jsonl')!=m['files']['prompts.jsonl']['sha256']:raise ValueError('Pilot prompts changed')
    prompts=read_prompts(pilot/'prompts.jsonl');dev=read_prompts(development)
    if {r['prompt'][-1]['content'] for r in prompts}&{r['prompt'][-1]['content'] for r in dev}:
        raise ValueError('Development/training prompt overlap')
    if not dev or not prompts:raise ValueError('Empty prompt set')
    output.mkdir(parents=True)
    for name in ('rl_gpu.py','rl_gpu.py.lock'):shutil.copyfile(root/'src'/name,output/name)
    shutil.copyfile(pilot/'prompts.jsonl',output/'prompts.jsonl')
    shutil.copyfile(development,output/'development.jsonl')
    chosen={**CONFIG,**(config or {})}
    if chosen['effective_batch_size']<2 or chosen['effective_batch_size']%2 or chosen['max_steps']<1:
        raise ValueError('Invalid DPO batch size or step count')
    if chosen['candidates_per_prompt']!=m['candidates_per_prompt']:
        raise ValueError('Candidate budget differs from pilot manifest')
    save(output/'config.json',chosen)
    if preferences:
        if not review:raise ValueError('Independent pair review required')
        value=json.loads(review.read_text())
        if not value['independent_review'] or value['approved_pairs_sha256']!=sha(preferences):
            raise ValueError('Review not bound to preferences')
        shutil.copyfile(preferences,output/'preferences.jsonl');shutil.copyfile(review,output/'review.json')
    manifest={'schema':1,'dataset_manifest_sha256':m['dataset_manifest_sha256'],
        'pilot_manifest_sha256':sha(pilot/'manifest.json'),'development_sha256':sha(development),
        'adapter_files':{n:sha(adapter/n) for n in ('adapter_config.json','adapter_model.safetensors')},
        'files':{p.name:sha(p) for p in output.iterdir()},'final_test_used':False}
    save(output/'manifest.json',manifest);return manifest


def launch(package,adapter_record,record,stage,ledger,authorization,timeout_minutes=None):
    import huggingface_hub as hf
    if record.exists():raise ValueError('Job receipt exists; never submit twice')
    auth=json.loads(authorization.read_text())
    if auth['scope'] not in ('one_preference_pilot','one_preference_round') or not auth['user_instruction'].strip():
        raise ValueError('Explicit preference-pilot authorization required')
    limit=10 if auth['scope']=='one_preference_pilot' else 20
    if auth['ceiling_usd']>limit:raise ValueError('Authorization exceeds the configured experiment bound')
    manifest=json.loads((package/'manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(package/name)!=pin:
            raise ValueError('Package changed')
    if stage=='train' and 'review.json' not in manifest['files']:raise ValueError('Unreviewed training package')
    old=json.loads(adapter_record.read_text());token=hf.get_token()
    job=hf.inspect_job(job_id=old['job_id'],namespace=old['namespace'],token=token)
    if job.status.stage!='COMPLETED':raise ValueError('SFT adapter job not complete')
    account=hf.whoami(token=token)['name'];bucket=old['bucket']
    if not bucket.startswith(account+'/') or not hf.bucket_info(bucket,token=token).private:
        raise ValueError('Private owned bucket required')
    hardware=next(h for h in hf.list_jobs_hardware(token=token) if h.name=='a100-large')
    minutes=timeout_minutes or {'sample':120,'train':90}[stage]
    if type(minutes) is not int or not 1<=minutes<=240:raise ValueError('Invalid allocation timeout')
    cap=cost_cap({'timeout_minutes':minutes},hardware)
    image=old['image'];verify_job_image(image)
    pin=sha(package/'manifest.json');prefix='rl-packages/'+pin
    sync_allowlist(hf,package,[*manifest['files'],'manifest.json'],f'hf://buckets/{bucket}/{prefix}',token)
    name='oracle-rl-'+stage+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    outputs='runs/'+name
    volumes=[hf.Volume(type='bucket',source=bucket,path=prefix,mount_path='/package',read_only=True),
        hf.Volume(type='bucket',source=bucket,path=old['output_prefix']+'/adapter',mount_path='/adapter',read_only=True),
        hf.Volume(type='bucket',source=bucket,path=outputs,mount_path='/outputs')]
    reserve(ledger,auth['ceiling_usd'],record,cap)
    receipt={'stage':stage,'namespace':account,'bucket':bucket,'output_prefix':outputs,'package_hash':pin,
        'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
        'package':str(package),'adapter_record':str(adapter_record),'adapter_record_sha256':sha(adapter_record),
        'authorization_sha256':sha(authorization),'hardware_cost_cap_usd':cap,'timeout_minutes':minutes,
        'flavor':hardware.name,'unit_cost_usd':hardware.unit_cost_usd,'unit_label':hardware.unit_label,
        'budget_ledger':str(ledger),'state':'submitting','automatic_retry':False}
    save(record,receipt)
    job=hf.run_job(image=image,command=['uv','run','--frozen','--python','3.12','/package/rl_gpu.py',
        '--stage',stage,'--package','/package','--adapter','/adapter','--output','/outputs'],
        flavor=hardware.name,timeout=str(minutes)+'m',attempts=1,name=name,secrets={'HF_TOKEN':token},volumes=volumes,
        env={'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'4',
             'UV_NO_PROGRESS':'1','PYTHONDONTWRITEBYTECODE':'1'},token=token)
    receipt.update(job_id=job.id,url=job.url,state=job.status.stage);save(record,receipt)
    return {k:receipt[k] for k in ('job_id','url','state','hardware_cost_cap_usd')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('package')
    for name in ('output','adapter','pilot','development'):prep.add_argument('--'+name,type=Path,required=True)
    for name in ('preferences','review'):prep.add_argument('--'+name,type=Path)
    prep.add_argument('--config',type=Path)
    launch_p=sub.add_parser('launch')
    for name in ('package','adapter-record','record','ledger','authorization'):launch_p.add_argument('--'+name,type=Path,required=True)
    launch_p.add_argument('--stage',choices=['sample','train'],required=True)
    launch_p.add_argument('--timeout-minutes',type=int)
    a=p.parse_args()
    result=package(a.output,a.adapter,a.pilot,a.development,a.preferences,a.review,json.loads(a.config.read_text()) if a.config else None) if a.command=='package' else launch(a.package,a.adapter_record,a.record,a.stage,a.ledger,a.authorization,a.timeout_minutes)
    print(json.dumps(result,indent=2))
