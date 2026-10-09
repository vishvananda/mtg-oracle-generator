#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""Bounded preference pilot: GPU sampling or DPO, with no judge calls on GPU."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def verify_inputs(package,adapter):
    manifest=json.loads((package/'manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(package/name)!=pin:
            raise ValueError('Package file changed: '+name)
    summary=json.loads((adapter/'run-summary.json').read_text())
    if summary['stopped_for_training_budget'] or summary['metrics']['epoch']<1 or summary['max_steps']!=-1:
        raise ValueError('Preference pilot must start from the completed SFT epoch')
    if summary['training_identity']['dataset_manifest_sha256']!=manifest['dataset_manifest_sha256']:
        raise ValueError('SFT and preference dataset differ')
    for name,pin in manifest['adapter_files'].items():
        if sha(adapter/name)!=pin:raise ValueError('Frozen SFT adapter changed')
    return manifest,summary


def read_prompts(path):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate prompt IDs')
    for row in rows:
        if set(row)!= {'id','prompt'} or [m['role'] for m in row['prompt']]!=['system','user']:
            raise ValueError('Generation inputs must contain only system/user prompts')
    return rows


def render_preferences(rows,tokenizer,max_length):
    rendered=[]
    for row in rows:
        if set(row)!={'prompt','chosen','rejected'} or row['chosen']==row['rejected']:
            raise ValueError('Invalid preference row')
        if [m['role'] for m in row['prompt']]!=['system','user']:
            raise ValueError('Unexpected preference prompt')
        value={'prompt':tokenizer.apply_chat_template(row['prompt'],tokenize=False,
                   add_generation_prompt=True,enable_thinking=False)}
        for key in ('chosen','rejected'):
            if len(row[key])!=1 or row[key][0]['role']!='assistant':raise ValueError('Unexpected completion')
            raw=row[key][0]['content'];json.loads(raw)
            full=tokenizer.apply_chat_template(row['prompt']+row[key],tokenize=False,
                       add_generation_prompt=False,enable_thinking=False)
            if not full.startswith(value['prompt']+raw):raise ValueError('Chat completion prefix differs')
            value[key]=raw
            length=sum(len(tokenizer(s,add_special_tokens=False)['input_ids']) for s in (value['prompt'],raw))+1
            if length>max_length:raise ValueError('Preference would be truncated')
        rendered.append(value)
    return rendered


def generate(model,tokenizer,rows,output,copies,batch_size,max_tokens,seed,sample):
    import torch
    from transformers import set_seed
    if output.exists():raise ValueError('Generation output already exists')
    scheduled=[(r,i) for r in rows for i in range(copies)]
    scheduled.sort(key=lambda pair:(len(pair[0]['prompt'][1]['content']),pair[0]['id'],pair[1]))
    model.eval();model.config.use_cache=True;tokenizer.padding_side='left'
    set_seed(seed);started=time.monotonic();tokens_total=0;done=0
    with output.open('x') as stream,torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
        for start in range(0,len(scheduled),batch_size):
            group=scheduled[start:start+batch_size]
            prompts=[tokenizer.apply_chat_template(r['prompt'],tokenize=False,add_generation_prompt=True,
                        enable_thinking=False) for r,_ in group]
            batch=tokenizer(prompts,return_tensors='pt',padding=True,add_special_tokens=False).to(model.device)
            width=batch['input_ids'].shape[1]
            if width+max_tokens>model.config.max_position_embeddings:raise ValueError('Context overflow')
            before=time.monotonic()
            options={'temperature':.8,'top_p':.95,'top_k':0} if sample else {}
            result=model.generate(**batch,do_sample=sample,max_new_tokens=max_tokens,
                       pad_token_id=tokenizer.eos_token_id,use_cache=True,**options)
            elapsed=time.monotonic()-before
            for (row,i),sequence in zip(group,result):
                tokens=sequence[width:].tolist()
                if tokenizer.eos_token_id in tokens:tokens=tokens[:tokens.index(tokenizer.eos_token_id)+1]
                value={'case_id':row['id'],'candidate_id':row['id']+f'-{i:02d}',
                    'raw':tokenizer.decode(tokens,skip_special_tokens=True),'output_tokens':len(tokens),
                    'hit_token_limit':len(tokens)>=max_tokens and tokens[-1]!=tokenizer.eos_token_id,
                    'batch_seconds':elapsed}
                stream.write(json.dumps(value,ensure_ascii=False)+'\n');tokens_total+=len(tokens);done+=1
            stream.flush();print(json.dumps({'generation':output.name,'done':done,'scheduled':len(scheduled),
                'tokens':tokens_total,'seconds':time.monotonic()-started}),flush=True)
    receipt={'complete':True,'cases':len(rows),'completions':done,'sha256':sha(output),'output_tokens':tokens_total,
             'seconds':time.monotonic()-started,'decoding':{'do_sample':sample,'temperature':.8 if sample else None,
             'top_p':.95 if sample else None,'top_k':0 if sample else None,'seed':seed,'batch_size':batch_size,
             'max_new_tokens':max_tokens,'enable_thinking':False},'backend':'transformers NF4 + SFT/DPO adapter'}
    save(output.with_suffix('.manifest.json'),receipt);return receipt


def adapter_hash(model,name):
    from peft import get_peft_model_state_dict
    h=hashlib.sha256()
    for key,tensor in sorted(get_peft_model_state_dict(model,adapter_name=name).items()):
        h.update(key.encode());h.update(tensor.detach().float().cpu().numpy().tobytes())
    return h.hexdigest()


def run(package,adapter,output,stage):
    manifest,summary=verify_inputs(package,adapter)
    if output.exists() and any(output.iterdir()):raise ValueError('Use empty output directory')
    output.mkdir(parents=True,exist_ok=True)
    config=json.loads((package/'config.json').read_text());base=summary['config']
    save(output/'identity.json',{'package_manifest_sha256':sha(package/'manifest.json'),'adapter_files':manifest['adapter_files'],
        'stage':stage,'base_model':base['model'],'base_revision':base['model_revision'],'config':config})
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig,set_seed
    from peft import PeftModel,prepare_model_for_kbit_training
    if not torch.cuda.is_available():raise ValueError('CUDA required')
    set_seed(config['seed']);torch.set_num_threads(4)
    tokenizer=AutoTokenizer.from_pretrained(base['model'],revision=base['model_revision'])
    tokenizer.pad_token=tokenizer.eos_token
    model=AutoModelForCausalLM.from_pretrained(base['model'],revision=base['model_revision'],
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16),
        torch_dtype=torch.bfloat16,device_map={'':torch.cuda.current_device()},attn_implementation='sdpa')
    # Keep frozen base precision identical for sampling and post-DPO generation.
    model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=stage=='train')
    model=PeftModel.from_pretrained(model,adapter,is_trainable=stage=='train',adapter_name='default')
    dev=read_prompts(package/'development.jsonl')
    if stage=='sample':
        generated=generate(model,tokenizer,read_prompts(package/'prompts.jsonl'),output/'candidates.jsonl',
             config['candidates_per_prompt'],config['generation_batch_size'],config['max_new_tokens'],config['seed'],True)
        development=generate(model,tokenizer,dev,output/'sft-development.jsonl',1,
             config['generation_batch_size'],config['max_new_tokens'],config['seed'],False)
        result={'stage':stage,'candidates':generated,'development':development}
    elif stage=='train':
        from datasets import Dataset
        from trl import DPOConfig,DPOTrainer
        from transformers import TrainerCallback
        review=json.loads((package/'review.json').read_text())
        if review['approved_pairs_sha256']!=sha(package/'preferences.jsonl') or not review['independent_review']:
            raise ValueError('Preference review not bound to training file')
        rows=[json.loads(l) for l in (package/'preferences.jsonl').read_text().splitlines()]
        if len(rows)<config['minimum_pairs']:raise ValueError('Too few independently reviewed preferences')
        data=render_preferences(rows,tokenizer,config['max_length'])
        model.load_adapter(adapter,adapter_name='reference',is_trainable=False)
        model.set_adapter('default');model.eval()
        initial=adapter_hash(model,'default');reference=adapter_hash(model,'reference')
        if initial!=reference:raise ValueError('Initial DPO and SFT reference weights differ')
        probe=tokenizer(data[0]['prompt'],return_tensors='pt',add_special_tokens=False).to(model.device)
        with torch.inference_mode():
            policy_logits=model(**probe).logits[:,-1].float()
            model.set_adapter('reference');reference_logits=model(**probe).logits[:,-1].float()
        torch.testing.assert_close(policy_logits,reference_logits,rtol=0,atol=0)
        model.set_adapter('default');model.config.use_cache=False
        steps=min(config['max_steps'],math.ceil(len(rows)/config['effective_batch_size']))
        args=DPOConfig(output_dir=str(output/'checkpoints'),model_adapter_name='default',ref_adapter_name='reference',
            learning_rate=config['learning_rate'],beta=config['beta'],loss_type='sigmoid',max_steps=steps,
            per_device_train_batch_size=2,gradient_accumulation_steps=config['effective_batch_size']//2,
            per_device_eval_batch_size=2,gradient_checkpointing=True,bf16=True,max_length=config['max_length'],
            max_prompt_length=None,max_completion_length=None,precompute_ref_log_probs=True,
            precompute_ref_batch_size=4,disable_dropout=True,logging_steps=1,save_strategy='steps',save_steps=25,
            save_total_limit=2,report_to='none',seed=config['seed'],data_seed=config['seed'],warmup_ratio=.1,
            optim='adamw_torch',remove_unused_columns=False)
        class Budget(TrainerCallback):
            stopped=False
            def on_train_begin(self,args,state,control,**kw):self.started=time.monotonic()
            def on_step_end(self,args,state,control,**kw):
                if state.global_step<state.max_steps and time.monotonic()-self.started>config['training_seconds']:
                    self.stopped=True;control.should_training_stop=True;control.should_save=True
                return control
        budget=Budget()
        trainer=DPOTrainer(model=model,args=args,processing_class=tokenizer,train_dataset=Dataset.from_list(data),callbacks=[budget])
        # This invokes TRL's own reference context, not the adapter-disabled base.
        batch=next(iter(trainer.get_train_dataloader()))
        if 'ref_chosen_logps' not in batch or 'ref_rejected_logps' not in batch:
            raise ValueError('Frozen SFT reference probabilities were not cached')
        result_train=trainer.train()
        if adapter_hash(model,'reference')!=reference:raise ValueError('Frozen SFT reference was modified')
        if adapter_hash(model,'default')==initial:raise ValueError('DPO did not change policy weights')
        model.set_adapter('default');model.save_pretrained(output/'adapter',selected_adapters=['default'])
        tokenizer.save_pretrained(output/'adapter');trainer.save_state()
        result={'stage':stage,'pairs':len(rows),'steps':trainer.state.global_step,'planned_steps':steps,
            'metrics':result_train.metrics,'reference_unchanged':True,'initial_reference_logits_equal':True,
            'stopped_for_budget':budget.stopped,'reference_sha256':reference,'initial_policy_sha256':initial,
            'final_policy_sha256':adapter_hash(model,'default'),'train_seconds':time.monotonic()-budget.started,
            'peak_gpu_gib':torch.cuda.max_memory_allocated()/1024**3}
        save(output/'training-summary.json',result)
        result['development']=generate(model,tokenizer,dev,output/'dpo-development.jsonl',1,
            config['generation_batch_size'],config['max_new_tokens'],config['seed'],False)
    else:raise ValueError('Unknown stage')
    save(output/'phase-complete.json',result)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('package','adapter','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',choices=['sample','train'],required=True)
    a=p.parse_args();run(a.package,a.adapter,a.output,a.stage)
