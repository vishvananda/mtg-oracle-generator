import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from finish_evaluation import collect_settled,complete_pair


def arm(directory,name):
    predictions=directory/f'{name}-validation.jsonl'
    predictions.write_text('{"example_id":"a","raw_text":"{}"}\n')
    manifest={'complete':True,'cases':1,'scheduled_cases':1,
              'adapter_files':{'weights':'hash'} if name=='adapter' else {},
              'predictions_sha256':digest(predictions.read_bytes())}
    predictions.with_suffix('.manifest.json').write_text(json.dumps(manifest))


class Collection(unittest.TestCase):
    def test_terminal_job_waits_for_late_adapter_upload_without_rerunning_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=root/'job.json';output=root/'download'
            record.write_text(json.dumps({'bucket':'owner/bucket','output_prefix':'runs/evaluation'}))
            receipt={'job_id':'job','state':'COMPLETED','package_hash':'package',
                     'dataset_manifest_sha256':'dataset','output_prefix':'runs/evaluation'}
            def collect(path,dest=None):
                if dest:
                    dest.mkdir();(dest/'job-result.json').write_text(json.dumps(receipt));arm(dest,'base')
                return receipt
            def sync(*args,**kwargs): arm(output,'adapter')
            sdk=SimpleNamespace(sync_bucket=sync)
            with patch('finish_evaluation.collect',side_effect=collect),patch.dict(sys.modules,{'huggingface_hub':sdk}),patch('finish_evaluation.time.sleep'):
                self.assertEqual(collect_settled(record,output,attempts=2),receipt)
            self.assertEqual(complete_pair(output)['adapter']['cases'],1)

    def test_missing_or_modified_predictions_cannot_be_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);arm(root,'base')
            with self.assertRaises(FileNotFoundError):complete_pair(root)
            arm(root,'adapter');(root/'adapter-validation.jsonl').write_text('changed\n')
            with self.assertRaisesRegex(ValueError,'do not match'):complete_pair(root)

    def test_collection_refuses_a_download_from_another_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);output=root/'download';output.mkdir()
            (output/'job-result.json').write_text(json.dumps({'job_id':'different'}))
            with patch('finish_evaluation.collect',return_value={'state':'COMPLETED','job_id':'expected'}),patch.dict(sys.modules,{'huggingface_hub':SimpleNamespace()}):
                with self.assertRaisesRegex(ValueError,'another job'):
                    collect_settled(root/'job.json',output)


if __name__=='__main__':unittest.main()
