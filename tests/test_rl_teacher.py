import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_teacher import add_constraints,batch_call,REVIEW,reviewer_case,select_pair


class Teacher(unittest.TestCase):
    def test_only_literal_evidenced_metadata_becomes_a_gate(self):
        c={'id':'x','request':'A planeswalker named Mosskeeper with five loyalty.','explicit_metadata':{}}
        checklist={'requirements':[{'requirement':'starting loyalty five','evidence':'five loyalty'}],
                   'additional_exact_metadata':[{'field':'name','value':'Mosskeeper','evidence':'named Mosskeeper'},
                                                {'field':'loyalty','value':'5','evidence':'five loyalty'}]}
        self.assertEqual(add_constraints(c,checklist)['explicit_metadata'],{'name':'Mosskeeper'})
        checklist['requirements'][0]['evidence']='Five loyalty'
        self.assertEqual(add_constraints(c,checklist)['explicit_metadata'],{'name':'Mosskeeper'})
        checklist['requirements'][0]['evidence']='seven loyalty'
        with self.assertRaisesRegex(ValueError,'evidence'):add_constraints(c,checklist)

    def test_source_identity_and_reference_do_not_reach_reviewer(self):
        c={'request':'a Giant','explicit_metadata':{},'reference':{'name':'SECRET'},'source_group_id':'secret'}
        row={'candidate_id':'bad-model-id','raw':'{"name":"Hill Giant"}','origin':'sampled_sft'}
        self.assertEqual(set(reviewer_case(c,row)),{'id','request','explicit_metadata','candidate'})
        with tempfile.TemporaryDirectory() as tmp:
            def fake(model,payload,schema,output,**kwargs):
                self.assertEqual(payload['cases'][0]['id'],'c0')
                self.assertNotIn('bad-model-id',json.dumps(payload))
                output.mkdir()
                return {'results':[{'id':'c0','verdict':'pass','confidence':'high','oracle_style':2,'violations':[]}]},{}
            with patch('rl_teacher.invoke',side_effect=fake):
                rows,_=batch_call([reviewer_case(c,row)],'judge',REVIEW,Path(tmp)/'out',Path(tmp)/'prefix')
            self.assertEqual(rows[0]['id'],'bad-model-id')

    def test_face_specific_fields_are_not_flattened_into_root_metadata(self):
        c={'id':'x','request':'A transforming card: First has loyalty 3, Second has loyalty 5.','explicit_metadata':{}}
        checklist={'requirements':[],'additional_exact_metadata':[
            {'field':'name','value':'First','evidence':'First has loyalty 3'},
            {'field':'name','value':'Second','evidence':'Second has loyalty 5'},
            {'field':'loyalty','value':'3','evidence':'loyalty 3'}]}
        self.assertEqual(add_constraints(c,checklist)['explicit_metadata'],{})

    def test_missing_labels_retry_without_fabrication_and_count_all_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake(model,payload,schema,output,**kwargs):
                output.mkdir()
                rows=[] if output.name=='out' else [{'id':'c0','verdict':'pass','confidence':'high','oracle_style':2,'violations':[]}]
                return {'results':rows},{'usage':[{'input_tokens':10}]}
            with patch('rl_teacher.invoke',side_effect=fake) as call:
                rows,receipt=batch_call([{'id':'actual'}],'judge',REVIEW,Path(tmp)/'out',Path(tmp)/'prefix')
            self.assertEqual(call.call_count,2)
            self.assertEqual(rows[0]['id'],'actual')
            self.assertEqual(sum(r['input_tokens'] for r in receipt['usage']),20)
        with tempfile.TemporaryDirectory() as tmp:
            def broken(model,payload,schema,output,**kwargs):
                output.mkdir();return {'results':[]},{'usage':[]}
            with patch('rl_teacher.invoke',side_effect=broken) as call:
                with self.assertRaisesRegex(ValueError,'bounded retries'):
                    batch_call([{'id':'actual'}],'judge',REVIEW,Path(tmp)/'out',Path(tmp)/'prefix')
            self.assertEqual(call.call_count,3)

    def test_wrong_explicit_name_cannot_be_a_positive_even_if_judge_passes(self):
        rows=[{'candidate_id':'a','raw':'{"name":"Wrong"}','origin':'sampled_sft'},
              {'candidate_id':'b','raw':'{"name":"Right"}','origin':'sol_repair'}]
        checks={k:{'schema_valid':True,'gate':'needs_intent_judge','deterministic_violations':[]} for k in ('a','b')}
        checks['a'].update(gate='explicit_metadata_or_required_characteristic',deterministic_violations=['name'])
        judgments={k:{'verdict':'pass','confidence':'high','oracle_style':2,'violations':[]} for k in ('a','b')}
        chosen,rejected=select_pair(rows,checks,judgments)
        self.assertEqual((chosen['candidate_id'],rejected['candidate_id']),('b','a'))
        rows[1]['raw']=rows[0]['raw']
        self.assertIsNone(select_pair(rows,checks,judgments))


if __name__=='__main__':unittest.main()
