import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from corpus_checks import deterministic_checks, source_name_issues
from corpus_recovery import assess, process_batch, apply_overlay, atomic, snapshot, load_overlay, prepare
from data_utils import digest, write_jsonl
from full_corpus import rules, project
from export_full_corpus import export


def row(description='A sorcery: I lose 3 life.'):
    return {'example_id':'row1','split':'train','source_group_id':'group1',
            'source':{'oracle_id':'card1','source_sha256':'sha'},'style':'mechanics',
            'description':description,'target':{'type_line':'Sorcery','oracle_text':'Each opponent loses 3 life.'},
            'expressed_constraints':{'type_line':'Sorcery','oracle_text':'Each opponent loses 3 life.'},
            'rules_coverage':'all','fidelity_controls_rejected':3,
            'fidelity_review':{'verdict':'reject','issues':['Wrong player.']},
            'deterministic_issues':[],'status':'quarantined'}


def source(): return {'oracle_id':'card1','source_sha256':'sha','name':'Fixture Card'}
def audit(): return {'changes':{},'holds':{}}


class ChecksTests(unittest.TestCase):
    def test_separate_face_costs_and_wrong_cost(self):
        p={'style':'complete','target':{'mana_cost':'{1}{B} // {R}',
             'card_faces':[{'mana_cost':'{1}{B}','type_line':'Creature — Human'},
                           {'mana_cost':'{R}','type_line':'Sorcery — Adventure'}]}}
        text='A Human creature for {1}{B}, with a sorcery Adventure for {R}.'
        self.assertEqual(deterministic_checks(p,text),[])
        self.assertIn('missing_or_changed_mana_cost:{1}{B}',deterministic_checks(p,text.replace('{1}{B}','{B}')))

    def test_plural_types_aura_and_punctuation(self):
        for typ, desc in [('Enchantment — Aura Role','Enchantment Aura Roles'),('Stickers','A sticker sheet'),
                          ('Enchantment — Aura','An Aura'),('Creature — Elemental?','An Elemental? creature')]:
            self.assertEqual(deterministic_checks({'style':'complete','target':{'type_line':typ}},desc),[])

    def test_token_name_not_leak_but_unrequested_name_is(self):
        p={'target':{},'reference_draft':{'type_line':'Token Creature — Zombie','oracle_text':'Decayed'}}
        self.assertEqual(source_name_issues({'name':'Zombie'},p,'A Zombie token with decayed.'),[])
        self.assertEqual(source_name_issues({'name':'Royal Impostor'},p,'Make Royal Impostor a zombie.'),['source_name_leak'])
        self.assertEqual(source_name_issues({'name':'Opt'},p,'An optional effect.'),[])
        for name, typ, colors in [('Red Dragon','Creature — Dragon',['R']),('Artifact Zombie','Token Artifact Creature — Zombie',[]),
                                  ('Human // Wolf','Token Creature — Human // Token Creature — Wolf',[])]:
            projection={'target':{'type_line':typ,'colors':colors}}
            self.assertEqual(source_name_issues({'name':name},projection,'Make a '+name+'.'),[])
        self.assertEqual(source_name_issues({'name':'Regeneration'},{'target':{'oracle_text':'{G}: Regenerate enchanted creature.'}},
                                           'An aura with regeneration.'),[])

    def test_common_type_shorthand(self):
        for typ, desc in [('Artifact','A mana rock'),('Creature','A flyer'),('Planeswalker','A walker')]:
            self.assertEqual(deterministic_checks({'style':'mechanics','target':{'type_line':typ}},desc),[])

    def test_sparse_requests_still_cannot_leak_exact_stats_or_cost(self):
        errors=deterministic_checks({'style':'concept','target':{'type_line':'Creature — Giant'}},'A 3/3 Giant for {3}{R}.')
        self.assertIn('detail_level_leaked_mana_cost',errors)
        self.assertIn('detail_level_leaked_exact_stats',errors)

    def test_shared_short_name_refers_to_current_face(self):
        self.assertEqual(rules('Transform Henrika.','Henrika Domnathi',[('Henrika, Infernal Seer','Draft Face 2')]),'Transform CARDNAME.')
        self.assertEqual(rules('Transform Garruk.','Garruk Relentless',[('Garruk, the Veil-Cursed','Draft Face 2')]),'Transform CARDNAME.')
        self.assertEqual(rules('Meld them into Hanweir, the Writhing Township.','Hanweir Battlements',
                               [('Hanweir, the Writhing Township','Draft Face 2')]),'Meld them into Draft Face 2.')
        self.assertEqual(rules('Jecht Beam — Draw a card.',"Braska's Final Aeon",[('Jecht, Reluctant Guardian','Draft Face 1')]),'Jecht Beam — Draw a card.')


class RecoveryTests(unittest.TestCase):
    def record(self, original=None):
        return assess(original or row(),source(),audit(),Draft202012Validator({}))

    def run_fake(self, fake, original=None):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp); (output/'records').mkdir()
            path=output/'records/row1.json'; atomic(path,self.record(original))
            config={'max_repair_attempts':2,'model':'fixture','instructions':'review','reasoning_effort':'medium'}
            process_batch(output,[path],config,call=fake)
            return json.loads(path.read_text())

    def responder(self, responses, calls, fail_controls=False):
        def call(model,payload,schema,run,**kwargs):
            calls.append(payload)
            # The real cases and shuffled controls must all be graded independently.
            reviews={}
            for case in payload['cases']:
                desc=case['description']
                if desc in ('A sorcery: I lose 3 life.','A sorcery that makes each opponent lose 3 life.','A sorcery: opponents lose 3 life.'):
                    response=responses[len(calls)-1]
                else:
                    good=desc in ('A planeswalker that makes plants and has a big card-draw ultimate.','A red instant for rummaging.',
                                  'A two-sided creature I can cast on either side, whose front can transform into its back.')
                    response={'verdict':'pass' if good else 'reject','issues':[] if good else ['Wrong mechanic.'],'suggested_description':None}
                    if fail_controls: response={'verdict':'pass','issues':[],'suggested_description':None}
                reviews[case['id']]=response
            return {'reviews':reviews},{'output_sha256':str(len(calls))}
        return call

    def test_repair_requires_fresh_review_and_preserves_original(self):
        calls=[]
        fake=self.responder([
            {'verdict':'reject','issues':['Wrong player.'],'suggested_description':'A sorcery that makes each opponent lose 3 life.'},
            {'verdict':'pass','issues':[],'suggested_description':None}],calls)
        result=self.run_fake(fake)
        self.assertEqual(result['state'],'accepted'); self.assertEqual(result['repair_attempts'],1)
        self.assertEqual(result['original'],row()); self.assertEqual(result['row']['target'],row()['target'])
        self.assertEqual(len(calls),2)
        self.assertNotIn('history',json.dumps(calls[1]))
        self.assertEqual(len(result['history']),2)

    def test_failed_controls_never_admit_rows(self):
        calls=[]
        fake=self.responder([{'verdict':'pass','issues':[],'suggested_description':None}],calls,True)
        with self.assertRaisesRegex(ValueError,'fidelity controls'): self.run_fake(fake)

    def test_two_repair_attempts_then_quarantine(self):
        calls=[]
        fake=self.responder([
            {'verdict':'reject','issues':['Wrong.'],'suggested_description':'A sorcery that makes each opponent lose 3 life.'},
            {'verdict':'uncertain','issues':['Unclear.'],'suggested_description':'A sorcery: opponents lose 3 life.'},
            {'verdict':'uncertain','issues':['Still unclear.'],'suggested_description':'A sorcery that makes each opponent lose 3 life.'}],calls)
        result=self.run_fake(fake)
        self.assertEqual(result['state'],'quarantined'); self.assertEqual(result['repair_attempts'],2)
        self.assertEqual(len(calls),3)

    def test_script_false_positive_recovered_without_rewrite(self):
        original=row('A sorcery that makes each opponent lose 3 life.')
        original.update(fidelity_review={'verdict':'pass','issues':[]},deterministic_issues=['source_name_leak'])
        recovered=self.record(original)
        self.assertEqual(recovered['state'],'accepted'); self.assertEqual(recovered['original'],original)

    def test_target_change_also_rechecks_previously_accepted(self):
        original=row('A sorcery that makes each opponent lose 3 life.')
        original.update(status='accepted',fidelity_review={'verdict':'pass','issues':[]})
        fixed={'type_line':'Sorcery','oracle_text':'Each opponent loses 4 life.'}
        changes={'changes':{'card1':{'new':fixed,'projections':{'mechanics':{'target':fixed,'draft_target':fixed}}}},'holds':{}}
        record=assess(original,source(),changes,Draft202012Validator({}))
        self.assertEqual(record['state'],'pending'); self.assertTrue(record['target_changed'])
        untouched,issues=apply_overlay(original,{},changes)
        self.assertEqual(issues,['target_audit_pending'])

    def test_source_conflict_stays_held(self):
        a=audit(); a['holds']['card1']={'issues':['source_layout_transform_conflict']}
        r=assess(row(),source(),a,Draft202012Validator({}))
        self.assertEqual(r['state'],'source_needs_review')

    def test_overlay_rejects_stale_original_split_and_target_mutations(self):
        original=row(); record=self.record()
        with self.assertRaisesRegex(ValueError,'Original row changed'):
            apply_overlay({**original,'description':'Changed'}, {'row1':record},audit())
        for key,value in [('split','test'),('target',{'oracle_text':'Draw a card.'})]:
            bad=copy.deepcopy(record); bad['row'][key]=value
            with self.assertRaises(ValueError): apply_overlay(original,{'row1':bad},audit())
        _,issues=apply_overlay(original,{'row1':record},audit())
        self.assertEqual(issues,['recovery_pending'])

    def test_recovery_snapshot_exports_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); root=base/'corpus'; root.mkdir(); (root/'queue').mkdir(); (root/'runs/00001').mkdir(parents=True)
            raw={'oracle_id':'card1','name':'Fixture Card','layout':'normal','type_line':'Sorcery',
                 'oracle_text':'Each opponent loses 3 life.','mana_cost':'{B}','colors':['B'],'rarity':'common'}
            bulk=base/'raw.jsonl.gz'
            with gzip.open(bulk,'wt') as f: f.write(json.dumps(raw)+'\n')
            src={**source(),'source_sha256':digest(raw),'source_faces':[], 'projections':project(raw)}
            files={'sources.jsonl':write_jsonl(root/'sources.jsonl',[src])}
            (root/'draft-schema.json').write_text(json.dumps({'type':'object'}))
            (root/'student-prompt.txt').write_text('Generate card JSON.')
            (root/'generation-config.json').write_text('{}')
            atomic(root/'manifest.json',{'version':'fixture','files':files,'source':{'source_file':str(bulk),'download_sha256':digest(bulk.read_bytes())}})
            original=row('A sorcery that makes each opponent lose 3 life.')
            original['source']['source_sha256']=digest(raw)
            original.update(fidelity_review={'verdict':'pass','issues':[]},deterministic_issues=['source_name_leak'])
            atomic(root/'queue/00001.json',{'id':'00001','state':'completed','fidelity_controls_rejected':3})
            path=root/'runs/00001/results.jsonl'; write_jsonl(path,[original]); before=path.read_bytes()
            recovery=base/'recovery'; result=prepare(root,recovery)
            self.assertEqual(result['states'],{'accepted':1})
            frozen=base/'snapshot'; snapshot(root,recovery,frozen)
            continued=base/'continued'; resumed=prepare(root,continued,prior=frozen)
            self.assertEqual(resumed['states'],{'accepted':1})
            self.assertEqual(json.loads((continued/'records/row1.json').read_text())['original'],original)
            report=export(root,base/'export',frozen)
            self.assertGreaterEqual(report['accepted_exported'],1); self.assertEqual(report['quarantined'],0)
            self.assertEqual(path.read_bytes(),before)
            (frozen/'overrides.jsonl').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'snapshot hash mismatch'): load_overlay(root,frozen)


if __name__=='__main__': unittest.main()
