import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from prediction_io import Predictions
from reuse_baseline import baseline_files
from test_full_run import fixture


class BaselineReuse(unittest.TestCase):
    def test_reuse_requires_identical_complete_unmodified_base_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);package=root/'package';dataset=root/'dataset'
            config=json.loads((package/'train-config.json').read_text())
            recipe=json.loads((package/'recipe.json').read_text())
            identities=[json.loads(line)['example_id'] for line in (dataset/'validation.messages.jsonl').read_text().splitlines()]
            identity={'dataset_manifest_sha256':digest((dataset/'manifest.json').read_bytes()),'split':'validation',
                'model':config['model'],'model_revision':config['model_revision'],'adapter_files':{},
                'config_sha256':digest((package/'train-config.json').read_bytes()),
                'config_canonical_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),'seed':config['seed'],
                'code_files':{n:digest((package/n).read_bytes()) for n in ('generate.py','generate.py.lock','prediction_io.py')},
                'selection':None,'selection_code_sha256':digest((package/'evaluation_selection.py').read_bytes()),
                'decoding':{'do_sample':False,'max_new_tokens':recipe['evaluation']['max_new_tokens'],
                            'enable_thinking':False,'batch_size':recipe['evaluation']['batch_size']},
                'backend':'transformers NF4, one GPU'}
            output=root/'base-validation.jsonl';writer=Predictions(output,identity,identities)
            for identifier in identities:writer.append({'example_id':identifier,'raw_text':'invalid output is retained'})
            writer.finish()
            self.assertEqual(len(baseline_files(package,dataset,'validation',output)),3)
            raw=output.read_bytes();output.write_bytes(raw+b'\n')
            with self.assertRaisesRegex(ValueError,'modified'):baseline_files(package,dataset,'validation',output)
            output.write_bytes(raw)
            request=output.with_suffix('.request.json');original=request.read_bytes()
            for changes in ({'adapter_files':{'adapter_model.safetensors':'tuned'}},{'seed':99},{'split':'test'}):
                value=json.loads(original);value.update(changes);request.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'request differs'):baseline_files(package,dataset,'validation',output)
            request.write_bytes(original)
            receipt=output.with_suffix('.manifest.json');value=json.loads(receipt.read_text());value['complete']=False
            receipt.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'incomplete'):baseline_files(package,dataset,'validation',output)


if __name__=='__main__':unittest.main()
