"""Recovery contracts; the CPU test exercises TRL's actual k-bit freeze path."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from custom_sft_gpu import activate_training_adapter, reuse_baselines
from rl_gpu import sha, save


class RecoveryTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('trl'), 'Pinned training environment required')
    def test_existing_adapter_trains_after_trl_kbit_preparation(self):
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import Qwen3Config, Qwen3ForCausalLM
        from trl.models.utils import prepare_peft_model
        torch.set_num_threads(2)
        base=Qwen3ForCausalLM(Qwen3Config(vocab_size=32,hidden_size=16,intermediate_size=32,
            num_hidden_layers=1,num_attention_heads=2,num_key_value_heads=1,head_dim=8))
        model=get_peft_model(base,LoraConfig(r=2,lora_alpha=4,target_modules=['q_proj','v_proj'],
                                            task_type='CAUSAL_LM'))
        model.add_adapter('dpo',LoraConfig(r=2,target_modules=['q_proj','v_proj']))
        with self.assertRaisesRegex(ValueError,'comparator'):
            activate_training_adapter(model)
        model.delete_adapter('dpo')
        # CPU weights, deliberately select the exact TRL QLoRA preparation path.
        # This checks trainability, not GPU quantization/numerical accuracy.
        base.is_loaded_in_4bit=True
        prepare_peft_model(model,None,SimpleNamespace(gradient_checkpointing=False,
                                                     gradient_checkpointing_kwargs=None,bf16=False))
        self.assertFalse(any(p.requires_grad for p in model.parameters()))
        names=activate_training_adapter(model)
        self.assertTrue(names)
        before={n:p.detach().clone() for n,p in model.named_parameters()}
        optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=.01)
        ids=torch.tensor([[1,2,3,4]])
        loss=model(input_ids=ids,labels=ids).loss
        self.assertTrue(torch.isfinite(loss))
        loss.backward();optimizer.step()
        changed={n for n,p in model.named_parameters() if not torch.equal(p,before[n])}
        self.assertTrue(changed)
        self.assertTrue(changed.issubset(set(names)))
        self.assertTrue(all(p.grad is None for n,p in model.named_parameters() if n not in names))

    def test_reuse_rejects_changed_prompts_or_partial_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';package.mkdir();out=root/'out';out.mkdir()
            for name in ('config.json','evaluation-prompts.jsonl','rl_gpu.py'):
                (package/name).write_text('unchanged')
            original={'adapters':{'sft':{},'dpo':{}},'dataset_manifest_sha256':'dataset',
                      'files':{n:sha(package/n) for n in ('config.json','evaluation-prompts.jsonl','rl_gpu.py')}}
            save(package/'baseline-package-manifest.json',original)
            identity={'package_sha256':sha(package/'baseline-package-manifest.json'),'adapters':original['adapters']}
            save(package/'baseline-identity.json',identity)
            manifest={**original,'baseline_reuse':identity}
            for arm in ('sft','dpo'):
                path=package/('baseline-'+arm+'.jsonl')
                path.write_text(json.dumps({'case_id':'a','candidate_id':'a-00','raw':'{}'})+'\n')
                save(path.with_suffix('.manifest.json'),{'complete':True,'cases':1,'completions':1,
                    'sha256':sha(path),'decoding':{'do_sample':False},'backend':'test'})
            self.assertEqual(set(reuse_baselines(package,out,manifest,[{'id':'a'}])),{'sft','dpo'})
            (package/'evaluation-prompts.jsonl').write_text('changed')
            with self.assertRaisesRegex(ValueError,'protocol changed'):
                reuse_baselines(package,out,manifest,[{'id':'a'}])
            (package/'evaluation-prompts.jsonl').write_text('unchanged')
            (package/'baseline-dpo.jsonl').write_text('')
            with self.assertRaisesRegex(ValueError,'changed baseline'):
                reuse_baselines(package,out,manifest,[{'id':'a'}])


if __name__=='__main__':unittest.main()
