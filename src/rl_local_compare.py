"""Run isolated local evaluation servers; never replace workshop processes."""
import argparse,json,subprocess,time,urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from rl_data import sha_file
from rl_serving_eval import generate,evaluate


@contextmanager
def server(binary,candidate,port,work):
    meta=json.loads(candidate.read_text());model=Path(meta['model_path'])
    if sha_file(model)!=meta['output_sha256']:raise ValueError('Serving artifact changed')
    work.mkdir(parents=True,exist_ok=True)
    cmd=[str(binary),'-m',str(model),'--host','127.0.0.1','--port',str(port),'--threads','8',
        '--threads-batch','8','--threads-http','2','--ctx-size','6144','--parallel','1','--jinja',
        '--no-webui','--cache-ram','256','--chat-template-kwargs','{"enable_thinking":false}',
        '--alias','oracle-isolated-'+meta['output_sha256'][:12]]
    with (work/'server.log').open('w') as log:
        process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (work/'launch.json').write_text(json.dumps({'pid':process.pid,'command':cmd,'candidate_sha256':sha_file(candidate)},indent=2)+'\n')
    endpoint=f'http://127.0.0.1:{port}'
    try:
        for _ in range(90):
            if process.poll() is not None:raise RuntimeError('Isolated server exited')
            try:
                with urllib.request.urlopen(endpoint+'/v1/models',timeout=2) as f:ready=json.load(f)
                if ready['data'][0]['id']==cmd[-1]:break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('Isolated server startup timed out')
        yield endpoint,meta
    finally:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait()


def run(cases,system,candidates,binary,output,port=4210,workers=2):
    output.mkdir(parents=True,exist_ok=True)
    def arm(index,name,candidate):
        target=output/(name+'.jsonl');receipt=target.with_suffix('.manifest.json')
        metadata=json.loads(candidate.read_text())
        if receipt.exists() and json.loads(receipt.read_text()).get('complete'):
            generate(cases,system,metadata['output_sha256'],'unused',target)
        else:
            with server(binary,candidate,port+index,output/(name+'-server')) as (endpoint,meta):
                generate(cases,system,meta['output_sha256'],endpoint,target)
        return target
    with ThreadPoolExecutor(max_workers=min(2,len(candidates))) as pool:
        pending={name:pool.submit(arm,i,name,candidate) for i,(name,candidate) in enumerate(candidates.items())}
        predictions={name:future.result() for name,future in pending.items()}
    return evaluate(cases,predictions,output/'comparison',workers)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('cases','system','binary','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--candidate',action='append',required=True,help='arm=/path/to/candidate.json')
    p.add_argument('--port',type=int,default=4210);p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    print(json.dumps(run(a.cases,a.system.read_text(),{k:Path(v) for k,v in (x.split('=',1) for x in a.candidate)},a.binary,a.output,a.port,a.workers),indent=2))
