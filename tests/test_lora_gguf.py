import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from export_lora_gguf import export_adapter
from rl_gpu import save, sha


class AdapterExportTests(unittest.TestCase):
    def test_reuse_and_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root/'base'; base.mkdir()
            (base/'config.json').write_text('{}')
            (base/'weights.safetensors').write_bytes(b'original base')
            quant = root/'base-Q4_K_M.gguf'; quant.write_bytes(b'base quant')
            converter = root/'convert.py'; converter.write_text('# pinned')
            adapter = root/'adapter'; adapter.mkdir()
            save(adapter/'adapter_config.json', {'peft_type': 'LORA'})
            (adapter/'adapter_model.safetensors').write_bytes(b'adapter')
            (adapter/'system-prompt.txt').write_text('system')
            receipt = root/'base-receipt.json'
            save(receipt, {'files': {quant.name: sha(quant)}, 'inputs': {
                'base_files': {p.name: sha(p) for p in base.iterdir()}, 'adapter_converter_sha256': sha(converter)}})
            output = root/'output'
            def convert(command, **_):
                self.assertEqual(command[-2:], ['--outtype', 'f16'])
                Path(command[command.index('--outfile')+1]).write_bytes(b'GGUF adapter')
            with patch('export_lora_gguf.subprocess.run', side_effect=convert) as runner:
                first = export_adapter(adapter, base, quant, receipt, converter, output)
                self.assertEqual(first, export_adapter(adapter, base, quant, receipt, converter, output))
                self.assertEqual(runner.call_count, 1)
                self.assertFalse(first['automatic_promotion'])
                self.assertEqual(first['files'][quant.name], sha(quant))
                (adapter/'adapter_model.safetensors').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'release changed'):
                    export_adapter(adapter, base, quant, receipt, converter, output)
                quant.write_bytes(b'different quant')
                with self.assertRaisesRegex(ValueError, 'Quantized base differs'):
                    export_adapter(adapter, base, quant, receipt, converter, output)

    def test_failed_conversion_has_no_completion_receipt(self):
        # Covered independently of real torch/llama.cpp; a failed subprocess must
        # never masquerade as an export that the release packager can consume.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); base = root/'base'; base.mkdir()
            (base/'config.json').write_text('{}')
            quant = root/'base-Q4_K_M.gguf'; quant.write_bytes(b'quant')
            converter = root/'convert.py'; converter.write_text('# pinned')
            adapter = root/'adapter'; adapter.mkdir()
            save(adapter/'adapter_config.json', {'peft_type': 'LORA'})
            (adapter/'adapter_model.safetensors').write_bytes(b'adapter')
            (adapter/'system-prompt.txt').write_text('system')
            receipt = root/'receipt.json'
            save(receipt, {'files': {quant.name: sha(quant)}, 'inputs': {
                'base_files': {'config.json': sha(base/'config.json')}, 'adapter_converter_sha256': sha(converter)}})
            with patch('export_lora_gguf.subprocess.run', side_effect=RuntimeError('conversion failed')):
                with self.assertRaisesRegex(RuntimeError, 'conversion failed'):
                    export_adapter(adapter, base, quant, receipt, converter, root/'output')
            self.assertFalse((root/'output/exports.json').exists())
