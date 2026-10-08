"""Run smoke, one bounded training segment, and development evaluation; stop for review.

Invoke using the separate HF Jobs controller environment. This command DOES
submit paid jobs, bounded by the immutable staged recipe and its shared ledger.
It never submits another training segment or accesses final-test prompts.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

from data_utils import digest
from hf_jobs import plan,launch,collect,save,TERMINAL
from run_package import verify_package
from paths import PROJECT
from train_qlora import publish_checkpoint


def run(package,dataset,output,cpu_python,ledger=None):
    manifest=verify_package(package);recipe=json.loads((package/'recipe.json').read_text())
    if recipe.get('purpose')!='checkpoint_segments' or recipe.get('budget_limit_usd')!=20:
        raise ValueError('This runner requires the reviewed $20 staged recipe')
    if set(recipe['hf']['stages'])!={'smoke','train','development'}:
        raise ValueError('Unexpected staged experiment phases')
    if digest((dataset/'manifest.json').read_bytes())!=manifest['dataset_manifest_sha256']:
        raise ValueError('Dataset differs from package')
    subprocess.run([str(cpu_python),'-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from validate_mtgish import MtgishValidator;MtgishValidator().close();import matplotlib',
        str(PROJECT/'src')],check=True)
    output.mkdir(parents=True,exist_ok=True)
    ledger=(ledger or output/'budget.json').resolve()
    identity={'package_hash':digest((package/'package-manifest.json').read_bytes()),
              'dataset_manifest_sha256':manifest['dataset_manifest_sha256'],
              'budget_ledger':str(ledger)}
    pin=output/'request.json'
    if pin.exists() and json.loads(pin.read_text())!=identity: raise ValueError('Experiment identity changed')
    save(pin,identity)
    for stage in ('smoke','train','development'):
        record=output/(stage+'-job.json');download=output/stage
        if not record.exists():
            planned=plan(package,stage,output/'smoke' if stage=='train' else None)
            cap={'smoke':1.26,'train':3.76,'development':1.26}[stage]
            launch(package,planned,record,cap,dataset=dataset if stage=='development' else None,
                   adapter_record=output/'train-job.json' if stage=='development' else None,
                   ledger=ledger)
        while True:
            status=collect(record)
            save(output/'status.json',{'phase':stage,**status,'automatic_continuation_after_review':False})
            print(json.dumps({'phase':stage,'state':status['state'],'url':status['url']}),flush=True)
            if status['state'] in TERMINAL: break
            time.sleep(30)
        if status['state']!='COMPLETED':
            if not download.exists(): collect(record,download)
            raise RuntimeError('Job did not complete; inspect its saved checkpoint/output before spending again')
        if not download.exists(): collect(record,download)
        elif not (download/'job-result.json').exists(): raise ValueError('Interrupted download; collect into a fresh directory')
        if stage in ('smoke','train') and recipe.get('publication'):
            for checkpoint in sorted((download/'adapter/public-checkpoints').glob('*')):
                if not (checkpoint/'publication.json').exists():
                    metadata=json.loads((checkpoint/'checkpoint.json').read_text())
                    receipt=publish_checkpoint(checkpoint,recipe['publication']['model_repo'],metadata['tag'])
                    save(checkpoint/'publication.json',receipt)
    report=output/'development-report'
    subprocess.run([str(cpu_python),str(PROJECT/'src/post_training.py'),'--dataset',str(dataset),
        '--adapter',str(output/'train/adapter'),'--generations',str(output/'development'),
        '--split','validation','--output',str(report)],check=True)
    save(output/'status.json',{'phase':'awaiting_quality_review','report':str(report),
        'budget_ledger':str(ledger),'automatic_continuation_after_review':False,
        'final_test_evaluated':False,'automatic_promotion':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cpu-python',type=Path,required=True)
    p.add_argument('--ledger',type=Path,help='Existing shared budget ledger when replacing an experiment')
    a=p.parse_args()
    try: run(a.package,a.dataset,a.output,a.cpu_python,a.ledger)
    except Exception as error:
        save(a.output/'status.json',{'phase':'stopped_for_error','error':str(error),'automatic_retry':False})
        raise
