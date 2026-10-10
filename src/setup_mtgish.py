#!/usr/bin/env python3
"""Extract mtgish's standalone Go parser into an isolated adapter workspace."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from paths import ARTIFACTS, PROJECT

REVISION='153451e44d88baabfeb9902092032c328f4d5416'


def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    source=ARTIFACTS/'mtgish-source'
    if not source.exists():
        subprocess.run(['git','clone','https://github.com/i5jb/mtgish.git',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'checkout','--detach',REVISION],check=True)
    head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if head!=REVISION: raise ValueError('Unexpected mtgish source revision')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip():
        raise ValueError('mtgish checkout must be clean')
    standard=ARTIFACTS/'mtgish-standard-workflow'
    standard.mkdir(exist_ok=True)
    for directory in ('grammars','data','go_mtg_parser'):
        shutil.copytree(source/directory,standard/directory,dirs_exist_ok=True)
    for name in ('preprocess_scryfall','merge_arena_changes','README.md','justfile'):
        shutil.copyfile(source/name,standard/name)
    for kind in ('english','mtgish'):
        generated=subprocess.check_output([sys.executable,str(standard/'grammars/generate_plurals'),f'--{kind}-grammar'])
        (standard/f'grammars/{kind}_grammar-type_line.json5').write_text(json.dumps(json.loads(generated),sort_keys=True,indent=2)+'\n')
    root=ARTIFACTS/'mtgish-adapter'
    root.mkdir(exist_ok=True)
    files={}
    for path in (source/'go_mtg_parser').iterdir():
        if path.suffix not in ('.go','.mod'): continue
        data=path.read_bytes()
        files[path.name]=hashlib.sha256(data).hexdigest()
        if path.name=='main.go':
            text=data.decode()
            if text.count('func main() {')!=1: raise ValueError('Upstream entrypoint changed')
            data=text.replace('func main() {','func upstreamMain() {',1).encode()
        (root/path.name).write_bytes(data)
    shutil.copyfile(source/'LICENSE',root/'LICENSE')
    shutil.copyfile(PROJECT/'src/mtgish-adapter.go',root/'adapter.go')
    go=shutil.which('go')
    if not go: raise RuntimeError('Install Go 1.18 or newer before building mtgish')
    env={**os.environ,'GOCACHE':str(ARTIFACTS/'go-cache'),'GOMAXPROCS':'2'}
    start=time.monotonic()
    subprocess.run([go,'build','-p','2','-o',str(standard/'go_mtg_parser/check'),'.'],cwd=standard/'go_mtg_parser',env=env,check=True)
    subprocess.run([go,'build','-p','2','-o',str(root/'mtgish-check.pending'),'.'],cwd=root,env=env,check=True)
    if (root/'mtgish-check').exists() and not (root/'mtgish-check-v1').exists(): shutil.copyfile(root/'mtgish-check',root/'mtgish-check-v1')
    (root/'mtgish-check.pending').replace(root/'mtgish-check')
    manifest={'repository':'https://github.com/i5jb/mtgish','revision':head,'source_files':files,
              'grammar_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((standard/'grammars').glob('*.json5'))},
              'adapter_sha256':hashlib.sha256((root/'adapter.go').read_bytes()).hexdigest(),
              'elapsed_seconds':round(time.monotonic()-start,2),
              'patch':'Rename upstream main to upstreamMain in the copy; parser and grammar unchanged.',
              'card_specific_rewrites':True, 'workflow':'upstream-standard-v2', 'workflow_root':str(standard),
              'source_status':subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True)}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
