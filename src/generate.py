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


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--adapter',type=Path,help='Omit to evaluate the same quantized base')
    p.add_argument('--split',choices=['validation','test'],default='validation')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-new-tokens',type=int,default=2048)
    a=p.parse_args()
    if a.output.exists() or a.output.with_suffix('.manifest.json').exists(): p.error('Use a fresh output path')
    if a.max_new_tokens<1: p.error('--max-new-tokens must be positive')
    manifest=verify(a.dataset)
    if a.split=='test' and manifest['status']!='complete': p.error('Final test requires a complete frozen release')
    if a.split+'.messages.jsonl' not in manifest['files']: p.error('Split missing from manifest')
    config=json.loads(a.config.read_text())
    import torch
    from transformers import AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig,set_seed
    from peft import PeftModel
    if not torch.cuda.is_available(): p.error('CUDA GPU required by this NF4 recipe')
    set_seed(config['seed'])
    dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer=AutoTokenizer.from_pretrained(config['model'],revision=config['model_revision'])
    model=AutoModelForCausalLM.from_pretrained(config['model'],revision=config['model_revision'],
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
            bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype),
        torch_dtype=dtype,device_map={'':torch.cuda.current_device()},attn_implementation='sdpa')
    if a.adapter: model=PeftModel.from_pretrained(model,a.adapter)
    model.eval()
    rows=[json.loads(line) for line in (a.dataset/(a.split+'.messages.jsonl')).read_text().splitlines()]
    a.output.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with a.output.open('w') as stream,torch.inference_mode(),torch.autocast('cuda',dtype=dtype):
        for i,row in enumerate(rows):
            messages=row['messages'][:2]
            if [m['role'] for m in messages]!=['system','user']: raise ValueError('Unexpected messages')
            prompt=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
            batch=tokenizer(prompt,return_tensors='pt',add_special_tokens=False).to(model.device)
            width=batch['input_ids'].shape[1]
            context_limit=getattr(model.config,'max_position_embeddings',None)
            if context_limit and width+a.max_new_tokens>context_limit:
                raise ValueError('Prompt plus generation exceeds model context; choose an appropriate model/budget')
            before=time.monotonic()
            generated=model.generate(**batch,do_sample=False,max_new_tokens=a.max_new_tokens,
                                      pad_token_id=tokenizer.eos_token_id,use_cache=True)
            tokens=generated[0,width:].tolist()
            prediction={'example_id':row['example_id'],'raw_text':tokenizer.decode(tokens,skip_special_tokens=True),
                        'output_tokens':len(tokens),'seconds':time.monotonic()-before,
                        'hit_token_limit':len(tokens)>=a.max_new_tokens and tokens[-1]!=tokenizer.eos_token_id}
            stream.write(json.dumps(prediction,ensure_ascii=False)+'\n');stream.flush()
            if (i+1)%100==0: print(json.dumps({'completed':i+1,'total':len(rows)}),flush=True)
    adapter_files={}
    if a.adapter:
        for name in ('adapter_config.json','adapter_model.safetensors'):
            with (a.adapter/name).open('rb') as stream: adapter_files[name]=hashlib.file_digest(stream,'sha256').hexdigest()
    receipt={'dataset_manifest_sha256':digest((a.dataset/'manifest.json').read_bytes()),'split':a.split,
        'predictions_sha256':digest(a.output.read_bytes()),'cases':len(rows),'model':config['model'],
        'model_revision':config['model_revision'],'adapter_files':adapter_files,'seed':config['seed'],
        'decoding':{'do_sample':False,'max_new_tokens':a.max_new_tokens,'enable_thinking':False},
        'backend':'transformers NF4, one GPU','elapsed_seconds':time.monotonic()-started}
    a.output.with_suffix('.manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
