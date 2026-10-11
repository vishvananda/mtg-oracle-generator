import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from repair_dataset import browser_examples, select_replay
from repair_evaluation import score, paired_report
from repair_expansion import mutations, edits, checked
from rl_serving_eval import generate


class RepairExpansionTests(unittest.TestCase):
    def card(self):
        return {'name': 'Sky Sage', 'rarity': 'mythic', 'mana_cost': '{2}{U}{U}',
            'colors': ['U'], 'type_line': 'Legendary Planeswalker — Sage', 'loyalty': '4',
            'oracle_text': '+1: Draw a card.\n-3: Return target creature to its owner\'s hand.'}

    def test_loyalty_corruptions_keep_cost_signs_and_metadata(self):
        card = self.card()
        rows = dict(mutations(card))
        self.assertEqual(rows['loyalty_colon']['oracle_text'],
            "+1 Draw a card.\n-3: Return target creature to its owner's hand.")
        for candidate in rows.values():
            self.assertEqual({k: v for k, v in candidate.items() if k != 'oracle_text'},
                             {k: v for k, v in card.items() if k != 'oracle_text'})
        self.assertTrue(card['oracle_text'].startswith('+1:'))
        kind, request, target = next(edits(card))
        self.assertEqual(kind, 'loyalty_edit')
        self.assertEqual(target['loyalty'], '5')
        self.assertEqual(target['oracle_text'], card['oracle_text'])

    def test_replay_never_includes_holdout_or_duplicate_family(self):
        rows = [{'example_id': str(i), 'source_group_id': str(i//2), 'split': 'train',
                 'target': self.card(), 'description': 'A planeswalker'} for i in range(30)]
        selected = select_replay(rows, {'0', '1'}, 10)
        self.assertEqual(len({r['source_group_id'] for r in selected}), 10)
        self.assertFalse({'0', '1'} & {r['source_group_id'] for r in selected})
        with self.assertRaisesRegex(ValueError, 'explicit training'):
            select_replay([{**rows[0], 'split': 'test'}], set(), 1)

    def test_parser_acceptance_does_not_reward_destructive_repair(self):
        card = self.card()
        case = {'panel': 'synthetic', 'current_card': card, 'target': card}
        removed = {**card, 'oracle_text': 'Draw a card.'}
        bad = score(case, removed, True, {'verdict': 'reject', 'issues': ['Deleted ability']}, True)
        self.assertFalse(bad['successful'])
        changed = {**card, 'loyalty': '9'}
        self.assertFalse(score(case, changed, True, {'verdict': 'pass', 'issues': []}, True)['successful'])
        self.assertFalse(score(case, None, False, None, False)['successful'])

    def test_unsupported_valid_control_requires_preserving_card(self):
        card = self.card()
        case = {'panel': 'real', 'current_card': card, 'target': card, 'unsupported_control': True}
        good = score(case, card, False, {'verdict': 'pass', 'issues': []}, True)
        self.assertTrue(good['successful'])
        self.assertFalse(good['faithful_and_parsed'])
        altered = {**card, 'oracle_text': 'Draw a card.'}
        self.assertFalse(score(case, altered, True, {'verdict': 'pass', 'issues': []}, True)['successful'])

    def test_browser_messages_are_sent_unchanged(self):
        case = {'id': 'repair', 'current_card': self.card(), 'target': self.card(),
                'requested_change': 'Fix Oracle wording.', 'diagnostic': {'error': 'No match'}}
        messages = browser_examples('Production system', [case])[0]['prompt']
        requests = []
        def reply(request, **kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}).encode())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cases = root/'cases.jsonl'
            cases.write_text(json.dumps({'id': 'repair', 'messages': messages})+'\n')
            with patch('rl_serving_eval.urllib.request.urlopen', side_effect=reply):
                generate(cases, 'Unused', 'weights', 'http://unused', root/'out.jsonl', case_messages=True)
            self.assertEqual(requests[0]['messages'], messages)

    def test_infrastructure_failure_and_unpaired_comparison_stop(self):
        class Broken:
            def check(self, card):
                return {'status': 'parser_error', 'error': 'Process died'}
        with self.assertRaises(RuntimeError):
            checked(Broken(), self.card())
        with self.assertRaisesRegex(ValueError, 'Unpaired'):
            paired_report([{'id': 'a', 'panel': 'real', 'source_group_id': 'family'}],
                          [{'case_id': 'a', 'arm': 'dpo', 'metrics': {'successful': True}}])


if __name__ == '__main__':
    unittest.main()
