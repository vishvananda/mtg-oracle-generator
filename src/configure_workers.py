"""Freeze the compact, cached teacher/reviewer configuration before a run."""
import argparse
import json
from corpus_protocol import upgrade_config

if __name__ == '__main__':
    from pathlib import Path
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--workers',type=int,default=3)
    p.add_argument('--model',default='gpt-6.1-sol')
    p.add_argument('--effort',choices=['low','medium','high','xhigh','max','ultra'],default='medium')
    p.add_argument('--transport',choices=['cached','direct'],default='cached')
    a=p.parse_args()
    if a.workers<1: p.error('--workers must be positive')
    if a.transport=='direct' and a.effort!='medium':
        p.error('The direct transport uses medium effort; select cached to configure effort')
    path=a.root/'generation-config-public.json'
    if path.exists(): p.error('Config is immutable; choose a new corpus root to change it')
    config=json.loads((a.root/'generation-config.json').read_text())
    if a.transport=='cached': config=upgrade_config(config)
    config.update(teacher=a.model,reviewer=a.model,workers=a.workers,reasoning_effort=a.effort)
    path.write_text(json.dumps(config,indent=2)+'\n')
    print(path)
