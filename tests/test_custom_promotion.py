import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from export_gguf import check_custom_export
from judge_display import display_card
from judge_display import judge_display
from rl_serving_eval import generate


class CustomPromotion(unittest.TestCase):
    def test_display_names_leave_the_serialized_card_and_metadata_unchanged(self):
        raw = {'name': 'Star Sage', 'oracle_text': 'When CARDNAME enters, draw a card.',
               'flavor_text': 'CARDNAME', 'card_faces': [
                   {'name': 'Night Sage', 'oracle_text': 'CARDNAME has flying.'}]}
        before = copy.deepcopy(raw)
        shown = display_card(raw)
        self.assertEqual(raw, before)
        self.assertEqual(shown['oracle_text'], 'When Star Sage enters, draw a card.')
        self.assertEqual(shown['card_faces'][0]['oracle_text'], 'Night Sage has flying.')
        self.assertEqual(shown['flavor_text'], 'CARDNAME')
        self.assertEqual(display_card(shown), shown)

    def test_identical_displayed_cards_share_one_blind_judgment(self):
        rows = [{'id': 'raw', 'request': 'A creature.', 'explicit_metadata': {},
                 'candidate': {'name': 'Star Sage', 'oracle_text': 'CARDNAME has flying.'}},
                {'id': 'displayed', 'request': 'A creature.', 'explicit_metadata': {},
                 'candidate': {'name': 'Star Sage', 'oracle_text': 'Star Sage has flying.'}}]
        def review(unique, *args):
            self.assertEqual(len(unique), 1)
            return {unique[0]['id']: {'verdict': 'pass', 'confidence': 'high',
                                      'oracle_style': 2, 'violations': []}}
        with patch('judge_display.batches', side_effect=review):
            result = judge_display(rows, Path('/unused'))
        self.assertEqual(result['raw']['verdict'], result['displayed']['verdict'])
        self.assertEqual(result['raw']['id'], 'raw')
        self.assertEqual(result['displayed']['id'], 'displayed')

    def test_parallel_serving_is_complete_resumable_and_identity_pinned(self):
        requests = []
        def respond(request, **kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{}'},
                                                       'finish_reason': 'stop'}],
                                         'usage': {'completion_tokens': 1}}).encode())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / 'cases.jsonl'
            cases.write_text('\n'.join(json.dumps({'id': str(i), 'request': 'Card ' + str(i)})
                                       for i in range(3)))
            output = root / 'predictions.jsonl'
            with patch('rl_serving_eval.urllib.request.urlopen', side_effect=respond) as fetch:
                receipt = generate(cases, 'System', 'weights', 'http://unused', output, workers=2)
                self.assertTrue(receipt['complete'])
                self.assertEqual(receipt['completions'], 3)
                self.assertEqual(len(set(r['case_id'] for r in map(json.loads, output.read_text().splitlines()))), 3)
                self.assertEqual(generate(cases, 'System', 'weights', 'http://unused', output, workers=2), receipt)
                self.assertEqual(fetch.call_count, 3)
                with self.assertRaisesRegex(ValueError, 'inputs changed'):
                    generate(cases, 'Changed system', 'weights', 'http://unused', output, workers=2)
                output.write_text(output.read_text() + '\n')
                with self.assertRaisesRegex(ValueError, 'output changed'):
                    generate(cases, 'System', 'weights', 'http://unused', output, workers=2)
            for request in requests:
                self.assertEqual(request['temperature'], 0)
                self.assertEqual(request['seed'], 17)
                self.assertEqual(request['messages'][0], {'role': 'system', 'content': 'System'})

    def test_export_requires_completed_matching_receipts_and_learned_weights(self):
        config = {'model': 'base', 'model_revision': 'pinned'}
        summary = dict(config=config, optimizer_steps=255, planned_steps=255,
                       stopped_for_budget=False, metrics={'epoch': 1}, loss_mask_verified=True,
                       initial_adapter_hash='before', final_adapter_hash='after',
                       parent_training_identity={'dataset_manifest_sha256': 'data'})
        identity = dict(config=config, package_sha256='package')
        receipt = dict(training=summary, package_sha256='package')
        self.assertEqual(check_custom_export(summary, identity, receipt), 'completed_custom_sft_pilot')
        for key, value in [('optimizer_steps', 254), ('stopped_for_budget', True),
                           ('loss_mask_verified', False), ('final_adapter_hash', 'before')]:
            changed = {**summary, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_custom_export(changed, identity, {**receipt, 'training': changed})
        with self.assertRaisesRegex(ValueError, 'receipt'):
            check_custom_export(summary, identity, {**receipt, 'package_sha256': 'other'})

    def test_failed_request_preserves_other_completions_for_resume(self):
        def respond(request, **kwargs):
            if json.loads(request.data)['messages'][-1]['content'] == 'Fail':
                raise OSError('Temporary server failure')
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{}'},
                                                       'finish_reason': 'stop'}]}).encode())
        with tempfile.TemporaryDirectory() as directory:
            cases, output = Path(directory) / 'cases.jsonl', Path(directory) / 'out.jsonl'
            cases.write_text('\n'.join(json.dumps({'id': str(i), 'request': text})
                                       for i, text in enumerate(['Fail', 'Pass', 'Also pass'])))
            with patch('rl_serving_eval.urllib.request.urlopen', side_effect=respond):
                with self.assertRaisesRegex(RuntimeError, 'completed results saved'):
                    generate(cases, 'System', 'weights', 'http://unused', output, workers=2)
            self.assertEqual({r['case_id'] for r in map(json.loads, output.read_text().splitlines())}, {'1', '2'})
            self.assertFalse(json.loads(output.with_suffix('.manifest.json').read_text())['complete'])


if __name__ == '__main__':
    unittest.main()
