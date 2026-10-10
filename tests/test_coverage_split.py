import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from coverage_split import features,choose_groups
from train_qlora import training_splits
from job_budget import reserve,settle_terminal
from run_two_way import await_job


class CoverageSplit(unittest.TestCase):
    def test_colors_and_faces(self):
        f=features({'layout':'transform','keywords':['Flying'], 'card_faces':[
            {'type_line':'Legendary Creature — Giant','colors':['W'],'oracle_text':'Flying'},
            {'type_line':'Planeswalker — Test','colors':['U'],'oracle_text':'+1: Draw a card.\n−2: Create a token.'}]})
        self.assertTrue({'type:creature','type:planeswalker','color:W','color:U','color_bucket:multicolor',
                         'color_combination:WU','keyword:flying','pattern:loyalty_negative'}<=f)
        self.assertIn('color_bucket:colorless',features({'type_line':'Artifact','colors':[]}))

    def test_rare_features_stay_in_train_and_concrete_panel_covers_strata(self):
        groups={}
        for i in range(100):
            fs={'type:creature','color:U','color_bucket:U','keyword:flying'}
            if i<3:fs.add('keyword:rare')
            if i in (8,9):fs.add('type:battle')
            groups[str(i)]={'cards':{str(i)},'features':fs,'card_features':{str(i):fs}}
        chosen,coverage=choose_groups(groups,5)
        self.assertFalse(chosen&{'0','1','2'})
        self.assertTrue(chosen&{'8','9'})
        self.assertEqual(coverage['keyword:rare']['training_families'],3)
        self.assertEqual(choose_groups(copy.deepcopy(groups),5)[0],chosen)
        impossible=copy.deepcopy(groups);impossible['0']['features'].add('type:unique')
        impossible['0']['card_features']['0'].add('type:unique')
        with self.assertRaisesRegex(ValueError,'Cannot cover'):choose_groups(impossible,5)

    def test_test_never_enters_preparation(self):
        self.assertEqual(training_splits({'monitoring_split':'train'}),('train',))
        self.assertEqual(training_splits({}),('train','validation'))
        with self.assertRaises(ValueError):training_splits({'monitoring_split':'test'})

    def test_failed_paid_stage_stops_the_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('run_two_way.collect',return_value={'state':'ERROR','job_id':'failed'}):
                with self.assertRaisesRegex(RuntimeError,'no paid retry'):
                    await_job(Path(tmp)/'record.json',Path(tmp)/'status.json','smoke',0)
            self.assertFalse(json.loads((Path(tmp)/'status.json').read_text())['automatic_retry'])

    def test_settlement_retains_old_cap_and_rejects_nonterminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=root/'job.json';ledger=root/'budget.json'
            record.write_text(json.dumps({'job_id':'test','hardware_cost_cap_usd':5,'timeout_minutes':100}))
            reserve(ledger,10,record,5)
            evidence={'job_id':'test','state':'CANCELED','started_at':'2026-10-08T01:00:00+00:00',
                      'observed_at':'2026-10-08T01:10:01+00:00'}
            with self.assertRaises(ValueError):settle_terminal(ledger,record,{**evidence,'state':'RUNNING'})
            result=settle_terminal(ledger,record,evidence)['reservations'][0]
            self.assertEqual(result['original_maximum_usd'],5)
            self.assertEqual(result['maximum_usd'],.65)
            self.assertFalse(result['settlement']['invoice_verified'])


if __name__=='__main__':unittest.main()
