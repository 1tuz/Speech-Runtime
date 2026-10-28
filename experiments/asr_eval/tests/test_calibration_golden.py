"""Ручные синтетические пары для калибровки, не образцы качества ASR."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli
import norm
import terms
import wer


class CalibrationGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Намеренно локальный словарь: примеры не настраивают test-выборку.
        vocabulary = {"terms": [
            {"id": "kubernetes", "class": "tools", "accept": ["Kubernetes"], "accept_translit": ["кубернетес"]},
            {"id": "postgresql", "class": "tools", "accept": ["PostgreSQL", "Postgres"], "accept_translit": ["постгрес"]},
            {"id": "jvm", "class": "abbrev", "accept": ["JVM"], "accept_translit": ["джей ви эм"]},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terms.json"
            path.write_text(json.dumps(vocabulary, ensure_ascii=False), encoding="utf-8")
            cls.vocab = terms.Vocabulary.from_path(str(path))

    def test_code_switching_wer_and_terms_have_different_definitions(self):
        ref = "развернули Kubernetes и PostgreSQL"
        hyp = "развернули кубернетес и Postgres"
        row = wer.score_pair(ref, hyp)
        # Две замены из четырёх слов; транслитерация и синоним — term TP.
        self.assertEqual((row["words"], row["substitutions"], row["deletions"], row["insertions"]), (4, 2, 0, 0))
        result = terms.classify(ref, hyp, self.vocab)
        self.assertEqual((result["exact_latin"], result["translit"], result["term_tp"]), (1, 1, 2))
        self.assertEqual(result["latin_form_accuracy"], 0.5)

    def test_jvm_transliteration_changes_wer_but_is_term_hit(self):
        row = wer.score_pair("JVM", "джей ви эм")
        self.assertEqual((row["words"], row["errors"]), (1, 3))
        metric = terms.classify("JVM", "джей ви эм", self.vocab)
        self.assertEqual((metric["translit"], metric["term_tp"], metric["term_fn"]), (1, 1, 0))
        self.assertEqual(metric["latin_form_accuracy"], 0.0)

    def test_leading_zero_in_fraction_is_deletion(self):
        row = wer.score_pair("релиз 1.05", "релиз 1.5")
        self.assertEqual((row["words"], row["substitutions"], row["deletions"], row["insertions"]), (5, 0, 1, 0))
        self.assertEqual(row["char_errors"], 4)
        self.assertNotEqual(norm.normalize("1.05"), norm.normalize("1.5"))

    def test_version_is_a_distinct_token(self):
        row = wer.score_pair("Kubernetes v1.29.0", "Kubernetes v1.29.1")
        self.assertEqual((row["words"], row["substitutions"], row["char_errors"]), (2, 1, 1))
        # Точность имени модели не подтверждает точность версии.
        metric = terms.classify("Kubernetes v1.29.0", "Kubernetes v1.29.1", self.vocab)
        self.assertEqual(metric["term_recall"], 1.0)

    def test_command_flag_is_checked_independently_of_wer(self):
        ref, hyp = "kubectl get pods -n prod", "kubectl get pods n prod"
        self.assertEqual(wer.score_pair(ref, hyp)["errors"], 0)
        command = cli.command_accuracy([{"reference": ref, "hypothesis": hyp, "commands": [ref]}])
        self.assertEqual((command["total"], command["correct"], command["accuracy"]), (1, 0, 0.0))

    def test_repeated_corrupt_term_cannot_reuse_one_token(self):
        result = terms.classify("Kubernetes Kubernetes", "Kubernete", self.vocab)
        self.assertEqual((result["corrupt"], result["miss"]), (1, 1))
        self.assertEqual((result["term_tp"], result["term_fn"]), (1, 1))
        self.assertAlmostEqual(result["term_f1"], 2 / 3)

    def test_insertions_on_empty_reference_count_in_corpus(self):
        corpus = wer.corpus([{"reference": "", "hypothesis": "JVM"},
                             {"reference": "PostgreSQL", "hypothesis": "PostgreSQL"}])
        self.assertEqual((corpus["words"], corpus["insertions"], corpus["wer"]), (1, 1, 1.0))
        self.assertEqual(corpus["cer"], 3 / 10)

    def test_no_commands_has_undefined_accuracy(self):
        result = cli.command_accuracy([{"reference": "сервис работает", "hypothesis": "сервис работает"}])
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["accuracy"])

if __name__ == "__main__":
    unittest.main()
