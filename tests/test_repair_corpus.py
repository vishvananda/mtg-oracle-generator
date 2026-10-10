import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from repair_corpus import select

class Parser:
    def check(self,card):
        if card['oracle_text']=='Flying':
            return {'status':'parsed','parse_complete':True}
        return {'status':'rejected','parse_complete':False,'error':'No match'}

class RepairCorpusTests(unittest.TestCase):
    def row(self,group='train',split='train'):
        return {'id':group,'source_group_id':group,'split':split,'request':'A flying bird',
            'target':{'name':'Cloudwing','type_line':'Creature — Bird','oracle_text':'Flying','power':'1','toughness':'1'}}
    def test_holdouts_cannot_become_training_examples(self):
        cases,meta,counts=select([self.row('reserved'),self.row()],{'reserved'},Parser(),20)
        self.assertEqual(len(cases),1)
        self.assertEqual(meta[0]['source_group_id'],'train')
        self.assertEqual(counts['heldout_family_skipped'],1)
        with self.assertRaises(ValueError):select([self.row(split='test')],set(),Parser(),20)
    def test_target_and_non_rules_fields_are_preserved(self):
        row=self.row();cases,_,_=select([row,row],set(),Parser(),20)
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]['target'],row['target'])
        self.assertEqual(cases[0]['current_card']['oracle_text'],'Flyng')
        self.assertEqual(cases[0]['current_card']['name'],'Cloudwing')
        self.assertEqual(row['target']['oracle_text'],'Flying')
    def test_infrastructure_failure_is_not_training_feedback(self):
        class Broken:
            def check(self,card):return {'status':'parser_error','parse_complete':False}
        with self.assertRaises(RuntimeError):select([self.row()],set(),Broken(),20)

if __name__=='__main__':unittest.main()
