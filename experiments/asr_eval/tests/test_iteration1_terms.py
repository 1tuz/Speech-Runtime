import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import terms


class TermGoldenCases(unittest.TestCase):
    def setUp(self):
        data = {"terms": [
            {"id": "helm", "class": "tool", "accept": ["Helm"], "accept_translit": ["хелм"]},
            {"id": "word", "class": "wordform", "accept": ["откатить"]},
        ]}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            json.dump(data, fh, ensure_ascii=False)
            path = fh.name
        try:
            self.vocab = terms.Vocabulary.from_path(path)
        finally:
            os.unlink(path)

    def test_cyrillic_word_is_not_latin(self):
        row = terms.classify("откатить", "откатить", self.vocab)
        self.assertEqual(row["exact_latin"], 0)
        self.assertIsNone(row["latin_form_accuracy"])
        self.assertEqual(row["per_term"][0]["spoken_form"], "other")

    def test_corrupt_token_only_matches_one_occurrence(self):
        row = terms.classify("Helm Helm", "Helmm", self.vocab)
        self.assertEqual((row["corrupt"], row["miss"]), (1, 1))

    def test_f1_uses_consistent_counts(self):
        row = terms.classify("Helm Helm", "Helmm", self.vocab)
        self.assertEqual((row["term_tp"], row["term_fp"], row["term_fn"]), (1, 0, 1))
        self.assertAlmostEqual(row["term_precision"], 1)
        self.assertAlmostEqual(row["term_recall"], .5)
        self.assertAlmostEqual(row["term_f1"], 2 / 3)
        agg = terms.aggregate([row])
        self.assertEqual((agg["term_tp"], agg["term_fp"], agg["term_fn"]), (1, 0, 1))
        self.assertAlmostEqual(agg["term_f1"], 2 / 3)

    def test_zero_tp_f1_is_zero_when_errors_exist(self):
        row = terms.classify("Helm", "ничего", self.vocab)
        self.assertEqual(row["term_f1"], 0.0)
        empty = terms.classify("текст", "текст", self.vocab)
        self.assertIsNone(empty["term_f1"])

    def test_transliteration_is_tp(self):
        row = terms.classify("Helm", "хелм", self.vocab)
        self.assertEqual((row["term_tp"], row["term_fp"], row["term_fn"]), (1, 0, 0))
        self.assertEqual(row["term_f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
