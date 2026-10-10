"""Create CDN-sized transport chunks without converting or requantizing weights."""
import argparse
import hashlib
import json
from pathlib import Path

MAX_BYTES = 512_000_000  # Strict decimal MB; stay below either interpretation.
CHUNK_BYTES = 256 * 1024 * 1024


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def prepare(root):
    upstream = json.loads((root / 'manifest.json').read_text())
    mapped, assets = [], []
    for entry in upstream['files']:
        relative = Path(entry['remote_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe upstream path')
        if relative.as_posix() == 'README.md':
            continue  # Upstream manifest's README hash is stale; not used for inference.
        source = root / relative
        if source.stat().st_size != entry['size'] or sha(source) != entry['sha256']:
            raise ValueError(f'Upstream integrity check failed: {relative}')
        if entry['size'] < MAX_BYTES:
            assets.append({'path': relative.as_posix(), 'bytes': entry['size'], 'sha256': entry['sha256']})
            continue
        chunks = []
        with source.open('rb') as stream:
            offset = 0
            while offset < entry['size']:
                path = Path('chunks') / entry['sha256'] / f'{len(chunks) + 1:05}.bin'
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                size = min(CHUNK_BYTES, entry['size'] - offset)
                temporary = target.with_suffix('.part')
                digest = hashlib.sha256()
                with temporary.open('wb') as output:
                    remaining = size
                    while remaining:
                        block = stream.read(min(8 * 1024 * 1024, remaining))
                        if not block:
                            raise ValueError('Unexpected end of model')
                        output.write(block)
                        digest.update(block)
                        remaining -= len(block)
                temporary.replace(target)
                record = {'path': path.as_posix(), 'bytes': size, 'sha256': digest.hexdigest()}
                chunks.append({**record, 'offset': offset})
                assets.append(record)
                offset += size
        mapped.append({'path': relative.as_posix(), 'bytes': entry['size'], 'sha256': entry['sha256'], 'chunks': chunks})
        print(f'{relative}: {len(chunks)} chunks, at most {CHUNK_BYTES:,} bytes', flush=True)
    manifest = {'version': 1, 'max_asset_bytes_exclusive': MAX_BYTES, 'chunk_bytes': CHUNK_BYTES,
                'files': mapped, 'assets': assets}
    temporary = root / 'transport.json.part'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(root / 'transport.json')
    print(f'{len(assets)} publishable assets verified; original oversized weights retained for old clients.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    prepare(parser.parse_args().root)
