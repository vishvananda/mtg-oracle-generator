import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from custom_training_data import family, target, prepare, read
from custom_sft_experiment import panel_report, verify_outputs
from data_utils import digest, write_jsonl
from export_custom_training import export, canonical_target


def card(name, text):
    return {'name':name,'rarity':'common','type_line':'Creature — Giant','colors':['R'],
            'mana_cost':'{3}{R}','power':'3','toughness':'3','oracle_text':text,'layout':'normal','set':'test'}


class CustomTrainingTests(unittest.TestCase):
    def test_reminder_fallback_preserves_supported_keyword_without_masking_errors(self):
        d=card('Cycler','Cycling {2} ({2}, Discard this card: Draw a card.)')
        parsed={'status':'parsed','parse_complete':True,'mtgish':'Card(Name: "Cycler", Rules: [Cycling(2)])'}
        class Parser:
            def check(self,value):
                return parsed if '(' in value['oracle_text'] else {'status':'rejected','parse_complete':False}
        canonical,_,policy=canonical_target(d,parsed,Parser())
        self.assertEqual(canonical,d);self.assertEqual(policy,'retained_for_parser')
        class Broken:
            def check(self,value):return {'status':'parser_error','parse_complete':False}
        with self.assertRaisesRegex(ValueError,'no longer parses'):canonical_target(d,parsed,Broken())
        different={**parsed,'mtgish':'Card(Name: "Cycler", Rules: [Cycling(3)])'}
        with self.assertRaisesRegex(ValueError,'changed the parsed mechanics'):canonical_target(d,different,Parser())

    def test_renames_numbers_and_colors_remain_in_one_family(self):
        a=card('First Giant','First Giant deals 3 damage to target green creature.')
        b=card('Second Giant','Second Giant deals 5 damage to target white creature.')
        self.assertEqual(family(a),family(b))
        self.assertEqual(target(a)['oracle_text'],'CARDNAME deals 3 damage to target green creature.')
        self.assertNotIn('set',target(a))
        other=card('First Giant','Search your library for a card named First Giant.')
        self.assertIn('named First Giant',target(other)['oracle_text'])

    def test_prepare_excludes_old_holdouts_and_original_training_from_new_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);official=root/'official';official.mkdir();source=root/'source';source.mkdir()
            cards=[card('Test '+str(i),text) for i,text in enumerate([
                'Flying','Haste','Trample','Vigilance','Reach','First strike','Double strike','Menace','Deathtouch','Lifelink'])]
            rows=[{'id':str(i),'draft':d,'validation':{'parse_complete':True}} for i,d in enumerate(cards)]
            duplicate=copy.deepcopy(rows[0]);duplicate['id']='duplicate';duplicate['draft']['name']='Other Flying Giant'
            rows.append(duplicate)
            source_pin=write_jsonl(source/'candidates.jsonl',rows)
            (source/'summary.json').write_text(json.dumps({'candidates':source_pin}))
            old_train={'example_id':'old','split':'train','source_group_id':family(cards[1]),'target':target(cards[1])}
            files={}
            for name,values in [('train.jsonl',[old_train]),('test.jsonl',[{'source_group_id':family(cards[2])}]),('heldout-reserve.jsonl',[])]:
                files[name]=write_jsonl(official/name,values)
            (official/'system-prompt.txt').write_text('Draft a card.')
            files['system-prompt.txt']={'sha256':digest((official/'system-prompt.txt').read_bytes())}
            (official/'manifest.json').write_text(json.dumps({'files':files}))
            prepare(source/'candidates.jsonl',official,root/'out',3,3)
            chosen=read(root/'out/selected.jsonl');train={r['family_id'] for r in chosen if r['split']=='train'}
            test={r['family_id'] for r in chosen if r['split']=='test'}
            self.assertFalse(train&test)
            self.assertNotIn(family(cards[1]),test)
            self.assertNotIn(family(cards[2]),train|test)
            report=json.loads((root/'out/preparation.json').read_text())
            self.assertEqual(report['excluded']['near_duplicate_custom_family'],1)
            # Changing the frozen official source must fail before any model calls.
            (official/'train.jsonl').write_text(json.dumps({**old_train,'example_id':'changed'})+'\n')
            with self.assertRaisesRegex(ValueError,'checksum'):
                prepare(source/'candidates.jsonl',official,root/'bad',3,3)

    def test_export_never_uses_partial_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'queue').mkdir()
            (root/'preparation.json').write_text('{}')
            (root/'queue/1.json').write_text('{"state":"running"}')
            with self.assertRaisesRegex(ValueError,'incomplete'):
                export(root,root/'export',root/'missing',root/'missing','prompt')
            self.assertFalse((root/'export').exists())

    def test_parser_pass_does_not_count_as_intent_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cases=[{'id':'a','panel':'custom_holdout','source_group_id':'family'}]
            write_jsonl(root/'cases.jsonl',cases)
            rows=[]
            for arm in ('sft','dpo','custom'):
                rows.append({'case_id':'a','arm':arm,'intent_faithful':arm!='custom',
                    'faithful_clean_oracle':arm!='custom',
                    'checks':{'schema_valid':True,'mtgish':{'parse_complete':True}}})
            write_jsonl(root/'scores.jsonl',rows)
            report=panel_report(root/'cases.jsonl',root/'scores.jsonl')['panels']['custom_holdout']
            self.assertEqual(report['counts']['custom']['mtgish_accepted'],1)
            self.assertEqual(report['counts']['custom']['faithful_and_parsed'],0)
            self.assertEqual(report['custom_minus_baseline']['dpo']['intent_faithful']['difference_percentage_points'],-100)


if __name__=='__main__':unittest.main()
