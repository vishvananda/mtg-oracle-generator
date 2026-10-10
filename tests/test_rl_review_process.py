import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rl_review_process import same_worker,review_process,should_look_ahead,lookahead_minutes,batch_limit


class ReviewScheduling(unittest.TestCase):
    def test_lookahead_requires_broad_yield_and_stops_near_target_or_batch_limit(self):
        self.assertFalse(should_look_ahead(1,43,None))
        self.assertTrue(should_look_ahead(2,304,261))
        self.assertFalse(should_look_ahead(2,900,261))
        self.assertFalse(should_look_ahead(3,600,261))

    def test_lookahead_preserves_training_budget_and_counts_active_reservations(self):
        ledger={'reservations':[{'maximum_usd':10.541751}]}
        minutes=lookahead_minutes(ledger,20,.041667)
        self.assertEqual(minutes,166)
        self.assertLessEqual(10.541751+(minutes+60)*.041667,20)
        ledger['reservations'].append({'maximum_usd':6.66672})
        self.assertIsNone(lookahead_minutes(ledger,20,.041667))

    def test_explicit_fourth_batch_is_allowed_without_enabling_a_fifth(self):
        self.assertEqual(batch_limit({}),3)
        limit=batch_limit({'maximum_batches':4})
        self.assertTrue(should_look_ahead(3,571,267,maximum_batches=limit))
        self.assertFalse(should_look_ahead(4,835,267,maximum_batches=limit))
        for value in (0,-1,True,4.0,'4'):
            with self.assertRaisesRegex(ValueError,'integer'):batch_limit({'maximum_batches':value})
        ledger={'reservations':[{'maximum_usd':10.541751},{'maximum_usd':6.66672}]}
        minutes=lookahead_minutes(ledger,28,.041667)
        self.assertEqual(minutes,180)
        self.assertLessEqual(17.208471+(minutes+60)*.041667,28)

    def test_pid_reuse_or_changed_command_is_not_an_active_review_worker(self):
        receipt={'pid':42,'start_ticks':'100','command':['python','review.py']}
        with patch('rl_review_process.process_identity',return_value=receipt):self.assertTrue(same_worker(receipt))
        for live in (None,{**receipt,'start_ticks':'200'},{**receipt,'command':['unrelated']}):
            with patch('rl_review_process.process_identity',return_value=live):self.assertFalse(same_worker(receipt))

    def test_attached_worker_finishes_without_duplicate_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record=root/'worker.json';result=root/'review.json';argv=['python','review.py']
            record.write_text(json.dumps({'pid':42,'start_ticks':'100','command':argv}))
            def completed(_):
                result.write_text('{"approved_pairs":10}');return False
            with patch('rl_review_process.same_worker',side_effect=completed),patch('rl_review_process.subprocess.Popen') as launch:
                review_process(argv,record,result,root)
                launch.assert_not_called()
            result.unlink()
            with patch('rl_review_process.same_worker',return_value=False),patch('rl_review_process.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError,'without final results'):review_process(argv,record,result,root)
                launch.assert_not_called()


if __name__=='__main__':unittest.main()
