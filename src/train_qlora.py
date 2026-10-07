#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "torch==2.8.0", "transformers==4.56.2", "trl==0.23.0",
#   "peft==0.17.1", "accelerate==1.10.1", "datasets==4.1.1",
#   "bitsandbytes==0.47.0", "huggingface-hub==0.34.4",
# ]
# ///
"""Portable GPU training entry point. --preflight needs only Python stdlib.

The default training run is a bounded 20-optimizer-step smoke run. Output is
written locally; upload/mount persistence is chosen by the job launcher.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import shutil
import time


def preflight(dataset, config):
    manifest = json.loads((dataset / 'manifest.json').read_text())
    result = {'model': config['model'], 'revision': config['model_revision'], 'splits': {}}
    for split in ('train', 'validation'):
        path = dataset / (split + '.sft.jsonl')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['files'][path.name]['sha256']:
            raise ValueError(f'Hash mismatch: {path.name}')
        rows = [json.loads(line) for line in data.splitlines()]
        for row in rows:
            if set(row) != {'prompt', 'completion'} or [m['role'] for m in row['prompt']] != ['system', 'user']:
                raise ValueError('Unexpected training input structure')
            if len(row['completion']) != 1 or row['completion'][0]['role'] != 'assistant':
                raise ValueError('Expected one assistant target')
            json.loads(row['completion'][0]['content'])
        result['splits'][split] = len(rows)
    if manifest['checks']['group_overlap'] or manifest['checks']['description_overlap']:
        raise ValueError('Dataset reports split leakage')
    return result


def validate_config(config, max_steps):
    if max_steps != -1 and max_steps < 1:
        raise ValueError('max_steps must be -1 (configured epochs) or a positive integer')
    if not config['completion_only_loss'] or config['packing']:
        raise ValueError('This recipe requires completion-only loss and unpacked examples')
    if not config['load_in_4bit'] or config['method'] != 'QLoRA':
        raise ValueError('This recipe implements 4-bit QLoRA')


def verify_completion_masks(tokenizer, processed, original):
    """Verify every actual training/evaluation mask, not just one random batch."""
    if len(processed) != len(original):
        raise ValueError('Processed dataset changed row count')
    for i, (row, target) in enumerate(zip(processed, original)):
        ids, mask = row['input_ids'], row.get('completion_mask', [])
        if len(ids) != len(mask) or set(mask) != {0, 1}:
            raise ValueError(f'Missing prompt/completion mask at row {i}')
        boundary = mask.index(1)
        if mask != [0]*boundary + [1]*(len(mask)-boundary):
            raise ValueError(f'Noncontiguous completion mask at row {i}')
        supervised = tokenizer.decode(ids[boundary:], skip_special_tokens=True).strip()
        if json.loads(supervised) != json.loads(target['completion'][0]['content']):
            raise ValueError(f'Loss mask does not cover exactly the assistant target at row {i}')


def tokenize_supervised(row, tokenizer, max_length):
    """Mask the assistant header too; TRL 0.23's conversational path includes it."""
    prefix = tokenizer.apply_chat_template(row['prompt'], tokenize=True, add_generation_prompt=True, enable_thinking=False)
    full = tokenizer.apply_chat_template(row['prompt']+row['completion'], tokenize=True, add_generation_prompt=False, enable_thinking=False)
    if full[:len(prefix)] != prefix:
        raise ValueError('Chat template prompt is not an exact prefix of the completed conversation')
    if len(full) > max_length:
        raise ValueError('Example exceeds max_length; refusing silent truncation')
    return {'input_ids': full, 'completion_mask': [0]*len(prefix)+[1]*(len(full)-len(prefix))}


def training_budget_exhausted(elapsed, steps, budget):
    # Leave enough time to finish the next optimizer step and save a useful adapter.
    return bool(budget and steps and elapsed + 2 * elapsed / steps >= budget)


def validation_indices(rows, limit):
    """A fixed development sample; final-test rows never enter the trainer."""
    if limit is None: return list(range(len(rows)))
    if type(limit) is not int or limit < 1: raise ValueError('validation_max_examples must be positive')
    return sorted(sorted(range(len(rows)), key=lambda i: hashlib.sha256(
        json.dumps(rows[i], sort_keys=True, ensure_ascii=False).encode()).hexdigest())[:limit])


def training_identity(dataset, config):
    manifest = json.loads((dataset/'manifest.json').read_text())
    return {'dataset_manifest_sha256': hashlib.sha256((dataset/'manifest.json').read_bytes()).hexdigest(),
            'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            'trainer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'train_sha256': manifest['files']['train.sft.jsonl']['sha256'],
            'validation_sha256': manifest['files']['validation.sft.jsonl']['sha256']}


def checkpoint_release(checkpoint, output, metadata, prompt):
    """Preserve only public inference files before optimizer checkpoint rotation."""
    output.mkdir(parents=True,exist_ok=True)
    for name in ('adapter_config.json','adapter_model.safetensors'):
        shutil.copyfile(checkpoint/name,output/name)
    (output/'system-prompt.txt').write_text(prompt)
    (output/'checkpoint.json').write_text(json.dumps(metadata,indent=2)+'\n')
    (output/'README.md').write_text(
        f'---\nbase_model: {metadata["config"]["model"]}\nlibrary_name: peft\npipeline_tag: text-generation\n'
        'license: apache-2.0\nlanguage: [en]\n---\n\n'
        f'# MTG Oracle — {metadata["phase"]}, step {metadata["step"]}\n\n'
        f'Research QLoRA adapter; epoch {metadata["epoch"]:.4f}. This is a checkpoint, '
        'not a claim that training or evaluation is complete. No final-test accuracy is claimed.\n\n'
        f'Dataset: {metadata["dataset_reference"]}. Exact base revision: `{metadata["config"]["model_revision"]}`. '
        f'This snapshot is tagged `{metadata["tag"]}`. Pin that tag/commit when downloading. '
        'Download the base separately; load with Transformers/PEFT, apply `system-prompt.txt`, '
        'and disable thinking. See `checkpoint.json` for the recipe and data/code identities.\n\n'
        'Base attribution: Qwen team, Apache 2.0. The dataset includes third-party Magic: The Gathering '
        'card content; retain its DATA_LICENSE.md and Wizards of the Coast/Scryfall/MTGJSON attribution. '
        'No artwork, optimizer state, credentials, or worker logs are included.\n')
    return output


def publish_checkpoint(directory,repo_id,tag):
    from huggingface_hub import HfApi
    api=HfApi()
    names=['adapter_config.json','adapter_model.safetensors','system-prompt.txt','checkpoint.json','README.md']
    existing=next((r for r in api.list_repo_refs(repo_id,repo_type='model').tags if r.name==tag),None)
    if existing:
        files={f.rfilename:f for f in api.repo_info(repo_id,repo_type='model',revision=existing.target_commit,files_metadata=True).siblings}
        for name in names:
            raw=(directory/name).read_bytes();remote=files.get(name)
            expected=hashlib.sha256(raw).hexdigest() if remote and remote.lfs else hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            actual=(remote.lfs.sha256 if remote.lfs else remote.blob_id) if remote else None
            if actual!=expected: raise ValueError('Refusing to replace an existing checkpoint tag: '+tag)
        return {'repo_id':repo_id,'revision':existing.target_commit,'tag':tag,
                'url':f'https://huggingface.co/{repo_id}/tree/{tag}'}
    receipt=api.upload_folder(repo_id=repo_id,repo_type='model',folder_path=directory,
        allow_patterns=names,
        commit_message='Publish '+tag)
    api.create_tag(repo_id=repo_id,repo_type='model',tag=tag,revision=receipt.oid)
    return {'repo_id':repo_id,'revision':receipt.oid,'tag':tag,'url':receipt.commit_url}


def generate_comparison(model, tokenizer, rows, output, adapter_enabled, max_new_tokens=400, compute_dtype=None):
    import torch
    model.eval()
    values = []
    context = nullcontext() if adapter_enabled else model.disable_adapter()
    # PEFT's k-bit preparation keeps some layers in FP32. Generation runs
    # outside Trainer's autocast context, so enter it explicitly just as the
    # training/evaluation forwards do; otherwise BF16 activations can reach an
    # FP32 output head (or vice versa).
    amp = torch.autocast(device_type=model.device.type, dtype=compute_dtype) if compute_dtype is not None else nullcontext()
    with context, torch.inference_mode(), amp:
        for row in rows:
            prompt = tokenizer.apply_chat_template(row['prompt'], tokenize=False, add_generation_prompt=True,
                                                   enable_thinking=False)
            batch = tokenizer(prompt, return_tensors='pt', add_special_tokens=False).to(model.device)
            started = time.monotonic()
            generated = model.generate(**batch, max_new_tokens=max_new_tokens, do_sample=False,
                                       use_cache=True, pad_token_id=tokenizer.eos_token_id)
            completion = generated[0, batch['input_ids'].shape[1]:].tolist()
            raw = tokenizer.decode(completion, skip_special_tokens=True)
            expected = json.loads(row['completion'][0]['content'])
            try:
                actual = json.loads(raw)
                valid_json = isinstance(actual, dict)
            except json.JSONDecodeError:
                actual, valid_json = None, False
            values.append({'description': row['prompt'][-1]['content'], 'raw_text': raw,
                           'expected': expected, 'intent': actual, 'json_object': valid_json,
                           'exact_object_match': actual == expected,
                           'output_tokens': len(completion),
                           'hit_token_limit': len(completion) >= max_new_tokens and completion[-1] != tokenizer.eos_token_id,
                           'generation_seconds': time.monotonic()-started})
    output.write_text(json.dumps(values, indent=2, ensure_ascii=False)+'\n')
    model.train()
    model.config.use_cache = False
    return {'count': len(values), 'json_objects': sum(v['json_object'] for v in values),
            'exact_object_matches': sum(v['exact_object_match'] for v in values),
            'note': 'Unconstrained generation, base NF4 versus the same NF4 model plus adapter. Exact match is not semantic accuracy.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('/tmp/card-intent-adapter'))
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--max-steps', type=int, default=20, help='-1 uses configured epochs')
    parser.add_argument('--generation-cases', type=int, default=16)
    parser.add_argument('--resume', type=Path, help='Trainer checkpoint to resume, never a bare adapter directory')
    parser.add_argument('--check-environment', action='store_true', help='Verify pinned imports without loading weights')
    parser.add_argument('--training-seconds', type=int, help='Stop after this training-loop budget; final evaluation/save still run')
    parser.add_argument('--publish-repo',help='Explicit public model repo for inference-only checkpoint snapshots')
    parser.add_argument('--publish-phase',choices=['smoke','train'],default='train')
    parser.add_argument('--public-dataset-reference',help='Pinned public dataset repo@commit')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    validate_config(config, args.max_steps)
    if args.publish_repo and not args.public_dataset_reference: raise ValueError('Public checkpoints require a pinned dataset reference')
    if args.generation_cases < 1:
        raise ValueError('At least one paired generation case is required')
    checked = preflight(args.dataset, config)
    print(json.dumps({'preflight': checked}), flush=True)
    if args.preflight:
        return
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback
    from trl import SFTConfig, SFTTrainer
    if args.check_environment:
        import importlib.metadata
        print(json.dumps({'dependencies': {p: importlib.metadata.version(p) for p in
                         ('torch','transformers','trl','peft','accelerate','datasets','bitsandbytes','huggingface-hub')},
                          'cuda_available': torch.cuda.is_available()}))
        return
    if not torch.cuda.is_available():
        raise RuntimeError('This training recipe requires a CUDA GPU; use --preflight on the CPU host.')
    started = time.monotonic()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Output directory is not empty; choose a fresh run directory')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.resume and not (args.resume/'trainer_state.json').is_file():
        raise ValueError('Resume path must contain trainer_state.json')
    initial_steps = json.loads((args.resume/'trainer_state.json').read_text())['global_step'] if args.resume else 0
    run_identity = training_identity(args.dataset, config)
    run_identity['max_steps'] = args.max_steps
    if args.resume:
        if json.loads((args.resume/'training-identity.json').read_text()) != run_identity:
            raise ValueError('Resume checkpoint belongs to another dataset or training config')
    (args.output/'training-identity.json').write_text(json.dumps(run_identity, indent=2)+'\n')
    tokenizer = AutoTokenizer.from_pretrained(config['model'], revision=config['model_revision'])
    data = load_dataset('json', data_files={s: str(args.dataset/(s+'.sft.jsonl')) for s in ('train', 'validation')})
    validation_total = len(data['validation'])
    selected_validation = validation_indices(data['validation'], config.get('validation_max_examples'))
    data['validation'] = data['validation'].select(selected_validation)
    validation_sample = {'available': validation_total, 'evaluated': len(selected_validation),
                         'indices_sha256': hashlib.sha256(json.dumps(selected_validation).encode()).hexdigest(),
                         'policy': 'fixed SHA-256 rank of complete validation records; no test data'}
    tokenized = data.map(lambda row: tokenize_supervised(row, tokenizer, config['max_length']))
    token_counts = [len(row['input_ids']) for row in tokenized['train']]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type=config['quant_type'],
                                      bnb_4bit_use_double_quant=config['double_quant'], bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(config['model'], revision=config['model_revision'],
                                                quantization_config=quantization, torch_dtype=dtype,
                                                device_map={'': torch.cuda.current_device()}, attn_implementation='sdpa')
    model.config.use_cache = False
    peft = LoraConfig(r=config['lora_r'], lora_alpha=config['lora_alpha'], lora_dropout=config['lora_dropout'],
                      target_modules=config['target_modules'], task_type='CAUSAL_LM', bias='none')
    training = SFTConfig(output_dir=str(args.output), max_steps=args.max_steps, num_train_epochs=config['epochs'],
                         max_length=config['max_length'], completion_only_loss=True, packing=False,
                         per_device_train_batch_size=config['per_device_train_batch_size'],
                         per_device_eval_batch_size=config.get('per_device_eval_batch_size',1), gradient_accumulation_steps=config['gradient_accumulation_steps'],
                         gradient_checkpointing=config.get('gradient_checkpointing',True), learning_rate=config['learning_rate'], warmup_ratio=config['warmup_ratio'],
                         bf16=dtype==torch.bfloat16, fp16=dtype==torch.float16,
                         seed=config['seed'], data_seed=config['seed'], logging_steps=1,
                         save_strategy='steps', save_steps=config.get('save_steps',10),
                         eval_strategy='steps' if config.get('eval_steps') else 'epoch',
                         eval_steps=config.get('eval_steps'), save_total_limit=2,
                         group_by_length=config.get('group_by_length',False),
                         report_to='none', optim='adamw_torch', eos_token=config.get('eos_token',tokenizer.eos_token))
    trainer = SFTTrainer(model=model, processing_class=tokenizer, args=training, peft_config=peft,
                          train_dataset=tokenized['train'], eval_dataset=tokenized['validation'])
    class TimeBudget(TrainerCallback):
        stopped = False
        def on_train_begin(self, args, state, control, **kwargs):
            self.started = time.monotonic()
        def on_step_end(self, training_args, state, control, **kwargs):
            if state.global_step < state.max_steps and training_budget_exhausted(time.monotonic()-self.started,
                                         state.global_step-initial_steps,args.training_seconds):
                self.stopped = True
                control.should_training_stop = True
                control.should_save = True
            return control
        def on_save(self, training_args, state, control, **kwargs):
            checkpoint=Path(training_args.output_dir)/f'checkpoint-{state.global_step}'
            (checkpoint/'training-identity.json').write_text(json.dumps(run_identity,indent=2)+'\n')
            if args.publish_repo:
                tag=f'{args.publish_phase}-step-{state.global_step:08d}'
                destination=args.output/'public-checkpoints'/tag
                public=checkpoint_release(checkpoint,destination,{'phase':args.publish_phase,'step':state.global_step,
                    'epoch':state.epoch,'tag':tag,'config':config,'training_identity':run_identity,
                    'dataset_reference':args.public_dataset_reference,'final_test_evaluated':False},
                    data['train'][0]['prompt'][0]['content'])
                try:
                    receipt=publish_checkpoint(public,args.publish_repo,tag)
                    (destination/'publication.json').write_text(json.dumps(receipt,indent=2)+'\n')
                except Exception as error:
                    # Preserve every adapter even if Hub upload fails; private checkpoint rotation may continue.
                    (destination/'publication-error.json').write_text(json.dumps({'error_type':type(error).__name__,
                        'retry_required':True,'repo_id':args.publish_repo,'tag':tag},indent=2)+'\n')
                    print(json.dumps({'checkpoint_publication':'deferred','tag':tag,'error_type':type(error).__name__}),flush=True)
    budget_callback=TimeBudget()
    trainer.add_callback(budget_callback)
    verify_completion_masks(tokenizer, trainer.train_dataset, data['train'])
    verify_completion_masks(tokenizer, trainer.eval_dataset, data['validation'])
    # Also verify the actual collator preserves the prompt exclusion.
    batch = next(iter(trainer.get_train_dataloader()))
    labels = batch['labels'][0]
    active = labels[labels != -100]
    if not len(active) or not (labels == -100).any():
        raise ValueError('Completion-only labels are missing or prompt tokens are supervised')
    decoded = tokenizer.decode(active.tolist(), skip_special_tokens=True).strip()
    json.loads(decoded)  # supervised tokens must be exactly the assistant JSON
    sample = sorted(data['validation'], key=lambda r: hashlib.sha256(r['prompt'][-1]['content'].encode()).hexdigest())[:args.generation_cases]
    generation_before = generate_comparison(trainer.model, tokenizer, sample, args.output/'base-generations.json', False, compute_dtype=dtype)
    base_metrics = trainer.evaluate()
    (args.output/'pre-training.json').write_text(json.dumps({'config': config, 'dataset': checked,
        'training_identity': run_identity, 'validation_sample': validation_sample,
        'loss_masks_checked': len(data['train'])+len(data['validation']), 'base_metrics': base_metrics,
        'base_generation': generation_before, 'resumed_from': str(args.resume) if args.resume else None}, indent=2)+'\n')
    setup_seconds = time.monotonic() - started
    torch.cuda.reset_peak_memory_stats()
    before = time.monotonic()
    run = trainer.train(resume_from_checkpoint=str(args.resume) if args.resume else None)
    training_seconds = time.monotonic() - before
    new_steps = trainer.state.global_step - initial_steps
    if new_steps < 1:
        raise ValueError('Training performed no new optimizer steps')
    trainer.save_model(str(args.output))
    trainer.save_state()
    tokenizer.save_pretrained(args.output)
    final_metrics = trainer.evaluate()
    generation_after = generate_comparison(trainer.model, tokenizer, sample, args.output/'adapter-generations.json', True, compute_dtype=dtype)
    if not math.isfinite(run.metrics['train_loss']) or not math.isfinite(final_metrics['eval_loss']):
        raise ValueError('Non-finite training or evaluation loss')
    summary = {'config': config, 'max_steps': args.max_steps, 'dataset': checked,
               'model_revision': config['model_revision'], 'gpu': torch.cuda.get_device_name(),
               'setup_seconds': setup_seconds, 'training_seconds': training_seconds,
               'peak_allocated_gib': torch.cuda.max_memory_allocated()/1024**3,
               'train_tokens_per_epoch': sum(token_counts), 'max_sequence_tokens': max(token_counts),
               'optimizer_steps': trainer.state.global_step,
               'new_optimizer_steps': new_steps,
               'training_budget_seconds': args.training_seconds,
               'stopped_for_training_budget': budget_callback.stopped,
               'seconds_per_optimizer_step': training_seconds/new_steps,
               'metrics': run.metrics, 'base_eval': base_metrics, 'adapter_eval': final_metrics,
               'base_generation': generation_before, 'adapter_generation': generation_after,
               'loss_mask_verified': True, 'loss_masks_checked': len(data['train'])+len(data['validation']),
               'resumed_from': str(args.resume) if args.resume else None,
               'training_identity': run_identity, 'validation_sample': validation_sample,
               'deployment_status': 'Research adapter; not merged, quantized or deployed.'}
    (args.output/'run-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
