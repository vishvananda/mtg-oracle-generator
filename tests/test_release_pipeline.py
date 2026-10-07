import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from data_utils import write_jsonl
from prepare_dataset import prepare,audit
from hub_dataset import verify
from train_qlora import preflight,tokenize_supervised,verify_completion_masks
from evaluate import score,aggregate
from validate_mtgish import bind_names


def row(identifier,split,description=None):
    return {'example_id':identifier,'split':split,'source_group_id':identifier,'style':'complete',
            'description':description or 'A creature '+identifier,'source':{'oracle_id':identifier},
            'target':{'name':'Invented '+identifier,'rarity':'common','type_line':'Creature — Giant',
                      'oracle_text':'','power':'3','toughness':'3','mana_cost':'{3}{R}'}}


def source(root,rows,complete=False):
    root.mkdir()
    files={}
    for split in ['train','validation','test']:
        files[split+'.jsonl']=write_jsonl(root/(split+'.jsonl'),[r for r in rows if r['split']==split])
    (root/'manifest.json').write_text(json.dumps({'version':'fixture','generation_complete':complete,'files':files}))


class DatasetTests(unittest.TestCase):
    def test_roundtrip_chat_and_trainer_formats_keep_names_rarity_and_holdouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source(root/'input',[row('a','train'),row('b','validation'),row('c','test')],True)
            result=prepare([root/'input'],root/'out','fixture')
            self.assertEqual(result['counts'],{'train':1,'validation':1,'test':1})
            verify(root/'out')
            self.assertEqual(preflight(root/'out',{'model':'test','model_revision':'hash'})['splits'],{'train':1,'validation':1})
            chat=json.loads((root/'out/train.messages.jsonl').read_text())
            supervised=json.loads((root/'out/train.sft.jsonl').read_text())
            self.assertEqual(chat['messages'],supervised['prompt']+supervised['completion'])
            target=json.loads(supervised['completion'][0]['content'])
            self.assertEqual(target['name'],'Invented a');self.assertEqual(target['rarity'],'common')

    def test_group_and_identity_leakage_are_rejected(self):
        a=row('a','train');b=row('b','test');b['source_group_id']='a'
        with self.assertRaisesRegex(ValueError,'group crosses'): audit([a,b])
        b['source_group_id']='b';b['source']['oracle_id']='a'
        with self.assertRaisesRegex(ValueError,'card crosses'): audit([a,b])

    def test_holdout_wins_description_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[row('a','train','A giant!'),row('b','test','a giant.'),row('c','train'),row('d','validation')]
            source(root/'input',rows,True)
            result=prepare([root/'input'],root/'out','fixture')
            self.assertEqual(result['duplicates_removed'],1)
            self.assertEqual(result['counts']['train'],1)
            self.assertEqual(json.loads((root/'out/test.jsonl').read_text())['example_id'],'b')

    def test_partial_is_labeled_and_hash_tampering_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source(root/'input',[row('a','train'),row('b','validation')])
            with self.assertRaisesRegex(ValueError,'not marked complete'): prepare([root/'input'],root/'out','fixture')
            result=prepare([root/'input'],root/'out','fixture',True)
            self.assertEqual(result['status'],'partial')
            (root/'out/train.sft.jsonl').write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'Hash mismatch'): verify(root/'out')

    def test_schema_and_unreviewed_failures_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a=row('a','train');a['fidelity_review']={'verdict':'uncertain','issues':[]}
            source(root/'input',[a,row('b','validation')])
            with self.assertRaisesRegex(ValueError,'review not passed'): prepare([root/'input'],root/'out','fixture',True)


class EvaluationTests(unittest.TestCase):
    schema={'type':'object','required':['type_line','oracle_text'],
            'properties':{'type_line':{'type':'string'},'oracle_text':{'type':'string'}}}
    def test_failures_ignored_and_missing_stay_in_denominator(self):
        cases=[row(str(i),'test') for i in range(5)]
        predictions=[{'example_id':'0','raw_text':'{"type_line":"Sorcery","oracle_text":"Draw two cards."}'},
                     {'example_id':'1','raw_text':'not json'},
                     {'example_id':'2','raw_text':'{"oracle_text":"Flying"}'},
                     {'example_id':'3','raw_text':'{"type_line":"Sorcery","oracle_text":"Ignored"}'}]
        def check(draft):
            ignored=draft['oracle_text']=='Ignored'
            return {'status':'ignored' if ignored else 'parsed','parse_complete':not ignored}
        summary=aggregate(score(cases,predictions,self.schema,check))
        self.assertEqual(summary['cases'],5);self.assertEqual(summary['mtgish_accepted'],1)
        self.assertEqual(summary['mtgish_acceptance_all_cases'],.2)
        self.assertEqual(summary['mtgish_acceptance_attempted'],1)
        self.assertEqual(summary['prediction_status']['missing'],1)

    def test_unknown_and_duplicate_predictions_fail(self):
        pred={'example_id':'a','raw_text':'{}'}
        with self.assertRaisesRegex(ValueError,'Duplicate prediction'): score([row('a','test')],[pred,pred],self.schema,None)
        with self.assertRaisesRegex(ValueError,'Unknown prediction'): score([], [pred],self.schema,None)

    def test_multiface_is_validated_as_whole_card_once(self):
        draft={'type_line':'Creature // Land','oracle_text':'','card_faces':[
            {'name':'Front','type_line':'Creature','oracle_text':'Flying'},
            {'name':'Back','type_line':'Land','oracle_text':'broken'}]}
        seen=[]
        def check(value): seen.append(value);return {'status':'rejected','parse_complete':False}
        results=score([row('a','test')],[{'example_id':'a','raw_text':json.dumps(draft)}],self.schema,check)
        self.assertEqual(len(seen),1);self.assertEqual(len(seen[0]['card_faces']),2)
        self.assertFalse(results[0]['mtgish_accepted'])

    def test_name_binding_retains_cross_face_references_and_original(self):
        draft={'name':'Front // Back','card_faces':[
            {'name':'Front','oracle_text':'Exile CARDNAME, then return it transformed into Draft Face 2.'},
            {'name':'Back','oracle_text':'CARDNAME has flying.'}]}
        bound=bind_names(draft)
        self.assertIn('Exile Front',bound['card_faces'][0]['oracle_text'])
        self.assertIn('into Back',bound['card_faces'][0]['oracle_text'])
        self.assertIn('CARDNAME',draft['card_faces'][0]['oracle_text'])


class TrainingTests(unittest.TestCase):
    class Tokenizer:
        def apply_chat_template(self,messages,**kwargs):
            return list(('PROMPT'+(messages[-1]['content'] if messages[-1]['role']=='assistant' else '')).encode())
        def decode(self,ids,**kwargs):return bytes(ids).decode()
    def test_completion_masks_cover_json_without_prompt_and_refuse_truncation(self):
        row={'prompt':[{'role':'system','content':'instructions'},{'role':'user','content':'request'}],
             'completion':[{'role':'assistant','content':'{"oracle_text":"Flying"}'}]}
        tokenizer=self.Tokenizer()
        encoded=tokenize_supervised(row,tokenizer,100)
        verify_completion_masks(tokenizer,[encoded],[row])
        with self.assertRaisesRegex(ValueError,'silent truncation'): tokenize_supervised(row,tokenizer,4)
        changed=copy.deepcopy(encoded);changed['completion_mask'][0]=1
        with self.assertRaisesRegex(ValueError,'Noncontiguous'): verify_completion_masks(tokenizer,[changed],[row])


if __name__=='__main__':unittest.main()
