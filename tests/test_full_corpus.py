import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from full_corpus import project, normalized_group, draft, rules
from corpus_workers import deterministic_checks, align_rows
from export_full_corpus import select_unique


def giant():
    return {'oracle_id':'fixture','name':'Hill Giant','layout':'normal','type_line':'Creature — Giant',
            'colors':['R'],'mana_cost':'{3}{R}','power':'3','toughness':'3','oracle_text':''}


class FullCorpusTests(unittest.TestCase):
    def test_sparse_input_can_have_completed_default_stats_and_cost(self):
        rows=project(giant())
        minimal,concept=rows[:2]
        self.assertNotIn('mana_cost',minimal['target'])
        self.assertEqual(minimal['draft_target']['mana_cost'],'{3}{R}')
        self.assertNotIn('power',concept['target'])
        self.assertEqual(concept['target']['design_notes'],['midsize'])
        self.assertEqual(concept['draft_target']['power'],'3')
        self.assertIn('power',concept['proposed_fields'])

    def test_partial_concept_does_not_add_unrequested_source_abilities(self):
        card=giant(); card['oracle_text']='Flying\nWhen this creature enters, draw two cards.'
        rows=project(card)
        self.assertEqual(rows[0]['draft_target']['oracle_text'],'')
        self.assertEqual(rows[1]['draft_target']['oracle_text'],'')
        self.assertIn('draw two cards',rows[2]['draft_target']['oracle_text'])

    def test_projection_ids_are_stable_and_distinct(self):
        self.assertEqual(project(giant()),project(giant()))
        self.assertEqual(len({r['id'] for r in project(giant())}),5)

    def test_functional_variants_group_together(self):
        a=giant(); a['oracle_text']='CARDNAME deals 3 damage to target creature.'
        b=copy.deepcopy(a); b.update(oracle_id='other',power='5',mana_cost='{4}{R}')
        b['oracle_text']='CARDNAME deals 5 damage to target creature.'
        self.assertEqual(normalized_group(a),normalized_group(b))

    def test_independent_faces_preserve_linked_layout(self):
        card={'oracle_id':'faces','name':'Front // Back','layout':'transform','type_line':'Creature — Human // Creature — Horror',
              'card_faces':[{'name':'Front','type_line':'Creature — Human','mana_cost':'{U}','colors':['U'],'power':'1','toughness':'1','oracle_text':'{T}: Transform Front.'},
                            {'name':'Back','type_line':'Creature — Horror','mana_cost':'','colors':['U'],'power':'3','toughness':'2','oracle_text':'Flying'}]}
        rows=project(card)
        self.assertNotIn('card_faces',rows[0]['draft_target'])
        self.assertEqual(rows[3]['draft_target']['layout'],'transform')
        self.assertEqual(len(rows[3]['draft_target']['card_faces']),2)
        self.assertEqual(rows[3]['draft_target']['card_faces'][0]['oracle_text'],'{T}: Transform CARDNAME.')

    def test_named_other_card_reference_is_not_rewritten(self):
        self.assertEqual(rules('Search for a card named Hill Giant.','Hill Giant'),
                         'Search for a card named Hill Giant.')

    def test_short_self_names_work_in_object_and_mana_references(self):
        self.assertEqual(rules('Put two +1/+1 counters on Nick Fury.','Nick Fury, Director of S.H.I.E.L.D.'),
                         'Put two +1/+1 counters on CARDNAME.')
        self.assertEqual(rules('Whenever you cast a spell using mana produced by Tecutlan, draw a card.',
                               'Tecutlan, the Searing Rift'),
                         'Whenever you cast a spell using mana produced by CARDNAME, draw a card.')

    def test_sparse_star_stats_get_editable_fixed_defaults(self):
        card=giant(); card.update(power='*',toughness='1+*',oracle_text="CARDNAME's power is equal to the number of cards in your hand.")
        rows=project(card)
        self.assertNotIn('power',rows[0]['target'])
        self.assertEqual(rows[0]['draft_target']['power'],'3')
        self.assertEqual(rows[3]['draft_target']['power'],'*')

    def test_teacher_guard_catches_actual_luna_mana_error(self):
        p={'style':'complete','target':{'type_line':'Legendary Planeswalker — Jace','mana_cost':'{2}{U}{U}'}}
        self.assertIn('missing_or_changed_mana_cost:{2}{U}{U}',deterministic_checks(p,'A legendary Jace planeswalker for {2}{U}.'))
        self.assertEqual(deterministic_checks(p,'A legendary Jace planeswalker for {2}{U}{U}.'),[])

    def test_shorthand_stats_and_subtype_can_imply_creature(self):
        p={'style':'casual_complete','target':{'type_line':'Creature — Giant','power':'3','toughness':'3','mana_cost':'{3}{R}'}}
        self.assertEqual(deterministic_checks(p,'A red 3/3 Giant for {3}{R}.'),[])

    def test_duplicate_description_keeps_holdout_and_removes_train(self):
        rows=[{'example_id':'a','split':'train','source_group_id':'one','description':'A 3/3 giant.'},
              {'example_id':'b','split':'test','source_group_id':'two','description':'a 3/3 giant!'}]
        chosen,duplicates=select_unique(rows)
        self.assertEqual([r['example_id'] for r in chosen],['b'])
        self.assertEqual(len(duplicates),1)

    def test_export_rejects_source_group_leak(self):
        rows=[{'example_id':'a','split':'train','source_group_id':'one','description':'A giant.'},
              {'example_id':'b','split':'test','source_group_id':'one','description':'Another giant.'}]
        with self.assertRaisesRegex(ValueError,'crosses splits'): select_unique(rows)

    def test_reviewer_ids_allow_reorder_but_never_typo_or_duplicate(self):
        self.assertEqual(align_rows([{'id':'b'},{'id':'a'}],['a','b']),[{'id':'a'},{'id':'b'}])
        with self.assertRaises(ValueError): align_rows([{'id':'a'},{'id':'a'}],['a','b'])
        with self.assertRaises(ValueError): align_rows([{'id':'a'},{'id':'bb'}],['a','b'])


if __name__=='__main__': unittest.main()
