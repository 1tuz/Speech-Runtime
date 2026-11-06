"""Offline unit tests for the pure parts of runtime_eval.batch_slice_eval.

No daemon and no audio: `compare_batch_runs` is exercised against small
hand-written artifacts, and the scorer reuse is checked the same way
`test_pipeline_eval` checks the pipeline wiring. The `run` subcommand needs a
live daemon and stays out of scope here.

Run from experiments/:
    python3 -m unittest runtime_eval.tests.test_batch_slice_eval
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_EVAL_DIR = os.path.dirname(HERE)
EXPERIMENTS_DIR = os.path.dirname(RUNTIME_EVAL_DIR)
for path in (EXPERIMENTS_DIR, RUNTIME_EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import batch_slice_eval as bse  # noqa: E402


def artifact(text: str, sha: str = "a" * 64, **extra) -> dict:
    """Minimal `run` artifact; extra keys (timings, runs) are passed through."""
    record = {"text": text, "sha256": sha, "tokens": [], "model": "gigaam"}
    record.update(extra)
    return record


class CompareTextTests(unittest.TestCase):
    def test_identical_texts(self):
        before = artifact("привет как дела")
        after = artifact("привет как дела")
        result = bse.compare_batch_runs(before, after)
        self.assertTrue(result["text"]["identical"])
        self.assertEqual(result["text"]["wer"], 0.0)
        self.assertEqual(result["text"]["deletions"], 0)
        self.assertEqual(result["text"]["insertions"], 0)
        self.assertTrue(result["same_audio"])

    def test_lost_and_added_words(self):
        before = artifact("один два три четыре")
        after = artifact("один два три три четыре пять")
        result = bse.compare_batch_runs(before, after)
        self.assertFalse(result["text"]["identical"])
        self.assertEqual(result["text"]["insertions"], 2)  # duplicated + new
        self.assertEqual(result["text"]["deletions"], 0)
        self.assertIn("пять", result["text"]["added_word_examples"])
        self.assertEqual(result["text"]["lost_word_examples"], [])

    def test_lost_words_are_deletions(self):
        before = artifact("один два три четыре")
        after = artifact("два три")
        result = bse.compare_batch_runs(before, after)
        self.assertEqual(result["text"]["deletions"], 2)
        self.assertEqual(result["text"]["insertions"], 0)
        self.assertEqual(sorted(result["text"]["lost_word_examples"]), ["один", "четыре"])

    def test_normalisation_applies(self):
        """Case/punctuation differences are not a change: same norm tokens."""
        before = artifact("Привет, Алексей! Как дела?")
        after = artifact("привет алексей как дела")
        result = bse.compare_batch_runs(before, after)
        self.assertEqual(result["text"]["wer"], 0.0)
        self.assertTrue(result["text"]["identical"] or result["text"]["wer"] == 0.0)

    def test_empty_before_is_scored_not_crashed(self):
        result = bse.compare_batch_runs(artifact(""), artifact("один два"))
        self.assertIsNone(result["text"]["wer"])  # no reference words -> no WER
        self.assertEqual(result["text"]["insertions"], 2)

    def test_empty_after_is_all_deletions(self):
        result = bse.compare_batch_runs(artifact("один два"), artifact(""))
        self.assertEqual(result["text"]["wer"], 1.0)
        self.assertEqual(result["text"]["deletions"], 2)


class CompareAudioTests(unittest.TestCase):
    def test_different_audio_is_flagged(self):
        result = bse.compare_batch_runs(
            artifact("текст", sha="a" * 64), artifact("текст", sha="b" * 64)
        )
        self.assertFalse(result["same_audio"])
        self.assertNotEqual(result["before_sha256"], result["after_sha256"])

    def test_missing_sha_is_unmeasured_not_guess(self):
        result = bse.compare_batch_runs(
            {"text": "текст", "tokens": []}, artifact("текст")
        )
        self.assertIsNone(result["same_audio"])


class CompareTimingTests(unittest.TestCase):
    def test_delta_over_runs(self):
        before = artifact(
            "текст",
            runs=[
                {"text": "текст", "asr_ms": 100, "wall_ms": 200, "queue_ms": 5},
                {"text": "текст", "asr_ms": 200, "wall_ms": 300, "queue_ms": 15},
            ],
        )
        after = artifact(
            "текст",
            runs=[
                {"text": "текст", "asr_ms": 150, "wall_ms": 400, "queue_ms": 0},
            ],
        )
        result = bse.compare_batch_runs(before, after)
        self.assertEqual(result["timing"]["asr_ms"], 0.0)     # 150 - 150
        self.assertEqual(result["timing"]["wall_ms"], 150.0)  # 400 - 250
        self.assertEqual(result["timing"]["queue_ms"], -10.0)  # 0 - 10

    def test_missing_timing_is_none(self):
        result = bse.compare_batch_runs(artifact("текст"), artifact("текст"))
        self.assertIsNone(result["timing"]["asr_ms"])
        self.assertIsNone(result["timing"]["wall_ms"])
        self.assertIsNone(result["timing"]["queue_ms"])

    def test_single_run_artifact_without_runs_key(self):
        result = bse.compare_batch_runs(
            artifact("текст", asr_ms=100), artifact("текст", asr_ms=120)
        )
        self.assertEqual(result["timing"]["asr_ms"], 20.0)


class CompareTokensTests(unittest.TestCase):
    def test_token_counts_reported(self):
        before = artifact("текст", tokens=[{"text": "т", "start_ms": 0}])
        after = artifact("текст", tokens=[{"text": "т", "start_ms": 0},
                                          {"text": "к", "start_ms": 320}])
        result = bse.compare_batch_runs(before, after)
        self.assertEqual(result["tokens"]["before"], 1)
        self.assertEqual(result["tokens"]["after"], 2)


class ScorerReuseTests(unittest.TestCase):
    """The comparison must reuse asr_eval scoring, not its own arithmetic."""

    def test_wer_matches_asr_eval_score_pair(self):
        import wer as wer_mod  # noqa: E402  (asr_eval)

        before_text, after_text = "один два три четыре", "один два три три пять"
        expected = wer_mod.score_pair(before_text, after_text)
        result = bse.compare_batch_runs(artifact(before_text), artifact(after_text))
        self.assertEqual(result["text"]["errors"], expected["errors"])
        self.assertEqual(result["text"]["words"], expected["words"])
        self.assertEqual(result["text"]["substitutions"], expected["substitutions"])


if __name__ == "__main__":
    unittest.main()
