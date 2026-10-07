import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from data_utils import digest
from training_rarity import add_training_rarity, rarity_record


class Rarity(unittest.TestCase):
    def setUp(self):
        self.card={'oracle_id':'id','id':'printing','set':'abc','rarity':'rare'}
        self.row={'example_id':'example','source':{'oracle_id':'id','source_sha256':digest(self.card)},
                  'split':'validation','source_group_id':'group','description':'a board wipe',
                  'target':{'type_line':'Sorcery','oracle_text':'Destroy all creatures.'}}

    def test_join_preserves_rules_splits_and_records_printing(self):
        for explicit in [False,True]:
            row=rarity_record(self.row,self.card,explicit=explicit)
            self.assertEqual(row['target']['rarity'],'rare')
            self.assertEqual(row['rarity_provenance']['printing_id'],'printing')
            for key in ['split','source_group_id']: self.assertEqual(row[key],self.row[key])
            self.assertEqual(row['target']['oracle_text'],self.row['target']['oracle_text'])
            if explicit: self.assertIn('Make it rare.',row['description'])
        self.assertNotIn('rarity',self.row['target'])
        self.row['expressed_constraints']={'rarity':'common'}
        self.assertEqual(rarity_record(self.row,self.card)['target']['rarity'],'common')

    def test_bulk_and_individual_hashes_are_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'oracle.gz'
            with gzip.open(path,'wt') as stream: stream.write(json.dumps(self.card)+'\n')
            (root/'manifest.json').write_text(json.dumps({'source':{'source_file':str(path),'download_sha256':digest(path.read_bytes())}}))
            self.assertEqual(add_training_rarity([self.row],root)[0]['target']['rarity'],'rare')
            self.row['source']['source_sha256']='bad'
            with self.assertRaisesRegex(ValueError,'snapshot'): add_training_rarity([self.row],root)
            path.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'hash'): add_training_rarity([self.row],root)

    def test_broad_corpus_follows_verified_parent_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); parent=root/'oracle'; parent.mkdir()
            broad=root/'broad'; broad.mkdir()
            bulk=parent/'oracle.gz'
            with gzip.open(bulk,'wt') as stream: stream.write(json.dumps(self.card)+'\n')
            manifest=parent/'manifest.json'
            manifest.write_text(json.dumps({'source':{'source_file':str(bulk),'download_sha256':digest(bulk.read_bytes())}}))
            (broad/'manifest.json').write_text(json.dumps({'source_root':'../oracle','source_manifest_sha256':digest(manifest.read_bytes())}))
            result=add_training_rarity([self.row],broad)
            self.assertEqual(result[0]['target']['rarity'],'rare')
            self.assertEqual(result[0]['split'],'validation')
            manifest.write_text(manifest.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'parent source manifest hash'):
                add_training_rarity([self.row],broad)

if __name__=='__main__': unittest.main()
