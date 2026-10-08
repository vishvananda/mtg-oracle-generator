"""Run one paid phase. Mounted outputs/checkpoints survive job timeouts."""
import argparse
import json
from pathlib import Path
import subprocess
import hashlib
from data_utils import digest
from evaluation_selection import evaluation_allowed


def check_package(root):
    manifest=json.loads((root/'package-manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or digest((root/name).read_bytes())!=pin['sha256']:
            raise ValueError('Package hash mismatch: '+name)
    return manifest


def commands(package, output, stage, dataset=None, adapter=None, resume=None):
    recipe=json.loads((package/'recipe.json').read_text());phase=recipe['hf']['stages'][stage]
    uv=['uv','run','--frozen','--python','3.12']
    if stage in ('smoke','train'):
        command=uv+[str(package/'train_qlora.py'),'--dataset',str(package/'data'),
            '--config',str(package/'train-config.json'),'--output',str(output/'adapter'),
            '--max-steps',str(phase['max_steps']),'--training-seconds',str(phase['training_seconds']),
            '--generation-cases','4']
        if resume: command+=['--resume',str(resume)]
        if recipe.get('publication'):
            publication=recipe['publication']
            command+=['--publish-repo',publication['model_repo'],'--publish-phase',stage,
                      '--public-dataset-reference',publication['dataset_reference']]
            if publication.get('tag_prefix'): command+=['--publish-prefix',publication['tag_prefix']]
        return [command]
    if not dataset or not adapter: raise ValueError('Evaluation needs the frozen dataset and trained adapter')
    evaluation=recipe['evaluation'];result=[];split='validation' if stage=='development' else stage
    for arm in ('base','adapter'):
        command=uv+[str(package/'generate.py'),'--dataset',str(dataset),'--config',str(package/'train-config.json'),
            '--split',split,'--output',str(output/f'{arm}-{split}.jsonl'),
            '--batch-size',str(evaluation['batch_size']),'--max-new-tokens',str(evaluation['max_new_tokens']),
            '--resume-output']
        if arm=='adapter': command+=['--adapter',str(adapter)]
        if stage=='development': command+=['--validation-limit',str(evaluation['development_cases'])]
        result.append(command)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,default=Path('/package'));p.add_argument('--output',type=Path,default=Path('/outputs'))
    p.add_argument('--stage',choices=['smoke','train','development','validation','test'],required=True)
    p.add_argument('--dataset',type=Path);p.add_argument('--adapter',type=Path);p.add_argument('--resume',type=Path)
    a=p.parse_args();manifest=check_package(a.package);a.output.mkdir(parents=True,exist_ok=True)
    if a.stage in ('development','validation','test'):
        if not a.dataset or not a.adapter: p.error('--dataset and --adapter are required for evaluation')
        if digest((a.dataset/'manifest.json').read_bytes())!=manifest['dataset_manifest_sha256']: raise ValueError('Evaluation dataset changed')
        summary=json.loads((a.adapter/'run-summary.json').read_text())
        evaluation_allowed(summary,'validation' if a.stage=='development' else a.stage,
                           {'development':True} if a.stage=='development' else None)
        if summary['training_identity']['dataset_manifest_sha256']!=manifest['dataset_manifest_sha256']:
            raise ValueError('Adapter belongs to another dataset')
        if summary['training_identity']['trainer_sha256']!=digest((a.package/'train_qlora.py').read_bytes()):
            raise ValueError('Adapter was trained with another trainer implementation')
        config=json.loads((a.package/'train-config.json').read_text())
        if summary['config']!=config or summary['training_identity']['config_sha256']!=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest():
            raise ValueError('Adapter belongs to another training config')
        frozen={'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
                'package_manifest_sha256':digest((a.package/'package-manifest.json').read_bytes()),
                'adapter_files':{n:digest((a.adapter/n).read_bytes()) for n in ('adapter_config.json','adapter_model.safetensors')},
                'evaluation':json.loads((a.package/'recipe.json').read_text())['evaluation'],
                'stage':a.stage,'split':'validation' if a.stage=='development' else a.stage}
        pin=a.output/'evaluation-freeze.json'
        if pin.exists() and json.loads(pin.read_text())!=frozen: raise ValueError('Frozen model/evaluation changed')
        pin.write_text(json.dumps(frozen,indent=2)+'\n')
    for command in commands(a.package,a.output,a.stage,a.dataset,a.adapter,a.resume):
        subprocess.run(command,check=True)
    (a.output/'phase-complete.json').write_text(json.dumps({'stage':a.stage,'package_manifest_sha256':digest((a.package/'package-manifest.json').read_bytes())},indent=2)+'\n')
