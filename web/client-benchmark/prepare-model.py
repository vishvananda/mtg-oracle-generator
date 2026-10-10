"""Stage a merged GGUF and system prompt for the static browser benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gguf', type=Path, required=True)
parser.add_argument('--system-prompt', type=Path, required=True)
parser.add_argument('--splitter', type=Path, required=True, help='llama-gguf-split executable')
parser.add_argument('--output', type=Path, default=Path('dist'))
parser.add_argument('--label', default='Qwen3 4B · SFT + DPO round two · Q4_0')
args = parser.parse_args()
source_hash = sha(args.gguf)
relative = Path('models') / source_hash[:8]
directory = args.output / relative
if directory.exists():
    parser.error('Use a fresh model output directory')
directory.mkdir(parents=True)
subprocess.run([str(args.splitter.resolve()), '--split-max-size', '480M',
                str(args.gguf.resolve()), str((directory / 'oracle-q4').resolve())], check=True)
shards = sorted(directory.glob('*.gguf'))
if not shards:
    raise ValueError('Splitter produced no model files')
if any(p.stat().st_size >= 512_000_000 for p in shards):
    raise ValueError('A model shard exceeds the strict Cloudflare cache size budget')
shutil.copyfile(args.system_prompt, args.output / 'system-prompt.txt')
manifest = {'label': args.label, 'runtime': '3.8.1', 'source_sha256': source_hash,
            'source_bytes': args.gguf.stat().st_size,
            'first_shard': str(relative / shards[0].name),
            'system_prompt_sha256': sha(args.output / 'system-prompt.txt'),
            'shards': [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in shards]}
(args.output / 'model.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'Staged {len(shards)} shards; source SHA-256 {source_hash}')
