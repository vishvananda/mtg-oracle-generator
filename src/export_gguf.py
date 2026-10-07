"""Merge into the pinned BF16 base, then export a separate CPU serving candidate.

Uses a supplied llama.cpp converter and quantizer; records their exact hashes.
Does not change the live model, service configuration, or editor.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def export(adapter,output,converter,quantizer,quantization='Q4_0',threads=8):
    import torch
    from huggingface_hub import snapshot_download
    from peft import PeftModel
    from transformers import AutoModelForCausalLM,AutoTokenizer
    summary=json.loads((adapter/'run-summary.json').read_text());config=summary['config']
    if output.exists(): raise ValueError('Use a fresh serving candidate directory')
    if summary.get('stopped_for_training_budget') or summary.get('max_steps')!=-1:
        raise ValueError('Export the completed full run, not a smoke or budget-stopped adapter')
    base=Path(snapshot_download(config['model'],revision=config['model_revision'],
        allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.tiktoken']))
    identity={'base_model':config['model'],'base_revision':config['model_revision'],
        'adapter_files':{n:sha(adapter/n) for n in ('adapter_config.json','adapter_model.safetensors')},
        'training_identity':summary['training_identity'],
        'converter_sha256':sha(converter),'quantizer_sha256':sha(quantizer),
        'converter_python_sources':{str(p.relative_to(converter.parent)):sha(p)
            for p in sorted(converter.parent.rglob('*.py'))},
        'base_files':{p.name:sha(p) for p in sorted(base.glob('*.safetensors'))},
        'recipe':'Pinned BF16 base + PEFT safe merge; F16 GGUF; one quantization pass',
        'quantization':quantization,'automatic_promotion':False}
    output.mkdir(parents=True);started=time.monotonic()
    (output/'request.json').write_text(json.dumps(identity,indent=2)+'\n')
    torch.set_num_threads(threads)
    model=AutoModelForCausalLM.from_pretrained(base,torch_dtype=torch.bfloat16,device_map='cpu',local_files_only=True)
    model=PeftModel.from_pretrained(model,adapter,is_trainable=False,local_files_only=True)
    model=model.merge_and_unload(safe_merge=True)
    merged=output/'merged';model.save_pretrained(merged,safe_serialization=True,max_shard_size='4GB')
    AutoTokenizer.from_pretrained(base,local_files_only=True).save_pretrained(merged)
    del model
    import gc
    gc.collect()
    f16=output/'model-F16.gguf';pending=output/f'model-{quantization}.pending.gguf'
    with (output/'conversion.log').open('w') as log:
        subprocess.run([sys.executable,str(converter),str(merged),'--outfile',str(f16),'--outtype','f16'],
                       stdout=log,stderr=log,check=True)
        subprocess.run([str(quantizer),str(f16),str(pending),quantization,str(threads)],stdout=log,stderr=log,check=True)
    result=output/f'model-{quantization}.gguf';pending.rename(result)
    identity.update(output_sha256=sha(result),model_path=str(result.resolve()),elapsed_seconds=time.monotonic()-started,
        versions={'torch':torch.__version__,'transformers':__import__('transformers').__version__,'peft':__import__('peft').__version__},
        stage='candidate_needs_serving_evaluation',backend='merged GGUF; differs from NF4 evaluation')
    (output/'candidate.json').write_text(json.dumps(identity,indent=2)+'\n')
    return identity


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adapter',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--converter',type=Path,required=True);p.add_argument('--quantizer',type=Path,required=True)
    p.add_argument('--quantization',choices=['Q4_0','Q4_K_M','Q8_0'],default='Q4_0');p.add_argument('--threads',type=int,default=8)
    a=p.parse_args()
    if a.threads<1: p.error('--threads must be positive')
    print(json.dumps(export(a.adapter,a.output,a.converter,a.quantizer,a.quantization,a.threads),indent=2))
