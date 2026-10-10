import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from judge_transport import invoke_judge


class BlindJudge(unittest.TestCase):
    def test_expected_labels_and_semantic_ids_never_reach_judge(self):
        cases=[{'id':'mutation-wrong-player','expected':'fail','request':'Draw a card.',
                'candidate':{'oracle_text':'Target opponent draws a card.'}}]
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'batch'
            def fake(model,payload,schema,folder,**kwargs):
                self.assertNotIn('mutation-wrong-player',json.dumps(payload))
                self.assertNotIn('expected',payload['cases'][0])
                self.assertEqual(payload['cases'][0]['id'],'c0')
                ids=schema['properties']['reviews']['anyOf'][1]['items']['properties']['id']['enum']
                self.assertEqual(ids,[f'c{i}' for i in range(9)])
                folder.mkdir()
                return {'reviews':[{'id':'c0','verdict':'fail','confidence':'high','oracle_style':2,'violations':['Wrong player']}]},{}
            with patch('judge_transport.invoke',side_effect=fake):
                value,_=invoke_judge('judge',cases,output,prefix_root=Path(tmp)/'prefix')
            self.assertEqual(value['reviews'][0]['id'],'mutation-wrong-player')

    def test_unknown_label_never_gets_guessed_or_attached_to_a_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake(model,payload,schema,folder,**kwargs):
                folder.mkdir()
                return {'reviews':[{'id':'c7'}]},{}
            with patch('judge_transport.invoke',side_effect=fake),self.assertRaisesRegex(ValueError,'unknown judge IDs'):
                invoke_judge('judge',[{'id':'x','request':'r','candidate':{}}],Path(tmp)/'b',prefix_root=Path(tmp)/'p')
