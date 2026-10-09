import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_recover import select_pair


PASS={'verdict':'pass','confidence':'high','oracle_style':2,'violations':[]}
FAIL={**PASS,'verdict':'fail','violations':['Wrong player']}


def row(id,actor,origin='sampled_sft'):
    return {'candidate_id':id,'raw':json.dumps({'oracle_text':actor+' draws a card.'}),'origin':origin}


class RecoverySelection(unittest.TestCase):
    def test_source_positive_requires_independent_pass(self):
        rows=[row('source','You','training_reference'),row('bad','Each opponent')]
        self.assertIsNone(select_pair(rows,{'source':FAIL,'bad':FAIL}))
        good,bad=select_pair(rows,{'source':PASS,'bad':FAIL})
        self.assertEqual(good['origin'],'training_reference');self.assertEqual(bad['candidate_id'],'bad')

    def test_generated_positive_is_preferred_and_source_is_never_negative(self):
        rows=[row('source','You','training_reference'),row('good','You'),row('bad','Each opponent')]
        good,_=select_pair(rows,{'source':PASS,'good':PASS,'bad':FAIL})
        self.assertEqual(good['candidate_id'],'good')
        self.assertIsNone(select_pair([row('source','Each opponent','training_reference'),row('good','You')],
                                      {'source':FAIL,'good':PASS}))

    def test_all_good_and_conflicting_identical_cards_never_form_pairs(self):
        self.assertIsNone(select_pair([row('a','You'),row('b','Each opponent')],{'a':PASS,'b':PASS}))
        self.assertIsNone(select_pair([row('a','You'),row('b','You')],{'a':PASS,'b':FAIL}))

    def test_uncertain_or_inconsistent_chosen_is_ineligible(self):
        rows=[row('a','You'),row('b','Each opponent')]
        for chosen in [{**PASS,'confidence':'medium'},{**PASS,'violations':['Contradiction']},{**PASS,'oracle_style':1}]:
            self.assertIsNone(select_pair(rows,{'a':chosen,'b':FAIL}))


if __name__=='__main__':unittest.main()
