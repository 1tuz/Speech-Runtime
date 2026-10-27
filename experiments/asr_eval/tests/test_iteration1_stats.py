"""Golden cases for paired and grouped uncertainty estimates."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import stats


class BootstrapGolden(unittest.TestCase):
    def test_grouped_samples_keep_each_group_and_both_systems_together(self):
        # Each meeting has equal A and B errors (1-1 and 2-2); hence every
        # grouped resample has delta 0, unlike resampling individual utterances.
        a = [1, 0, 2, 0]
        b = [0, 1, 0, 2]
        words = [1, 1, 1, 1]
        grouped = stats.paired_bootstrap(a, words, b, groups=['m1', 'm1', 'm2', 'm2'], replications=100, seed=4)
        self.assertEqual(grouped['difference'], 0)
        self.assertEqual((grouped['ci_low'], grouped['ci_high']), (0, 0))
        self.assertEqual(grouped['groups'], 2)
        self.assertEqual(grouped, stats.paired_bootstrap(a, words, b, groups=['m1', 'm1', 'm2', 'm2'], replications=100, seed=4))
        ungrouped = stats.paired_bootstrap(a, words, b, replications=100, seed=4)
        self.assertLess(ungrouped['ci_low'], 0)
        self.assertGreater(ungrouped['ci_high'], 0)

    def test_group_validation(self):
        for groups in (['a'], ['a', ''], ['a', 'a'], ['a', None]):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                stats.paired_bootstrap([0, 1], [1, 1], [1, 0], groups=groups)

    def test_invalid_counts_and_replications_rejected(self):
        for a, words, b, reps in (([True], [1], [0], 10), ([0], [True], [0], 10),
                                   ([0], [1], [False], 10), ([0], [1], [0], True)):
            with self.subTest(a=a, words=words, b=b, reps=reps), self.assertRaises(ValueError):
                stats.paired_bootstrap(a, words, b, replications=reps)

    def test_invalid_quantile_rejected(self):
        for q in (-0.1, 1.1, float('nan')):
            with self.subTest(q=q), self.assertRaises(ValueError):
                stats.percentile([1, 2], q)

    def test_reports_discarded_zero_denominator_samples(self):
        result = stats.paired_bootstrap([1, 0], [0, 1], [0, 0], replications=100, seed=3)
        self.assertGreater(result['skipped_zero_words'], 0)
        self.assertEqual(result['replications'] + result['skipped_zero_words'], 100)

if __name__ == '__main__':
    unittest.main()
