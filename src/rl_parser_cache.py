"""Exact-card cache and per-thread parser instances, pinned to upstream artifacts."""
import json,os,tempfile,threading
from pathlib import Path

from data_utils import digest
from rl_data import sha_file
from validate_mtgish import MtgishValidator


class ParserCache:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.local=threading.local();self.instances=[];self.lock=threading.Lock()
        parser=MtgishValidator();self.provenance=parser.provenance;parser.close()
        self.pin=digest(self.provenance)

    def key(self,card):return digest([self.pin,card])

    def store(self,card,result):
        if result.get('status')=='parser_error':return
        key=self.key(card)
        with tempfile.NamedTemporaryFile(mode='w',dir=self.root,delete=False) as stream:
            json.dump({'key':key,'result':result},stream);name=stream.name
        os.replace(name,self.root/(key+'.json'))

    def seed(self,scored):
        manifest=json.loads((scored/'manifest.json').read_text());path=scored/'scored.jsonl'
        if manifest['parser']!=self.provenance or manifest['file']['sha256']!=sha_file(path):
            raise ValueError('Prior parser output is not from these exact artifacts')
        count=0
        for row in map(json.loads,path.read_text().splitlines()):
            a=row['assessment']
            if a['raw_sha256']!=digest(row['raw']):raise ValueError('Prior assessment/raw mismatch')
            if 'mtgish' in a:
                self.store(json.loads(row['raw']),a['mtgish']);count+=1
        return count

    def check(self,card):
        key=self.key(card);path=self.root/(key+'.json')
        if path.exists():
            saved=json.loads(path.read_text())
            if saved['key']!=key:raise ValueError('Parser cache identity mismatch')
            return saved['result']
        if not hasattr(self.local,'parser'):
            self.local.parser=MtgishValidator()
            with self.lock:self.instances.append(self.local.parser)
        result=self.local.parser.check(card);self.store(card,result);return result

    def __enter__(self):return self
    def __exit__(self,*args):
        for parser in self.instances:parser.close()
