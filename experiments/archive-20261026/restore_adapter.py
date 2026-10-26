"""Restore a hash-verified inference run from the immutable research archive."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in value:
        raise ValueError(f'Unsafe archive path: {value}')
    return Path(*path.parts)


def restore(manifest_path, experiment, output, download=None):
    manifest = json.loads(Path(manifest_path).read_text())
    record = manifest['adapters'][experiment]
    revision = record['hugging_face_revision']
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Restoration requires an immutable 40-character HF commit')
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    paths = [(name, safe_relative(name)) for name in record['files']]
    if download is None:
        from huggingface_hub import hf_hub_download
        download = hf_hub_download
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=output.parent) as staging:
        stage = Path(staging)
        for name, relative in paths:
            source = Path(download(
                repo_id=manifest['hugging_face_repository'],
                revision=revision, filename=f'{experiment}/{name}',
            ))
            expected = record['files'][name]
            with source.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if source.stat().st_size != expected['bytes'] or digest != expected['sha256']:
                raise ValueError(f'Archive hash mismatch: {name}')
            # Exporters expect run receipts alongside an adapter/ directory.
            target = stage.joinpath(*relative.parts[1:]) if relative.parts[0] == 'run' else stage/'adapter'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (stage/'archive-reference.json').write_text(json.dumps({
            'repository': manifest['hugging_face_repository'], 'revision': revision,
            'experiment': experiment, 'base_model': manifest['base_model'],
            'base_revision': manifest['base_revision'],
        }, indent=2)+'\n')
        if output.exists():
            raise ValueError('Output appeared during restoration')
        stage.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path(__file__).with_name('archive-manifest.json'))
    args = parser.parse_args()
    print(restore(args.manifest, args.experiment, args.output))
