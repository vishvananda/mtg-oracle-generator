import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from gpu_workflow import commands
from hf_jobs import plan,cost_cap,launch
from paths import PROJECT
from post_training import paired_identity,comparison
from prediction_io import Predictions
from preflight_run import inspect
from prepare_dataset import prepare
from prepare_model_release import prepare as model_release
from run_package import package,verify_package,prepare_run
from train_qlora import training_identity,validation_indices,checkpoint_release
from test_release_pipeline import row,source
from evaluation_selection import POLICY,select_cases,evaluation_allowed
from job_budget import reserve
from hub_dataset import publication_readme


def fixture(root):
    source(root/'input',[row('a','train'),row('b','validation'),row('c','test')],True)
    prepare([root/'input'],root/'dataset','fixture')
    return package(root/'dataset',root/'package',PROJECT/'configs/full-run.json')


class Preparation(unittest.TestCase):
    def test_training_package_excludes_holdouts_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);manifest=verify_package(root/'package')
            self.assertFalse(manifest['test_in_training_package'])
            self.assertEqual(sorted(p.name for p in (root/'package/data').iterdir()),
                             ['manifest.json','train.sft.jsonl','validation.sft.jsonl'])
            (root/'package/extra-token.txt').write_text('do not upload')
            with self.assertRaisesRegex(ValueError,'Unlisted'): verify_package(root/'package')
            (root/'package/extra-token.txt').unlink()
            (root/'package/train-config.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'changed'): verify_package(root/'package')

    def test_wait_then_freeze_is_idempotent_and_recovers_missing_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);recovery=root/'recovery';recovery.mkdir()
            recipe=PROJECT/'configs/full-run.json'
            self.assertEqual(prepare_run([recovery],root/'run',recipe)['stage'],'waiting_for_final_exports')
            source(root/'input',[row('a','train'),row('b','validation'),row('c','test')],True)
            (recovery/'latest-export.json').write_text(json.dumps({'path':str(root/'input'),
                'manifest_sha256':digest((root/'input/manifest.json').read_bytes())}))
            result=prepare_run([recovery],root/'run',recipe)
            self.assertEqual(result['stage'],'prepared');self.assertFalse(result['automatic_gpu_launch'])
            self.assertEqual(prepare_run([recovery],root/'run',recipe),result)
            (root/'run/preparation.json').unlink()
            rebuilt=prepare_run([recovery],root/'run',recipe)
            self.assertEqual(rebuilt['package_manifest_sha256'],result['package_manifest_sha256'])

    def test_cpu_preflight_counts_loyalty_and_refuses_overlength(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=row('a','train');a['target'].update(type_line='Planeswalker — Test',loyalty='4')
            source(root/'input',[a,row('b','validation'),row('c','test')],True)
            prepare([root/'input'],root/'dataset','fixture')
            config=json.loads((PROJECT/'configs/qwen3-4b-full.json').read_text())
            tokenizer=SimpleNamespace(apply_chat_template=lambda *args,**kw:[0]*5000)
            result=inspect(root/'dataset',config,tokenizer)
            self.assertEqual(result['splits']['train']['planeswalker_faces_with_loyalty'],1)
            self.assertFalse(result['ready']);self.assertEqual(result['splits']['train']['over_context'],1)

    def test_monitoring_sample_is_fixed_and_resume_identity_binds_config(self):
        rows=[{'i':i} for i in range(100)]
        selected=validation_indices(rows,7)
        self.assertEqual(selected,validation_indices(rows,7));self.assertEqual(len(selected),7)
        reverse=list(reversed(rows))
        self.assertEqual({rows[i]['i'] for i in selected},{reverse[i]['i'] for i in validation_indices(reverse,7)})
        with self.assertRaises(ValueError): validation_indices(rows,0)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            self.assertNotEqual(training_identity(root/'dataset',{'lr':1}),training_identity(root/'dataset',{'lr':2}))


class GenerationResume(unittest.TestCase):
    def test_interrupted_batch_preserves_completed_cases_and_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'predictions.jsonl';identity={'split':'test'}
            writer=Predictions(output,identity,['a','b'])
            writer.append({'example_id':'a','raw_text':'broken JSON is still a completed prediction'})
            writer.close();saved=output.read_bytes()
            with output.open('ab') as stream: stream.write(b'{"example_id":"b"')
            writer=Predictions(output,identity,['a','b'],True)
            self.assertEqual(output.read_bytes(),saved);self.assertEqual(writer.done,{'a'})
            self.assertEqual(len(list(output.parent.glob('*.interrupted-*'))),1)
            writer.append({'example_id':'b','raw_text':'{}'});receipt=writer.finish()
            self.assertTrue(receipt['complete']);self.assertEqual(receipt['cases'],2)
            with self.assertRaisesRegex(ValueError,'request changed'):
                Predictions(output,{'split':'validation'},['a','b'],True)
            output.write_text('changed')
            with self.assertRaisesRegex(ValueError,'modified'): Predictions(output,identity,['a','b'],True)

    def test_missing_duplicate_and_unknown_cases_do_not_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'predictions.jsonl';writer=Predictions(output,{},['a','b'])
            writer.append({'example_id':'a'})
            for key in ('a','unknown'):
                with self.assertRaisesRegex(ValueError,'Unknown/duplicate'): writer.append({'example_id':key})
            with self.assertRaisesRegex(ValueError,'incomplete'): writer.finish()
            self.assertFalse(output.with_suffix('.manifest.json').exists())


class Jobs(unittest.TestCase):
    def test_full_run_requires_matching_completed_smoke_and_no_implicit_paid_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            smokeplan=plan(root/'package','smoke');self.assertEqual(smokeplan['attempts'],1)
            with self.assertRaisesRegex(ValueError,'requires a collected smoke'): plan(root/'package','train')
            smoke=root/'smoke';(smoke/'adapter').mkdir(parents=True)
            (smoke/'job-result.json').write_text(json.dumps({'state':'COMPLETED','stage':'smoke','package_hash':smokeplan['package_hash']}))
            config=json.loads((root/'package/train-config.json').read_text())
            summary={'config':config,'optimizer_steps':20,'loss_mask_verified':True,'stopped_for_training_budget':False,
                'training_identity':training_identity(root/'dataset',config),'adapter_eval':{'eval_loss':.4},
                'setup_seconds':30,'seconds_per_optimizer_step':3}
            (smoke/'adapter/run-summary.json').write_text(json.dumps(summary))
            self.assertGreater(plan(root/'package','train',smoke)['measured_estimate_minutes'],0)
            summary['config']['seed']+=1;(smoke/'adapter/run-summary.json').write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError,'Smoke did not validate'): plan(root/'package','train',smoke)

    def test_cost_cap_rejects_increase_before_any_upload(self):
        hardware=SimpleNamespace(unit_label='minute',unit_cost_usd=.05,name='a100-large')
        self.assertEqual(cost_cap({'timeout_minutes':30},hardware,2),1.5)
        for budget in (1,float('nan'),-1):
            with self.assertRaises(ValueError): cost_cap({'timeout_minutes':30},hardware,budget)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root);planned=plan(root/'package','smoke')
            sdk=SimpleNamespace(__version__='2.1.1',get_token=lambda:'secret',whoami=lambda **kw:{'name':'test'},
                                list_jobs_hardware=lambda **kw:[hardware])
            with patch.dict(sys.modules,{'huggingface_hub':sdk}):
                with self.assertRaisesRegex(ValueError,'above the explicit'):
                    launch(root/'package',planned,root/'job.json',.01)
            self.assertFalse((root/'job.json').exists())

    def test_commands_separate_training_and_paired_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            train=commands(root/'package',root/'out','train')
            self.assertNotIn('--split',train[0]);self.assertIn('-1',train[0])
            with self.assertRaisesRegex(ValueError,'frozen dataset'): commands(root/'package',root/'out','test')
            paired=commands(root/'package',root/'out','test',root/'dataset',root/'adapter')
            self.assertEqual(len(paired),2);self.assertNotIn('--adapter',paired[0]);self.assertIn('--adapter',paired[1])
            self.assertTrue(all('--resume-output' in c for c in paired))


class Reports(unittest.TestCase):
    def test_development_panel_is_fixed_across_formats_and_covers_walkers_and_faces(self):
        rows=[]
        for i in range(400):
            for j in range(3):
                item=row(f'{i}-{j}','validation');item['source_group_id']=str(i);item['style']=str(j)
                if i<60: item['target']['type_line']='Planeswalker'
                if 60<=i<100: item['target']['card_faces']=[{'type_line':'Creature'},{'type_line':'Land'}]
                rows.append(item)
        selection={'policy':POLICY,'limit':256};chosen=select_cases(rows,selection)
        self.assertEqual(len(chosen),256);self.assertEqual(len({r['source_group_id'] for r in chosen}),256)
        self.assertGreaterEqual(sum(r['target']['type_line']=='Planeswalker' for r in chosen),32)
        self.assertGreaterEqual(sum('card_faces' in r['target'] for r in chosen),16)
        messages=[{k:r[k] for k in ('example_id','source_group_id','style')}|{'messages':[{'content':json.dumps(r['target'])}]} for r in rows]
        self.assertEqual([r['example_id'] for r in chosen],[r['example_id'] for r in select_cases(list(reversed(messages)),selection)])

    def test_partial_epochs_only_allow_development_and_never_smoke_or_final_test(self):
        summary={'max_steps':-1,'stopped_for_training_budget':True,'metrics':{'epoch':.07},'config':{'epochs':1}}
        selection={'policy':POLICY,'limit':256}
        self.assertFalse(evaluation_allowed(summary,'validation',selection))
        for split,panel in [('test',selection),('test',None),('validation',None)]:
            with self.assertRaises(ValueError): evaluation_allowed(summary,split,panel)
        with self.assertRaisesRegex(ValueError,'smoke'):
            evaluation_allowed({**summary,'max_steps':20},'validation',selection)

    def test_paired_comparison_rejects_different_settings_and_parser(self):
        base={'complete':True,'cases':3,'scheduled_cases':3,'adapter_files':{},'decoding':{'batch_size':16}}
        adapter={**base,'adapter_files':{'weights':'hash'}}
        paired_identity(base,adapter)
        with self.assertRaisesRegex(ValueError,'conditions differ'):
            paired_identity(base,{**adapter,'decoding':{'batch_size':8}})
        with self.assertRaisesRegex(ValueError,'incomplete'): paired_identity(base,{**adapter,'complete':False})
        metrics={'generation':base,'cases':3,'parser':{'revision':'one'},'split':'test','dataset_manifest_sha256':'data',
                 'mtgish_acceptance_all_cases':1/3}
        tuned={**metrics,'generation':adapter,'mtgish_acceptance_all_cases':2/3}
        report=comparison(metrics,tuned);self.assertAlmostEqual(report['acceptance_change_percentage_points'],100/3)
        with self.assertRaisesRegex(ValueError,'Parser'): comparison(metrics,{**tuned,'parser':{'revision':'two'}})

    def test_release_labels_actual_evidence_and_excludes_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);adapter=root/'adapter';adapter.mkdir()
            for name in ('adapter_config.json','adapter_model.safetensors'): (adapter/name).write_text('fixture')
            (adapter/'optimizer.pt').write_text('private checkpoint')
            summary={'config':{'model':'Qwen/Test','lora_r':16},'model_revision':'pinned',
                     'training_identity':{'dataset_manifest_sha256':'dataset'}}
            (adapter/'run-summary.json').write_text(json.dumps(summary));prompt=root/'prompt.txt';prompt.write_text('prompt')
            pending=model_release(adapter,prompt,root/'pending','dataset@pin','Test')
            self.assertFalse(pending['final_test_evaluated']);self.assertNotIn('optimizer.pt',pending['files'])
            self.assertNotIn('16k pilot',(root/'pending/README.md').read_text())
            report={'adapter_files':{n:digest((adapter/n).read_bytes()) for n in ('adapter_config.json','adapter_model.safetensors')},
                    'training_summary_sha256':digest((adapter/'run-summary.json').read_bytes()),
                    'dataset_manifest_sha256':'dataset','final_test':True,'split':'test',
                    'adapter':{'mtgish_acceptance_all_cases':.5,'cases':10}}
            evidence=root/'comparison.json';evidence.write_text(json.dumps(report))
            published=model_release(adapter,prompt,root/'complete','dataset@pin','Test',evidence)
            self.assertTrue(published['final_test_evaluated'])
            report['dataset_manifest_sha256']='wrong';evidence.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'another model/dataset'):
                model_release(adapter,prompt,root/'wrong','dataset@pin','Test',evidence)


class StagedBudget(unittest.TestCase):
    def test_publication_uses_an_absolute_license_link_without_changing_data_description(self):
        value=publication_readme('---\nlicense_link: DATA_LICENSE.md\n---\nData description\n','owner/dataset')
        self.assertIn('https://huggingface.co/datasets/owner/dataset/blob/main/DATA_LICENSE.md',value)
        self.assertTrue(value.endswith('Data description\n'))

    def test_public_checkpoint_allowlist_preserves_weights_without_optimizer_or_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);checkpoint=root/'checkpoint';checkpoint.mkdir()
            for name in ('adapter_config.json','adapter_model.safetensors','optimizer.pt','worker.log'):
                (checkpoint/name).write_text(name)
            metadata={'phase':'train','step':200,'epoch':.01,'dataset_reference':'owner/data@pin','tag':'train-step-00000200',
                      'config':{'model':'Qwen/Test','model_revision':'hash'},'training_identity':{'dataset':'pin'}}
            checkpoint_release(checkpoint,root/'public',metadata,'prompt')
            self.assertEqual({p.name for p in (root/'public').iterdir()},
                {'adapter_config.json','adapter_model.safetensors','system-prompt.txt','README.md','checkpoint.json'})
            self.assertEqual((root/'public/adapter_model.safetensors').read_text(),'adapter_model.safetensors')
            self.assertIn('No final-test accuracy',(root/'public/README.md').read_text())

    def test_total_budget_survives_restarts_and_does_not_auto_retry_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger=root/'budget.json'
            reserve(ledger,20,root/'smoke.json',1.25)
            reserve(ledger,20,root/'segment1.json',3.75)
            reserve(ledger,20,root/'eval1.json',1.25)
            for name,amount in [('segment2',5),('segment3',5)]: reserve(ledger,20,root/(name+'.json'),amount)
            with self.assertRaisesRegex(ValueError,'exhausted'): reserve(ledger,20,root/'too-much.json',5)
            with self.assertRaisesRegex(ValueError,'already'): reserve(ledger,20,root/'smoke.json',1.25)
            with self.assertRaisesRegex(ValueError,'changed'): reserve(ledger,50,root/'higher.json',1)
            self.assertEqual(sum(r['maximum_usd'] for r in json.loads(ledger.read_text())['reservations']),16.25)

    def test_staged_commands_preserve_full_scheduler_and_limit_only_development(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            package(root/'dataset',root/'staged',PROJECT/'configs/staged-run.json')
            planned=plan(root/'staged','smoke')
            self.assertEqual(planned['experiment_budget_usd'],20)
            with self.assertRaisesRegex(ValueError,'not enabled'): plan(root/'staged','test')
            trained=commands(root/'staged',root/'out','train',resume=root/'previous/checkpoint-1000')[0]
            self.assertEqual(trained[trained.index('--max-steps')+1],'-1')
            self.assertIn('--resume',trained)
            paired=commands(root/'staged',root/'out','development',root/'dataset',root/'adapter')
            self.assertTrue(all(c[c.index('--validation-limit')+1]=='256' for c in paired))
            self.assertTrue(all(c[c.index('--split')+1]=='validation' for c in paired))
            recipe=json.loads((root/'staged/recipe.json').read_text())
            recipe['publication']={'model_repo':'owner/model','dataset_reference':'owner/data@pin','tag_prefix':'prepared-v1'}
            (root/'staged/recipe.json').write_text(json.dumps(recipe))
            command=commands(root/'staged',root/'out','smoke')[0]
            self.assertEqual(command[command.index('--publish-prefix')+1],'prepared-v1')


if __name__=='__main__': unittest.main()
