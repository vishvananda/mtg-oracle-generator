"""Freeze a training-only package and a separate complete evaluation dataset."""
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import time

from data_utils import digest
from hub_dataset import verify
from paths import PROJECT
from train_qlora import preflight,validate_config

SOURCES=('train_qlora.py','train_qlora.py.lock','generate.py','generate.py.lock',
         'data_utils.py','hub_dataset.py','prediction_io.py','evaluation_selection.py','gpu_workflow.py',
         'prepared_training.py')


def package(dataset, output, recipe_path, prepared=None):
    if output.exists(): raise ValueError('Use a new immutable package directory')
    manifest=verify(dataset)
    if manifest['status']!='complete': raise ValueError('Full run requires a complete, frozen dataset')
    recipe=json.loads(recipe_path.read_text())
    config_path=PROJECT/recipe['training_config'];config=json.loads(config_path.read_text())
    validate_config(config,-1);preflight(dataset,config)
    if prepared:
        from prepared_training import inspect_prepared
        inspect_prepared(prepared,dataset,config)
    output.mkdir(parents=True);(output/'data').mkdir()
    for name in SOURCES: shutil.copyfile(PROJECT/'src'/name,output/name)
    for name in (('manifest.json',) if prepared else ('manifest.json','train.sft.jsonl','validation.sft.jsonl')):
        shutil.copyfile(dataset/name,output/'data'/name)
    if prepared:
        for name in ('prepared-training.json','prepared-training.tar.gz'):
            shutil.copyfile(prepared/name,output/'data'/name)
    shutil.copyfile(config_path,output/'train-config.json')
    shutil.copyfile(recipe_path,output/'recipe.json')
    shutil.copyfile(dataset/'system-prompt.txt',output/'system-prompt.txt')
    files={str(p.relative_to(output)):{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size}
           for p in sorted(output.rglob('*')) if p.is_file()}
    value={'format_version':1,'dataset_manifest_sha256':digest((dataset/'manifest.json').read_bytes()),
           'dataset_release':manifest['release'],'counts':manifest['counts'],
           'effective_batch_size':config['per_device_train_batch_size']*config['gradient_accumulation_steps'],
           'optimizer_steps':math.ceil(manifest['counts']['train']/config['per_device_train_batch_size']/config['gradient_accumulation_steps'])*config['epochs'],
           'validation_monitoring_examples':min(config.get('validation_max_examples',manifest['counts']['validation']),manifest['counts']['validation']),
           'test_in_training_package':False,'prepared_training':bool(prepared),
           'files':files,'automatic_gpu_launch':False}
    (output/'package-manifest.json').write_text(json.dumps(value,indent=2)+'\n')
    return value


def verify_package(root):
    manifest=json.loads((root/'package-manifest.json').read_text())
    allowed=set(SOURCES)|{'data/manifest.json','train-config.json','recipe.json','system-prompt.txt'}
    allowed |= ({'data/prepared-training.json','data/prepared-training.tar.gz'} if manifest.get('prepared_training') else
                {'data/train.sft.jsonl','data/validation.sft.jsonl'})
    if set(manifest['files'])!=allowed or manifest['test_in_training_package'] is not False:
        raise ValueError('Unexpected training package files')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    if actual!=allowed|{'package-manifest.json'}: raise ValueError('Unlisted file in training package')
    for name,pin in manifest['files'].items():
        if any(p.is_symlink() for p in [root,root/name,*list((root/name).parents)[:len(Path(name).parts)-1]]) or digest((root/name).read_bytes())!=pin['sha256']:
            raise ValueError('Package file changed: '+name)
    if digest((root/'data/manifest.json').read_bytes())!=manifest['dataset_manifest_sha256']:
        raise ValueError('Package dataset identity mismatch')
    return manifest


def verify_continuation(package_root,parent):
    """Permit only operational time/budget changes to an immutable package."""
    current=verify_package(package_root);previous=verify_package(parent)
    if {k:v for k,v in current.items() if k!='files'}!={k:v for k,v in previous.items() if k!='files'}:
        raise ValueError('Continuation changed dataset or training metadata')
    if {k:v for k,v in current['files'].items() if k!='recipe.json'}!={k:v for k,v in previous['files'].items() if k!='recipe.json'}:
        raise ValueError('Continuation changed frozen code, data, prompt or model config')
    recipes=[json.loads((p/'recipe.json').read_text()) for p in (package_root,parent)]
    for recipe in recipes:
        recipe.pop('budget_limit_usd',None)
        phase=recipe['hf']['stages']['train']
        phase.pop('timeout_minutes',None);phase.pop('training_seconds',None)
    if recipes[0]!=recipes[1]: raise ValueError('Continuation changed more than training time and budget')
    return digest((parent/'package-manifest.json').read_bytes())


def continue_package(parent,output,training_seconds,timeout_minutes,budget_limit_usd):
    manifest=verify_package(parent)
    if output.exists(): raise ValueError('Use a new immutable continuation package')
    if (not isinstance(training_seconds,int) or not isinstance(timeout_minutes,int)
            or not 0<training_seconds<timeout_minutes*60
            or not math.isfinite(budget_limit_usd) or budget_limit_usd<=0):
        raise ValueError('Positive time/budget limits and shutdown headroom required')
    recipe=json.loads((parent/'recipe.json').read_text())
    recipe['budget_limit_usd']=budget_limit_usd
    recipe['hf']['stages']['train'].update(training_seconds=training_seconds,timeout_minutes=timeout_minutes)
    shutil.copytree(parent,output)
    (output/'recipe.json').write_text(json.dumps(recipe,indent=2)+'\n')
    manifest=copy.deepcopy(manifest)
    manifest['files']['recipe.json']={'sha256':digest((output/'recipe.json').read_bytes()),'bytes':(output/'recipe.json').stat().st_size}
    (output/'package-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    verify_continuation(output,parent)
    return manifest


def ready_exports(recovery_dirs):
    inputs=[];waiting=[]
    for directory in recovery_dirs:
        pointer=directory/'latest-export.json'
        if not pointer.exists(): waiting.append(str(directory));continue
        receipt=json.loads(pointer.read_text());root=Path(receipt['path'])
        if digest((root/'manifest.json').read_bytes())!=receipt['manifest_sha256']:
            raise ValueError('Recovery export changed')
        report=json.loads((root/'manifest.json').read_text())
        if report.get('generation_complete') is not True: waiting.append(str(directory));continue
        inputs.append(root)
    return inputs,waiting


def prepare_run(recovery_dirs, output, recipe_path):
    from prepare_dataset import prepare
    inputs,waiting=ready_exports(recovery_dirs)
    output.mkdir(parents=True,exist_ok=True)
    status=output/'preparation.json'
    if (output/'package/package-manifest.json').exists():
        verify(output/'dataset');verify_package(output/'package')
        if status.exists() and json.loads(status.read_text()).get('stage')=='prepared':
            return json.loads(status.read_text())
    if waiting:
        result={'stage':'waiting_for_final_exports','waiting':waiting,'automatic_gpu_launch':False,'updated_at':time.time()}
    else:
        recipe=json.loads(recipe_path.read_text())
        identity={'inputs':{str(p.resolve()):digest((p/'manifest.json').read_bytes()) for p in inputs},
                  'recipe_sha256':digest(recipe_path.read_bytes()),
                  'config_sha256':digest((PROJECT/recipe['training_config']).read_bytes())}
        pin=output/'preparation-inputs.json'
        if pin.exists() and json.loads(pin.read_text())!=identity: raise ValueError('Preparation inputs changed')
        pin.write_text(json.dumps(identity,indent=2)+'\n')
        # Stage large outputs atomically. Only our disposable staging directories may be removed.
        for name in ('dataset','package'):
            staging=output/(name+'.preparing')
            if staging.exists(): shutil.rmtree(staging)
            if not (output/name).exists():
                if name=='dataset': prepare(inputs,staging,recipe['release'])
                else: package(output/'dataset',staging,recipe_path)
                staging.rename(output/name)
        manifest=verify(output/'dataset');prepared=verify_package(output/'package')
        result={'stage':'prepared','dataset':str((output/'dataset').resolve()),'package':str((output/'package').resolve()),
                'counts':manifest['counts'],'optimizer_steps':prepared['optimizer_steps'],
                'dataset_manifest_sha256':prepared['dataset_manifest_sha256'],
                'package_manifest_sha256':digest((output/'package/package-manifest.json').read_bytes()),
                'automatic_gpu_launch':False,'updated_at':time.time()}
    status.write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--recovery',type=Path,action='append')
    source.add_argument('--dataset',type=Path,help='Package an existing verified complete HF dataset')
    source.add_argument('--continue-package',type=Path,help='Copy a verified package, changing only training time/budget limits')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--recipe',type=Path,default=PROJECT/'configs/full-run.json')
    p.add_argument('--prepared',type=Path,help='Verified CPU token cache (with --dataset)')
    p.add_argument('--watch',action='store_true')
    p.add_argument('--training-seconds',type=int);p.add_argument('--timeout-minutes',type=int)
    p.add_argument('--budget-limit-usd',type=float)
    a=p.parse_args()
    if a.continue_package:
        if a.watch or a.prepared or any(v is None for v in (a.training_seconds,a.timeout_minutes,a.budget_limit_usd)):
            p.error('Continuation requires explicit training-seconds, timeout-minutes and budget-limit-usd')
        print(json.dumps(continue_package(a.continue_package,a.output,a.training_seconds,a.timeout_minutes,a.budget_limit_usd),indent=2))
        raise SystemExit(0)
    if a.dataset:
        if a.watch: p.error('--watch is for recovery exports')
        print(json.dumps(package(a.dataset,a.output,a.recipe,a.prepared),indent=2));raise SystemExit(0)
    if a.prepared: p.error('--prepared requires --dataset')
    while True:
        result=prepare_run(a.recovery,a.output,a.recipe);print(json.dumps(result),flush=True)
        if result['stage']=='prepared' or not a.watch: break
        time.sleep(30)
