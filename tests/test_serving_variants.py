import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from judge_display import judge_display, reusable_judgments
from rl_serving_eval import generate
from rl_teacher import prompt


class ServingVariants(unittest.TestCase):
    def test_variant_provenance_prevents_reusing_another_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cases = root/'cases.jsonl'; output = root/'predictions.jsonl'
            cases.write_text(json.dumps({'id': 'one', 'request': 'A flying creature.'})+'\n')
            response = {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}
            with patch('rl_serving_eval.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
                receipt = generate(cases, 'system', 'base', 'http://unused', output,
                    backend='llama.cpp Q4_K_M CPU + F16 LoRA', adapter_sha='adapter-one')
            self.assertEqual(receipt['adapter_sha256'], 'adapter-one')
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                generate(cases, 'system', 'base', 'http://unused', output,
                    backend=receipt['backend'], adapter_sha='adapter-two')

    def test_reuse_checks_judge_protocol_and_does_not_rejudge_identical_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/'source'; (source/'judge').mkdir(parents=True)
            output = root/'new'; output.mkdir()
            case = {'id': 'arm-one', 'request': 'Flying.', 'explicit_metadata': {},
                    'candidate': {'name': 'Sky', 'oracle_text': 'Flying'}}
            key = digest({k: v for k, v in case.items() if k != 'id'})
            result = {'id': key, 'verdict': 'pass', 'confidence': 'high', 'oracle_style': 2, 'violations': []}
            report = {'judge_model': 'gpt-6.1-sol', 'judge_effort': 'medium',
                      'rubric_sha256': digest(prompt('reward-judge-v3.txt')), 'judge_display_names': True}
            (source/'comparison.json').write_text(json.dumps(report))
            (source/'judge/results.jsonl').write_text(json.dumps(result)+'\n')
            with patch('judge_display.batches', return_value={}) as review:
                actual = judge_display([case], output, reuse_evaluation=source)
                self.assertEqual(review.call_args.args[0], [])
                self.assertEqual(actual['arm-one']['verdict'], 'pass')
            report['judge_effort'] = 'high'
            (source/'comparison.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'protocol differs'):
                reusable_judgments(source)


if __name__ == '__main__':
    unittest.main()
