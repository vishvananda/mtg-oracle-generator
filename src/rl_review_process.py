"""Track local review workers so an orchestrator restart preserves active work."""
import json,subprocess,time
from pathlib import Path

from rl_gpu import save


def process_identity(pid):
    root=Path('/proc')/str(pid)
    try:
        fields=(root/'stat').read_text().rpartition(') ')[2].split()
        if fields[0]=='Z':return None
        return {'pid':pid,'start_ticks':fields[19],
                'command':(root/'cmdline').read_bytes().decode().rstrip('\0').split('\0')}
    except (FileNotFoundError,ProcessLookupError):return None


def same_worker(receipt):
    live=process_identity(receipt['pid'])
    return live is not None and all(live[k]==receipt[k] for k in ('pid','start_ticks','command'))


def review_process(command,record,result,cwd):
    if result.exists():return
    child=None
    if record.exists():
        receipt=json.loads(record.read_text())
        if receipt['command']!=command:raise ValueError('Existing review worker command differs')
    else:
        with record.with_suffix('.log').open('a') as log:
            child=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        receipt=process_identity(child.pid)
        if receipt is None:raise RuntimeError('Review worker exited before its launch was recorded')
        # exec may briefly show the parent's command; use the explicit argv.
        receipt['command']=command
        save(record,receipt)
    while same_worker(receipt):time.sleep(5)
    if child is not None:child.wait()
    if not result.exists():raise RuntimeError('Review worker exited without final results; preserve partial batches for inspection')
    json.loads(result.read_text())


def should_look_ahead(index,approved,previous_yield,target=1000,maximum_batches=3):
    # Require a completed broad batch. The initial audit is error-enriched.
    # A 50% yield margin limits speculative work near the collection target.
    return (index<maximum_batches and previous_yield is not None
            and approved+previous_yield*1.5<target)


def lookahead_minutes(ledger,limit,price_per_minute,maximum=180,minimum=150):
    used=sum(row['maximum_usd'] for row in ledger.get('reservations',[]))
    # Keep a full 60-minute training reservation available even if sampling
    # consumes its entire timeout. Settled reservations include failed jobs.
    available=int((limit-used)/price_per_minute)-60
    return min(maximum,available) if available>=minimum else None
