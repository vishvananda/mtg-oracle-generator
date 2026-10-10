"""Drain and restart an existing corpus coordinator with a new worker count.

Run on the worker host with write access to the queue. Defaults to a read-only
plan; --apply preserves completed work and never starts a second coordinator.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def read_json(path):
    return json.loads(path.read_text())


def queue_states(root):
    return dict(Counter(read_json(p)['state'] for p in (root/'queue').glob('*.json')))


def replace_argument(command, flag, value):
    if command.count(flag) != 1:
        raise ValueError(f'Expected exactly one {flag} in recorded launch command')
    command[command.index(flag)+1] = str(value)


def plan(root, workers):
    root = root.resolve()
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be positive')
    active = read_json(root/'active-launch.json')
    if Path(active['record']).name != active['record']:
        raise ValueError('Launch record must be inside the queue directory')
    previous = read_json(root/active['record'])
    command = list(previous['command'])
    if 'run' not in command or not any(Path(arg).name == 'corpus_workers.py' for arg in command):
        raise ValueError('Active process is not a corpus coordinator')
    if Path(command[command.index('--root')+1]).resolve() != root:
        raise ValueError('Recorded coordinator belongs to another queue')
    old_config = Path(command[command.index('--config')+1])
    if not old_config.is_absolute():
        raise ValueError('Expected an absolute configuration path')
    config = read_json(old_config)
    before = config.get('workers', 2)
    states = queue_states(root)
    if set(states)-{'completed','pending','running'}:
        raise ValueError('Queue has failed/review batches; inspect before restarting')
    if not states.get('pending') and not states.get('running'):
        raise ValueError('Queue is already complete')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    name = f'workers{workers}-{tag}'
    config.update(workers=workers,
                  parent_config_sha256=hashlib.sha256(old_config.read_bytes()).hexdigest())
    config_path = root/f'generation-config-{name}.json'
    replace_argument(command, '--config', config_path)
    if '--export-output' in command:
        replace_argument(command, '--export-output', root/'exports'/name)
    return {'root':str(root), 'workers_before':before, 'workers_after':workers,
            'previous_active':active, 'previous_command':previous['command'],
            'config':config, 'config_path':str(config_path), 'command':command,
            'launch_record':f'launch-{name}.json', 'log':str(root/f'{name}.log'),
            'states':states, 'automatic_retries':0, 'automatic_training':False}


def atomic_json(path, value):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def matching_process(pid, command):
    """Only wait on the recorded process; never signal a stale or unrelated PID."""
    path = Path(f'/proc/{pid}/cmdline')
    if not path.exists():
        return False
    actual = path.read_bytes().rstrip(b'\0').decode().split('\0')
    if actual == ['']:
        return False  # Exited process awaiting collection.
    if actual != command:
        raise RuntimeError('Recorded PID now belongs to a different command')
    return True


def apply(root, workers, timeout=2000):
    root = root.resolve()
    # Serializes this utility. The existing coordinator retains its own lock.
    with (root/'worker-resize.lock').open('a') as control:
        fcntl.flock(control, fcntl.LOCK_EX | fcntl.LOCK_NB)
        change = plan(root, workers)
        if change['workers_before'] == workers:
            raise ValueError('Active launch already requests this worker count')
        drain = root/'drain.request.json'
        if drain.exists():
            raise ValueError('Another drain request exists; leaving it untouched')
        # Detect a stale PID before changing queue control files.
        pid = change['previous_active']['pid']
        matching_process(pid, change['previous_command'])
        nonce = uuid.uuid4().hex
        with drain.open('x') as stream:
            json.dump({'reason':'resize_workers','workers':workers,'request_id':nonce},stream)
        held = False
        try:
            deadline = time.monotonic()+timeout
            # Wait for both the batch pool and its optional final export to exit.
            with (root/'coordinator.lock').open('a') as coordinator:
                last_notice = 0
                while True:
                    if not held:
                        try:
                            fcntl.flock(coordinator, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            held = True
                        except BlockingIOError:
                            pass
                    old_alive = matching_process(pid, change['previous_command'])
                    if held and not old_alive:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Coordinator/export did not drain within the timeout; no replacement started')
                    if time.monotonic()-last_notice >= 15:
                        print(json.dumps({'stage':'draining','states':queue_states(root)}),flush=True)
                        last_notice = time.monotonic()
                    time.sleep(1)
                states = queue_states(root)
                if set(states)-{'completed','pending'}:
                    raise RuntimeError('Unfinished or failed batches remain; no replacement started')
                if not states.get('pending'):
                    return {'stage':'already_completed','states':states,'replacement_started':False}
                if read_json(root/'active-launch.json') != change['previous_active']:
                    raise RuntimeError('Active launch changed while draining')
                if read_json(drain).get('request_id') != nonce:
                    raise RuntimeError('Drain request changed while waiting')
                config_path = Path(change['config_path'])
                with config_path.open('x') as stream:
                    json.dump(change['config'],stream,indent=2);stream.write('\n')
                drain.unlink()
            # The child takes coordinator.lock itself. If an unrelated launcher
            # races us, one exits rather than processing the same queue twice.
            script = next(Path(arg) for arg in change['command'] if Path(arg).name=='corpus_workers.py')
            started = time.time()
            with Path(change['log']).open('x') as log:
                process = subprocess.Popen(change['command'],stdin=subprocess.DEVNULL,
                    stdout=log,stderr=log,cwd=script.parent.parent,start_new_session=True)
            record = {'pid':process.pid,'command':change['command'],'started_at':started,
                      'workers':workers,'config':change['config_path'],
                      'teacher':change['config']['teacher'],'reviewer':change['config']['reviewer'],
                      'reasoning_effort':change['config']['reasoning_effort'],
                      'state_at_resume':states,'previous_pid':pid,'automatic_retries':0,
                      'automatic_training':False,'public_uploads':False,'log':change['log']}
            atomic_json(root/change['launch_record'],record)
            atomic_json(root/'active-launch.json',{'record':change['launch_record'],'pid':process.pid})
            for _ in range(30):
                if process.poll() is not None:
                    raise RuntimeError('Replacement exited during startup; inspect '+change['log'])
                try:
                    progress = read_json(root/'progress.json')
                except FileNotFoundError:
                    progress = {}
                if progress.get('workers')==workers and progress.get('updated_at',0)>=started:
                    return {'stage':'running','pid':process.pid,'workers':workers,
                            'states':progress['states'],'log':change['log']}
                time.sleep(1)
            raise RuntimeError('Replacement launched but startup is unconfirmed; inspect '+change['log'])
        finally:
            # Remove only our request, never another operator's drain flag.
            if drain.exists() and read_json(drain).get('request_id')==nonce:
                drain.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--workers',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--timeout',type=float,default=2000)
    args = parser.parse_args()
    if args.timeout<=0: parser.error('--timeout must be positive')
    if args.apply:
        result = apply(args.root,args.workers,args.timeout)
    else:
        result = plan(args.root,args.workers)
        result.pop('config')
        result['stage']='planned_only'
    print(json.dumps(result,indent=2))
