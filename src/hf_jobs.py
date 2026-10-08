"""Plan, submit and collect separate, budget-bounded HF Jobs phases.

Only `launch` submits paid compute. Outputs use persistent private bucket mounts.
Use the isolated client version in configs/full-run.json, not the GPU environment.
"""
import argparse
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

from data_utils import digest
from hub_dataset import verify
from job_image import verify_job_image
from run_package import verify_package,verify_continuation
from job_budget import reserve

TERMINAL={'COMPLETED','ERROR','CANCELED','DELETED'}


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n')


def plan(package,stage,smoke=None,compatible_package=None):
    manifest=verify_package(package);recipe=json.loads((package/'recipe.json').read_text())
    config=json.loads((package/'train-config.json').read_text());settings=recipe['hf']
    if stage not in settings['stages']: raise ValueError('Stage is not enabled by this recipe')
    phase=settings['stages'][stage]
    package_hash=digest((package/'package-manifest.json').read_bytes())
    parent_hash=verify_continuation(package,compatible_package) if compatible_package else None
    estimate=None
    if stage=='train':
        if not smoke: raise ValueError('Full training requires a collected smoke run from this package')
        receipt=json.loads((smoke/'job-result.json').read_text())
        summary=json.loads((smoke/'adapter/run-summary.json').read_text())
        if receipt['state']!='COMPLETED' or receipt['stage']!='smoke' or receipt['package_hash'] not in {package_hash,parent_hash}:
            raise ValueError('Smoke job did not complete using this exact package')
        if (summary['config']!=config or not summary['loss_mask_verified'] or summary['stopped_for_training_budget']
                or summary['optimizer_steps']<settings['stages']['smoke']['max_steps']
                or summary['training_identity']['dataset_manifest_sha256']!=manifest['dataset_manifest_sha256']
                or not math.isfinite(summary['adapter_eval']['eval_loss'])):
            raise ValueError('Smoke did not validate this dataset/configuration')
        estimate=(summary['setup_seconds']+manifest['optimizer_steps']*summary['seconds_per_optimizer_step']*1.25+600)/60
        # This is an estimate, not permission to increase the configured budget.
    return {'stage':stage,'package_hash':package_hash,'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
            'flavor':settings['flavor'],'image':settings['image'],'timeout_minutes':phase['timeout_minutes'],
            'attempts':1,'private_storage':True,'measured_estimate_minutes':estimate,
            'automatic_promotion':False,'counts':manifest['counts'],'optimizer_steps':manifest['optimizer_steps'],
            'training_mode':recipe.get('purpose','full_epoch'),'experiment_budget_usd':recipe.get('budget_limit_usd'),
            'compatible_package_sha256':parent_hash}


def cost_cap(planned,hardware,budget=None):
    if hardware.unit_label!='minute' or hardware.unit_cost_usd<=0: raise ValueError('Unexpected HF billing unit/price')
    cap=planned['timeout_minutes']*hardware.unit_cost_usd
    if budget is not None and (not math.isfinite(budget) or budget<=0 or cap>budget):
        raise ValueError(f'Job timeout permits ${cap:.4f}, above the explicit ${budget} budget')
    return cap


def sync_allowlist(hf,root,names,destination,token):
    with tempfile.TemporaryDirectory(prefix='oracle-job-upload-') as tmp:
        staging=Path(tmp)
        for name in names:
            if Path(name).is_absolute() or '..' in Path(name).parts or (root/name).is_symlink():
                raise ValueError('Unsafe upload member')
            target=staging/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(root/name,target)
        hf.sync_bucket(str(staging),destination,token=token,quiet=True)


def launch(package,planned,record_path,budget,dataset=None,adapter_record=None,resume_record=None,checkpoint=None,ledger=None,compatible_package=None,reuse_base=None):
    import huggingface_hub as hf
    recipe=json.loads((package/'recipe.json').read_text());settings=recipe['hf']
    if hf.__version__!=settings['client_version']: raise ValueError('Use HF client '+settings['client_version'])
    if record_path.exists(): raise ValueError('Job record exists; inspect it before submitting another job')
    if planned.get('experiment_budget_usd') is not None and not ledger:
        raise ValueError('This experiment requires a shared --ledger for its total budget')
    manifest=verify_package(package)
    if digest((package/'package-manifest.json').read_bytes())!=planned['package_hash']: raise ValueError('Package changed after planning')
    parent_hash=verify_continuation(package,compatible_package) if compatible_package else None
    if planned.get('compatible_package_sha256')!=parent_hash: raise ValueError('Continuation package changed after planning')
    token=hf.get_token()
    if not token: raise ValueError('HF credentials missing')
    account=hf.whoami(token=token)['name'];bucket=account+'/'+settings['bucket_name']
    hardware=next(h for h in hf.list_jobs_hardware(token=token) if h.name==planned['flavor'])
    cap=cost_cap(planned,hardware,budget)
    image_check=verify_job_image(planned['image'])
    mounts=[];command=['python','/package/gpu_workflow.py','--stage',planned['stage']]
    run_name='oracle-'+planned['stage']+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output_prefix='runs/'+run_name
    # Inspect upstream job records before uploading or starting compute.
    def previous(path,stage,completed=False):
        old=json.loads(path.read_text())
        if old['package_hash'] not in {planned['package_hash'],parent_hash} or old['bucket']!=bucket or old['stage']!=stage:
            raise ValueError('Previous job identity does not match')
        job=hf.inspect_job(job_id=old['job_id'],namespace=old['namespace'],token=token)
        if job.status.stage not in ({'COMPLETED'} if completed else TERMINAL):
            raise ValueError('Previous job is not in the required terminal state')
        return old
    evaluation=planned['stage'] in ('development','validation','test')
    baseline_names=None
    if reuse_base:
        if not evaluation or not dataset or resume_record: raise ValueError('Reuse a baseline only for a fresh evaluation job')
        from reuse_baseline import baseline_files
        baseline_names=baseline_files(package,dataset,planned['stage'],reuse_base)
    if evaluation:
        if not dataset or not adapter_record: raise ValueError('Evaluation needs --dataset and --adapter-record')
        data_manifest=verify(dataset)
        if data_manifest['status']!='complete' or digest((dataset/'manifest.json').read_bytes())!=planned['dataset_manifest_sha256']:
            raise ValueError('Evaluation dataset differs from training')
        trained=previous(adapter_record,'train',True)
        mounts.append(hf.Volume(type='bucket',source=bucket,path=trained['output_prefix']+'/adapter',mount_path='/adapter',read_only=True))
        data_prefix='datasets/'+planned['dataset_manifest_sha256']
        mounts.append(hf.Volume(type='bucket',source=bucket,path=data_prefix,mount_path='/dataset',read_only=True))
        command+=['--dataset','/dataset','--adapter','/adapter']
    if resume_record:
        old=previous(resume_record,planned['stage'])
        if evaluation:
            if old.get('adapter_record_hash')!=digest(adapter_record.read_bytes()): raise ValueError('Resume adapter changed')
            output_prefix=old['output_prefix']
        else:
            if not checkpoint or not re.fullmatch(r'checkpoint-[0-9]+',checkpoint):
                raise ValueError('Training resume needs --checkpoint checkpoint-N')
            mounts.append(hf.Volume(type='bucket',source=bucket,path=old['output_prefix']+'/adapter',mount_path='/previous',read_only=True))
            command+=['--resume','/previous/'+checkpoint]
    elif checkpoint: raise ValueError('--checkpoint requires --resume-record')
    hf.create_bucket(bucket,private=True,exist_ok=True,token=token)
    if hf.bucket_info(bucket,token=token).private is not True: raise ValueError('A private bucket is required')
    package_prefix='packages/'+planned['package_hash']
    sync_allowlist(hf,package,[*manifest['files'],'package-manifest.json'],f'hf://buckets/{bucket}/{package_prefix}',token)
    if evaluation:
        sync_allowlist(hf,dataset,[*data_manifest['files'],'manifest.json','README.md'],f'hf://buckets/{bucket}/{data_prefix}',token)
    if baseline_names:
        sync_allowlist(hf,reuse_base.parent,baseline_names,f'hf://buckets/{bucket}/{output_prefix}',token)
    mounts+=[hf.Volume(type='bucket',source=bucket,path=package_prefix,mount_path='/package',read_only=True),
             hf.Volume(type='bucket',source=bucket,path=output_prefix,mount_path='/outputs')]
    record={**planned,'run_name':run_name,'bucket':bucket,'output_prefix':output_prefix,'namespace':account,
            'hardware_cost_cap_usd':cap,'image_check':image_check,'state':'submitting','public_uploads':bool(recipe.get('publication')),
            'adapter_record_hash':digest(adapter_record.read_bytes()) if adapter_record else None,
            'resumed_from':str(resume_record) if resume_record else None,
            'reused_baseline_sha256':digest(reuse_base.read_bytes()) if reuse_base else None}
    if ledger:
        if planned.get('experiment_budget_usd') is None: raise ValueError('Recipe must declare the experiment budget')
        reserve(ledger,planned['experiment_budget_usd'],record_path,cap)
        record['budget_ledger']=str(ledger.resolve())
    if recipe.get('publication'):
        publication=recipe['publication'];api=hf.HfApi(token=token)
        api.create_repo(publication['model_repo'],repo_type='model',private=False,exist_ok=True)
        if api.repo_info(publication['model_repo'],repo_type='model').private:
            raise ValueError('Checkpoint publication target must be public')
        record['publication']=publication
    # Retain 'submitting' on ambiguous API failures so a retry cannot silently duplicate a job.
    save(record_path,record)
    job=hf.run_job(image=planned['image'],command=command,flavor=planned['flavor'],timeout=str(planned['timeout_minutes'])+'m',
        attempts=1,name=run_name,secrets={'HF_TOKEN':token},volumes=mounts,token=token,
        env={'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'4',
             'UV_NO_PROGRESS':'1','PYTHONDONTWRITEBYTECODE':'1'})
    record.update(job_id=job.id,url=job.url,state=job.status.stage);save(record_path,record)
    return {k:record[k] for k in ('job_id','url','state','hardware_cost_cap_usd')}


def collect(record_path,output=None):
    import huggingface_hub as hf
    record=json.loads(record_path.read_text())
    job=hf.inspect_job(job_id=record['job_id'],namespace=record['namespace'])
    result={k:record[k] for k in ('job_id','stage','package_hash','dataset_manifest_sha256','output_prefix')}
    result.update(state=job.status.stage,url=job.url)
    if output:
        if result['state'] not in TERMINAL: raise ValueError('Collect only terminal jobs for consistent outputs')
        if output.exists(): raise ValueError('Use a fresh collection directory')
        output.mkdir(parents=True)
        hf.sync_bucket(f"hf://buckets/{record['bucket']}/{record['output_prefix']}",str(output),quiet=True)
        save(output/'job-result.json',result)
        result['downloaded_to']=str(output)
    # The launch receipt is immutable; inspection must not change resume identities.
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','launch','status','download'])
    p.add_argument('--package',type=Path);p.add_argument('--stage',choices=['smoke','train','development','validation','test'],default='smoke')
    p.add_argument('--smoke',type=Path);p.add_argument('--dataset',type=Path);p.add_argument('--adapter-record',type=Path)
    p.add_argument('--record',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--max-cost-usd',type=float);p.add_argument('--live-price',action='store_true')
    p.add_argument('--resume-record',type=Path);p.add_argument('--checkpoint')
    p.add_argument('--ledger',type=Path,help='Shared experiment budget receipt')
    p.add_argument('--compatible-package',type=Path,help='Verified parent package; only training time/budget changes are allowed')
    p.add_argument('--reuse-base',type=Path,help='Completed, hash-verified base predictions from the identical generation protocol')
    a=p.parse_args()
    if a.action in ('status','download'):
        if not a.record or (a.action=='download' and not a.output): p.error('--record and (for download) --output required')
        result=collect(a.record,a.output if a.action=='download' else None)
    else:
        if not a.package: p.error('--package required')
        result=plan(a.package,a.stage,a.smoke,a.compatible_package)
        if a.live_price:
            import huggingface_hub as hf
            hardware=next(h for h in hf.list_jobs_hardware() if h.name==result['flavor'])
            result['hardware_cost_cap_usd']=cost_cap(result,hardware)
        if a.action=='launch':
            if a.max_cost_usd is None or not a.record: p.error('Launch requires --max-cost-usd and a new --record')
            result=launch(a.package,result,a.record,a.max_cost_usd,a.dataset,a.adapter_record,a.resume_record,a.checkpoint,a.ledger,a.compatible_package,a.reuse_base)
    print(json.dumps(result,indent=2))
