"""Conservative experiment ledger: every submitted job reserves its full timeout cost."""
import fcntl
import json
import math
from pathlib import Path
import time


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
