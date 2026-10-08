import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest,write_jsonl
from oracle_retrieval import allowed_ids,document,load_index
from rl_data import select,prepare,export_pairs
from rl_rewards import inspect,combine
from rl_score import bind_candidates,score


def examples():
    rows=[]
    for i in range(40):
        for variant in range(2):
            colors=[['W'],['U'],['B'],['R'],['G'],[]][i%6]
            target={'name':'Example','rarity':'common','type_line':'Artifact','oracle_text':'{T}: Draw a card.','colors':colors}
            if i%2:target.update(type_line='Creature — Wizard',power='2',toughness='2')
            rows.append({'example_id':f'{i}-{variant}','source_group_id':str(i),'source':{'oracle_id':str(i)},
                         'split':'train','style':['broad','complete','shorthand'][i%3],
                         'description':'A card that taps to draw a card.','target':target,
                         'specified_fields':['oracle_text','type_line'],'expressed_constraints':{'oracle_text':target['oracle_text'],'type_line':target['type_line']}})
    return rows


class RLData(unittest.TestCase):
    def test_scoring_binds_only_known_unique_raw_candidates(self):
        cases={'known':{'id':'known'}};row={'case_id':'known','candidate_id':'first','raw':'{}'}
        self.assertEqual(bind_candidates(cases,[row]),[(cases['known'],row)])
        for rows in [[row,row],[{**row,'case_id':'heldout'}],[{**row,'raw':{}}],[{**row,'hit_token_limit':'false'}]]:
            with self.assertRaises(ValueError):bind_candidates(cases,rows)

    def dataset(self,root):
        root.mkdir();rows=examples()
        files={'train.jsonl':write_jsonl(root/'train.jsonl',rows)}
        splits={str(i):'train' for i in range(40)};splits['heldout']='test'
        (root/'split-map.json').write_text(json.dumps(splits))
        (root/'system-prompt.txt').write_text('Return editable card JSON.')
        for name in ['split-map.json','system-prompt.txt']:
            files[name]={'sha256':digest((root/name).read_bytes())}
        (root/'manifest.json').write_text(json.dumps({'files':files}))

    def test_selection_is_stable_balanced_and_family_disjoint(self):
        rows=examples();selected,coverage=select(rows,20,'test')
        self.assertEqual(selected,select(reversed(rows),20,'test')[0])
        self.assertEqual(len({r['source_group_id'] for r in selected}),20)
        self.assertEqual(sum(k.startswith('color_combination:') for k in coverage),6)
        self.assertEqual(sum(k.startswith('style:') for k in coverage),3)
        self.assertTrue(all(not r['explicit_metadata'] for r in selected))
        with self.assertRaises(ValueError):select([{**rows[0],'split':'test'}],1,'test')

    def test_retrieval_excludes_holdout_and_checks_split_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'data';self.dataset(root)
            self.assertNotIn('heldout',allowed_ids(root,'train-reward')[0])
            self.assertIn('heldout',allowed_ids(root,'all-reference')[0])
            (root/'split-map.json').write_text('{}')
            with self.assertRaises(ValueError):allowed_ids(root,'train-reward')

    def test_judge_context_rejects_full_reference_index_and_corruption(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_jsonl(root/'cards.jsonl',[{'split':'train'},{'split':'test'}])
            np.save(root/'vectors.npy',np.eye(2,dtype=np.float32))
            manifest={'scope':'all-reference','holdout_cards_indexed':1,'dimensions':2,
                      'vector_sha256':digest((root/'vectors.npy').read_bytes()),
                      'cards_sha256':digest((root/'cards.jsonl').read_bytes())}
            (root/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(len(load_index(root)[2]),2)
            with self.assertRaises(ValueError):load_index(root,reward_context=True)
            (root/'manifest.json').write_text(json.dumps({**manifest,'scope':'train-reward','holdout_cards_indexed':0}))
            with self.assertRaises(ValueError):load_index(root,reward_context=True)
            (root/'cards.jsonl').write_text('changed')
            with self.assertRaises(ValueError):load_index(root)

    def test_embedding_replaces_self_reference_but_keeps_other_names(self):
        card={'name':'Own Name','type_line':'Creature','oracle_text':'Sacrifice Own Name: Search for a card named Other Name.'}
        text=document(card);self.assertNotIn('Own Name',text);self.assertIn('Other Name',text)
        self.assertIn('CARDNAME',text)

    def test_prepare_has_no_target_in_generator_prompt_and_fails_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';self.dataset(data)
            result=prepare(data,root/'pilot',20,candidates=2)
            self.assertEqual(result['planned_completions'],40)
            prompt=json.loads((root/'pilot/prompts.jsonl').read_text().splitlines()[0])
            self.assertEqual(set(prompt),{'id','prompt'})
            self.assertEqual([m['role'] for m in prompt['prompt']],['system','user'])
            with (data/'train.jsonl').open('a') as stream:stream.write('\n')
            with self.assertRaises(ValueError):prepare(data,root/'other',20)

    def test_export_preserves_exact_generated_text_and_blocks_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';self.dataset(data);pilot=root/'pilot'
            prepare(data,pilot,20,candidates=2)
            case=json.loads((pilot/'cases.jsonl').read_text().splitlines()[0])
            schema={'type':'object'};parser=lambda c:{'status':'parsed','parse_complete':True}
            rows=[]
            for label,verdict in [('good','pass'),('bad','fail')]:
                card=copy.deepcopy(case['reference'])
                if verdict=='fail':card['oracle_text']='{T}: Gain 1 life.'
                raw=json.dumps(card,indent=2)
                assessment=inspect(case,raw,schema,parser)
                judgment={'verdict':verdict,'confidence':'high','oracle_style':2,'violations':[] if verdict=='pass' else ['Wrong effect']}
                assessment=combine(assessment,judgment,parser(card))
                rows.append({'case_id':case['id'],'candidate_id':label,'raw':raw,'assessment':assessment})
            scored=root/'scored.jsonl';write_jsonl(scored,rows)
            result=export_pairs(pilot,scored,root/'pairs')
            self.assertEqual(result['pairs'],1)
            pair=json.loads((root/'pairs/preferences.jsonl').read_text())
            self.assertEqual(pair['chosen'][0]['content'],rows[0]['raw'])
            rows[1]['assessment'].update(eligible=False,reward=None)
            write_jsonl(scored,rows)
            self.assertEqual(export_pairs(pilot,scored,root/'uncertain')['pairs'],0)
            rows[0]['raw']='changed';write_jsonl(scored,rows)
            with self.assertRaises(ValueError):export_pairs(pilot,scored,root/'tampered')

    def test_host_scoring_exports_semantic_pair_with_mocked_external_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';self.dataset(data);pilot=root/'pilot'
            prepare(data,pilot,20,candidates=2)
            case=json.loads((pilot/'cases.jsonl').read_text().splitlines()[0])
            good=copy.deepcopy(case['reference']);bad={**good,'oracle_text':'{T}: Gain 1 life.'}
            rows=[{'case_id':case['id'],'candidate_id':label,'raw':json.dumps(card)} for label,card in [('a',good),('b',bad)]]
            write_jsonl(root/'raw.jsonl',rows)
            def judge(model,payload,*args,**kwargs):
                self.assertTrue(all('reference' not in c and 'assessment' not in c for c in payload['cases']))
                reviews=[{'id':c['id'],'confidence':'high','oracle_style':2,
                          'verdict':'pass' if 'Draw' in c['candidate']['oracle_text'] else 'fail',
                          'violations':[] if 'Draw' in c['candidate']['oracle_text'] else ['Wrong effect']} for c in payload['cases']]
                return {'reviews':reviews},{'usage':[{}],'base_initialization_receipt':'test-only'}
            parser=MagicMock();parser.provenance={'test_only':True}
            parser.check.return_value={'status':'parsed','parse_complete':True}
            with patch('rl_score.MtgishValidator') as factory,patch('rl_score.invoke',side_effect=judge):
                factory.return_value.__enter__.return_value=parser
                result=score(pilot,root/'raw.jsonl',root/'scored','test-judge')
            self.assertEqual(result['gates'],{'faithful_candidate':1,'intent_failure':1})
            self.assertEqual(export_pairs(pilot,root/'scored/scored.jsonl',root/'pairs')['pairs'],1)


if __name__=='__main__':unittest.main()
