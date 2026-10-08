import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from finish_fixed_epoch import (authorization,ensure_room,expected_identity,inspect_segment,
    segment_limits,CHECKPOINT_FILES)
from hf_jobs import plan
from job_budget import reserve
from run_package import continue_package
from test_full_run import fixture


class FixedEpoch(unittest.TestCase):
    def prepared_segment(self,root,step,complete=False):
        manifest=fixture(root)
        # Enough fixture examples to model an interrupted epoch without modifying
        # the frozen trainer or config used for the identity checks.
        path=root/'package/package-manifest.json';m=json.loads(path.read_text());m['optimizer_steps']=100
        path.write_text(json.dumps(m))
        package=root/'package';job={'package_hash':digest(path.read_bytes())}
        output=root/'download';adapter=output/'adapter';checkpoint=adapter/f'checkpoint-{step}'
        checkpoint.mkdir(parents=True)
        identity=expected_identity(package);config=json.loads((package/'train-config.json').read_text())
        summary={'training_identity':identity,'config':config,'optimizer_steps':step,
                 'base_eval':{'eval_loss':2.},'adapter_eval':{'eval_loss':.5},
                 'metrics':{'train_loss':.6,'epoch':1 if complete else .5},
                 'stopped_for_training_budget':not complete,'seconds_per_optimizer_step':4}
        (output/'phase-complete.json').write_text(json.dumps({'stage':'train','package_manifest_sha256':job['package_hash']}))
        (adapter/'run-summary.json').write_text(json.dumps(summary))
        for name in CHECKPOINT_FILES:(checkpoint/name).write_text('checkpoint data')
        (checkpoint/'training-identity.json').write_text(json.dumps(identity))
        (checkpoint/'trainer_state.json').write_text(json.dumps({'global_step':step,'max_steps':100}))
        return package,job,output,checkpoint

    def test_budget_stop_resumes_with_full_optimizer_state_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,job,output,checkpoint=self.prepared_segment(Path(tmp),50)
            result=inspect_segment(output,package,job,{'state':'COMPLETED'},20)
            self.assertFalse(result['complete']);self.assertEqual(result['checkpoint'],'checkpoint-50')
            with self.assertRaisesRegex(ValueError,'progress'):
                inspect_segment(output,package,job,{'state':'COMPLETED'},50)
            (checkpoint/'optimizer.pt').unlink()
            with self.assertRaises(FileNotFoundError):inspect_segment(output,package,job,{'state':'COMPLETED'},20)

    def test_hard_timeout_uses_checkpoint_but_other_failures_never_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,job,output,checkpoint=self.prepared_segment(Path(tmp),50)
            (output/'phase-complete.json').unlink();(output/'adapter/run-summary.json').unlink()
            result=inspect_segment(output,package,job,{'state':'ERROR','message':'Job timeout'},0)
            self.assertEqual(result['step'],50)
            for terminal in ({'state':'ERROR','message':'CUDA out of memory'},{'state':'CANCELED'}):
                with self.assertRaisesRegex(ValueError,'Only normal budget'):
                    inspect_segment(output,package,job,terminal,0)
            (checkpoint/'training-identity.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'another dataset'):
                inspect_segment(output,package,job,{'state':'ERROR','message':'Job timeout'},0)

    def test_completed_epoch_does_not_request_more_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,job,output,checkpoint=self.prepared_segment(Path(tmp),100,complete=True)
            result=inspect_segment(output,package,job,{'state':'COMPLETED'},50)
            self.assertTrue(result['complete']);self.assertIsNone(result['checkpoint'])
            with self.assertRaisesRegex(ValueError,'No authorized training'):segment_limits(result,100)

    def test_scoped_budget_top_up_requires_pinned_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);auth=root/'authorization.json';ledger=root/'budget.json'
            auth.write_text(json.dumps({'scope':'finish_existing_epoch_and_final_test','user_instruction':'Finish the epoch',
                                        'target_optimizer_steps':100}))
            pin=digest(auth.read_bytes());reserve(ledger,60,root/'old-job.json',59)
            self.assertEqual(ensure_room(ledger,3.25,auth,pin),62.25)
            saved=json.loads(ledger.read_text());self.assertEqual(saved['reservations'][0]['maximum_usd'],59)
            self.assertIn(pin,saved['amendments'][0]['reason'])
            auth.write_text('{}')
            with self.assertRaisesRegex(ValueError,'authorization changed'):ensure_room(ledger,10,auth,pin)

    def test_later_continuation_reuses_verified_original_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            continue_package(root/'package',root/'second',3600,90,60)
            continue_package(root/'second',root/'third',1800,60,65)
            smoke=root/'smoke';(smoke/'adapter').mkdir(parents=True)
            base=plan(root/'package','smoke');config=json.loads((root/'package/train-config.json').read_text())
            (smoke/'job-result.json').write_text(json.dumps({'state':'COMPLETED','stage':'smoke','package_hash':base['package_hash']}))
            (smoke/'adapter/run-summary.json').write_text(json.dumps({'config':config,'optimizer_steps':20,
                'loss_mask_verified':True,'stopped_for_training_budget':False,'training_identity':expected_identity(root/'package'),
                'adapter_eval':{'eval_loss':.5},'setup_seconds':20,'seconds_per_optimizer_step':4}))
            with self.assertRaisesRegex(ValueError,'Smoke job did not complete'):
                plan(root/'third','train',smoke,compatible_package=root/'second')
            result=plan(root/'third','train',smoke,compatible_package=root/'second',smoke_package=root/'package')
            self.assertEqual(result['experiment_budget_usd'],65)


if __name__=='__main__':unittest.main()
