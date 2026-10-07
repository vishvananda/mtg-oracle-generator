"""Persistent JSONL client for the unchanged mtgish whole-card workflow."""
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile

from data_utils import digest
from paths import ARTIFACTS
from setup_mtgish import REVISION
from mtgish_workflow import input_for, ROOT


def bind_names(draft):
    card=copy.deepcopy(draft)
    card.setdefault('name','Draft Card')
    faces=card.get('card_faces') or [card]
    aliases={}
    for i,face in enumerate(faces):
        face.setdefault('name',card['name'] if len(faces)==1 else f'Draft Face {i+1}')
        aliases[f'Draft Face {i+1}']=face['name']
    for face in faces:
        text=face.get('oracle_text','').replace('CARDNAME',face['name'])
        for old,new in aliases.items(): text=text.replace(old,new)
        face['oracle_text']=text
    return card


class MtgishValidator:
    def __init__(self,timeout=30):
        self.timeout=timeout
        self.process=None
        self.stderr=None
        root=ARTIFACTS/'mtgish-adapter'
        self.binary=root/'mtgish-check'
        manifest=json.loads((root/'manifest.json').read_text())
        if manifest['revision']!=REVISION or manifest['workflow']!='upstream-standard-v2':
            raise ValueError('Unexpected mtgish revision/workflow')
        for name,pin in manifest['grammar_files'].items():
            if digest((ROOT/'grammars'/name).read_bytes())!=pin:
                raise ValueError('mtgish grammar changed: '+name)
        self.provenance={'revision':REVISION,'workflow':manifest['workflow'],
                         'manifest_sha256':digest((root/'manifest.json').read_bytes()),
                         'binary_sha256':digest(self.binary.read_bytes()),
                         'grammar_files':manifest['grammar_files']}

    def close(self):
        if self.process is not None:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
            self.process.stdin.close(); self.process.stdout.close()
            self.process=None
        if self.stderr is not None:
            self.stderr.close(); self.stderr=None

    def check(self,draft):
        try:
            payload=input_for(bind_names(draft))
            if payload.get('preprocessing_status')=='out_of_scope':
                return {'status':'out_of_scope','parse_complete':False,'scope':'whole_card'}
            if self.process is None:
                self.stderr=tempfile.TemporaryFile(mode='w+')
                self.process=subprocess.Popen([str(self.binary),str(ROOT/'grammars')],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True,bufsize=1,
                    env={**os.environ,'GOMAXPROCS':'2'})
            self.process.stdin.write(json.dumps(payload)+'\n'); self.process.stdin.flush()
            if not select.select([self.process.stdout],[],[],self.timeout)[0]:
                raise TimeoutError('mtgish reply timed out')
            result=json.loads(self.process.stdout.readline())
            if not isinstance(result.get('parse_complete'),bool): raise ValueError('Malformed parser reply')
            return result
        except (OSError,ValueError,TimeoutError,subprocess.SubprocessError) as error:
            self.close()
            return {'status':'parser_error','parse_complete':False,'error':str(error),'scope':'whole_card'}

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
