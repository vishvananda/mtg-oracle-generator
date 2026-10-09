import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from export_gguf import check_dpo_export


class DpoExport(unittest.TestCase):
    def test_complete_policy_and_reference_are_required(self):
        summary=dict(stage='train',steps=6,planned_steps=6,metrics={'epoch':1.0},
                     reference_unchanged=True,initial_reference_logits_equal=True,
                     reference_sha256='initial',initial_policy_sha256='initial',
                     final_policy_sha256='final',stopped_for_budget=False)
        identity=dict(stage='train',base_model='base',base_revision='revision')
        self.assertEqual(check_dpo_export(summary,identity,summary),'completed_dpo_pilot')
        for key,value in [('steps',5),('stopped_for_budget',True),('reference_unchanged',False),
                          ('final_policy_sha256','initial'),('reference_sha256','changed')]:
            bad={**summary,key:value}
            with self.subTest(key=key),self.assertRaises(ValueError):
                check_dpo_export(bad,identity,bad)
        receipt=copy.deepcopy(summary);receipt['steps']=5
        with self.assertRaisesRegex(ValueError,'receipt'):
            check_dpo_export(summary,identity,receipt)
        with self.assertRaisesRegex(ValueError,'pinned'):
            check_dpo_export(summary,{'stage':'train'},summary)


if __name__=='__main__':unittest.main()
