#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""Greedy, unconstrained GPU generation on a named frozen split; never loads targets into prompts."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from data_utils import digest
from hub_dataset import verify
from prediction_io import Predictions
from evaluation_selection import POLICY,select_cases


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--adapter',type=Path,help='Omit to evaluate the same quantized base')
    p.add_argument('--split',choices=['validation','test'],default='validation')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-new-tokens',type=int,default=2048)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--resume-output',action='store_true')
    p.add_argument('--validation-limit',type=int,help='Fixed development panel; forbidden for test')
    a=p.parse_args()
    if a.max_new_tokens<1 or a.batch_size<1: p.error('Token and batch budgets must be positive')
    if a.validation_limit is not None and (a.split!='validation' or a.validation_limit<1):
        p.error('A positive sample limit is allowed only for validation')
    manifest=verify(a.dataset)
    if a.split=='test' and manifest['status']!='complete': p.error('Final test requires a complete frozen release')
    if a.split+'.messages.jsonl' not in manifest['files']: p.error('Split missing from manifest')
    config=json.loads(a.config.read_text())
    rows=[json.loads(line) for line in (a.dataset/(a.split+'.messages.jsonl')).read_text().splitlines()]
    selection={'policy':POLICY,'limit':a.validation_limit} if a.validation_limit else None
    rows=select_cases(rows,selection)
    adapter_files={}
    if a.adapter:
        for name in ('adapter_config.json','adapter_model.safetensors'):
            with (a.adapter/name).open('rb') as stream: adapter_files[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    identity={'dataset_manifest_sha256':digest((a.dataset/'manifest.json').read_bytes()),'split':a.split,
        'model':config['model'],'model_revision':config['model_revision'],'adapter_files':adapter_files,
        'config_sha256':digest(a.config.read_bytes()),
        'config_canonical_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'seed':config['seed'],
        'code_files':{n:digest((Path(__file__).parent/n).read_bytes()) for n in ('generate.py','generate.py.lock','prediction_io.py')},
        'selection':selection,'selection_code_sha256':digest((Path(__file__).parent/'evaluation_selection.py').read_bytes()),
        'decoding':{'do_sample':False,'max_new_tokens':a.max_new_tokens,'enable_thinking':False,'batch_size':a.batch_size},
        'backend':'transformers NF4, one GPU'}
    writer=Predictions(a.output,identity,[r['example_id'] for r in rows],a.resume_output)
    if all(r['example_id'] in writer.done for r in rows):
        writer.finish();raise SystemExit(0)
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig,set_seed
    from peft import PeftModel
    if not torch.cuda.is_available(): p.error('CUDA GPU required by this NF4 recipe')
    set_seed(config['seed'])
    dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer=AutoTokenizer.from_pretrained(config['model'],revision=config['model_revision'])
    tokenizer.padding_side='left';tokenizer.pad_token=tokenizer.eos_token
    model=AutoModelForCausalLM.from_pretrained(config['model'],revision=config['model_revision'],
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype),
        torch_dtype=dtype,device_map={'':torch.cuda.current_device()},attn_implementation='sdpa')
    if a.adapter: model=PeftModel.from_pretrained(model,a.adapter)
    model.eval()
    # Similar prompt lengths reduce padding. The schedule and batch policy are frozen.
    rows.sort(key=lambda r:(len(r['messages'][1]['content']),r['example_id']))
    started=time.monotonic()
    try:
      with torch.inference_mode(),torch.autocast('cuda',dtype=dtype):
        for i in range(0,len(rows),a.batch_size):
            group=rows[i:i+a.batch_size];prompts=[]
            if all(r['example_id'] in writer.done for r in group): continue
            for row in group:
                messages=row['messages'][:2]
                if [m['role'] for m in messages]!=['system','user']: raise ValueError('Unexpected messages')
                prompts.append(tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False))
            batch=tokenizer(prompts,return_tensors='pt',padding=True,add_special_tokens=False).to(model.device)
            width=batch['input_ids'].shape[1]
            context_limit=getattr(model.config,'max_position_embeddings',None)
            if context_limit and width+a.max_new_tokens>context_limit:
                raise ValueError('Prompt plus generation exceeds model context; choose an appropriate model/budget')
            before=time.monotonic()
            generated=model.generate(**batch,do_sample=False,max_new_tokens=a.max_new_tokens,
                                      pad_token_id=tokenizer.eos_token_id,use_cache=True)
            elapsed=time.monotonic()-before
            for row, sequence in zip(group,generated):
                if row['example_id'] in writer.done: continue
                tokens=sequence[width:].tolist()
                if tokenizer.eos_token_id in tokens: tokens=tokens[:tokens.index(tokenizer.eos_token_id)+1]
                writer.append({'example_id':row['example_id'],'raw_text':tokenizer.decode(tokens,skip_special_tokens=True),
                    'output_tokens':len(tokens),'seconds':elapsed/len(group),'batch_seconds':elapsed,
                    'hit_token_limit':len(tokens)>=a.max_new_tokens and tokens[-1]!=tokenizer.eos_token_id})
            print(json.dumps({'completed':len(writer.done),'scheduled':writer.identity['scheduled_cases'],
                             'session_seconds':time.monotonic()-started}),flush=True)
      writer.finish()
    finally: writer.close()
