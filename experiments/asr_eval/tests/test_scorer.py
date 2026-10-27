"""Self-checks norm-v1 / wer / terms / stats / dataset. Только stdlib.

Запуск:
    python3 -m unittest discover -s experiments/asr_eval/tests -t experiments/asr_eval

Каждый тест фиксирует правило из V8 §2.5/§3.1/§3.2, а не реализацию: ожидаемые
значения — из таблиц V8 либо из арифметики, посчитанной вручную в комментарии.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import align  # noqa: E402
import dataset as dataset_mod  # noqa: E402
import norm  # noqa: E402
import stats  # noqa: E402
import terms as terms_mod  # noqa: E402
import wer as wer_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.dirname(HERE)


class NormalizerRules(unittest.TestCase):
    """V8 §2.5, таблица правил нормализации v1."""

    def test_lowercase_and_punctuation(self):
        self.assertEqual(norm.normalize("Получили, Ошибку."), "получили ошибку")

    def test_latin_preserved_and_lowercased(self):
        # V8: латиница не транслитерируется; регистр для WER снимается.
        self.assertEqual(norm.normalize("Kubernetes в проде"), "kubernetes в проде")

    def test_yo_folded_in_both_sides(self):
        self.assertEqual(norm.normalize("ещё"), norm.normalize("еще"))

    def test_translit_is_not_equal_to_latin(self):
        # V8 §2.5: транслит не допустим в WER.
        self.assertNotEqual(norm.normalize("kubernetes"), norm.normalize("кубернетес"))

    def test_cicd_forms_equivalent(self):
        # V8 §2.5: `CI/CD` = `ci cd` = `ci-cd` = `cicd`.
        expected = norm.normalize("cicd")
        for form in ("CI/CD", "ci cd", "ci-cd", "CI CD"):
            self.assertEqual(norm.normalize(form), expected, form)

    def test_glue_independent_of_ascii_neighbours(self):
        # Регрессия: `_glue` склеивал весь латинский прогон и терял CI/CD в контексте.
        self.assertEqual(norm.normalize("kubectl CI/CD Helm"), "kubectl cicd helm")
        self.assertEqual(norm.normalize("CI/CD helm"), "cicd helm")
        self.assertEqual(norm.normalize("helm CI/CD"), "helm cicd")
        self.assertEqual(norm.normalize("kubectl ci cd helm"), "kubectl cicd helm")
        self.assertEqual(norm.tokens("kubectl CI/CD Helm", keep_case=True), ["kubectl", "CICD", "Helm"])

    def test_glue_does_not_invent_pairs(self):
        # Никаких склеек, кроме объявленных: `kube ctl` ≠ `kubectl`.
        self.assertEqual(norm.normalize("kube ctl"), "kube ctl")
        self.assertNotEqual(norm.normalize("kube ctl"), norm.normalize("kubectl"))

    def test_postgresql_not_split(self):
        # V8 §2.5: разрыв на два слова недопустим.
        self.assertNotEqual(norm.normalize("postgresql"), norm.normalize("postgre sql"))

    def test_k8s_is_not_kubernetes(self):
        self.assertNotEqual(norm.normalize("k8s"), norm.normalize("kubernetes"))

    def test_digits_inside_terms_untouched(self):
        # §2.5 запрещает схлопывать идентификаторы с цифрами.
        self.assertEqual(norm.normalize("k8s p50 S3"), "k8s p50 s3")

    def test_integer_expanded(self):
        self.assertEqual(norm.normalize("29"), "двадцать девять")
        self.assertEqual(norm.normalize("100"), "сто")
        self.assertEqual(norm.normalize("1000"), "одна тысяча")
        self.assertEqual(norm.normalize("21"), "двадцать один")

    def test_decimal_expanded_consistently(self):
        # V8 §2.5: `1.29` и «один точка двадцать девять» должны совпасть.
        self.assertEqual(norm.normalize("1.29"), norm.normalize("один точка двадцать девять"))

    def test_comma_decimal_same_as_dot(self):
        self.assertEqual(norm.normalize("2,5 ГБ"), norm.normalize("2.5 ГБ"))

    def test_version_and_cidr_stay_digits(self):
        self.assertEqual(norm.normalize("1.29.0"), "1.29.0")
        self.assertEqual(norm.normalize("10.0.0.0/8"), "10.0.0.0/8")

    def test_leading_zeros_read_digitwise(self):
        self.assertEqual(norm.normalize("007"), "ноль ноль семь")

    def test_ordinal_suffix_split(self):
        # Ограничение norm-v1: суффикс — отдельный токен, а не порядковое числительное.
        self.assertEqual(norm.normalize("500-е"), "пятьсот е")

    def test_filler_removed_from_hypothesis_only(self):
        ref, hyp = norm.normalize_pair("задеплоили сервис", "ну задеплоили э сервис")
        self.assertEqual(ref, hyp)
        self.assertEqual(ref, ["задеплоили", "сервис"])

    def test_filler_kept_when_present_in_reference(self):
        ref, hyp = norm.normalize_pair("ну задеплоили", "задеплоили")
        self.assertEqual(ref, ["ну", "задеплоили"])
        self.assertEqual(hyp, ["задеплоили"])

    def test_disfluency_stripped_from_reference(self):
        self.assertEqual(
            norm.remove_disfluencies(["э", "я", "типа", "не", "знаю"]),
            ["я", "не", "знаю"],
        )

    def test_version_constant(self):
        self.assertEqual(norm.VERSION, "norm-v2")

    def test_number_words_plural_forms(self):
        self.assertEqual(norm.number_to_words_ru(1), ["один"])
        self.assertEqual(norm.number_to_words_ru(2), ["два"])
        self.assertEqual(norm.number_to_words_ru(5), ["пять"])
        self.assertEqual(norm.number_to_words_ru(11), ["одиннадцать"])
        self.assertEqual(norm.number_to_words_ru(21), ["двадцать", "один"])
        self.assertEqual(norm.number_to_words_ru(2000), ["две", "тысячи"])
        self.assertEqual(norm.number_to_words_ru(5000), ["пять", "тысяч"])
        self.assertEqual(
            norm.number_to_words_ru(1_234_567),
            ["один", "миллион", "двести", "тридцать", "четыре", "тысячи",
             "пятьсот", "шестьдесят", "семь"],
        )

    def test_number_out_of_range(self):
        with self.assertRaises(ValueError):
            norm.number_to_words_ru(10 ** 12)


class Alignment(unittest.TestCase):
    def test_ops_and_mapping(self):
        ops, mapping = align.align(["a", "b", "c"], ["a", "x", "c", "d"])
        kinds = [op for op, _, _ in ops]
        self.assertEqual(kinds.count(align.MATCH), 2)
        self.assertEqual(kinds.count(align.SUB), 1)
        self.assertEqual(kinds.count(align.INS), 1)
        self.assertEqual(mapping, {0: 0, 1: 1, 2: 2})

    def test_deletion(self):
        ops, _ = align.align(["a", "b", "c"], ["a", "c"])
        self.assertEqual(align.edit_counts(ops), (0, 1, 0))

    def test_empty_sequences(self):
        self.assertEqual(align.distance([], []), 0)
        self.assertEqual(align.distance(["a"], []), 1)
        self.assertEqual(align.distance([], ["a", "b"]), 2)

    def test_distance_symmetric(self):
        a, b = ["kubernetes", "get", "pods"], ["kubernets", "get"]
        self.assertEqual(align.distance(a, b), align.distance(b, a))


class WER(unittest.TestCase):
    def test_perfect_match(self):
        report = wer_mod.corpus([{"id": "u1", "reference": "kubectl get pods", "hypothesis": "kubectl get pods"}])
        self.assertEqual(report["wer"], 0.0)
        self.assertEqual(report["errors"], 0)

    def test_corpus_level_not_mean(self):
        # 1 ошибка на 1 слово + 0 ошибок на 4 слова = 1/5 = 0.2 (среднее было бы 0.5).
        report = wer_mod.corpus([
            {"id": "a", "reference": "сто", "hypothesis": "двести"},
            {"id": "b", "reference": "а б в г", "hypothesis": "а б в г"},
        ])
        self.assertAlmostEqual(report["wer"], 0.2)
        self.assertEqual(report["words"], 5)

    def test_sid_breakdown(self):
        row = wer_mod.score_pair("а б в", "а х в г")
        self.assertEqual((row["substitutions"], row["deletions"], row["insertions"]), (1, 0, 1))
        self.assertEqual(row["errors"], 2)

    def test_normalized_before_counting(self):
        row = wer_mod.score_pair("Kubernetes, в проде.", "kubernetes в проде")
        self.assertEqual(row["errors"], 0)

    def test_cer_ignores_spaces(self):
        row = wer_mod.score_pair("привет мир", "приветмир")
        # 2 слова против 1: удаление + замена = 2 ошибки; по символам текст совпал.
        self.assertEqual(row["errors"], 2)
        self.assertEqual(row["char_errors"], 0)

    def test_empty_corpus_is_null_not_zero(self):
        report = wer_mod.corpus([])
        self.assertIsNone(report["wer"])
        self.assertIsNone(report["cer"])


def _vocab(terms_payload: list[dict]) -> terms_mod.Vocabulary:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"terms": terms_payload}, fh, ensure_ascii=False)
        path = fh.name
    try:
        return terms_mod.Vocabulary.from_path(path)
    finally:
        os.unlink(path)


class TermMetrics(unittest.TestCase):
    def setUp(self):
        self.vocab = _vocab([
            {"id": "kubernetes", "class": "tools", "accept": ["Kubernetes"],
             "accept_translit": ["кубернетес"]},
            {"id": "helm", "class": "tools", "accept": ["Helm"], "accept_translit": ["хелм"]},
            {"id": "cicd", "class": "abbrev", "accept": ["CI/CD"], "accept_translit": []},
            {"id": "redis", "class": "tools", "accept": ["Redis"], "accept_translit": ["редис"]},
        ])

    def test_exact_latin(self):
        report = terms_mod.classify("задеплоили Helm в прод", "задеплоили helm в прод", self.vocab)
        self.assertEqual(report["exact_latin"], 1)
        self.assertEqual(report["recall_exact"], 1.0)
        self.assertEqual(report["latin_form_accuracy"], 1.0)

    def test_latin_form_accuracy_undefined_without_hits(self):
        # 0/0 → n/a, а не 0: иначе модель без единого попадания выглядит «нулевой».
        report = terms_mod.classify("подняли Helm", "подняли нечто", self.vocab)
        self.assertIsNone(report["latin_form_accuracy"])

    def test_case_accuracy_requires_canonical_case(self):
        good = terms_mod.classify("Kubernetes", "Kubernetes", self.vocab)
        bad = terms_mod.classify("Kubernetes", "kubernetes", self.vocab)
        self.assertEqual(good["case_accuracy"], 1.0)
        self.assertEqual(bad["case_accuracy"], 0.0)
        self.assertEqual(bad["exact_latin"], 1)  # класс не зависит от регистра

    def test_case_accuracy_ignores_separators(self):
        report = terms_mod.classify("настроили CI/CD", "настроили CI-CD", self.vocab)
        self.assertEqual(report["exact_latin"], 1)
        self.assertEqual(report["case_accuracy"], 1.0)

    def test_translit(self):
        report = terms_mod.classify("подняли кубернетес", "подняли кубернетес", self.vocab)
        self.assertEqual(report["translit"], 1)
        self.assertEqual(report["exact_latin"], 0)
        self.assertEqual(report["latin_form_accuracy"], 0.0)

    def test_corrupt(self):
        report = terms_mod.classify("подняли kubernetes", "подняли kubernets", self.vocab)
        self.assertEqual(report["corrupt"], 1)
        self.assertEqual(report["recall_any"], 1.0)
        self.assertEqual(report["recall_exact"], 0.0)

    def test_short_term_not_corrupt(self):
        # `CI/CD` → форма `cicd` (4 символа): порог 1, `cica` — corrupt, `xxxx` — miss.
        near = terms_mod.classify("настроили CI/CD", "настроили cicd", self.vocab)
        self.assertEqual(near["exact_latin"], 1)
        far = terms_mod.classify("настроили CI/CD", "настроили xxxx", self.vocab)
        self.assertEqual(far["miss"], 1)

    def test_miss(self):
        report = terms_mod.classify("подняли Helm", "подняли что-то", self.vocab)
        self.assertEqual(report["miss"], 1)
        self.assertEqual(report["recall_exact"], 0.0)  # вхождение есть, попаданий нет

    def test_window_is_positional(self):
        # Термин есть в гипотезе, но далеко от своей позиции → не exact, а miss.
        ref = " ".join(["слово"] * 8 + ["Helm"])
        hyp = "helm " + " ".join(["слово"] * 8)
        report = terms_mod.classify(ref, hyp, self.vocab)
        self.assertEqual(report["miss"], 1)
        self.assertEqual(report["exact_latin"], 0)

    def test_window_tolerates_shift(self):
        ref = "задеплоили Helm в прод"
        hyp = "задеплоили новый Helm в прод"
        report = terms_mod.classify(ref, hyp, self.vocab)
        self.assertEqual(report["exact_latin"], 1)

    def test_precision_penalises_hallucination(self):
        # В референсе термина нет, в гипотезе есть → FP, precision = 0.
        report = terms_mod.classify("задеплоили сервис", "задеплоили helm сервис", self.vocab)
        self.assertEqual((report["term_tp"], report["term_fp"]), (0, 1))
        self.assertEqual(report["term_precision"], 0.0)

    def test_aggregate_sums_counts(self):
        rows = [
            terms_mod.classify("Helm", "Helm", self.vocab),
            terms_mod.classify("Helm", "хелм", self.vocab),
        ]
        total = terms_mod.aggregate(rows)
        self.assertEqual(total["occurrences"], 2)
        self.assertEqual(total["exact_latin"], 1)
        self.assertEqual(total["translit"], 1)
        self.assertAlmostEqual(total["recall_exact"], 0.5)
        self.assertAlmostEqual(total["latin_form_accuracy"], 0.5)

    def test_case_accuracy_aggregated_without_rounding(self):
        rows = [
            terms_mod.classify("Helm Helm", "Helm helm", self.vocab),
        ]
        total = terms_mod.aggregate(rows)
        self.assertEqual((total["case_hits"], total["case_total"]), (1, 2))
        self.assertAlmostEqual(total["case_accuracy"], 0.5)

    def test_corrupt_is_scoped_to_current_term(self):
        # Регрессия: `Dockerr` засчитывался как искажённый `Helm` (подмена термина).
        report = terms_mod.classify("Helm", "Dockerr", self.vocab)
        self.assertEqual((report["corrupt"], report["exact_latin"], report["miss"]), (0, 0, 1))

    def test_duplicate_hypothesis_term_counts_as_false_positive(self):
        report = terms_mod.classify("Helm", "Helm Helm", self.vocab)
        self.assertEqual((report["term_tp"], report["term_fp"]), (1, 1))
        self.assertAlmostEqual(report["term_precision"], 0.5)

    def test_unknown_form_ownership_rejected_by_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "terms.json"), "w", encoding="utf-8") as fh:
                json.dump({"terms": [
                    {"id": "a", "class": "tools", "accept": ["Helm"]},
                    {"id": "b", "class": "tools", "accept": ["helm"]},
                ]}, fh)
            os.makedirs(os.path.join(tmp, "x"), exist_ok=True)
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("уже принадлежит" in e for e in report["errors"]))


class CompareCoverage(unittest.TestCase):
    """Регрессии CLI: покрытие A/B, дубликаты id, смешение систем, метаданные отчёта."""

    def _dataset(self, tmp: str, ids: list[str]) -> str:
        dataset_dir = os.path.join(tmp, "ds")
        os.makedirs(dataset_dir, exist_ok=True)
        with open(os.path.join(dataset_dir, "manifest.tsv"), "w", encoding="utf-8") as fh:
            for seg in ids:
                fh.write(f"{seg}.wav\tтекст {seg}\n")
        return dataset_dir

    def _hyp(self, tmp: str, name: str, rows: list[tuple[str, str]]) -> str:
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            for seg, text in rows:
                fh.write(f"{seg}\t{text}\n")
        return path

    def test_compare_detects_missing_id_in_one_system_only(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = self._dataset(tmp, ["u1", "u2"])
            a = self._hyp(tmp, "a.tsv", [("u1", "текст u1"), ("u2", "текст u2")])
            b = self._hyp(tmp, "b.tsv", [("u1", "текст u1")])
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["compare", "--dataset", dataset_dir, "--a", a, "--b", b, "--replications", "10"])
            self.assertIn("система B", str(ctx.exception))

    def test_compare_detects_unknown_extra_id(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = self._dataset(tmp, ["u1"])
            a = self._hyp(tmp, "a.tsv", [("u1", "текст u1")])
            b = self._hyp(tmp, "b.tsv", [("u1", "текст u1"), ("ghost", "лишний")])
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["compare", "--dataset", dataset_dir, "--a", a, "--b", b, "--replications", "10"])
            self.assertIn("неизвестных id", str(ctx.exception))

    def test_duplicate_hypothesis_id_rejected(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = self._dataset(tmp, ["u1"])
            hyp = self._hyp(tmp, "a.tsv", [("u1", "первый"), ("u1", "второй")])
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["score", "--dataset", dataset_dir, "--hypotheses", hyp])
            self.assertIn("дубликат id", str(ctx.exception))

    def test_empty_hypothesis_id_rejected(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = self._dataset(tmp, ["u1"])
            hyp = self._hyp(tmp, "a.tsv", [("", "текст")])
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["score", "--dataset", dataset_dir, "--hypotheses", hyp])
            self.assertIn("пустой id", str(ctx.exception))

    def test_report_with_two_systems_rejected(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"results": [
                    {"engine": "parakeet", "utteranceResults": [
                        {"id": "u1", "reference": "a", "hypothesis": "a"}]},
                    {"engine": "nemotron", "utteranceResults": [
                        {"id": "u1", "reference": "a", "hypothesis": "b"}]},
                ]}, fh)
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["score", "--report", path])
            message = str(ctx.exception)
            self.assertIn("2 систем", message)
            self.assertIn("parakeet", message)

    def test_score_records_norm_and_vocabulary_identity(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = self._dataset(tmp, ["u1"])
            hyp = self._hyp(tmp, "a.tsv", [("u1", "текст u1")])
            out = os.path.join(tmp, "score.json")
            self.assertEqual(cli.main([
                "score", "--dataset", dataset_dir, "--hypotheses", hyp,
                "--terms", os.path.join(EVAL_DIR, "terms.json"), "--json", out,
            ]), 0)
            with open(out, encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual(report["norm_version"], norm.VERSION)
            self.assertEqual(report["vocabulary"]["version"], "terms-v1")
            self.assertEqual(len(report["vocabulary"]["sha256"]), 64)

    def test_invalid_manifest_fails_instead_of_partial_score(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = os.path.join(tmp, "ds")
            os.makedirs(dataset_dir)
            with open(os.path.join(dataset_dir, "manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("u1.wav\tтекст\n")
                fh.write("битая строка без таба\n")
            hyp = self._hyp(tmp, "a.tsv", [("u1", "текст")])
            with self.assertRaises(ValueError):
                dataset_mod.load_manifest_map(dataset_dir)
            # CLI превращает это в код возврата 2 без traceback.
            self.assertEqual(cli.main(["score", "--dataset", dataset_dir, "--hypotheses", hyp]), 2)


class Stats(unittest.TestCase):
    def test_binomial_halfwidth_matches_v8_table(self):
        # V8 §2.6: при T=400, p=0.7 полуширина ≈ ±4.5 п.п.
        self.assertAlmostEqual(stats.binomial_halfwidth(0.7, 400), 0.0449, places=3)
        self.assertAlmostEqual(stats.binomial_halfwidth(0.5, 100), 0.098, places=3)

    def test_percentile(self):
        self.assertEqual(stats.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(stats.percentile([5], 0.9), 5)

    def test_bootstrap_detects_large_difference(self):
        # A всегда ошибается в 3 словах из 10, B — никогда.
        words = [10] * 40
        errors_a = [3] * 40
        errors_b = [0] * 40
        result = stats.paired_bootstrap(errors_a, words, errors_b, replications=200, seed=1)
        self.assertAlmostEqual(result["difference"], 0.3)
        self.assertGreater(result["ci_low"], 0)

    def test_bootstrap_identical_systems_centres_on_zero(self):
        words = [10] * 30
        errors = [1, 2, 0, 3, 1] * 6
        result = stats.paired_bootstrap(errors, words, list(errors), replications=200, seed=2)
        self.assertEqual(result["difference"], 0.0)
        self.assertLessEqual(result["ci_low"], 0.0)
        self.assertGreaterEqual(result["ci_high"], 0.0)

    def test_bootstrap_is_deterministic(self):
        words = [10] * 20
        a = [1, 2] * 10
        b = [2, 1] * 10
        first = stats.paired_bootstrap(a, words, b, replications=50, seed=7)
        second = stats.paired_bootstrap(a, words, b, replications=50, seed=7)
        self.assertEqual(first, second)

    def test_bootstrap_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([1], [10], [1, 2])

    def test_bootstrap_rejects_zero_words(self):
        with self.assertRaises(ValueError) as ctx:
            stats.paired_bootstrap([0, 0], [0, 0], [0, 0], replications=10)
        self.assertIn("сумма слов", str(ctx.exception))

    def test_bootstrap_rejects_non_positive_replications(self):
        for replications in (0, -5):
            with self.assertRaises(ValueError) as ctx:
                stats.paired_bootstrap([1], [10], [0], replications=replications)
            self.assertIn("replications", str(ctx.exception))

    def test_bootstrap_rejects_negative_counts(self):
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([-1], [10], [0], replications=10)
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([1], [-10], [0], replications=10)


class DatasetValidation(unittest.TestCase):
    def _write(self, tmp: str, rows: list[dict], manifest: list[tuple[str, str]], terms: list[dict]) -> None:
        with open(os.path.join(tmp, "manifest.tsv"), "w", encoding="utf-8") as fh:
            for audio, ref in manifest:
                fh.write(f"{audio}\t{ref}\n")
        with open(os.path.join(tmp, "metadata.jsonl"), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(os.path.join(tmp, "terms.json"), "w", encoding="utf-8") as fh:
            json.dump({"terms": terms}, fh, ensure_ascii=False)

    def _row(self, seg: str, split: str, **kwargs) -> dict:
        base = {
            "segment_id": seg,
            "recording_id": "rec-" + seg,
            "register": "R1",
            "speaker_id": "spk-" + seg,
            "mic_class": "headset",
            "noise_class": "none",
            "duration_s": 60.0,
            "split": split,
        }
        base.update(kwargs)
        return base

    def test_clean_small_dataset_passes_but_warns_on_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "dev"), self._row("b", "test")]
            self._write(tmp, rows, [("a.wav", "привет"), ("b.wav", "привет")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertTrue(report["ok"], report["errors"])
            self.assertTrue(any("test" in w and "ч <" in w for w in report["warnings"]))
            self.assertEqual(report["coverage"]["segments"], 2)

    def test_speaker_leak_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                self._row("a", "dev", speaker_id="same"),
                self._row("b", "test", speaker_id="same"),
            ]
            self._write(tmp, rows, [("a.wav", "x"), ("b.wav", "y")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("утечка: диктор" in e for e in report["errors"]))

    def test_recording_leak_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                self._row("a", "dev", recording_id="meet-1"),
                self._row("b", "test", recording_id="meet-1"),
            ]
            self._write(tmp, rows, [("a.wav", "x"), ("b.wav", "y")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertTrue(any("утечка: запись" in e for e in report["errors"]))

    def test_unknown_term_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "dev", terms=[{"term_id": "ghost"}])]
            self._write(tmp, rows, [("a.wav", "x")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("неизвестный term_id" in e for e in report["errors"]))

    def test_bad_metadata_json_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("a.wav\tx\n")
            with open(os.path.join(tmp, "metadata.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("{не json}\n")
                fh.write(json.dumps({"segment_id": "a"}) + "\n")
            with open(os.path.join(tmp, "terms.json"), "w", encoding="utf-8") as fh:
                json.dump({"terms": []}, fh)
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("невалидный JSON" in e for e in report["errors"]))
            self.assertTrue(any("нет полей" in e for e in report["errors"]))

    def test_segment_missing_from_manifest_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "dev"), self._row("b", "dev")]
            self._write(tmp, rows, [("a.wav", "x")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("но не в манифесте" in e for e in report["errors"]))

    def test_missing_files_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["errors"]), 3)  # manifest, metadata, terms

    def test_coverage_distinguishes_dictionary_from_encountered_terms(self):
        # Регрессия: 300 записей словаря выдавались за 300 терминов в аудио.
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "dev", terms=[{"term_id": "helm"}])]
            self._write(tmp, rows, [("a.wav", "x")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]},
                         {"id": "redis", "class": "tools", "accept": ["Redis"]},
                         {"id": "loki", "class": "tools", "accept": ["Loki"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertEqual(report["coverage"]["dictionary_terms"], 3)
            self.assertEqual(report["coverage"]["unique_terms"], 1)
            self.assertTrue(any("уникальных терминов в разметке 1 <" in w for w in report["warnings"]))

    def test_non_native_share_counts_unique_speakers(self):
        # Регрессия: доля считалась по сегментам, а V8 §2.2 требует долю дикторов.
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                self._row("a", "dev", speaker_id="spk-native", speaker_native=True),
                self._row("b", "dev", speaker_id="spk-native", speaker_native=True),
                self._row("c", "dev", speaker_id="spk-native2", speaker_native=True),
                self._row("d", "dev", speaker_id="spk-native3", speaker_native=True),
                self._row("e", "dev", speaker_id="spk-foreign", speaker_native=False),
            ]
            self._write(tmp, rows, [(f"{r['segment_id']}.wav", "x") for r in rows],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            # 1 неродной из 4 уникальных дикторов = 25 % (по сегментам было бы 20 %).
            self.assertAlmostEqual(report["coverage"]["non_native_share"], 0.25)
            self.assertTrue(any("неродных дикторов 25%" in w for w in report["warnings"]))

    def test_conflicting_speaker_native_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                self._row("a", "dev", speaker_id="spk", speaker_native=True),
                self._row("b", "dev", speaker_id="spk", speaker_native=False),
            ]
            self._write(tmp, rows, [("a.wav", "x"), ("b.wav", "y")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("противоречивые speaker_native" in e for e in report["errors"]))

    def test_non_finite_and_bool_duration_rejected(self):
        for bad in (float("nan"), float("inf"), True, "60"):
            with tempfile.TemporaryDirectory() as tmp:
                rows = [self._row("a", "dev", duration_s=bad)]
                self._write(tmp, rows, [("a.wav", "x")],
                            [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
                report = dataset_mod.validate_dataset(tmp)
                self.assertFalse(report["ok"], bad)
                self.assertTrue(any("duration_s" in e for e in report["errors"]), bad)
                self.assertEqual(report["coverage"].get("hours_total"), 0.0)

    def test_unhashable_identifier_rejected_without_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "dev", speaker_id=["не", "строка"])]
            self._write(tmp, rows, [("a.wav", "x")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("идентификаторы должны быть" in e for e in report["errors"]))

    def test_non_object_metadata_row_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("a.wav\tx\n")
            with open(os.path.join(tmp, "metadata.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]\n")
            with open(os.path.join(tmp, "terms.json"), "w", encoding="utf-8") as fh:
                json.dump({"terms": []}, fh)
            report = dataset_mod.validate_dataset(tmp)
            self.assertFalse(report["ok"])
            self.assertTrue(any("ожидался JSON-объект" in e for e in report["errors"]))

    def test_term_coverage_threshold_uses_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [self._row("a", "test", terms=[{"term_id": "helm"}, {"term_id": "helm"}])]
            self._write(tmp, rows, [("a.wav", "x")],
                        [{"id": "helm", "class": "tools", "accept": ["Helm"]}])
            report = dataset_mod.validate_dataset(tmp)
            self.assertEqual(report["coverage"]["term_occurrences_test"], 2)
            self.assertTrue(any("вхождений терминов в test 2 <" in w for w in report["warnings"]))


class ShippedVocabulary(unittest.TestCase):
    """Проверки реального terms.json (§2.3): валидность данных, не реализация."""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(EVAL_DIR, "terms.json")
        with open(cls.path, encoding="utf-8") as fh:
            cls.data = json.load(fh)

    def test_schema_and_classes(self):
        terms = self.data["terms"]
        self.assertGreaterEqual(len(terms), 300, "§2.3 требует ≥ 300 терминов")
        classes = {t["class"] for t in terms}
        self.assertEqual(classes, set(dataset_mod.TERM_CLASSES))
        for term in terms:
            self.assertTrue(term["accept"], term["id"])
            self.assertIsInstance(term["accept_translit"], list)

    def test_ids_and_forms_unique(self):
        terms = self.data["terms"]
        ids = [t["id"] for t in terms]
        self.assertEqual(len(ids), len(set(ids)))
        seen: dict[str, str] = {}
        for term in terms:
            for form in term["accept"]:
                key = " ".join(form.lower().split())
                self.assertNotIn(key, seen, f"форма '{form}' у {term['id']} и {seen.get(key)}")
                seen[key] = term["id"]

    def test_translit_class_has_translits(self):
        for term in self.data["terms"]:
            if term["class"] == "translit":
                self.assertTrue(term["accept_translit"], term["id"])

    def test_forms_survive_normalizer(self):
        # Формы должны оставаться непустыми после norm-v1, иначе термин не найдётся.
        for term in self.data["terms"]:
            for form in term["accept"]:
                self.assertTrue(norm.tokens(form), f"{term['id']}: '{form}' → пусто")

    def test_vocabulary_loads_and_matches_realistic_hypothesis(self):
        vocab = terms_mod.Vocabulary.from_path(self.path)
        report = terms_mod.classify(
            "задеплоили Helm в Kubernetes, потом посмотрели Grafana и PostgreSQL",
            "задеплоили хелм в кубернетес, потом посмотрели grafana и postgres",
            vocab,
        )
        verdicts = {(r["term_id"], r["verdict"]) for r in report["per_term"]}
        self.assertIn(("helm", "translit"), verdicts)
        self.assertIn(("kubernetes", "translit"), verdicts)
        self.assertIn(("grafana", "exact_latin"), verdicts)
        self.assertIn(("postgresql", "exact_latin"), verdicts)

    def test_no_two_word_form_collides_with_single_word_form(self):
        # `postgre sql` не должен случайно стать формой `postgresql`.
        vocab = terms_mod.Vocabulary.from_path(self.path)
        tokens = norm.tokens("postgre sql")
        self.assertNotIn("postgresql", tokens)
        self.assertNotIn("postgresql", {occ.term_id for occ in vocab.occurrences(tokens)})


class CLIEndToEnd(unittest.TestCase):
    def test_score_from_tsv_and_report(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = os.path.join(tmp, "ds")
            os.makedirs(dataset_dir)
            with open(os.path.join(dataset_dir, "manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("u1.wav\tзадеплоили Helm\n")
                fh.write("u2.wav\tподняли Kubernetes\n")
            with open(os.path.join(dataset_dir, "terms.json"), "w", encoding="utf-8") as fh:
                json.dump({"terms": [
                    {"id": "helm", "class": "tools", "accept": ["Helm"], "accept_translit": ["хелм"]},
                    {"id": "kubernetes", "class": "tools", "accept": ["Kubernetes"],
                     "accept_translit": ["кубернетес"]},
                ]}, fh, ensure_ascii=False)
            hyp = os.path.join(tmp, "hyp.tsv")
            with open(hyp, "w", encoding="utf-8") as fh:
                fh.write("u1\tзадеплоили helm\n")
                fh.write("u2\tподняли кубернетес\n")

            out_json = os.path.join(tmp, "score.json")
            code = cli.main([
                "score", "--dataset", dataset_dir, "--hypotheses", hyp,
                "--terms", os.path.join(dataset_dir, "terms.json"), "--json", out_json,
            ])
            self.assertEqual(code, 0)
            with open(out_json, encoding="utf-8") as fh:
                report = json.load(fh)
            # `кубернетес` против `Kubernetes` — замена, 1 ошибка на 4 слова.
            self.assertAlmostEqual(report["wer"], 0.25)
            self.assertEqual(report["terms"]["exact_latin"], 1)
            self.assertEqual(report["terms"]["translit"], 1)

            compare_code = cli.main([
                "compare", "--dataset", dataset_dir, "--a", hyp, "--b", hyp,
                "--replications", "50",
            ])
            self.assertEqual(compare_code, 0)

    def test_score_from_asr_bench_report(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "asr.json")
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump({"results": [{"utteranceResults": [
                    {"id": "u1", "reference": "Helm", "hypothesis": "helm"},
                ]}]}, fh)
            out_json = os.path.join(tmp, "score.json")
            code = cli.main([
                "score", "--report", report_path,
                "--terms", os.path.join(EVAL_DIR, "terms.json"), "--json", out_json,
            ])
            self.assertEqual(code, 0)
            with open(out_json, encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual(report["terms"]["exact_latin"], 1)

    def test_missing_hypotheses_fails_loudly(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            dataset_dir = os.path.join(tmp, "ds")
            os.makedirs(dataset_dir)
            with open(os.path.join(dataset_dir, "manifest.tsv"), "w", encoding="utf-8") as fh:
                fh.write("u1.wav\tтекст\n")
            hyp = os.path.join(tmp, "hyp.tsv")
            with open(hyp, "w", encoding="utf-8") as fh:
                fh.write("other\tтекст\n")
            with self.assertRaises(SystemExit):
                cli.main(["score", "--dataset", dataset_dir, "--hypotheses", hyp])


if __name__ == "__main__":
    unittest.main(verbosity=2)
