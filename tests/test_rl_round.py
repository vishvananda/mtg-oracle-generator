import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_gpu import sha,save
from rl_round_run import checked_audit
from rl_round_expand import merge
from rl_parser_cache import ParserCache


class RoundIntegrity(unittest.TestCase):
    def test_audit_gate_rejects_changed_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);review=root/'refined';review.mkdir()
            save(review/'review.json',{'pairs':8});(review/'preferences.jsonl').write_text('{}\n')
            save(root/'audit-quality.json',{'passed':True,'review_directory':'refined',
                'review_sha256':sha(review/'review.json'),'preferences_sha256':sha(review/'preferences.jsonl')})
            self.assertEqual(checked_audit(root),review)
            (review/'preferences.jsonl').write_text('changed')
            with self.assertRaisesRegex(ValueError,'quality review'):checked_audit(root)

    def test_merge_keeps_families_disjoint_and_full_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);review=root/'input';review.mkdir()
            save(root/'reserved-families.json',{'source_group_ids':['heldout']})
            (review/'preferences.jsonl').write_text('{}\n'*10)
            metadata=[{'approved':True,'source_group_id':str(i),'chosen_origin':'sampled_sft'} for i in range(10)]
            path=review/'audit.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in metadata))
            save(review/'review.json',{'independent_review':True,'approved_pairs':10,
                 'approved_pairs_sha256':sha(review/'preferences.jsonl')})
            self.assertEqual(merge(root,[review],root/'merged')['approved_pairs'],8)
            metadata[0]['source_group_id']='heldout';path.write_text(''.join(json.dumps(r)+'\n' for r in metadata))
            with self.assertRaisesRegex(ValueError,'Evaluation family'):merge(root,[review],root/'bad')

    def test_parser_cache_pins_grammar_and_does_not_cache_infrastructure_errors(self):
        class Parser:
            provenance={'revision':'a'}
            calls=0
            def close(self):pass
            def check(self,card):
                Parser.calls+=1
                return {'status':'parser_error' if card.get('error') else 'parsed','parse_complete':not card.get('error')}
        with tempfile.TemporaryDirectory() as tmp,patch('rl_parser_cache.MtgishValidator',Parser):
            with ParserCache(tmp) as cache:
                cache.check({'name':'x'});cache.check({'name':'x'})
                self.assertEqual(Parser.calls,1)
                cache.check({'error':True});cache.check({'error':True})
                self.assertEqual(Parser.calls,3)
            Parser.provenance={'revision':'b'}
            with ParserCache(tmp) as cache:cache.check({'name':'x'})
            self.assertEqual(Parser.calls,4)


if __name__=='__main__':unittest.main()
