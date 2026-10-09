"""Exercise the actual generation loop on CPU, including a simulated CUDA OOM."""
import contextlib,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_gpu import generate


class SamplingRecovery(unittest.TestCase):
    def test_completed_slots_survive_allocation_retry_and_receipt_is_complete(self):
        try:import torch
        except ImportError:self.skipTest('Run this test in the pinned training environment')
        class Batch(dict):
            def to(self,device):return self
        class Tokenizer:
            eos_token_id=9
            padding_side='right'
            def apply_chat_template(self,prompt,**kwargs):return 'prompt'
            def __call__(self,prompts,**kwargs):return Batch(input_ids=torch.ones((len(prompts),2),dtype=torch.long))
            def decode(self,tokens,**kwargs):return 'generated text'
        class Model:
            device='cpu'
            config=SimpleNamespace(max_position_embeddings=100,use_cache=False)
            def eval(self):pass
            def generate(self,input_ids,**kwargs):
                if len(input_ids)>2:raise torch.OutOfMemoryError('injected allocation failure')
                return torch.cat((input_ids,torch.tensor([[3,4,9]]*len(input_ids))),dim=1)
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
            root=Path(tmp);partial=root/'partial.jsonl'
            saved={'case_id':'a','candidate_id':'a-00','raw':'preserve this malformed result',
                   'output_tokens':4,'hit_token_limit':False,'batch_seconds':1.2}
            partial.write_text(json.dumps(saved)+'\n')
            for name in ('memory_allocated','memory_reserved','max_memory_allocated'):
                stack.enter_context(patch('torch.cuda.'+name,return_value=0))
            stack.enter_context(patch('torch.autocast',side_effect=lambda *a,**k:contextlib.nullcontext()))
            cases=[{'id':key,'prompt':[{'role':'system','content':'draft'},{'role':'user','content':'card'}]} for key in ('a','b','c','d')]
            receipt=generate(Model(),Tokenizer(),cases,root/'result.jsonl',2,4,8,17,True,partial)
            rows=list(map(json.loads,(root/'result.jsonl').read_text().splitlines()))
            self.assertEqual(rows[0],saved)
            self.assertEqual(len(rows),8)
            self.assertEqual(len({r['candidate_id'] for r in rows}),8)
            self.assertEqual(receipt['reused_completions'],1)
            self.assertEqual(receipt['oom_reductions'],[{'after_completions':1,'previous_batch_size':4,'next_batch_size':2}])
            self.assertTrue(receipt['complete'])


if __name__=='__main__':unittest.main()
