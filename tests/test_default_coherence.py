import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from default_coherence import sparse_defaults,coherent_record
from full_corpus import project


class DefaultCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.card={'oracle_id':'fixture','name':'Test Drone','layout':'normal','type_line':'Creature — Eldrazi Drone',
                   'colors':[],'mana_cost':'{2}{U}','power':'1','toughness':'4','oracle_text':'Devoid\nFlying'}

    def test_sparse_source_devoid_is_not_required_by_omitted_source_colors(self):
        projections={r['style']:r for r in project(self.card)}
        minimal=projections['minimal']['draft_target']
        self.assertEqual(minimal['oracle_text'],'')
        self.assertEqual(minimal['mana_cost'],'{2}{U}')
        self.assertEqual(minimal['colors'],['U'])
        concept=projections['concept']['draft_target']
        self.assertEqual(concept['colors'],[])
        self.assertEqual(concept['mana_cost'],'{3}')
        self.assertIn('Devoid',projections['complete']['draft_target']['oracle_text'])
        self.assertEqual(projections['complete']['draft_target']['colors'],[])

    def test_explicit_characteristics_are_never_rewritten(self):
        target={'colors':[],'mana_cost':'{U}','oracle_text':''}
        fixed,changes=sparse_defaults(target,{'colors':[],'mana_cost':'{U}'})
        self.assertEqual(fixed,target)
        self.assertFalse(changes)

    def test_old_frozen_record_keeps_adjustment_provenance(self):
        row={'style':'concept','target':{'colors':[],'mana_cost':'{B/P}','oracle_text':''},'expressed_constraints':{'colors':[]}}
        fixed=coherent_record(row)
        self.assertEqual(fixed['target']['mana_cost'],'{1}')
        self.assertEqual(fixed['target_before_completion_adjustment'],row['target'])
        self.assertEqual(row['target']['mana_cost'],'{B/P}')
        self.assertIs(coherent_record(fixed),fixed)


if __name__=='__main__': unittest.main()
