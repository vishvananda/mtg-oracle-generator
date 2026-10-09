import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_rewards import inspect,combine,judge_payload,align_judgments,group_is_usable
from reward_probe import fixtures

PARSED={'status':'parsed','parse_complete':True}
REJECTED={'status':'rejected','parse_complete':False}
JUDGE={'verdict':'pass','confidence':'high','oracle_style':2,'violations':[]}


class Rewards(unittest.TestCase):
    def setUp(self):
        self.schema=json.loads((Path(__file__).resolve().parents[1]/'schemas/design-draft-v1.json').read_text())
        self.schema['required']+=['name','rarity']

    def report(self,case,parser=PARSED):
        return inspect(case,json.dumps(case['candidate']),self.schema,lambda c:parser)

    def test_syntax_cannot_rescue_changed_meaning(self):
        case=fixtures()[1]
        result=combine(self.report(case),{**JUDGE,'verdict':'fail','violations':['Wrong player.']},PARSED)
        self.assertEqual(result['gate'],'intent_failure')
        self.assertLess(result['reward'],0)

    def test_parser_gap_is_neutral_and_infrastructure_failure_abstains(self):
        case=fixtures()[20]
        self.assertEqual(combine(self.report(case,REJECTED),JUDGE,REJECTED)['reward'],.95)
        self.assertEqual(combine(self.report(case,REJECTED),JUDGE,PARSED)['reward'],.9)
        error={'status':'parser_error','parse_complete':False}
        for own,reference in [(error,PARSED),(PARSED,error)]:
            result=combine(self.report(case,own),JUDGE,reference)
            self.assertFalse(result['eligible']);self.assertIsNone(result['reward'])

    def test_sparse_defaults_and_requested_vanilla_are_valid(self):
        for case in fixtures()[24:26]:
            result=combine(self.report(case),JUDGE,PARSED)
            self.assertEqual(result['reward'],1)
            self.assertFalse(result['has_oracle_text'])
        bad=fixtures()[23]
        self.assertLess(combine(self.report(bad),JUDGE,PARSED)['reward'],0)

    def test_missing_colors_or_stats_cannot_match_explicit_colorless(self):
        case=copy.deepcopy(fixtures()[0]);case['explicit_metadata']={'colors':[]}
        del case['candidate']['colors']
        self.assertIn('explicit_colors_missing',self.report(case)['deterministic_violations'])
        case=copy.deepcopy(fixtures()[16]);del case['candidate']['loyalty']
        self.assertIn('missing_loyalty',self.report(case)['deterministic_violations'])

    def test_blank_name_and_broken_output_are_penalized(self):
        case=copy.deepcopy(fixtures()[0]);case['candidate']['name']=' '
        self.assertLess(self.report(case)['reward'],0)
        for raw in ['no JSON','[]','{"name":"test"}']:
            result=inspect(case,raw,self.schema,lambda c:PARSED)
            self.assertEqual(result['reward'],-1)
        self.assertEqual(inspect(case,'{}',self.schema,lambda c:PARSED,hit_token_limit=True)['gate'],'truncated_generation')

    def test_judge_uncertainty_excludes_entire_group(self):
        report=self.report(fixtures()[0]);good=combine(report,JUDGE,PARSED)
        bad=combine(report,{**JUDGE,'verdict':'fail','violations':['Missing ability.']},PARSED)
        for judgment in [None,{**JUDGE,'confidence':'medium'},{**JUDGE,'verdict':'uncertain'}]:
            uncertain=combine(report,judgment,PARSED)
            self.assertFalse(uncertain['eligible']);self.assertIsNone(uncertain['reward'])
            self.assertFalse(group_is_usable([good,bad,uncertain]))
        self.assertTrue(group_is_usable([good,bad]))
        self.assertFalse(group_is_usable([bad,bad]))
        self.assertFalse(group_is_usable([good,good]))

    def test_judge_is_blinded_and_alignment_fails_closed(self):
        cases=fixtures()[:2];payload=judge_payload(cases)
        self.assertNotIn('expected',payload['cases'][0]);self.assertNotIn('variant',payload['cases'][0])
        reviews=[dict(JUDGE,id=c['id']) for c in cases]
        self.assertEqual(len(align_judgments({'reviews':reviews},cases)),2)
        for bad in [None,reviews[:1],[reviews[0],reviews[0]]]:
            with self.assertRaises(ValueError):align_judgments({'reviews':bad},cases)

    def test_contradictory_judgment_is_audited_and_excludes_its_group(self):
        report=self.report(fixtures()[0])
        contradictory={**JUDGE,'violations':['The requested fear reminder text is omitted.']}
        result=combine(report,contradictory,PARSED)
        self.assertEqual(result['gate'],'judge_inconsistent')
        self.assertEqual(result['judgment'],contradictory)
        self.assertFalse(result['eligible']);self.assertIsNone(result['reward'])
        good=combine(report,JUDGE,PARSED)
        bad=combine(report,{**JUDGE,'verdict':'fail','violations':['Wrong effect.']},PARSED)
        self.assertFalse(group_is_usable([good,bad,result]))
        self.assertTrue(group_is_usable([good,bad]))


if __name__=='__main__':unittest.main()
