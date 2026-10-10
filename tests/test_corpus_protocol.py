import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from corpus_protocol import teacher_request, teacher_reply, review_schema, upgrade_config
from corpus_workers import prompt_config, run_job, run
from codex_cached_batch import invoke, batch_usage
from test_broad_corpus import card
from prepare_broad_corpus import projections


class CompactProtocolTests(unittest.TestCase):
    def test_schema_stable_when_cards_and_source_ids_change(self):
        first = {'instructions': 'same', 'projections': [{'id': 'source-one', 'target': {'oracle_text': 'Draw a card.'}}]}
        second = {'instructions': 'same', 'projections': [{'id': 'source-two', 'target': {'oracle_text': 'Gain 3 life.'}}]}
        original = copy.deepcopy(first)
        payload, schema, labels = teacher_request(first)
        self.assertEqual(first, original)
        self.assertEqual(schema, teacher_request(second)[1])
        self.assertEqual(payload['projections'][0]['id'], 'd01')
        self.assertEqual(teacher_reply({'descriptions': {'d01': 'Draw one card.'}}, labels)['descriptions'][0]['id'], 'source-one')
        for value in (None, {}, {'d02': 'Wrong'}, {'d01': ''}):
            with self.assertRaises(ValueError):
                teacher_reply({'descriptions': value}, labels)
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate({'descriptions': {'d01': 'ok', 'd02': 'extra'}})

    def test_review_requires_every_label_and_issue_shape(self):
        self.assertEqual(review_schema(iter(['case_001', 'case_002'])),
                         review_schema(['case_001', 'case_002']))
        validator = Draft202012Validator(review_schema(['case_001', 'case_002']))
        validator.validate({'reviews': None})  # Setup only; batch admission rejects null.
        with self.assertRaises(ValidationError):
            validator.validate({'reviews': {'case_001': {'verdict': 'pass', 'issues': []}}})

    def test_config_upgrade_keeps_frozen_terminology_and_policy(self):
        for policy in ('exact_constraints', 'plausible_completion'):
            old = prompt_config(policy)
            snapshot = copy.deepcopy(old)
            new = upgrade_config(old)
            self.assertEqual(old, snapshot)
            self.assertEqual(new['reviewer_terminology'], old['reviewer_terminology'])
            self.assertIn(old['reviewer_terminology'], new['instructions'])
            self.assertNotIn('"coverage_notes"', new['instructions'])

    def test_forks_share_instruction_base_without_prior_card_data(self):
        calls = []
        def fake_execute(model, prompt, schema, output, cwd, effort, parent=None):
            output.mkdir(parents=True)
            receipt = {'thread_id': 'base-id' if parent is None else 'child-id',
                       'usage': [{'input_tokens': 100 if parent is None else 250, 'output_tokens': 5}]}
            (output/'receipt.json').write_text(json.dumps(receipt))
            calls.append((prompt, cwd, parent))
            return ({'reviews': None} if parent is None else {'reviews': {}}), receipt
        with tempfile.TemporaryDirectory() as tmp, patch('codex_cached_batch.execute', side_effect=fake_execute):
            root = Path(tmp)
            for i in range(2):
                invoke('fixture', {'instructions': 'Fixed rubric', 'cases': [{'card': f'card-{i}'}]},
                       review_schema([]), root/f'call-{i}', prefix_root=root/'bases')
            self.assertEqual(len(calls), 3)
            self.assertNotIn('card-0', calls[0][0])
            self.assertEqual(calls[1][2], 'base-id')
            self.assertEqual(calls[2][2], 'base-id')
            self.assertEqual(calls[1][1], calls[2][1])
            self.assertNotIn('card-0', calls[2][0])
            self.assertNotIn('Fixed rubric', calls[2][0])

    def test_usage_excludes_the_base_request_once(self):
        base = [{'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 5}]
        total = [{'input_tokens': 250, 'cached_input_tokens': 110, 'output_tokens': 35}]
        self.assertEqual(batch_usage(total, base),
                         [{'input_tokens': 150, 'cached_input_tokens': 90, 'output_tokens': 30}])
        with self.assertRaises(ValueError):
            batch_usage(base, total)

    def test_drain_preserves_pending_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'queue').mkdir()
            (root/'generation-config.json').write_text('{}')
            (root/'drain.request.json').write_text('{}')
            job = {'id': '00001', 'state': 'pending', 'cards': []}
            path = root/'queue/00001.json'; path.write_text(json.dumps(job))
            with patch('corpus_workers.run_job') as execute:
                run(root, 1)
                execute.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), job)

    def test_queue_runs_configured_number_of_workers(self):
        barrier = threading.Barrier(3)
        lock = threading.Lock()
        active = 0
        peak = 0
        def execute(path, config):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            job = json.loads(path.read_text())
            job.update(state='completed', accepted=1, quarantined=0)
            path.write_text(json.dumps(job))
            with lock:
                active -= 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'queue').mkdir()
            (root/'generation-config.json').write_text(json.dumps({'workers': 3}))
            for i in range(6):
                (root/'queue'/f'{i:05}.json').write_text(json.dumps({'id': f'{i:05}', 'state': 'pending'}))
            with patch('corpus_workers.run_job', side_effect=execute):
                run(root, 6)
            progress = json.loads((root/'progress.json').read_text())
            self.assertEqual(peak, 3)
            self.assertEqual(progress['workers'], 3)
            self.assertEqual(progress['states'], {'completed': 6})

    def test_invalid_worker_count_fails_before_starting_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in (0, -1, True, '3'):
                (root/'generation-config.json').write_text(json.dumps({'workers': value}))
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    run(root, 1)

    def test_compact_job_preserves_controls_and_permanent_ids(self):
        source = card(); source['projections'] = projections(source)
        config = upgrade_config({'teacher': 'fixture', 'reviewer': 'fixture', **prompt_config('plausible_completion')})
        def fake(model, payload, schema, output, **kwargs):
            if 'projections' in payload:
                return {'descriptions': {p['id']: 'A planeswalker that grows plants and has a card-draw ultimate.'
                                          for p in payload['projections']}}, {'prompt_sha256': 'p', 'output_sha256': 't'}
            good = {'A planeswalker that grows plants and has a card-draw ultimate.',
                    'A planeswalker that makes plants and has a big card-draw ultimate.', 'A red instant for rummaging.'}
            return {'reviews': {c['id']: {'verdict': 'pass' if c['description'] in good else 'reject',
                                         'issues': [] if c['description'] in good else ['Contradiction.']}
                                for c in payload['cases']}}, {'output_sha256': 'r'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'queue').mkdir(); (root/'runs/00001').mkdir(parents=True)
            path = root/'queue/00001.json'
            path.write_text(json.dumps({'id': '00001', 'state': 'pending', 'cards': [source]}))
            with patch('corpus_workers.invoke_cached', side_effect=fake):
                run_job(path, config)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['state'], 'completed')
            self.assertEqual(saved['fidelity_controls_rejected'], 3)
            self.assertEqual(saved['completion_controls_checked'], 4)
            rows = [json.loads(line) for line in (root/'runs/00001/results.jsonl').read_text().splitlines()]
            self.assertEqual([r['example_id'] for r in rows], [p['id'] for p in source['projections']])


if __name__ == '__main__':
    unittest.main()
