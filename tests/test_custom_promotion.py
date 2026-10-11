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
from compare_custom_q4 import run as compare_q4
from rl_data import sha_file


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

    def test_completed_arm_reuses_verified_outputs_without_starting_a_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / name for name in ['cases', 'system', 'custom', 'dpo', 'server']}
            for name, path in paths.items():
                path.write_text(name)
            paths['cases'].write_text(json.dumps({'id': 'one', 'request': 'A creature.'}) + '\n')
            output = root / 'results'
            output.mkdir()
            prediction = output / 'custom.jsonl'
            response = {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}
            with patch('rl_serving_eval.urllib.request.urlopen',
                       return_value=io.BytesIO(json.dumps(response).encode())):
                generate(paths['cases'], paths['system'].read_text(), sha_file(paths['custom']),
                         'http://unused', prediction, workers=2)
            with patch('compare_custom_q4.socket.socket', side_effect=AssertionError('No socket needed')), \
                 patch('compare_custom_q4.subprocess.Popen', side_effect=AssertionError('No model needed')):
                compare_q4(**paths, output=output, port=4221, threads=8, slots=2,
                           judges=3, generate_arm='custom')
                prediction.write_text(prediction.read_text() + '\n')
                with self.assertRaisesRegex(ValueError, 'output changed'):
                    compare_q4(**paths, output=output, port=4221, threads=8, slots=2,
                               judges=3, generate_arm='custom')


if __name__ == '__main__':
    unittest.main()
