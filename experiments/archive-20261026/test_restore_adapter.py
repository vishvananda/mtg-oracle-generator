import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('restore_adapter', Path(__file__).with_name('restore_adapter.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestoreChecks(unittest.TestCase):
    def test_verify_and_place_receipts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); payload = root/'payload'; payload.write_bytes(b'correct bytes')
            expected = {'bytes': payload.stat().st_size, 'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}
            manifest = {'hugging_face_repository': 'owner/archive', 'base_model': 'base', 'base_revision': 'base-pin',
                        'adapters': {'candidate': {'hugging_face_revision': 'a'*40,
                        'files': {'adapter_model.safetensors': expected, 'run/identity.json': expected}}}}
            path = root/'manifest.json'; path.write_text(json.dumps(manifest)); calls = []
            def download(**kwargs): calls.append(kwargs); return payload
            output = root/'restored'; module.restore(path, 'candidate', output, download)
            self.assertEqual((output/'adapter/adapter_model.safetensors').read_bytes(), b'correct bytes')
            self.assertEqual((output/'identity.json').read_bytes(), b'correct bytes')
            self.assertTrue(all(c['revision'] == 'a'*40 for c in calls))
            with self.assertRaises(ValueError): module.restore(path, 'candidate', output, download)

    def test_corruption_leaves_no_completed_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); payload = root/'payload'; payload.write_bytes(b'bad')
            manifest = {'adapters': {'candidate': {'hugging_face_revision': 'b'*40,
                        'files': {'adapter_model.safetensors': {'bytes': 3, 'sha256': '0'*64}}}},
                        'hugging_face_repository': 'owner/archive'}
            path = root/'manifest.json'; path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                module.restore(path, 'candidate', root/'restored', lambda **_: payload)
            self.assertFalse((root/'restored').exists())
            self.assertEqual(list(root.glob('.restore-*')), [])

    def test_paths_and_unpinned_revisions_rejected(self):
        for path in ['../escape', '/absolute', 'run/../../escape', 'run\\escape']:
            with self.assertRaises(ValueError): module.safe_relative(path)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root/'manifest.json'
            path.write_text(json.dumps({'adapters': {'candidate': {'hugging_face_revision': 'main', 'files': {}}}}))
            with self.assertRaisesRegex(ValueError, 'immutable'):
                module.restore(path, 'candidate', root/'restored', lambda **_: self.fail('must not download'))


if __name__ == '__main__': unittest.main()
