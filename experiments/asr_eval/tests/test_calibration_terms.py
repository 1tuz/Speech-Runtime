"""Синтетические примеры проверки словаря; аудио и эталон здесь отсутствуют."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cli
import norm
import terms

class DictionaryCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vocab = terms.Vocabulary.from_path(str(ROOT / "terms.json"))

    def test_wordform_note_does_not_label_cyrillic_as_latin(self):
        with (ROOT / "terms.json").open(encoding="utf-8") as stream:
            note = json.load(stream)["note"]
        self.assertNotIn("'exact_latin' означает точное совпадение русской", note)

    def test_technical_terms_and_transliterations(self):
        cases = {"Kubernetes": ("kubernetes", "exact_latin"),
                 "кубернетес": ("kubernetes", "translit"),
                 "PostgreSQL": ("postgresql", "exact_latin"),
                 "постгрес": ("postgresql", "translit"),
                 "kubectl": ("kubectl", "exact_latin"),
                 "кубектл": ("kubectl", "translit"),
                 "JVM": ("jvm", "exact_latin"),
                 "джей ви эм": ("jvm", "translit")}
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                matches = self.vocab.occurrences(norm.tokens(phrase))
                self.assertEqual([(m.term_id, m.form_kind) for m in matches], [expected])

    def test_command_flag_loss_requires_command_metric(self):
        item = {"reference": "kubectl get pods -n prod",
                "hypothesis": "kubectl get pods n prod",
                "commands": ["kubectl get pods -n prod"]}
        result = cli.command_accuracy([item])
        self.assertEqual((result["correct"], result["total"]), (0, 1))

    def test_all_normalized_forms_have_one_owner(self):
        owners = {}
        for form, term, _ in self.vocab.forms:
            previous = owners.setdefault(form, term.id)
            self.assertEqual(previous, term.id, f"{form}: {previous} vs {term.id}")
