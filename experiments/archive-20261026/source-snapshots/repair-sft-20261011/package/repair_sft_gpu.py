#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""One repair SFT pass from the deployed DPO adapter; no automatic promotion."""
import argparse
import json
import math
from pathlib import Path
import tempfile
import time

from custom_sft_gpu import activate_training_adapter
from export_gguf import check_dpo_export
from prepared_training import load_prepared
from rl_gpu import adapter_hash, save, sha


def verify(package, parent):
    manifest = json.loads((package/'package-manifest.json').read_text())
    for name, pin in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(package/name) != pin:
            raise ValueError('Frozen package differs: ' + name)
    for name, pin in manifest['parent_files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(parent/name) != pin:
            raise ValueError('Frozen parent differs: ' + name)
    identity = json.loads((parent/'identity.json').read_text())
    summary = json.loads((parent/'training-summary.json').read_text())
    complete = json.loads((parent/'phase-complete.json').read_text())
    check_dpo_export(summary, identity, complete)
    config = json.loads((package/'config.json').read_text())
    if (config['model'] != identity['base_model'] or config['model_revision'] != identity['base_revision']
            or config['epochs'] != 1 or not config['completion_only_loss'] or config['packing']):
        raise ValueError('Unsupported continuation recipe')
    return manifest, identity, config


def run(package, parent, output):
    manifest, previous, config = verify(package, parent)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    save(output/'identity.json', {'package_sha256': sha(package/'package-manifest.json'),
        'parent_files': manifest['parent_files'], 'config': config,
        'starting_point': 'deployed DPO round two', 'fresh_optimizer': True, 'automatic_promotion': False})
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback, set_seed
    from peft import PeftModel, prepare_model_for_kbit_training
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available():
        raise ValueError('CUDA required')
    set_seed(config['seed']); torch.set_num_threads(8)
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(config['model'], revision=config['model_revision'])
    tokenizer.pad_token = tokenizer.eos_token; tokenizer.padding_side = 'right'
    with tempfile.TemporaryDirectory(prefix='oracle-repair-prepared-') as cache:
        data, metadata = load_prepared(package, package, config, Path(cache)/'data')
        base = AutoModelForCausalLM.from_pretrained(config['model'], revision=config['model_revision'],
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16, device_map={'': torch.cuda.current_device()}, attn_implementation='sdpa')
        base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
        model = PeftModel.from_pretrained(base, parent/'adapter', is_trainable=True, adapter_name='default')
        model.config.use_cache = False
        args = SFTConfig(output_dir=str(output/'checkpoints'), num_train_epochs=1, max_steps=-1,
            max_length=config['max_length'], completion_only_loss=True, packing=False,
            dataset_kwargs={'skip_prepare_dataset': True}, remove_unused_columns=False,
            per_device_train_batch_size=config['per_device_train_batch_size'],
            per_device_eval_batch_size=config['per_device_eval_batch_size'],
            gradient_accumulation_steps=config['gradient_accumulation_steps'],
            gradient_checkpointing=True, learning_rate=config['learning_rate'],
            warmup_ratio=config['warmup_ratio'], bf16=True, seed=config['seed'], data_seed=config['seed'],
            logging_steps=1, save_strategy='steps', save_steps=32, save_total_limit=2,
            eval_strategy='no', report_to='none', optim='adamw_torch', group_by_length=True,
            eos_token=tokenizer.eos_token)
        trainer = SFTTrainer(model=model, processing_class=tokenizer, args=args,
            train_dataset=data['train'], eval_dataset=data['validation'])
        names = activate_training_adapter(trainer.model)
        initial = adapter_hash(trainer.model, 'default')
        save(output/'trainable-parameters.json', {'names': names,
            'parameters': sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)})
        batch = next(iter(trainer.get_train_dataloader()))
        if not (batch['labels'] == -100).any():
            raise ValueError('Prompt loss mask missing')
        for labels in batch['labels']:
            json.loads(tokenizer.decode(labels[labels != -100].tolist(), skip_special_tokens=True))
        before_metrics = trainer.evaluate()
        class Budget(TrainerCallback):
            stopped = False
            def on_train_begin(self, args, state, control, **kwargs):
                self.started = time.monotonic()
            def on_step_end(self, args, state, control, **kwargs):
                if time.monotonic() - self.started > config['training_seconds']:
                    self.stopped = True; control.should_training_stop = True; control.should_save = True
                return control
        budget = Budget(); trainer.add_callback(budget)
        torch.cuda.reset_peak_memory_stats(); before = time.monotonic()
        trained = trainer.train(); elapsed = time.monotonic() - before
        trainer.save_state(); model.save_pretrained(output/'adapter', selected_adapters=['default'])
        tokenizer.save_pretrained(output/'adapter')
        (output/'adapter/system-prompt.txt').write_text((package/'serving-system.txt').read_text())
        after_metrics = trainer.evaluate()
        planned = math.ceil(len(data['train']) / (config['per_device_train_batch_size'] * config['gradient_accumulation_steps']))
        summary = {'config': config, 'dataset': metadata['preflight'], 'optimizer_steps': trainer.state.global_step,
            'planned_steps': planned, 'training_seconds': elapsed, 'setup_seconds': before-started,
            'metrics': trained.metrics, 'base_eval': before_metrics, 'adapter_eval': after_metrics,
            'validation_sample': metadata['validation_sample'], 'loss_mask_verified': True,
            'peak_allocated_gib': torch.cuda.max_memory_allocated()/1024**3,
            'initial_adapter_hash': initial, 'final_adapter_hash': adapter_hash(model, 'default'),
            'stopped_for_budget': budget.stopped, 'parent_training_identity': previous, 'automatic_promotion': False}
        save(output/'training-summary.json', summary)
        if (budget.stopped or trainer.state.global_step != planned or summary['final_adapter_hash'] == initial
                or not all(math.isfinite(v) for v in (trained.metrics['train_loss'], after_metrics['eval_loss']))):
            raise ValueError('Incomplete or invalid learned update; no promotion')
        save(output/'phase-complete.json', {'training': summary,
            'package_sha256': sha(package/'package-manifest.json'),
            'adapter_files': {n: sha(output/'adapter'/n) for n in ('adapter_config.json', 'adapter_model.safetensors')}})
        print(json.dumps({'completed': True, 'steps': planned, 'training_seconds': elapsed}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('package', 'parent', 'output'):
        p.add_argument('--'+key, type=Path, required=True)
    run(**vars(p.parse_args()))
