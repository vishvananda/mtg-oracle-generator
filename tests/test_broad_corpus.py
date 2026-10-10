import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from corpus_workers import prompt_config, run_job
from full_corpus import project
from prepare_broad_corpus import projections


def card():
    source={'oracle_id':'fixture','name':'Fixture Walker','layout':'normal','split':'train',
            'source_group_id':'fixture-group','source_sha256':'source-hash','source_updated_at':'fixture',
            'type_line':'Legendary Planeswalker — Nissa','colors':['G'],'mana_cost':'{1}{G}{G}',
            'loyalty':'3','oracle_text':'+1: Create a 0/1 green Plant creature token.\n−7: Draw seven cards.'}
    source['projections']=project(source)
    return source


class BroadCorpusTests(unittest.TestCase):
    def test_broad_targets_keep_full_card_without_claiming_full_input_coverage(self):
        source=card(); original=copy.deepcopy(source)
        rows=projections(source)
        self.assertEqual(source,original)
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertIn('−7:',row['draft_target']['oracle_text'])
            self.assertEqual(row['target'],{})
            self.assertEqual(row['specified_fields'],[])
            self.assertEqual(row['field_annotation'],'not_extracted')
            self.assertEqual(row['rules_coverage'],'summary')
        self.assertEqual(rows,projections(source))
        self.assertTrue({r['id'] for r in rows}.isdisjoint({r['id'] for r in source['projections']}))

    def test_broad_job_uses_frozen_guide_and_omission_controls(self):
        config={'teacher':'fixture-model','reviewer':'fixture-model',**prompt_config('plausible_completion')}
        guide=config['reviewer_terminology']
        self.assertEqual(config['terminology_sha256'],digest(guide.encode()))
        source=card(); source['projections']=projections(source)
        calls=[]
        def fake_invoke(model,payload,schema,output):
            calls.append(payload)
            if 'projections' in payload:
                return {'descriptions':[{'id':p['id'],'description':'A planeswalker that grows plants and has a card-draw ultimate.',
                                         'coverage_notes':'Costs and quantities omitted.'} for p in payload['projections']]}, {'prompt_sha256':'p','output_sha256':'t'}
            reviews={}
            for case in payload['cases']:
                good=case['description'] in {
                    'A planeswalker that grows plants and has a card-draw ultimate.',
                    'A planeswalker that makes plants and has a big card-draw ultimate.',
                    'A red instant for rummaging.'}
                reviews[case['id']]={'verdict':'pass' if good else 'reject','issues':[] if good else ['Contradiction.']}
            return {'reviews':reviews},{'output_sha256':'r'}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'queue').mkdir(); (root/'runs/00001').mkdir(parents=True)
            job=root/'queue/00001.json'
            job.write_text(json.dumps({'id':'00001','state':'pending','cards':[source]}))
            # No live prompt files are available at execution; the config is sufficient.
            with patch('corpus_workers.PROJECT',root/'missing'),patch('corpus_workers.invoke',side_effect=fake_invoke):
                run_job(job,config)
            saved=json.loads(job.read_text())
            self.assertEqual(saved['state'],'completed')
            self.assertEqual(saved['accepted'],2)
            self.assertEqual(saved['completion_controls_checked'],4)
            self.assertIn(guide,calls[0]['instructions'])
            self.assertIn(guide,calls[1]['instructions'])
            rows=[json.loads(s) for s in (root/'runs/00001/results.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['target']['loyalty'],'3')
            self.assertEqual(rows[0]['expressed_constraints'],{})
            self.assertEqual(rows[0]['description_policy'],'plausible_completion')

    def test_broad_job_refuses_legacy_exact_only_review(self):
        source=card(); source['projections']=projections(source)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'queue').mkdir()
            job=root/'queue/00001.json'
            job.write_text(json.dumps({'id':'00001','state':'pending','cards':[source]}))
            with patch('corpus_workers.invoke') as invoke:
                run_job(job,{'instructions':'old prompt'})
                invoke.assert_not_called()
            self.assertIn('plausible-completion review policy',json.loads(job.read_text())['error'])


if __name__=='__main__': unittest.main()
