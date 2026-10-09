"""Carry an authorized larger preference round through training and comparisons.

The small audit must have a hash-bound quality review before any GPU submission.
No paid retries, full SFT epochs or workshop promotion are automatic.
"""
import argparse,fcntl,json,shutil,subprocess,time
from datetime import datetime,timezone
from pathlib import Path

from data_utils import write_jsonl
from hf_jobs import collect,save,TERMINAL
from job_budget import settle_terminal
from rl_gpu import sha
from rl_jobs import package,launch
from rl_pipeline import verify_collected


def checked_audit(root):
    quality=json.loads((root/'audit-quality.json').read_text())
    review=root/quality['review_directory']
    if (not quality['passed'] or quality['review_sha256']!=sha(review/'review.json')
            or quality['preferences_sha256']!=sha(review/'preferences.jsonl')):
        raise ValueError('Small-batch quality review is missing or changed')
    return review


def run(root,cpu_python,export_python,sft_adapter,sft_record,dataset,old_pilot,local_binary,converter,quantizer,recovery=None):
    import huggingface_hub as hf
    repo=Path(__file__).resolve().parents[1]
    auth=root/'authorization.json';auth_hash=sha(auth);status=root/'status.json'
    def update(phase,**values):
        if sha(auth)!=auth_hash:raise ValueError('Authorization changed')
        save(status,{'phase':phase,'updated_at':datetime.now(timezone.utc).isoformat(),
            'authorization_sha256':auth_hash,'automatic_promotion':False,**values})
        print(json.dumps({'phase':phase,**values}),flush=True)
    def command(script,*args,python=cpu_python):
        for name,pin in identity['files'].items():
            if sha(repo/name)!=pin:raise ValueError('Pipeline source changed while running: '+name)
        subprocess.run([str(python),'-u',str(repo/'src'/script),*map(str,args)],check=True,cwd=repo)
    def gpu(stage,pack,record,destination,minutes):
        if not record.exists():launch(pack,sft_record,record,stage,root/'budget.json',auth,minutes)
        while True:
            live=collect(record);update(stage+'_gpu',**live)
            if live['state'] in TERMINAL:break
            time.sleep(30)
        if live['state']!='COMPLETED':
            failed=json.loads(record.read_text())
            state=hf.inspect_job(job_id=failed['job_id'],namespace=failed['namespace'])
            if state.started_at and state.finished_at:
                settle_terminal(root/'budget.json',record,{'job_id':state.id,'state':state.status.stage,
                    'started_at':state.started_at.isoformat(),'finished_at':state.finished_at.isoformat()})
            raise RuntimeError('GPU job stopped; inspect receipt before any paid retry')
        if not destination.exists():collect(record,destination)
        job=json.loads(record.read_text())
        for attempt in range(19):
            try:verify_collected(destination,pack,stage);break
            except (OSError,ValueError,KeyError):
                if attempt==18:raise
                time.sleep(10)
                hf.sync_bucket(f"hf://buckets/{job['bucket']}/{job['output_prefix']}",str(destination),ignore_times=True,quiet=True)
        state=hf.inspect_job(job_id=job['job_id'],namespace=job['namespace'])
        settle_terminal(root/'budget.json',record,{'job_id':state.id,'state':state.status.stage,
            'started_at':state.started_at.isoformat(),'finished_at':state.finished_at.isoformat(),
            'observed_at':datetime.now(timezone.utc).isoformat()})
    with (root/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        audit=checked_audit(root)
        code_files=[p for folder in ('src','prompts','schemas') for p in (repo/folder).rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts]
        identity={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
                  'files':{str(p.relative_to(repo)):sha(p) for p in sorted(code_files)}}
        recovery_plan=json.loads(recovery.read_text()) if recovery else {}
        if recovery and (not recovery_plan['reason'].strip() or recovery_plan['replacement_commit']!=identity['commit']):
            raise ValueError('Recovery must describe the inspected failure and pin the replacement commit')
        pin=root/'pipeline-code.json'
        if pin.exists() and json.loads(pin.read_text())!=identity:
            if not recovery or recovery_plan['previous_pipeline_code_sha256']!=sha(pin):
                raise ValueError('Pipeline code changed; inspect before resuming')
            history=root/'pipeline-code-history';history.mkdir(exist_ok=True)
            shutil.copyfile(pin,history/(sha(pin)+'.json'))
            save(history/(sha(recovery)+'.json'),recovery_plan)
        save(pin,identity)
        em=json.loads((root/'evaluation/manifest.json').read_text())
        for name,pin in em['files'].items():
            if sha(root/'evaluation'/name)!=pin['sha256']:raise ValueError('Frozen evaluation changed')
        # Align NF4 and CPU generation prompts. The legacy SFT recipe is retained
        # in its own report; new comparisons use this same production prompt.
        dev=root/'production-development-prompts.jsonl'
        if not dev.exists():
            rows=list(map(json.loads,(root/'evaluation/development-prompts.jsonl').read_text().splitlines()))
            for row in rows:row['prompt'][0]['content']=(root/'serving-system.txt').read_text()
            write_jsonl(dev,rows)
        configuration={'seed':17,'generation_batch_size':24,'minimum_pairs':1000,'max_steps':250,
                       'training_seconds':2400,'save_total_limit':12,'evaluate_midpoint':True}
        reviews=[audit];pilots=[root/'audit-pilot']
        count=json.loads((reviews[0]/'review.json').read_text())['approved_pairs']
        for index in range(1,4):
            if count>=1000:break
            batch=root/f'batch-{index:02d}';pilot=batch/'pilot'
            update('preparing_expansion',batch=index,approved_pairs=count)
            if not (pilot/'manifest.json').exists():
                command('rl_round_expand.py','prepare','--root',root,'--dataset',dataset,
                    '--old-pilot',old_pilot,'--index',index,'--count',2048)
            override=recovery_plan.get('sampling',{}).get(str(index))
            pack=root/override['package'] if override else batch/'sample-package'
            record=root/override['record'] if override else batch/'sample-job.json'
            if override and not (pack/'resume-provenance.json').exists():
                raise ValueError('Recovery package lacks validated sampling provenance')
            if not pack.exists():package(pack,sft_adapter,pilot,dev,config=configuration)
            gpu('sample',pack,record,batch/'sample',180)
            review=batch/'review';update('sol_review_and_repair',batch=index,approved_pairs=count)
            if not (review/'review.json').exists():
                command('rl_teacher.py','--pilot',pilot,'--candidates',batch/'sample/candidates.jsonl',
                    '--output',review,'--workers',6)
            value=json.loads((review/'review.json').read_text())
            if value.get('excluded_checklists',0)>value['scheduled_requests']*.02:
                raise ValueError('Too many requirement extraction failures; inspect before further sampling')
            count+=value['approved_pairs'];reviews.append(review);pilots.append(pilot)
            update('expansion_review_complete',batch=index,approved_pairs=count)
        if count<1000:raise ValueError('Three batches did not reach 1000 verified pairs; preserve data and inspect yield')
        merged=root/'merged-review'
        if not (merged/'review.json').exists():
            args=['merge','--root',root,'--output',merged,'--maximum',2000]
            for review in reviews:args+=['--review',review]
            command('rl_round_expand.py',*args)
        combined=root/'combined-pilot'
        if not combined.exists():
            combined.mkdir();cases=[];prompts=[]
            for pilot in pilots:
                m=json.loads((pilot/'manifest.json').read_text())
                for name in ('cases.jsonl','prompts.jsonl'):
                    if sha(pilot/name)!=m['files'][name]['sha256']:raise ValueError('Pilot changed')
                cases+=list(map(json.loads,(pilot/'cases.jsonl').read_text().splitlines()))
                prompts+=list(map(json.loads,(pilot/'prompts.jsonl').read_text().splitlines()))
            if len({c['source_group_id'] for c in cases})!=len(cases):raise ValueError('Training families repeated')
            files={n:write_jsonl(combined/n,v) for n,v in [('cases.jsonl',cases),('prompts.jsonl',prompts)]}
            save(combined/'manifest.json',{'files':files,'source_split':'train','test_or_reserve_used':False,
                'dataset_manifest_sha256':sha(dataset/'manifest.json'),'candidates_per_prompt':4,
                'unique_families':len(cases),'reserved_families_sha256':sha(root/'reserved-families.json')})
        pack=root/'train-package'
        if not pack.exists():package(pack,sft_adapter,combined,dev,merged/'preferences.jsonl',merged/'review.json',configuration)
        update('training_preflight')
        command('rl_gpu.py','--stage','preflight','--package',pack,'--adapter',sft_adapter,
            '--output',root/'training-preflight',python=export_python)
        gpu('train',pack,root/'train-job.json',root/'train',60)
        summary=json.loads((root/'train/training-summary.json').read_text())
        if summary['stopped_for_budget'] or summary['steps']!=summary['planned_steps']:raise ValueError('Training incomplete')
        update('evaluating_nf4',pairs=summary['pairs'],steps=summary['steps'])
        if not (root/'nf4-development/comparison.json').exists():
            command('rl_serving_eval.py','evaluate','--cases',root/'evaluation/development-cases.jsonl',
                '--prediction','sft='+str(root/'batch-01/sample/sft-development.jsonl'),
                '--prediction','midpoint='+str(root/'train/midpoint-development.jsonl'),
                '--prediction','dpo='+str(root/'train/dpo-development.jsonl'),
                '--output',root/'nf4-development','--workers',6)
        update('exporting_q4_candidate')
        serving=root/'serving-final'
        if not (serving/'candidate.json').exists():
            command('export_gguf.py','--adapter',root/'train/adapter','--dpo-run',root/'train',
                '--output',serving,'--converter',converter,'--quantizer',quantizer,python=export_python)
        update('evaluating_production_q4')
        # The explicit original serving artifact is supplied in the baseline receipt.
        baseline=json.loads((root/'baseline-q4-launch.json').read_text())['command']
        sft_candidate=Path(next(v.split('=',1)[1] for v in baseline if v.startswith('sft=')))
        # Reuse the exact frozen SFT generations; generate() rechecks identity
        # and output hashes before accepting this cache in the new comparison.
        baseline_output=Path(baseline[baseline.index('--output')+1])
        if not (root/'final-q4/sft.jsonl').exists():
            (root/'final-q4').mkdir(exist_ok=True)
            for suffix in ('.jsonl','.manifest.json'):
                shutil.copyfile(baseline_output/('sft'+suffix),root/'final-q4'/('sft'+suffix))
        if not (root/'final-q4/comparison/comparison.json').exists():
            command('rl_local_compare.py','--cases',root/'evaluation/development-cases.jsonl',
                '--system',root/'serving-system.txt','--binary',local_binary,'--output',root/'final-q4',
                '--candidate','sft='+str(sft_candidate),'--candidate','dpo='+str(serving/'candidate.json'),
                '--port',4220,'--workers',6)
        # Selection policy was fixed before seeing release outcomes: evaluate the
        # completed one-pass model. Midpoint is a diagnostic, not a hidden search.
        save(root/'selection.json',{'selected':'completed_one_pass_dpo','policy':'fixed final checkpoint',
            'midpoint_use':'development diagnostic only; no tuning against release set',
            'serving_sha256':json.loads((serving/'candidate.json').read_text())['output_sha256'],
            'development_comparison_sha256':sha(root/'final-q4/comparison/comparison.json')})
        update('locked_release_evaluation')
        if not (root/'release-q4/comparison/comparison.json').exists():
            command('rl_local_compare.py','--cases',root/'evaluation/release-cases.jsonl',
                '--system',root/'serving-system.txt','--binary',local_binary,'--output',root/'release-q4',
                '--candidate','sft='+str(sft_candidate),'--candidate','dpo='+str(serving/'candidate.json'),
                '--port',4220,'--workers',6)
        update('publishing_results')
        command('rl_round_publish.py','--root',root,'--prepare-only')
        command('rl_round_publish.py','--root',root,'--upload-only',python=Path(__import__('sys').executable))
        update('completed_for_review',pairs=summary['pairs'],steps=summary['steps'],
            adapter=str(root/'train/adapter'),report=str(root/'report'),
            release_comparison=str(root/'release-q4/comparison/comparison.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','cpu-python','export-python','sft-adapter','sft-record','dataset','old-pilot','local-binary','converter','quantizer'):
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--recovery',type=Path,help='Reviewed recovery receipt pinning code and explicit replacement sample jobs')
    a=p.parse_args()
    try:run(a.root,a.cpu_python,a.export_python,a.sft_adapter,a.sft_record,a.dataset,a.old_pilot,a.local_binary,a.converter,a.quantizer,a.recovery)
    except BlockingIOError:raise
    except Exception as error:
        save(a.root/'status.json',{'phase':'stopped_for_review','error':str(error),'automatic_paid_retry':False,'automatic_promotion':False})
        raise
