import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from training_names import named_record


class Names(unittest.TestCase):
    def setUp(self):
        self.source = {'oracle_id':'id', 'source_sha256':'hash', 'name':'Hill Giant', 'source_faces':[]}
        self.row = {'example_id':'example', 'split':'train', 'source_group_id':'group',
                    'source':{'oracle_id':'id','source_sha256':'hash'}, 'description':'a 3/3 giant',
                    'target':{'type_line':'Creature — Giant','oracle_text':'When CARDNAME enters, draw a card.'},
                    'proposed_fields':[], 'expressed_constraints':{}}

    def test_proposed_and_requested_names_preserve_mechanics_and_splits(self):
        proposed = named_record(self.row,self.source)
        self.assertEqual(proposed['target']['name'],'Hill Giant')
        explicit = named_record(self.row,self.source,'Winter’s Promise')
        self.assertEqual(explicit['target']['name'],'Winter’s Promise')
        self.assertIn('Name it "Winter’s Promise".',explicit['description'])
        self.assertEqual(explicit['expressed_constraints']['name'],'Winter’s Promise')
        self.assertNotIn('name',explicit['proposed_fields'])
        for key in ['source_group_id','split']: self.assertEqual(explicit[key],self.row[key])
        self.assertEqual(explicit['target']['oracle_text'],self.row['target']['oracle_text'])
        self.assertNotIn('name',self.row['target'])

    def test_multiface_names_and_cross_references_stay_linked(self):
        self.source.update(name='Day // Night',source_faces=[{'name':'Day'},{'name':'Night'}])
        self.row['target']={'card_faces':[{'name':'Draft Face 1','oracle_text':'Transform CARDNAME into Draft Face 2.'},
                                        {'name':'Draft Face 2','oracle_text':'Return Draft Face 1.'}]}
        result=named_record(self.row,self.source)
        self.assertEqual(result['target']['name'],'Day // Night')
        self.assertEqual(result['target']['card_faces'][0]['oracle_text'],'Transform CARDNAME into Night.')
        self.assertEqual(result['target']['card_faces'][1]['oracle_text'],'Return Day.')
        self.row['target']={'type_line':'Creature','oracle_text':''}
        self.assertEqual(named_record(self.row,self.source)['target']['name'],'Day')

    def test_wrong_source_is_refused(self):
        with self.assertRaisesRegex(ValueError,'snapshot'):
            named_record(self.row,{**self.source,'source_sha256':'different'})

if __name__=='__main__': unittest.main()
