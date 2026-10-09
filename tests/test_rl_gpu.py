import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_gpu import verify_inputs,read_prompts,render_preferences,sha,save


class Tokenizer:
    def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False,enable_thinking=False):
        text=''.join('<'+m['role']+'>'+m['content'] for m in messages)
        return text+'<assistant>' if add_generation_prompt else text+'<eos>'
    def __call__(self,text,add_special_tokens=False):return {'input_ids':list(text.encode())}


class PreferenceContracts(unittest.TestCase):
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
