#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""A bounded second SFT epoch with an uninterrupted full-epoch schedule.

The first segment starts from the completed original SFT adapter with a fresh
optimizer. Later explicitly authorized segments can restore its full checkpoint.
Stopping at the review checkpoint must not decay the learning rate to zero.
"""
import argparse
import json
import math
from pathlib import Path
import tempfile
import time

from custom_sft_gpu import activate_training_adapter
from prepared_training import load_prepared
from rl_gpu import adapter_hash, save, sha


def verify(package, parent):
    manifest = json.loads((package/'package-manifest.json').read_text())
    for root, pins in ((package, manifest['files']), (parent, manifest['parent_files'])):
        for name, pin in pins.items():
            if Path(name).is_absolute() or '..' in Path(name).parts or sha(root/name) != pin:
                raise ValueError('Frozen input differs: ' + name)
    previous = json.loads((parent/'run-summary.json').read_text())
    config = json.loads((package/'config.json').read_text())
    if (previous['metrics']['epoch'] < 1 or previous['stopped_for_training_budget']
            or previous['optimizer_steps'] != 19774):
        raise ValueError('The original SFT epoch must be complete')
    for key in ('model', 'model_revision', 'max_length', 'completion_only_loss', 'packing',
                'per_device_train_batch_size', 'gradient_accumulation_steps'):
        if config[key] != previous['config'][key]:
            raise ValueError('Original SFT recipe changed: ' + key)
    if (sha(package/'manifest.json') != previous['training_identity']['dataset_manifest_sha256']
            or config['epochs'] != 1 or config['monitoring_split'] != 'train'
            or not 0 < config['learning_rate'] < previous['config']['learning_rate']):
        raise ValueError('Expected unchanged data and a lower-rate second pass')
    if not 0 < config['stop_after_steps'] <= config['schedule_steps']:
        raise ValueError('Stop checkpoint must be within the full epoch schedule')
    return manifest, previous, config


def stop_reason(step, elapsed, stop_step, seconds):
    if step >= stop_step:
        return 'review_checkpoint'
    if elapsed >= seconds:
        return 'time_budget'
    return None


def run(package, parent, output, resume=None):
    manifest, previous, config = verify(package, parent)
    identity = {'package_sha256': sha(package/'package-manifest.json'), 'config': config,
        'parent_files': manifest['parent_files'], 'starting_point': 'completed original SFT epoch',
        'parent_training_identity': previous['training_identity'], 'fresh_optimizer': resume is None,
        'automatic_promotion': False}
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    if resume:
        saved = json.loads((resume/'continuation-identity.json').read_text())
        if saved['parent_files'] != identity['parent_files'] or saved['parent_training_identity'] != identity['parent_training_identity']:
            raise ValueError('Resume checkpoint belongs to another starting model or dataset')
        for key in set(config) - {'stop_after_steps', 'training_seconds'}:
            if config[key] != saved['config'][key]:
                raise ValueError('Resume schedule or recipe changed: ' + key)
        for name in ('optimizer.pt', 'scheduler.pt', 'rng_state.pth', 'trainer_state.json'):
            if not (resume/name).is_file():
                raise ValueError('Incomplete resumable checkpoint: ' + name)
    output.mkdir(parents=True, exist_ok=True)
    save(output/'identity.json', identity)
    import torch
    from peft import PeftModel, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback, set_seed
    from trl import SFTConfig, SFTTrainer
    if not torch.cuda.is_available():
        raise ValueError('CUDA required')
    set_seed(config['seed']); torch.set_num_threads(8)
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(config['model'], revision=config['model_revision'])
    tokenizer.pad_token = tokenizer.eos_token; tokenizer.padding_side = 'right'
    with tempfile.TemporaryDirectory(prefix='oracle-epoch2-') as cache:
        data, metadata = load_prepared(package, package, config, Path(cache)/'data')
        schedule = math.ceil(len(data['train']) / (config['per_device_train_batch_size'] * config['gradient_accumulation_steps']))
        if schedule != config['schedule_steps']:
            raise ValueError('Full-epoch step count changed')
        print(json.dumps({'phase': 'data_ready', 'rows': len(data['train']), 'schedule_steps': schedule}), flush=True)
        base = AutoModelForCausalLM.from_pretrained(config['model'], revision=config['model_revision'],
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16, device_map={'': torch.cuda.current_device()}, attn_implementation='sdpa')
        base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
        model = PeftModel.from_pretrained(base, resume or parent, is_trainable=True, adapter_name='default')
        model.config.use_cache = False
        args = SFTConfig(output_dir=str(output/'checkpoints'), num_train_epochs=1, max_steps=-1,
            max_length=config['max_length'], completion_only_loss=True, packing=False,
            dataset_kwargs={'skip_prepare_dataset': True}, remove_unused_columns=False,
            per_device_train_batch_size=config['per_device_train_batch_size'],
            per_device_eval_batch_size=config['per_device_eval_batch_size'],
            gradient_accumulation_steps=config['gradient_accumulation_steps'],
            gradient_checkpointing=True, learning_rate=config['learning_rate'],
            lr_scheduler_type='linear', warmup_ratio=config['warmup_ratio'], bf16=True,
            seed=config['seed'], data_seed=config['data_seed'], logging_steps=10,
            save_strategy='steps', save_steps=config['save_steps'], save_total_limit=2,
            eval_strategy='steps', eval_steps=config['eval_steps'], report_to='none',
            optim='adamw_torch', group_by_length=True, eos_token=tokenizer.eos_token)
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
        class Segment(TrainerCallback):
            reason = None
            def on_train_begin(self, args, state, control, **kwargs):
                self.started = time.monotonic()
                if state.max_steps != schedule:
                    raise ValueError('Trainer schedule differs from the full epoch')
            def on_step_end(self, args, state, control, **kwargs):
                self.reason = stop_reason(state.global_step, time.monotonic()-self.started,
                                          config['stop_after_steps'], config['training_seconds'])
                if self.reason:
                    control.should_training_stop = True; control.should_save = True
                return control
            def on_save(self, args, state, control, **kwargs):
                checkpoint = Path(args.output_dir)/f'checkpoint-{state.global_step}'
                save(checkpoint/'continuation-identity.json', identity)
        segment = Segment(); trainer.add_callback(segment)
        save(output/'pre-training.json', {'diagnostic': before_metrics, 'validation_sample': metadata['validation_sample'],
            'schedule_steps': schedule, 'stop_after_steps': config['stop_after_steps'], 'initial_adapter_hash': initial})
        torch.cuda.reset_peak_memory_stats(); before = time.monotonic()
        trained = trainer.train(resume_from_checkpoint=str(resume) if resume else None)
        elapsed = time.monotonic()-before
        trainer.save_state(); trainer.model.save_pretrained(output/'adapter', selected_adapters=['default'])
        tokenizer.save_pretrained(output/'adapter')
        (output/'adapter/system-prompt.txt').write_text(metadata['system_prompt'])
        after_metrics = trainer.evaluate()
        summary = {'config': config, 'optimizer_steps': trainer.state.global_step,
            'planned_steps': config['stop_after_steps'], 'schedule_steps': schedule,
            'training_seconds': elapsed, 'setup_seconds': before-started, 'metrics': trained.metrics,
            'base_eval': before_metrics, 'adapter_eval': after_metrics,
            'validation_sample': metadata['validation_sample'], 'loss_mask_verified': True,
            'peak_allocated_gib': torch.cuda.max_memory_allocated()/1024**3,
            'initial_adapter_hash': initial, 'final_adapter_hash': adapter_hash(trainer.model, 'default'),
            'stop_reason': segment.reason, 'parent_training_identity': previous['training_identity'],
            'resume_checkpoint': f'checkpoints/checkpoint-{trainer.state.global_step}', 'automatic_promotion': False}
        save(output/'training-summary.json', summary)
        if (segment.reason != 'review_checkpoint' or trainer.state.global_step != config['stop_after_steps']
                or summary['final_adapter_hash'] == initial
                or not all(math.isfinite(v) for v in (trained.metrics['train_loss'], after_metrics['eval_loss']))):
            raise ValueError('Incomplete or invalid update; checkpoint retained for inspection')
        checkpoint = output/summary['resume_checkpoint']
        files = ('adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt', 'scheduler.pt',
                 'rng_state.pth', 'trainer_state.json', 'continuation-identity.json')
        save(output/'phase-complete.json', {'training': summary,
            'package_sha256': identity['package_sha256'],
            'adapter_files': {n: sha(output/'adapter'/n) for n in files[:2]},
            'checkpoint_files': {n: sha(checkpoint/n) for n in files}})
        print(json.dumps({'completed': True, 'steps': trainer.state.global_step, 'training_seconds': elapsed}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('package', 'parent', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    run(**vars(parser.parse_args()))
