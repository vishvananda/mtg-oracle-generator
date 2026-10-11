"""Export a pinned base and separate F16 adapters without merging them."""
import argparse
import json
from pathlib import Path
import subprocess

from rl_gpu import save, sha
from serving_quantization import quantize


def prepare(root):
    spec = json.loads((root/'export.json').read_text())
    experiment = json.loads((root/'experiment.json').read_text())
    base = Path(spec['base']); models = root/'models'; models.mkdir(exist_ok=True)
    identity = {'base_files': {p.name: sha(p) for p in sorted(base.iterdir()) if p.is_file()},
        'converter_sha256': sha(Path(spec['converter'])),
        'adapter_converter_sha256': sha(Path(spec['adapter_converter'])),
        'adapters': {arm: {name: sha(Path(folder)/name) for name in
            ('adapter_config.json', 'adapter_model.safetensors')} for arm, folder in spec['adapters'].items()},
        'recipe': 'Pinned BF16 base -> F16 GGUF -> Q4_K_M; cumulative adapters -> separate F16 GGUF'}
    receipt = root/'exports.json'
    if receipt.exists():
        old = json.loads(receipt.read_text())
        if old['inputs'] != identity or any(sha(models/name) != pin for name, pin in old['files'].items()):
            raise ValueError('Adapter export identity changed')
        return old
    f16 = models/'base-F16.gguf'
    def convert(command, destination):
        pending = destination.with_suffix('.pending.gguf')
        with destination.with_suffix('.log').open('w') as log:
            subprocess.run([spec['python'], *command, '--outfile', str(pending), '--outtype', 'f16'],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        pending.rename(destination)
    convert([spec['converter'], str(base)], f16)
    quantize(f16, models/'base-Q4_K_M.gguf', 'Q4_K_M', Path(experiment['quantizer']))
    for arm, folder in spec['adapters'].items():
        convert([spec['adapter_converter'], folder, '--base', str(base)], models/(arm+'-adapter-F16.gguf'))
    value = {'inputs': identity, 'files': {p.name: sha(p) for p in models.glob('*.gguf')},
             'bytes': {p.name: p.stat().st_size for p in models.glob('*.gguf')}, 'automatic_promotion': False}
    save(receipt, value)
    print(json.dumps({'sizes': value['bytes']}), flush=True)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    prepare(parser.parse_args().root)
