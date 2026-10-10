"""Audit the exact assets to publish to R2, optionally writing an upload list."""
import argparse
import hashlib
import json
from pathlib import Path

LIMIT = 512_000_000


def digest(path, combined=None):
    result = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            result.update(block)
            if combined is not None:
                combined.update(block)
    return result.hexdigest()


def audit(root, hashes=False):
    records = []

    def add(path, size=None, sha=None):
        target = root / path
        actual = target.stat().st_size
        if actual >= LIMIT or (size is not None and actual != size):
            raise ValueError(f'Asset size check failed: {path}: {actual}')
        if hashes and sha and digest(target) != sha:
            raise ValueError(f'Asset checksum failed: {path}')
        records.append({'path': str(path), 'bytes': actual, **({'sha256': sha} if sha else {})})

    add(Path('model.json'))
    add(Path('system-prompt.txt'))
    model = json.loads((root / 'model.json').read_text())
    model_dir = Path(model['first_shard']).parent
    for shard in model['shards']:
        add(model_dir / shard['name'], shard['bytes'], shard['sha256'])
    image_dir = Path('image-models/bonsai-ternary-2c24c81')
    add(image_dir / 'transport.json')
    manifest = json.loads((root / image_dir / 'transport.json').read_text())
    for asset in manifest['assets']:
        add(image_dir / asset['path'], asset['bytes'], asset['sha256'])
    if hashes:
        for file in manifest['files']:
            combined = hashlib.sha256()
            for chunk in file['chunks']:
                digest(root / image_dir / chunk['path'], combined)
            if combined.hexdigest() != file['sha256']:
                raise ValueError(f'Reassembled checksum failed: {file["path"]}')
    return {'max_asset_bytes_exclusive': LIMIT, 'count': len(records),
            'largest_bytes': max(r['bytes'] for r in records), 'hashes_verified': hashes,
            'assets': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Served model-bench directory')
    parser.add_argument('--hashes', action='store_true', help='Verify every hash, including reconstructed source weights')
    parser.add_argument('--output', type=Path, help='Write exact R2 upload list; excludes old oversized originals')
    args = parser.parse_args()
    result = audit(args.root, args.hashes)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'assets'}))
