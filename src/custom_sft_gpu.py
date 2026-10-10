#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""One additional SFT pass from completed SFT, with fixed SFT/DPO comparisons.

This is a new optimizer/scheduler on new data, not a checkpoint resume. It never
trains the comparator adapter or evaluates held-out loss during training.
"""
import argparse
import json
import math
from pathlib import Path
import tempfile
import time

from rl_gpu import sha, save, generate, read_prompts, adapter_hash
from prepared_training import load_prepared


def verify(package, sft, dpo):
    manifest = json.loads((package/'package-manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(package/name)!=pin:
            raise ValueError('Package file mismatch: '+name)
    for key,directory in [('sft',sft),('dpo',dpo)]:
        for name,pin in manifest['adapters'][key].items():
            if sha(directory/name)!=pin:raise ValueError('Adapter identity mismatch: '+key+'/'+name)
    summary = json.loads((sft/'run-summary.json').read_text())
    if summary['metrics']['epoch']<1 or summary['stopped_for_training_budget']:
        raise ValueError('Starting SFT epoch is incomplete')
    config = json.loads((package/'config.json').read_text())
    if any(config[k]!=summary['config'][k] for k in ('model','model_revision')):
        raise ValueError('Continuation base model differs from the completed SFT')
    return manifest,summary


def run(package,sft,dpo,output):
    manifest,previous = verify(package,sft,dpo)
    if output.exists() and any(output.iterdir()):raise ValueError('Use an empty output directory')
    output.mkdir(parents=True,exist_ok=True)
    config = json.loads((package/'config.json').read_text())
    save(output/'identity.json', {'package_sha256':sha(package/'package-manifest.json'),
        'adapters':manifest['adapters'],'config':config,'starting_point':'completed SFT, not DPO',
        'fresh_optimizer':True,'automatic_promotion':False})
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig,TrainerCallback,set_seed
    from peft import PeftModel,prepare_model_for_kbit_training
    from trl import SFTConfig,SFTTrainer
    if not torch.cuda.is_available():raise ValueError('CUDA required')
    set_seed(config['seed']);torch.set_num_threads(8)
    started=time.monotonic()
    tokenizer=AutoTokenizer.from_pretrained(config['model'],revision=config['model_revision'])
    tokenizer.pad_token=tokenizer.eos_token
    with tempfile.TemporaryDirectory(prefix='oracle-custom-prepared-') as cache:
        data,metadata=load_prepared(package,package,config,Path(cache)/'data')
        base=AutoModelForCausalLM.from_pretrained(config['model'],revision=config['model_revision'],
            quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16,device_map={'':torch.cuda.current_device()},attn_implementation='sdpa')
        base=prepare_model_for_kbit_training(base,use_gradient_checkpointing=True)
        model=PeftModel.from_pretrained(base,sft,is_trainable=True,adapter_name='default')
        model.load_adapter(dpo,adapter_name='dpo',is_trainable=False)
        prompts=read_prompts(package/'evaluation-prompts.jsonl')
        generation={}
        def predict(arm):
            return generate(model,tokenizer,prompts,output/(arm+'.jsonl'),1,
                config['generation_batch_size'],config['max_new_tokens'],config['seed'],False)
        model.set_adapter('dpo')
        generation['dpo']=predict('dpo')
        model.set_adapter('default')
        initial_hash=adapter_hash(model,'default')
        generation['sft']=predict('sft')
        model.delete_adapter('dpo');model.set_adapter('default')
        if adapter_hash(model,'default')!=initial_hash:raise ValueError('Baseline generation changed SFT weights')
        tokenizer.padding_side='right';model.config.use_cache=False
        args=SFTConfig(output_dir=str(output/'checkpoints'),num_train_epochs=1,max_steps=-1,
            max_length=config['max_length'],completion_only_loss=True,packing=False,
            dataset_kwargs={'skip_prepare_dataset':True},remove_unused_columns=False,
            per_device_train_batch_size=config['per_device_train_batch_size'],
            per_device_eval_batch_size=config['per_device_eval_batch_size'],
            gradient_accumulation_steps=config['gradient_accumulation_steps'],
            gradient_checkpointing=True,learning_rate=config['learning_rate'],
            warmup_ratio=config['warmup_ratio'],bf16=True,seed=config['seed'],data_seed=config['seed'],
            logging_steps=1,save_strategy='steps',save_steps=64,save_total_limit=2,
            eval_strategy='no',report_to='none',optim='adamw_torch',group_by_length=True,
            eos_token=tokenizer.eos_token)
        trainer=SFTTrainer(model=model,processing_class=tokenizer,args=args,
            train_dataset=data['train'],eval_dataset=data['validation'])
        trainable=[n for n,p in trainer.model.named_parameters() if p.requires_grad]
        if not trainable or any('lora_' not in n or '.default.' not in n for n in trainable):
            raise ValueError('Expected only the original SFT LoRA parameters to be trainable')
        batch=next(iter(trainer.get_train_dataloader()))
        if not (batch['labels']==-100).any():raise ValueError('Prompt mask missing')
        for labels in batch['labels']:
            json.loads(tokenizer.decode(labels[labels!=-100].tolist(),skip_special_tokens=True))
        before_metrics=trainer.evaluate()
        class Budget(TrainerCallback):
            stopped=False
            def on_train_begin(self,args,state,control,**kwargs):self.started=time.monotonic()
            def on_step_end(self,args,state,control,**kwargs):
                if time.monotonic()-self.started>config['training_seconds']:
                    self.stopped=True;control.should_training_stop=True;control.should_save=True
                return control
        budget=Budget();trainer.add_callback(budget)
        torch.cuda.reset_peak_memory_stats();before=time.monotonic()
        result=trainer.train();elapsed=time.monotonic()-before
        trainer.save_state()
        # Save before post-training generation so interruption preserves the model.
        trainer.model.save_pretrained(output/'adapter',selected_adapters=['default'])
        tokenizer.save_pretrained(output/'adapter')
        (output/'adapter/system-prompt.txt').write_text(metadata['system_prompt'])
        final_metrics=trainer.evaluate()
        steps=trainer.state.global_step
        planned=math.ceil(len(data['train'])/(config['per_device_train_batch_size']*config['gradient_accumulation_steps']))
        summary={'config':config,'dataset':metadata['preflight'],'optimizer_steps':steps,'planned_steps':planned,
            'training_seconds':elapsed,'setup_and_baseline_seconds':before-started,
            'metrics':result.metrics,'base_eval':before_metrics,'adapter_eval':final_metrics,
            'validation_sample':metadata['validation_sample'],'loss_mask_verified':True,
            'peak_allocated_gib':torch.cuda.max_memory_allocated()/1024**3,
            'initial_adapter_hash':initial_hash,'final_adapter_hash':adapter_hash(model,'default'),
            'stopped_for_budget':budget.stopped,'parent_training_identity':previous['training_identity'],
            'automatic_promotion':False}
        save(output/'training-summary.json',summary)
        if budget.stopped or steps!=planned or summary['initial_adapter_hash']==summary['final_adapter_hash']:
            raise ValueError('Training incomplete or weights unchanged; saved candidate is not a completed experiment')
        if any(not math.isfinite(v) for v in (result.metrics['train_loss'],final_metrics['eval_loss'])):
            raise ValueError('Nonfinite training metrics')
        generation['custom']=predict('custom')
        save(output/'phase-complete.json',{'training':summary,'generation':generation,
            'package_sha256':sha(package/'package-manifest.json'),
            'adapter_files':{n:sha(output/'adapter'/n) for n in ('adapter_config.json','adapter_model.safetensors')}})
        print(json.dumps({'completed':True,'steps':steps,'training_seconds':elapsed}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('package','sft','dpo','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();run(a.package,a.sft,a.dpo,a.output)
