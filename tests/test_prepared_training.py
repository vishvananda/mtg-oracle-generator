import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from prepared_training import prepare, load_prepared, inspect_prepared, tokenize_batch, ARCHIVE, MANIFEST
from train_qlora import tokenize_supervised, verify_completion_masks
from run_package import package, verify_package
from paths import PROJECT
from prepare_dataset import prepare as prepare_dataset
from test_release_pipeline import row, source


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        if isinstance(messages[0], list):
            return [self.apply_chat_template(m, **kwargs) for m in messages]
        prompt = [m for m in messages if m['role'] != 'assistant']
        prefix = json.dumps(prompt, ensure_ascii=False)+'ASSISTANT:'
        target = messages[-1]['content'] if messages[-1]['role'] == 'assistant' else ''
        return list((prefix+target).encode())

    def decode(self, ids, **kwargs): return bytes(ids).decode()


class BatchedTokenization(unittest.TestCase):
    def test_batch_is_identical_to_existing_path_and_preserves_unicode_targets(self):
        samples = [{'prompt': [{'role':'system','content':'JSON only'}, {'role':'user','content':description}],
                    'completion':[{'role':'assistant','content':json.dumps({'name':'Æther','oracle_text':description},ensure_ascii=False)}]}
                   for description in ('Flying', '−2: Draw two cards.', 'First line\nSecond line')]
        batch = {key:[r[key] for r in samples] for key in ('prompt','completion')}
        result = tokenize_batch(batch, Tokenizer(), 4096)
        expected = [tokenize_supervised(r, Tokenizer(), 4096) for r in samples]
        self.assertEqual(result['input_ids'], [r['input_ids'] for r in expected])
        self.assertEqual(result['completion_mask'], [r['completion_mask'] for r in expected])
        with self.assertRaisesRegex(ValueError, 'silent truncation'): tokenize_batch(batch, Tokenizer(), 5)
        bad = copy.deepcopy(batch); bad['completion'][0][0]['content'] = 'not JSON'
        with self.assertRaises(ValueError): tokenize_batch(bad, Tokenizer(), 4096)


@unittest.skipUnless(importlib.util.find_spec('datasets') and importlib.util.find_spec('transformers'), 'CPU tokenizer dependencies required')
class PreparedRoundtrip(unittest.TestCase):
    def test_parallel_preparation_roundtrip_freeze_and_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source(root/'input', [row('a','train'),row('b','train'),row('v1','validation'),
                                 row('v2','validation'),row('t','test')], True)
            prepare_dataset([root/'input'], root/'data', 'fixture')
            config = json.loads((PROJECT/'configs/qwen3-4b-full.json').read_text())
            report = prepare(root/'data', config, root/'prepared', workers=2, tokenizer=Tokenizer())
            self.assertEqual(report['counts'], {'train':2,'validation':2})
            data, metadata = load_prepared(root/'prepared',root/'data',config,root/'loaded')
            originals = [json.loads(line) for line in (root/'data/train.sft.jsonl').read_text().splitlines()]
            verify_completion_masks(Tokenizer(), data['train'], originals)
            self.assertEqual(set(data['train'].column_names), {'input_ids','completion_mask','length'})
            self.assertEqual(metadata['loss_masks_checked'], 4)
            self.assertNotIn('test',data)
            self.assertEqual(prepare(root/'data',config,root/'prepared',tokenizer=Tokenizer()),report)
            package(root/'data',root/'package',PROJECT/'configs/staged-run.json',root/'prepared')
            frozen = verify_package(root/'package')
            self.assertTrue(frozen['prepared_training'])
            self.assertFalse((root/'package/data/train.sft.jsonl').exists())
            self.assertTrue((root/'package/data'/ARCHIVE).exists())
            inspect_prepared(root/'prepared',root/'data',{**config,'per_device_train_batch_size':16})
            for changes in ({'max_length':2048},{'model_revision':'other'},{'validation_max_examples':1}):
                with self.assertRaisesRegex(ValueError,'identity changed'):
                    inspect_prepared(root/'prepared',root/'data',{**config,**changes})
            with (root/'prepared'/ARCHIVE).open('ab') as stream: stream.write(b'changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                inspect_prepared(root/'prepared',root/'data',config)


if __name__ == '__main__': unittest.main()
