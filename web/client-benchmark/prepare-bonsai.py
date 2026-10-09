"""Download and SHA-256 verify the pinned, public Bonsai ternary model bundle."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = 'prism-ml/bonsai-image-ternary-4B-mlx-2bit'
REVISION = '2c24c81b934a658ba5590cf39088ba929985b4a8'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('dist/image-models/bonsai-ternary-2c24c81'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
base = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/'
with urllib.request.urlopen(base + 'manifest.json', timeout=60) as response:
    manifest_bytes = response.read()
manifest = json.loads(manifest_bytes)
# This revision's README was edited after its manifest hash was recorded.
# It is not an inference asset; omit it instead of weakening weight verification.
files = [entry for entry in manifest['files'] if entry['remote_path'] != 'README.md']


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(entry):
    relative = Path(entry['remote_path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe model manifest path')
    target = args.output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == entry['size'] and sha(target) == entry['sha256']:
        return f'Verified cached {relative}'
    temporary = target.with_name(target.name + '.part')
    digest = hashlib.sha256()
    with urllib.request.urlopen(base + relative.as_posix(), timeout=120) as source, temporary.open('wb') as dest:
        while chunk := source.read(8 * 1024 * 1024):
            dest.write(chunk)
            digest.update(chunk)
    if temporary.stat().st_size != entry['size'] or digest.hexdigest() != entry['sha256']:
        raise ValueError(f'Model verification failed: {relative}')
    temporary.replace(target)
    return f'Downloaded and verified {relative} ({entry["size"]:,} bytes)'


with ThreadPoolExecutor(max_workers=3) as pool:
    for message in pool.map(download, files):
        print(message, flush=True)
(args.output / 'manifest.json').write_bytes(manifest_bytes)
print(f'Complete: {MODEL}@{REVISION}, {sum(entry["size"] for entry in files):,} verified bytes (README omitted)', flush=True)
