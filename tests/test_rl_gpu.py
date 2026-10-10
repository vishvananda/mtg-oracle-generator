import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_gpu import verify_inputs,read_prompts,render_preferences,sha,save,completed_candidates,adaptive_generate


class Tokenizer:
    def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False,enable_thinking=False):
        text=''.join('<'+m['role']+'>'+m['content'] for m in messages)
        return text+'<assistant>' if add_generation_prompt else text+'<eos>'
    def __call__(self,text,add_special_tokens=False):return {'input_ids':list(text.encode())}


class PreferenceContracts(unittest.TestCase):
    def test_resumption_preserves_invalid_outputs_and_rejects_bad_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'partial.jsonl';prompts=[{'id':'a'}]
            row={'case_id':'a','candidate_id':'a-00','raw':'not valid JSON',
                 'output_tokens':3,'hit_token_limit':False}
            path.write_text(json.dumps(row)+'\n')
            self.assertEqual(list(completed_candidates(path,prompts,4).values()),[row])
            path.write_text((json.dumps(row)+'\n')*2)
            with self.assertRaisesRegex(ValueError,'duplicate'):completed_candidates(path,prompts,4)
            row['candidate_id']='b-00';path.write_text(json.dumps(row)+'\n')
            with self.assertRaisesRegex(ValueError,'Unknown'):completed_candidates(path,prompts,4)

    def test_allocation_retry_halves_batch_without_dropping_or_duplicating_candidates(self):
        class OOM(Exception):pass
        attempted=[];reductions=[];cleaned=[]
        def attempt(group):
            attempted.append(group)
            if len(group)>2:raise OOM('simulated GPU allocation failure')
            return [i*10 for i in group]
        got=list(adaptive_generate(list(range(7)),6,attempt,OOM,lambda:cleaned.append(True),
                                   lambda old,new:reductions.append((old,new))))
        self.assertEqual([i for group,_ in got for i in group],list(range(7)))
        self.assertEqual([i for _,values in got for i in values],[i*10 for i in range(7)])
        self.assertEqual(reductions,[(6,3),(3,1)])
        self.assertEqual(len(cleaned),2)
        def broken(group):raise ValueError('unrelated bug')
        with self.assertRaisesRegex(ValueError,'unrelated'):list(adaptive_generate([1,2],2,broken,OOM,lambda:None,lambda *_:None))
        def impossible(group):raise OOM('single example cannot fit')
        with self.assertRaisesRegex(OOM,'single example'):list(adaptive_generate([1],1,impossible,OOM,lambda:None,lambda *_:None))

    def test_generation_inputs_reject_assistant_targets_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'prompts.jsonl'
            row={'id':'a','prompt':[{'role':'system','content':'draft'},{'role':'user','content':'giant'}]}
            path.write_text(json.dumps(row)+'\n');self.assertEqual(len(read_prompts(path)),1)
            path.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
            with self.assertRaisesRegex(ValueError,'Duplicate'):read_prompts(path)
            row['prompt'].append({'role':'assistant','content':'answer'})
            path.write_text(json.dumps(row)+'\n')
            with self.assertRaisesRegex(ValueError,'system/user'):read_prompts(path)

    def test_truncation_is_rejected_and_prompt_is_not_in_completion(self):
        row={'prompt':[{'role':'system','content':'draft'},{'role':'user','content':'giant'}],
            'chosen':[{'role':'assistant','content':'{"power":"3"}'}],
            'rejected':[{'role':'assistant','content':'{"power":"1"}'}]}
        result=render_preferences([row],Tokenizer(),100)
        self.assertEqual(result[0]['chosen'],'{"power":"3"}')
        self.assertTrue(result[0]['prompt'].endswith('<assistant>'))
        with self.assertRaisesRegex(ValueError,'truncated'):render_preferences([row],Tokenizer(),10)
        bad=copy.deepcopy(row);bad['rejected']=bad['chosen']
        with self.assertRaisesRegex(ValueError,'Invalid preference'):render_preferences([bad],Tokenizer(),100)

    def test_starting_adapter_must_complete_epoch_and_match_exact_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';adapter=root/'adapter';package.mkdir();adapter.mkdir()
            (package/'config.json').write_text('{}')
            (adapter/'adapter_model.safetensors').write_bytes(b'frozen')
            summary={'stopped_for_training_budget':False,'metrics':{'epoch':1},'max_steps':-1,
                     'training_identity':{'dataset_manifest_sha256':'dataset'}}
            save(adapter/'run-summary.json',summary)
            save(package/'manifest.json',{'files':{'config.json':sha(package/'config.json')},
                'dataset_manifest_sha256':'dataset','adapter_files':{'adapter_model.safetensors':sha(adapter/'adapter_model.safetensors')}})
            verify_inputs(package,adapter)
            summary['stopped_for_training_budget']=True;save(adapter/'run-summary.json',summary)
            with self.assertRaisesRegex(ValueError,'completed SFT'):verify_inputs(package,adapter)
            summary['stopped_for_training_budget']=False;save(adapter/'run-summary.json',summary)
            (adapter/'adapter_model.safetensors').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Frozen SFT'):verify_inputs(package,adapter)


if __name__=='__main__':unittest.main()
