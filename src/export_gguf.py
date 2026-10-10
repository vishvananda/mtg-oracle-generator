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


def check_export_scope(summary,allow_partial=False):
    if summary.get('max_steps')!=-1:
        raise ValueError('A smoke checkpoint cannot be exported as a serving candidate')
    partial=bool(summary.get('stopped_for_training_budget'))
    if partial and not allow_partial:
        raise ValueError('Budget-stopped adapter needs explicit --allow-partial for preview export')
    return 'partial_epoch_preview' if partial else 'completed_training'


def check_dpo_export(summary,identity,completion):
    if summary.get('stage')!='train' or identity.get('stage')!='train':
        raise ValueError('DPO export requires a training run')
    if any(completion.get(k)!=v for k,v in summary.items()):
        raise ValueError('DPO completion receipt disagrees with training summary')
    if (not isinstance(summary.get('steps'),int) or summary['steps']<=0 or summary['steps']!=summary.get('planned_steps')
            or summary.get('stopped_for_budget') or summary.get('metrics',{}).get('epoch',0)<1):
        raise ValueError('DPO pilot has not completed its planned pass')
    if (summary.get('reference_unchanged') is not True
            or summary.get('initial_reference_logits_equal') is not True
            or not summary.get('reference_sha256') or not summary.get('initial_policy_sha256')
            or not summary.get('final_policy_sha256')
            or summary['final_policy_sha256']==summary.get('initial_policy_sha256')
            or summary.get('reference_sha256')!=summary.get('initial_policy_sha256')):
        raise ValueError('DPO reference/policy integrity check failed')
    if not identity.get('base_model') or not identity.get('base_revision'):
        raise ValueError('DPO base model must be pinned')
    return 'completed_dpo_pilot'


def export(adapter,output,converter,quantizer,quantization='Q4_0',threads=8,allow_partial=False,dpo_run=None):
    import torch
    from huggingface_hub import snapshot_download
    from peft import PeftModel
    from transformers import AutoModelForCausalLM,AutoTokenizer
    if output.exists(): raise ValueError('Use a fresh serving candidate directory')
    if dpo_run is None:
        summary=json.loads((adapter/'run-summary.json').read_text());config=summary['config']
        scope=check_export_scope(summary,allow_partial)
    else:
        from safetensors.torch import load_file
        if adapter.resolve()!=(dpo_run/'adapter').resolve():
            raise ValueError('Adapter must belong to the supplied DPO run')
        summary=json.loads((dpo_run/'training-summary.json').read_text())
        provenance=json.loads((dpo_run/'identity.json').read_text())
        completion=json.loads((dpo_run/'phase-complete.json').read_text())
        scope=check_dpo_export(summary,provenance,completion)
        tensor_hash=hashlib.sha256()
        for name,tensor in sorted(load_file(adapter/'adapter_model.safetensors').items()):
            tensor_hash.update(name.encode());tensor_hash.update(tensor.float().numpy().tobytes())
        if tensor_hash.hexdigest()!=summary['final_policy_sha256']:
            raise ValueError('Saved DPO adapter differs from the trained policy')
        config={'model':provenance['base_model'],'model_revision':provenance['base_revision']}
        summary={**summary,'training_identity':provenance,'optimizer_steps':summary['steps']}
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
        'quantization':quantization,'automatic_promotion':False,'training_scope':scope,
        'optimizer_steps':summary.get('optimizer_steps'),'training_epoch':summary.get('metrics',{}).get('epoch')}
    if dpo_run is not None:
        identity.update(dpo_summary=summary,adapter_loading='Final DPO adapter directly on original base; do not stack SFT adapter')
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
    p.add_argument('--allow-partial',action='store_true',help='Export a budget-stopped training checkpoint as a labeled preview')
    p.add_argument('--dpo-run',type=Path,help='Completed DPO run containing identity, summary, completion receipt and adapter')
    a=p.parse_args()
    if a.threads<1: p.error('--threads must be positive')
    print(json.dumps(export(a.adapter,a.output,a.converter,a.quantizer,a.quantization,a.threads,a.allow_partial,a.dpo_run),indent=2))
