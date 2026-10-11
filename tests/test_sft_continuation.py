import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from sft_continuation_gpu import stop_reason, verify
from rl_gpu import sha
from export_gguf import check_continuation_export


class ContinuationTests(unittest.TestCase):
    def test_export_requires_complete_review_segment_not_full_epoch(self):
        config = {'model_revision': 'revision', 'stop_after_steps': 4944}
        summary = {'config': config, 'optimizer_steps': 4944, 'planned_steps': 4944,
            'schedule_steps': 19774, 'stop_reason': 'review_checkpoint', 'loss_mask_verified': True,
            'parent_training_identity': {'original': 'pin'}, 'initial_adapter_hash': 'old', 'final_adapter_hash': 'new'}
        identity = {'config': config, 'package_sha256': 'package'}
        receipt = {'training': summary, 'package_sha256': 'package'}
        self.assertEqual(check_continuation_export(summary, identity, receipt), 'completed_sft_review_segment')
        for key, value in [('optimizer_steps', 4900), ('stop_reason', 'time_budget'),
                           ('final_adapter_hash', 'old'), ('loss_mask_verified', False)]:
            bad = {**summary, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_continuation_export(bad, identity, {**receipt, 'training': bad})

    def test_stop_at_review_checkpoint_without_finishing_schedule(self):
        self.assertIsNone(stop_reason(4943, 17000, 4944, 21600))
        self.assertEqual(stop_reason(4944, 17004, 4944, 21600), 'review_checkpoint')
        self.assertEqual(stop_reason(4200, 21600, 4944, 21600), 'time_budget')

    def test_reject_changed_data_or_starting_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package = root/'package'; parent = root/'parent'
            package.mkdir(); parent.mkdir()
            (package/'manifest.json').write_text('{}')
            config = {'model': 'model', 'model_revision': 'revision', 'max_length': 4096,
                'completion_only_loss': True, 'packing': False, 'per_device_train_batch_size': 16,
                'gradient_accumulation_steps': 1, 'epochs': 1, 'monitoring_split': 'train',
                'learning_rate': 3e-5, 'stop_after_steps': 4944, 'schedule_steps': 19774}
            previous = {'config': {**config, 'learning_rate': 1e-4},
                'metrics': {'epoch': 1}, 'stopped_for_training_budget': False, 'optimizer_steps': 19774,
                'training_identity': {'dataset_manifest_sha256': sha(package/'manifest.json')}}
            (parent/'run-summary.json').write_text(json.dumps(previous))
            def freeze():
                (package/'config.json').write_text(json.dumps(config))
                pins = {'files': {n: sha(package/n) for n in ('config.json', 'manifest.json')},
                    'parent_files': {'run-summary.json': sha(parent/'run-summary.json')}}
                (package/'package-manifest.json').write_text(json.dumps(pins))
            freeze(); self.assertEqual(verify(package, parent)[2]['schedule_steps'], 19774)
            (package/'manifest.json').write_text('{"different":true}')
            freeze()
            with self.assertRaisesRegex(ValueError, 'unchanged data'):
                verify(package, parent)
            (package/'manifest.json').write_text('{}')
            config['model_revision'] = 'different'
            freeze()
            with self.assertRaisesRegex(ValueError, 'model_revision'):
                verify(package, parent)
            config['model_revision'] = 'revision'; config['learning_rate'] = 0
            freeze()
            with self.assertRaisesRegex(ValueError, 'lower-rate'):
                verify(package, parent)


if __name__ == '__main__':
    unittest.main()
