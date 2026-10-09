"""Run one explicitly authorized offline preference pilot; never deploy automatically."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
from pathlib import Path
import subprocess
import time

from hf_jobs import collect,save,TERMINAL
from job_budget import settle_terminal
from rl_jobs import package,launch
from rl_gpu import sha,read_prompts


def verify_collected(destination,package,stage):
    """Require complete, hash-matching generations after bucket writes settle."""
    identity=json.loads((destination/'identity.json').read_text())
    if identity['package_manifest_sha256']!=sha(package/'manifest.json') or identity['stage']!=stage:
        raise ValueError('Collected job/package differs')
    phase=json.loads((destination/'phase-complete.json').read_text())
    if phase['stage']!=stage:raise ValueError('Collected phase differs')
    config=json.loads((package/'config.json').read_text())
    outputs=[('development','sft-development' if stage=='sample' else 'dpo-development','development',1)]
    if stage=='sample':outputs.append(('candidates','candidates','prompts',config['candidates_per_prompt']))
    for key,name,source,copies in outputs:
        path=destination/(name+'.jsonl')
        receipt=json.loads(path.with_suffix('.manifest.json').read_text())
        expected={r['id'] for r in read_prompts(package/(source+'.jsonl'))}
        if (not receipt['complete'] or receipt!=phase[key] or receipt['sha256']!=sha(path)
                or receipt['cases']!=len(expected) or receipt['completions']!=len(expected)*copies):
            raise ValueError('Generation receipt is incomplete or changed: '+name)
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        expected_pairs={(case_id,case_id+f'-{i:02d}') for case_id in expected for i in range(copies)}
        if len(rows)!=len(expected_pairs) or {(r['case_id'],r['candidate_id']) for r in rows}!=expected_pairs:
            raise ValueError('Generation contains missing, unexpected or duplicate candidates: '+name)
    if stage=='train':
        summary=json.loads((destination/'training-summary.json').read_text())
        if any(phase[k]!=v for k,v in summary.items()):raise ValueError('Training summary differs from completed phase')
        for name in ('adapter_config.json','adapter_model.safetensors'):
            if not (destination/'adapter'/name).stat().st_size:raise ValueError('Empty trained adapter')
    return phase


def run(root,sft_adapter,sft_record,pilot,cpu_python,authorization):
    import huggingface_hub as hf
    root.mkdir(parents=True,exist_ok=True);repo=Path(__file__).resolve().parents[1]
    status=root/'status.json';ledger=root/'budget.json';auth_hash=sha(authorization)
    def update(phase,**kwargs):
        if sha(authorization)!=auth_hash:raise ValueError('Authorization changed')
        save(status,{'phase':phase,'updated_at':datetime.now(timezone.utc).isoformat(),
            'authorization_sha256':auth_hash,'automatic_promotion':False,**kwargs})
        print(json.dumps({'phase':phase,**kwargs}),flush=True)
    def command(script,*args):subprocess.run([str(cpu_python),str(repo/'src'/script),*map(str,args)],check=True)
    def gpu(stage,pack,record,destination):
        if not record.exists():
            launch(pack,sft_record,record,stage,ledger,authorization)
        while True:
            result=collect(record);update(stage+'_gpu',**result)
            if result['state'] in TERMINAL:break
            time.sleep(30)
        if result['state']!='COMPLETED':raise RuntimeError(stage+' job did not complete; inspect partial outputs before retrying')
        job=json.loads(record.read_text())
        if not destination.exists():collect(record,destination)
        # Mounted files can settle a little after the job reaches terminal state.
        last_error=None
        for attempt in range(19):
            try:
                verify_collected(destination,pack,stage)
                break
            except (OSError,ValueError,KeyError) as error:last_error=error
            if attempt==18:raise RuntimeError('Job outputs did not settle: '+str(last_error))
            time.sleep(10)
            hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(destination),ignore_times=True,quiet=True)
        live=hf.inspect_job(job_id=job['job_id'],namespace=job['namespace'])
        settle_terminal(ledger,record,{'job_id':live.id,'state':live.status.stage,
            'started_at':live.started_at.isoformat(),'finished_at':live.finished_at.isoformat(),
            'observed_at':datetime.now(timezone.utc).isoformat()})
    with (root/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        update('waiting_for_final_sft',adapter=str(sft_adapter))
        while not (sft_adapter/'run-summary.json').exists():time.sleep(30)
        # Do not rent a GPU until the independent calibration is complete.
        update('waiting_for_calibration')
        for name in ('calibration-blind-luna','calibration-blind-sol'):
            path=root/name/'summary.json'
            while not path.exists():time.sleep(30)
            calibration=json.loads(path.read_text())
            if calibration['labeled_cases']<200 or calibration['agreement']/calibration['labeled_cases']<.95 or calibration['false_accepts']:
                raise ValueError('Judge calibration needs review before training: '+name)
        dev=root/'development-prompts.jsonl'
        pack=root/'sample-package'
        if not pack.exists():package(pack,sft_adapter,pilot,dev)
        gpu('sample',pack,root/'sample-job.json',root/'sample')
        update('scoring_candidates')
        if not (root/'scored/manifest.json').exists():
            command('rl_score.py','--pilot',pilot,'--candidates',root/'sample/candidates.jsonl',
                '--output',root/'scored','--judge-model','gpt-6-luna','--effort','medium')
        update('exporting_pairs')
        if not (root/'pairs/manifest.json').exists():
            command('rl_data.py','export-pairs','--pilot',pilot,'--scored',root/'scored/scored.jsonl','--output',root/'pairs')
        update('independent_pair_review')
        if not (root/'review/review.json').exists():
            command('rl_review.py','--pilot',pilot,'--pairs',root/'pairs','--output',root/'review',
                '--model','gpt-6.1-sol','--effort','medium')
        reviewed=json.loads((root/'review/review.json').read_text())
        if reviewed['approved_pairs']<32:raise ValueError('Fewer than 32 independently reviewed pairs; inspect candidate coverage/repairs')
        train_pack=root/'train-package'
        if not train_pack.exists():package(train_pack,sft_adapter,pilot,dev,root/'review/preferences.jsonl',root/'review/review.json')
        gpu('train',train_pack,root/'train-job.json',root/'train')
        summary=json.loads((root/'train/training-summary.json').read_text())
        if summary['stopped_for_budget'] or summary['steps']!=summary['planned_steps']:
            raise ValueError('Preference training incomplete; preserve checkpoint for review')
        update('evaluating_dpo',steps=summary['steps'],pairs=summary['pairs'])
        if not (root/'comparison/comparison.json').exists():
            command('rl_evaluate.py','--cases',root/'development-cases.jsonl','--sft',root/'sample/sft-development.jsonl',
                '--dpo',root/'train/dpo-development.jsonl','--output',root/'comparison','--model','gpt-6.1-sol')
        update('completed_for_review',comparison=str(root/'comparison/comparison.json'),
            adapter=str(root/'train/adapter'),pairs=summary['pairs'],steps=summary['steps'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','sft-adapter','sft-record','pilot','cpu-python','authorization'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try:run(a.root,a.sft_adapter,a.sft_record,a.pilot,a.cpu_python,a.authorization)
    except BlockingIOError:raise
    except Exception as error:
        save(a.root/'status.json',{'phase':'stopped_for_review','error':str(error),'automatic_retry':False,'automatic_promotion':False})
        raise
