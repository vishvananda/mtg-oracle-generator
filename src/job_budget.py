"""Conservative experiment ledger: every submitted job reserves its full timeout cost."""
import fcntl
import argparse
import json
import math
from pathlib import Path
import time


def amend_limit(path,expected_limit,new_limit,reason):
    """Explicit operator action; retain every earlier job reservation."""
    if not math.isfinite(new_limit) or new_limit<=0 or not reason.strip():
        raise ValueError('Positive finite limit and authorization reason required')
    path=Path(path)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        ledger=json.loads(path.read_text())
        if ledger['limit_usd']!=expected_limit: raise ValueError('Budget changed since review')
        if sum(r['maximum_usd'] for r in ledger['reservations'])>new_limit:
            raise ValueError('New limit is below existing reservations')
        ledger.setdefault('amendments',[]).append({'previous_limit_usd':expected_limit,'new_limit_usd':new_limit,
                                                   'reason':reason,'amended_at':time.time()})
        ledger['limit_usd']=new_limit
        pending=path.with_suffix('.pending.json');pending.write_text(json.dumps(ledger,indent=2)+'\n');pending.replace(path)
        return ledger


def reserve(path,limit,record,amount):
    if not math.isfinite(limit) or limit<=0 or not math.isfinite(amount) or amount<=0:
        raise ValueError('Finite positive budget and reservation required')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        ledger=json.loads(path.read_text()) if path.exists() else {'limit_usd':limit,'reservations':[]}
        if ledger['limit_usd']!=limit: raise ValueError('Experiment budget changed')
        key=str(record.resolve())
        if any(r['record']==key for r in ledger['reservations']): raise ValueError('Job already has a budget reservation')
        if sum(r['maximum_usd'] for r in ledger['reservations'])+amount>limit:
            raise ValueError('Experiment budget exhausted; no job submitted')
        ledger['reservations'].append({'record':key,'maximum_usd':amount,'reserved_at':time.time()})
        pending=path.with_suffix('.pending.json');pending.write_text(json.dumps(ledger,indent=2)+'\n');pending.replace(path)
        return ledger


if __name__=='__main__':
    p=argparse.ArgumentParser(description='Record an explicitly authorized experiment budget change; never submits jobs.')
    p.add_argument('--ledger',type=Path,required=True);p.add_argument('--expected-limit',type=float,required=True)
    p.add_argument('--new-limit',type=float,required=True);p.add_argument('--reason',required=True)
    a=p.parse_args();print(json.dumps(amend_limit(a.ledger,a.expected_limit,a.new_limit,a.reason),indent=2))
