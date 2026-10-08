"""CPU-only, reusable tokenization with exact data/tokenizer/mask provenance.

Requires datasets==4.1.1 and transformers==4.56.2, not PyTorch. Only training
and the fixed monitoring-validation sample enter the archive; never final test.
"""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

from train_qlora import preflight, tokenize_supervised, verify_completion_masks, validation_indices, training_splits, monitoring_data

MANIFEST = 'prepared-training.json'
ARCHIVE = 'prepared-training.tar.gz'


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(dataset, config):
    return {
        'format_version': 1,
        'dataset_manifest_sha256': file_hash(dataset/'manifest.json'),
        'model': config['model'], 'model_revision': config['model_revision'],
        'max_length': config['max_length'],
        'validation_max_examples': config.get('validation_max_examples'),
        'monitoring_split': config.get('monitoring_split','validation'),
        'preprocessor_sha256': file_hash(Path(__file__)),
        'semantics_sha256': hashlib.sha256(''.join(inspect.getsource(f) for f in
            (preflight, tokenize_supervised, verify_completion_masks, validation_indices, training_splits, monitoring_data)).encode()).hexdigest(),
        'versions': {p: importlib.metadata.version(p) for p in ('datasets', 'transformers', 'tokenizers')},
    }


def tokenize_batch(batch, tokenizer, max_length):
    prompts, completions = batch['prompt'], batch['completion']
    prefixes = tokenizer.apply_chat_template(prompts, tokenize=True, add_generation_prompt=True, enable_thinking=False)
    full = tokenizer.apply_chat_template([p+c for p, c in zip(prompts, completions)],
        tokenize=True, add_generation_prompt=False, enable_thinking=False)
    masks = []
    for prefix, ids in zip(prefixes, full):
        if ids[:len(prefix)] != prefix:
            raise ValueError('Chat template prompt is not an exact prefix of the completed conversation')
        if len(ids) > max_length:
            raise ValueError('Example exceeds max_length; refusing silent truncation')
        masks.append([0]*len(prefix)+[1]*(len(ids)-len(prefix)))
    # Check every target on the CPU before caching. GPU jobs check artifact hashes
    # and the actual collator instead of decoding the entire corpus again.
    verify_completion_masks(tokenizer,
        [{'input_ids': ids, 'completion_mask': mask} for ids, mask in zip(full, masks)],
        [{'completion': completion} for completion in completions])
    return {'input_ids': full, 'completion_mask': masks, 'length': [len(ids) for ids in full]}


def inspect_prepared(root, dataset, config):
    metadata = json.loads((root/MANIFEST).read_text())
    if metadata['identity'] != identity(dataset, config):
        raise ValueError('Prepared training data/tokenizer identity changed')
    if metadata['archive_sha256'] != file_hash(root/ARCHIVE):
        raise ValueError('Prepared training archive hash mismatch')
    if metadata.get('loss_mask_verified') is not True:
        raise ValueError('Prepared completion masks were not verified')
    if set(metadata['counts']) != {'train', 'validation'}:
        raise ValueError('Unexpected prepared dataset splits')
    return metadata


def prepare(dataset, config, output, workers=8, batch_size=128, tokenizer=None):
    from datasets import load_dataset
    from transformers import AutoTokenizer
    if workers < 1 or batch_size < 1: raise ValueError('Positive worker and batch counts required')
    if output.exists():
        return inspect_prepared(output, dataset, config)
    started = time.monotonic()
    cache_identity = identity(dataset, config)
    checked = preflight(dataset, config)
    tokenizer = tokenizer or AutoTokenizer.from_pretrained(config['model'], revision=config['model_revision'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output.name+'.preparing-', dir=output.parent) as tmp:
        staging = Path(tmp)
        data = load_dataset('json', data_files={s: str(dataset/(s+'.sft.jsonl')) for s in training_splits(config)})
        sample = monitoring_data(data,config)
        encoded = data.map(tokenize_batch, batched=True, batch_size=batch_size, num_proc=workers,
            fn_kwargs={'tokenizer': tokenizer, 'max_length': config['max_length']},
            load_from_cache_file=False, desc='CPU tokenize and verify completion masks')
        # Save only training columns. Keep four raw validation prompts separately
        # for the bounded before/after generation check, avoiding large nested
        # prompt/completion columns in the training dataloader.
        samples = sorted(data['validation'], key=lambda r: hashlib.sha256(r['prompt'][-1]['content'].encode()).hexdigest())[:16]
        prompt = data['train'][0]['prompt'][0]['content']
        lengths = list(encoded['train']['length'])
        for split in encoded:
            encoded[split] = encoded[split].remove_columns(['prompt', 'completion'])
        saved = staging/'dataset'
        encoded.save_to_disk(str(saved))
        files = {str(p.relative_to(saved)): file_hash(p) for p in sorted(saved.rglob('*')) if p.is_file()}
        archive = staging/ARCHIVE
        with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
            for name in files: tar.add(saved/name, arcname=name, recursive=False)
        if identity(dataset, config) != cache_identity:
            raise ValueError('Preparation inputs or code changed during the run; rebuild in a fresh directory')
        metadata = {'identity': cache_identity, 'preflight': checked,
            'counts': {s: len(encoded[s]) for s in encoded}, 'validation_sample': sample,
            'generation_samples': samples, 'system_prompt': prompt,
            'train_tokens_per_epoch': sum(lengths), 'max_sequence_tokens': max(lengths),
            'loss_mask_verified': True, 'loss_masks_checked': sum(len(d) for d in encoded.values()),
            'files': files, 'archive_sha256': file_hash(archive),
            'preparation_seconds': time.monotonic()-started, 'workers': workers, 'batch_size': batch_size}
        (staging/MANIFEST).write_text(json.dumps(metadata, indent=2)+'\n')
        shutil.rmtree(saved)
        # Atomic final directory; interrupted preparation is never reusable.
        staging.rename(output)
    return metadata


def load_prepared(root, dataset, config, destination):
    from datasets import load_from_disk
    metadata = inspect_prepared(root, dataset, config)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(root/ARCHIVE, 'r:gz') as tar:
        seen = set()
        for member in tar.getmembers():
            name = member.name
            if (not member.isfile() or Path(name).is_absolute() or '..' in Path(name).parts
                    or name not in metadata['files'] or name in seen):
                raise ValueError('Unexpected prepared archive member: '+name)
            seen.add(name)
        if seen != set(metadata['files']): raise ValueError('Incomplete prepared archive')
        tar.extractall(destination, filter='data')
    for name, expected in metadata['files'].items():
        if file_hash(destination/name) != expected: raise ValueError('Prepared dataset file hash mismatch: '+name)
    encoded = load_from_disk(str(destination))
    if {s: len(encoded[s]) for s in encoded} != metadata['counts']:
        raise ValueError('Prepared dataset counts changed')
    return encoded, metadata


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, required=True); p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--workers', type=int, default=8)
    p.add_argument('--batch-size', type=int, default=128)
    a = p.parse_args()
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    report = prepare(a.dataset, json.loads(a.config.read_text()), a.output, a.workers, a.batch_size)
    print(json.dumps({k: report[k] for k in ('counts', 'preparation_seconds', 'workers', 'batch_size', 'archive_sha256')}, indent=2))
