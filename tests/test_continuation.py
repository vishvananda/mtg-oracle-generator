import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from export_gguf import check_export_scope
from job_budget import amend_limit,reserve
from run_package import continue_package,verify_continuation
from test_full_run import fixture
from watch_epoch import run as watch_epoch,collect_training


class Continuation(unittest.TestCase):
    def test_only_operational_limits_may_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            continue_package(root/'package',root/'extended',68400,1199,60)
            self.assertEqual(verify_continuation(root/'extended',root/'package'),
                             digest((root/'package/package-manifest.json').read_bytes()))
            for name in ('train-config.json','generate.py','recipe.json'):
                file=root/'extended'/name;original=file.read_bytes()
                if name=='recipe.json':
                    recipe=json.loads(original);recipe['evaluation']['batch_size']+=1
                    file.write_text(json.dumps(recipe))
                else:file.write_bytes(original+b' ')
                manifest=root/'extended/package-manifest.json';old=manifest.read_bytes()
                value=json.loads(old);value['files'][name]={'sha256':digest(file.read_bytes()),'bytes':file.stat().st_size}
                manifest.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'Continuation changed'):
                    verify_continuation(root/'extended',root/'package')
                file.write_bytes(original);manifest.write_bytes(old)

    def test_budget_amendment_keeps_prior_reservations_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=root/'budget.json'
            reserve(ledger,20,root/'earlier.json',8.75)
            with self.assertRaisesRegex(ValueError,'changed'):reserve(ledger,60,root/'new.json',50)
            amended=amend_limit(ledger,20,60,'User approved finishing the epoch with a $60 total cap')
            self.assertEqual(amended['reservations'][0]['maximum_usd'],8.75)
            self.assertEqual(amended['amendments'][0]['previous_limit_usd'],20)
            reserve(ledger,60,root/'new.json',50)
            with self.assertRaisesRegex(ValueError,'exhausted'):reserve(ledger,60,root/'too-much.json',2)
            with self.assertRaisesRegex(ValueError,'changed'):amend_limit(ledger,20,100,'stale review')

    def test_partial_export_is_explicit_and_never_allows_smoke(self):
        partial={'max_steps':-1,'stopped_for_training_budget':True}
        with self.assertRaisesRegex(ValueError,'allow-partial'):check_export_scope(partial)
        self.assertEqual(check_export_scope(partial,True),'partial_epoch_preview')
        with self.assertRaisesRegex(ValueError,'smoke'):check_export_scope({'max_steps':20},True)
        self.assertEqual(check_export_scope({'max_steps':-1}),'completed_training')

    def test_failed_training_never_submits_a_paid_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);record=root/'train-job.json'
            record.write_text(json.dumps({'stage':'train','resumed_from':'previous.json','budget_ledger':'budget.json',
                'package_hash':digest((root/'package/package-manifest.json').read_bytes())}))
            with patch('watch_epoch.collect',return_value={'state':'ERROR'}),patch('watch_epoch.launch') as launch:
                with self.assertRaisesRegex(RuntimeError,'failed'):
                    watch_epoch(root/'package',record,root/'dataset',root/'baseline',root/'output',Path(sys.executable))
                launch.assert_not_called()

    def test_completed_job_with_partial_epoch_cannot_trigger_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);record=root/'train-job.json';output=root/'download';(output/'adapter').mkdir(parents=True)
            identity=digest((root/'package/package-manifest.json').read_bytes())
            record.write_text(json.dumps({'job_id':'job','package_hash':identity}))
            (output/'job-result.json').write_text(json.dumps({'job_id':'job'}))
            (output/'phase-complete.json').write_text(json.dumps({'stage':'train','package_manifest_sha256':identity}))
            manifest=json.loads((root/'package/package-manifest.json').read_text())
            (output/'adapter/run-summary.json').write_text(json.dumps({'stopped_for_training_budget':True,
                'training_identity':{'dataset_manifest_sha256':manifest['dataset_manifest_sha256']}}))
            with patch.dict(sys.modules,{'huggingface_hub':object()}):
                with self.assertRaisesRegex(ValueError,'before completing'):
                    collect_training(record,output,root/'package')


if __name__=='__main__':unittest.main()
