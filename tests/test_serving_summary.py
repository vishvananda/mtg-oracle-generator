from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from summarize_serving_quantization import paired


class ServingSummary(unittest.TestCase):
    def outcomes(self, flags):
        return [{'arm': arm, 'case_id': str(i), 'family': str(i//2),
                 'panel': 'development', 'faithful_and_parsed': flag}
                for arm, values in flags.items() for i, flag in enumerate(values)]

    def test_interval_resamples_families_and_reports_paired_changes(self):
        rows = self.outcomes({'before': [False, False, True, True],
                              'after': [True, True, False, False]})
        result = paired(rows, 'before', 'after')
        self.assertEqual(result['families'], 2)
        self.assertEqual((result['gained'], result['lost']), (2, 2))
        self.assertEqual(result['difference_pp'], 0)
        self.assertEqual(result['family_bootstrap_95_percent_interval_pp'], [-100, 100])

    def test_incomplete_or_duplicate_outputs_are_not_valid_comparisons(self):
        rows = self.outcomes({'before': [True, False], 'after': [True, True]})
        with self.assertRaisesRegex(ValueError, 'Unpaired'):
            paired(rows[:-1], 'before', 'after')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            paired(rows+[rows[0]], 'before', 'after')

    def test_family_and_panel_identity_must_match(self):
        rows = self.outcomes({'before': [True], 'after': [True]})
        rows[-1]['family'] = 'another'
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            paired(rows, 'before', 'after')

    def test_exact_pair_test_does_not_overstate_four_discordant_examples(self):
        rows = self.outcomes({'before': [True]*4, 'after': [False]*4})
        for row in rows:
            row['family'] = row['case_id']
        result = paired(rows, 'before', 'after')
        self.assertEqual(result['exact_paired_discordance_p_value'], .125)


if __name__ == '__main__':
    unittest.main()
