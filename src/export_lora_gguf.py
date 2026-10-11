"""Export one cumulative LoRA against a previously pinned browser base.

Reuses the base from serving_adapter_export.py; never merges or requantizes it.
This produces release artifacts only, without changing a deployment.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from rl_gpu import save, sha


def export_adapter(adapter, base, base_gguf, base_receipt, converter, output, python=sys.executable, system_prompt=None):
    system_prompt = system_prompt or adapter/'system-prompt.txt'
    prompt_hash = sha(system_prompt)
    pinned = json.loads(base_receipt.read_text())
    base_hash = sha(base_gguf)
    if base_gguf.name != 'base-Q4_K_M.gguf' or pinned['files'].get(base_gguf.name) != base_hash:
        raise ValueError('Quantized base differs from pinned original export')
    base_files = pinned['inputs']['base_files']
    if not base_files or any(Path(name).name != name or sha(base/name) != pin for name, pin in base_files.items()):
        raise ValueError('Original base files differ from pinned export')
    config = json.loads((adapter/'adapter_config.json').read_text())
    if config.get('peft_type') != 'LORA' or config.get('modules_to_save') or config.get('use_dora'):
        raise ValueError('This release path requires a standard cumulative LoRA')
    converter_hash = sha(converter)
    if converter_hash != pinned['inputs']['adapter_converter_sha256']:
        raise ValueError('Adapter converter changed; revalidate it before release')
    inputs = {'base_files': base_files, 'adapter_converter_sha256': converter_hash,
              'base_export_receipt_sha256': sha(base_receipt),
              'adapters': {'candidate': {name: sha(adapter/name) for name in
                  ('adapter_config.json', 'adapter_model.safetensors')}},
              'system_prompt_sha256': prompt_hash,
              'recipe': 'Reuse pinned original Q4_K_M base; cumulative adapter -> separate F16 GGUF'}
    output.mkdir(parents=True, exist_ok=True)
    destination = output/'adapter-F16.gguf'
    receipt = output/'exports.json'
    if receipt.exists():
        old = json.loads(receipt.read_text())
        if old['inputs'] != inputs or old['files'].get(base_gguf.name) != base_hash or old['files'].get(destination.name) != sha(destination) or sha(output/'system-prompt.txt') != prompt_hash:
            raise ValueError('Existing adapter release changed')
        return old
    pending = destination.with_suffix('.pending.gguf')
    with (output/'export.log').open('w') as log:
        subprocess.run([python, str(converter.resolve()), str(adapter.resolve()), '--base', str(base.resolve()),
                        '--outfile', str(pending.resolve()), '--outtype', 'f16'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    if not 0 < pending.stat().st_size < 512_000_000:
        raise ValueError('Adapter exceeds browser asset budget')
    pending.replace(destination)
    (output/'system-prompt.txt').write_bytes(system_prompt.read_bytes())
    if sha(output/'system-prompt.txt') != prompt_hash:
        raise ValueError('Prompt changed during export')
    value = {'inputs': inputs, 'files': {base_gguf.name: base_hash, destination.name: sha(destination)},
             'bytes': {base_gguf.name: base_gguf.stat().st_size, destination.name: destination.stat().st_size},
             'automatic_promotion': False}
    # The completion receipt is written last, after conversion and hashing succeed.
    temporary = receipt.with_suffix('.pending.json')
    save(temporary, value)
    temporary.replace(receipt)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('adapter', 'base', 'base-gguf', 'base-receipt', 'converter', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--system-prompt', type=Path, help='Defaults to adapter/system-prompt.txt')
    args = parser.parse_args()
    result = export_adapter(**vars(args))
    print(json.dumps({'bytes': result['bytes'], 'automatic_promotion': False}))
