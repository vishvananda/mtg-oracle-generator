import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from original_wordings import signature, normalize_face, assemble_faces, pair, functional_exclusions
from full_corpus import project


class OriginalWordingsTests(unittest.TestCase):
    def test_conservative_changes_match_but_mechanics_do_not(self):
        self.assertEqual(signature('When CARDNAME comes into play, draw a card.','Creature'),
                         signature('When this creature enters, draw a card.','Creature'))
        for wrong in ['You may draw a card.','Each opponent draws a card.','Draw two cards.']:
            self.assertNotEqual(signature(wrong,'Sorcery'),signature('Draw a card.','Sorcery'))
        self.assertNotEqual(signature('Destroy target creature.','Sorcery'),signature('Exile target creature.','Sorcery'))

    def test_mana_pool_whitespace_regression(self):
        self.assertEqual(signature('{T}: Add {G} to your mana pool.','Creature'),signature('{T}: Add {G}.','Creature'))

    def test_old_vocabulary_is_training_data(self):
        for name,old,new,typ in [
            ('Lightning Bolt','CARDNAME does 3 damage to one target.','CARDNAME deals 3 damage to any target.','Instant'),
            ('Wrath of God','Bury all creatures.',"Destroy all creatures. They can't be regenerated.",'Sorcery'),
            ('Llanowar Elves','Tap to add 1 green mana to your mana pool. This tap can be played as an interrupt.','{T}: Add {G}.','Creature'),
            ('Angelic Chorus','Whenever a creature comes into play under your control, you gain life equal to its toughness.','Whenever a creature you control enters, you gain life equal to its toughness.','Enchantment')]:
            self.assertEqual(functional_exclusions(name,[old],[new],[typ]),[],name)

    def test_explicit_obsolete_rule_and_functional_errata_are_excluded(self):
        self.assertEqual(functional_exclusions('Upwelling',["Mana pools don't empty. (This effect stops mana burn.)"],
            ["Players don't lose unspent mana as steps and phases end."],['Enchantment'])[0]['id'],'mana_burn_removed')
        self.assertTrue(functional_exclusions("Ajani's Pridemate",['Whenever you gain life, you may put a +1/+1 counter on CARDNAME.'],
            ['Whenever you gain life, put a +1/+1 counter on CARDNAME.'],['Creature']))
        fixed='When CARDNAME enters, exile another target creature or artifact until CARDNAME leaves the battlefield.'
        old=fixed.replace('another target','target')
        self.assertTrue(functional_exclusions('Hostage Taker',[old],[fixed],['Creature']))
        self.assertEqual(functional_exclusions('Hostage Taker',[fixed],[fixed],['Creature']),[])

    def test_changed_companion_reminder_is_not_discarded_before_check(self):
        old='Companion — Your deck contains only odd costs. (If this card is your chosen companion, you may cast it once from outside the game.)'
        current='Companion — Your deck contains only odd costs.'
        self.assertEqual(functional_exclusions('Test Companion',[old],[current],['Creature'])[0]['id'],'original_companion_rule')

    def test_unambiguous_number_changes_are_functional(self):
        reasons=functional_exclusions('Fixture',['Draw 2 cards.'],['Draw 3 cards.'],['Sorcery'])
        self.assertEqual(reasons[0]['id'],'changed_explicit_number')

    def test_may_can_be_redundant_for_tap_or_untap_and_land_permission(self):
        for old,new in [('Tap or untap target artifact.','You may tap or untap target artifact.'),
                        ('Play up to three additional lands this turn.','You may play up to three additional lands this turn.')]:
            self.assertEqual(functional_exclusions('Fixture',[old],[new],['Sorcery']),[])

    def test_obsolete_triggered_keyword_reminders_are_excluded(self):
        self.assertEqual(functional_exclusions('Fixture',
            ['Lifelink (Whenever this creature deals damage, you gain that much life.)'],['Lifelink'],['Creature'])[0]['id'],
            'triggered_lifelink_reminder')
        self.assertEqual(functional_exclusions('Fixture',
            ['Lifelink (Damage dealt by this creature also causes you to gain that much life.)'],['Lifelink'],['Creature']),[])

    def test_mana_costs_negation_and_durations_are_not_ignored(self):
        for a,b in [('{U}: Draw a card.','{U}{U}: Draw a card.'),
                    ("This creature can't attack.",'This creature can attack.'),
                    ('Gain control of target creature.','Gain control of target creature until end of turn.')]:
            self.assertNotEqual(signature(a,'Creature'),signature(b,'Creature'))

    def test_printed_input_keeps_reminder_while_name_is_normalized(self):
        source={'name':'Test Bird','source_faces':[]}
        text='Flying (This creature cannot be blocked except by creatures with flying.)\nTest Bird gets +1/+1.'
        value=normalize_face(text,source,0,keep_reminder=True)
        self.assertIn('(This creature cannot',value)
        self.assertIn('CARDNAME gets',value)

    def test_diagnostic_signature_distinguishes_punctuation_scope(self):
        self.assertNotEqual(signature('Until end of turn, draw a card. Draw another card.','Sorcery'),
                            signature('Until end of turn: draw a card; draw another card.','Sorcery'))

    def test_faces_require_complete_unambiguous_pair(self):
        source={'source_faces':[{'name':'Front'},{'name':'Back'}]}
        front={'name':'Front // Back','faceName':'Front'}; back={'name':'Front // Back','faceName':'Back'}
        self.assertEqual(assemble_faces(source,[back,front]),[front,back])
        self.assertIsNone(assemble_faces(source,[front]))
        self.assertIsNone(assemble_faces(source,[front,front,back]))

    def test_printing_inherits_source_split_and_group(self):
        card={'oracle_id':'id','name':'Hill Giant','layout':'normal','type_line':'Creature — Giant',
              'colors':['R'],'mana_cost':'{3}{R}','power':'3','toughness':'3','oracle_text':''}
        source={'oracle_id':'id','name':'Hill Giant','source_sha256':'sha','source_updated_at':'date',
                'layout':'normal','split':'test','source_group_id':'held-out','projections':project(card)}
        row=pair(source,[''],'printed_wording',{})
        self.assertEqual((row['split'],row['source_group_id']),('test','held-out'))
        self.assertNotIn('Hill Giant',row['description'])
        self.assertEqual(row['target']['mana_cost'],'{3}{R}')


if __name__=='__main__': unittest.main()
