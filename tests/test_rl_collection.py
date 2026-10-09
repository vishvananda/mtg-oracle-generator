import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_gpu import sha,save
from rl_pipeline import verify_collected


class CollectionIntegrity(unittest.TestCase):
    def fixture(self,root,stage='sample'):
        package=root/'package';output=root/'output';package.mkdir();output.mkdir()
        save(package/'manifest.json',{'identity':'frozen package'})
        save(package/'config.json',{'candidates_per_prompt':2})
        prompt={'id':'a','prompt':[{'role':'system','content':'draft'},{'role':'user','content':'giant'}]}
        for name in ('prompts','development'):
            (package/(name+'.jsonl')).write_text(json.dumps(prompt)+'\n')
        save(output/'identity.json',{'stage':stage,'package_manifest_sha256':sha(package/'manifest.json')})
        phase={'stage':stage}
        files=[('development','sft-development' if stage=='sample' else 'dpo-development',1)]
        if stage=='sample':files.append(('candidates','candidates',2))
        for key,name,copies in files:
            path=output/(name+'.jsonl')
            path.write_text(''.join(json.dumps({'case_id':'a','candidate_id':f'a-{i:02d}','raw':'{}'})+'\n' for i in range(copies)))
            receipt={'complete':True,'sha256':sha(path),'cases':1,'completions':copies}
            save(path.with_suffix('.manifest.json'),receipt);phase[key]=receipt
        if stage=='train':
            summary={'stage':stage,'steps':1,'pairs':32};phase.update(summary)
            save(output/'training-summary.json',summary)
            (output/'adapter').mkdir()
            for name in ('adapter_config.json','adapter_model.safetensors'):(output/'adapter'/name).write_bytes(b'adapter')
        save(output/'phase-complete.json',phase)
        return package,output,phase

    def test_complete_sample_and_train_are_accepted(self):
        for stage in ('sample','train'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                package,output,phase=self.fixture(Path(tmp),stage)
                self.assertEqual(verify_collected(output,package,stage),phase)

    def test_completion_marker_cannot_hide_a_late_prediction_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,output,_=self.fixture(Path(tmp))
            path=output/'candidates.jsonl';path.write_text(path.read_text().splitlines()[0]+'\n')
            with self.assertRaisesRegex(ValueError,'incomplete or changed'):
                verify_collected(output,package,'sample')

    def test_duplicate_candidate_rejected_even_with_matching_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,output,phase=self.fixture(Path(tmp))
            path=output/'candidates.jsonl';row=path.read_text().splitlines()[0]+'\n';path.write_text(row*2)
            phase['candidates']['sha256']=sha(path)
            save(path.with_suffix('.manifest.json'),phase['candidates']);save(output/'phase-complete.json',phase)
            with self.assertRaisesRegex(ValueError,'duplicate candidates'):
                verify_collected(output,package,'sample')

    def test_changed_package_and_missing_adapter_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            package,output,_=self.fixture(Path(tmp),'train')
            (output/'adapter/adapter_model.safetensors').write_bytes(b'')
            with self.assertRaisesRegex(ValueError,'Empty trained adapter'):
                verify_collected(output,package,'train')
            save(package/'manifest.json',{'identity':'different job'})
            with self.assertRaisesRegex(ValueError,'job/package differs'):
                verify_collected(output,package,'train')


if __name__=='__main__':unittest.main()
