"""Offline unit tests for the pure parts of runtime_eval.

No daemon is started and no model is loaded: only `build_window`, reference
concatenation, config/grid parsing and the WER wiring against the asr_eval
modules with tiny fixed fixtures.

Run from experiments/:
    python3 -m unittest runtime_eval.tests.test_pipeline_eval
"""

from __future__ import annotations

import os
import json
import struct
import sys
import tempfile
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_EVAL_DIR = os.path.dirname(HERE)
EXPERIMENTS_DIR = os.path.dirname(RUNTIME_EVAL_DIR)
for path in (EXPERIMENTS_DIR, RUNTIME_EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import pipeline_eval as pe  # noqa: E402
from runtime_eval import runtime_client as rc  # noqa: E402

import norm as norm_mod  # noqa: E402  (asr_eval)


def utterance(uid: str, reference: str, n_samples: int) -> dict:
    """Deterministic non-silent samples: 1 kHz-ish tone, amplitude 0.5."""
    samples = [
        16000 if (i // 16) % 2 == 0 else -16000 for i in range(n_samples)
    ]
    return {"id": uid, "reference": reference, "samples": samples}


class BuildWindowTests(unittest.TestCase):
    def test_offsets_and_gap(self):
        # 1000 ms and 500 ms utterances, 700 ms gap, 200/1200 ms lead/trail.
        utts = [utterance("a", "первый", 16000), utterance("b", "второй", 8000)]
        samples, entries = pe.build_window(utts, 700, lead_ms=200, trail_ms=1200)
        self.assertEqual(entries[0]["start_ms"], 200)
        self.assertEqual(entries[0]["end_ms"], 1200)
        self.assertEqual(entries[0]["duration_ms"], 1000)
        self.assertEqual(entries[0]["gap_before_ms"], 0)
        self.assertEqual(entries[1]["gap_before_ms"], 700)
        self.assertEqual(entries[1]["start_ms"], 1900)
        self.assertEqual(entries[1]["end_ms"], 2400)
        expected_len = pe.ms_to_samples(200 + 1000 + 700 + 500 + 1200)
        self.assertEqual(len(samples), expected_len)
        # Silence inside the gap and after the trail.
        gap_start = pe.ms_to_samples(1200)
        gap_end = pe.ms_to_samples(1900)
        self.assertTrue(all(v == 0 for v in samples[gap_start:gap_end]))
        self.assertTrue(all(v == 0 for v in samples[pe.ms_to_samples(2400):]))
        # Speech is not zeroed.
        self.assertTrue(any(v != 0 for v in samples[pe.ms_to_samples(200):pe.ms_to_samples(1200)]))

    def test_gaps_schedule_per_junction(self):
        utts = [utterance(str(i), f"utt {i}", 16000) for i in range(4)]
        _, entries = pe.build_window(utts, gap_ms=0, gaps=[100, 200, 300])
        self.assertEqual([e["gap_before_ms"] for e in entries], [0, 100, 200, 300])
        self.assertEqual(entries[2]["start_ms"], 1000 + 100 + 1000 + 200)

    def test_single_gap_value_applies_to_all_junctions(self):
        utts = [utterance(str(i), f"utt {i}", 8000) for i in range(3)]
        _, entries = pe.build_window(utts, gap_ms=0, gaps=[250])
        self.assertEqual([e["gap_before_ms"] for e in entries], [0, 250, 250])

    def test_wrong_gaps_length_is_an_error(self):
        utts = [utterance("a", "x", 1600), utterance("b", "y", 1600)]
        # 2 entries for 1 junction: ambiguous, rejected.
        with self.assertRaises(ValueError):
            pe.build_window(utts, gap_ms=0, gaps=[100, 200])
        three = utts + [utterance("c", "z", 1600)]
        # 2 entries for 2 junctions is a valid schedule: must NOT raise.
        built = pe.build_window(three, gap_ms=0, gaps=[100, 200])
        self.assertEqual(
            [entry["gap_before_ms"] for entry in built[1]], [0, 100, 200]
        )
        # 3 entries for 2 junctions: rejected.
        with self.assertRaises(ValueError):
            pe.build_window(three, gap_ms=0, gaps=[100, 200, 300])

    def test_empty_window_is_an_error(self):
        with self.assertRaises(ValueError):
            pe.build_window([], gap_ms=100)

    def test_lead_only(self):
        utts = [utterance("a", "текст", 1600)]
        samples, entries = pe.build_window(utts, 0, lead_ms=300, trail_ms=0)
        self.assertEqual(entries[0]["start_ms"], 300)
        self.assertEqual(len(samples), pe.ms_to_samples(300 + 100))


class ConcatenationTests(unittest.TestCase):
    ENTRIES = [
        {"id": "a", "reference": "Привет, как дела?"},
        {"id": "b", "reference": "Нормально, а у тебя?"},
    ]

    def test_reference_concatenation_joins_with_space(self):
        self.assertEqual(
            pe.concatenated_reference(self.ENTRIES),
            "Привет, как дела? Нормально, а у тебя?",
        )

    def test_hypothesis_concatenation_skips_empty_finals(self):
        finals = [{"text": "привет как дела"}, {"text": ""}, {"text": "нормально"}]
        self.assertEqual(
            pe.concatenated_hypothesis(finals), "привет как дела нормально"
        )

    def test_no_finals_gives_empty_hypothesis(self):
        self.assertEqual(pe.concatenated_hypothesis([]), "")


class WerWiringTests(unittest.TestCase):
    """The harness must reuse asr_eval scoring, not its own arithmetic."""

    REF = "раз два три четыре"
    HYP = "раз два три четыре"  # identical -> 0 errors

    def test_score_text_delegates_to_asr_eval(self):
        row = pe.score_text(self.REF, self.HYP)
        self.assertEqual(row["words"], 4)
        self.assertEqual(row["errors"], 0)
        self.assertEqual(row["wer"] if "wer" in row else row["errors"] / row["words"], 0)

    def test_score_pair_counts_one_substitution(self):
        result = pe.score_text("раз два три", "раз два пять")
        self.assertEqual(result["substitutions"], 1)
        self.assertEqual(result["errors"], 1)

    def test_normalisation_applies(self):
        # Punctuation is stripped and case folded by norm-v2 before WER.
        result = pe.score_text("Раз, два!", "раз два")
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["words"], 2)

    def test_corpus_wer_uses_asr_eval_corpus(self):
        items = [
            {"id": "a", "reference": "раз два", "hypothesis": "раз два"},
            {"id": "b", "reference": "три четыре", "hypothesis": "три пять"},
        ]
        corpus = pe.corpus_wer(items)
        self.assertEqual(corpus["words"], 4)
        self.assertEqual(corpus["errors"], 1)
        self.assertAlmostEqual(corpus["wer"], 0.25)
        self.assertEqual(len(corpus["per_utterance"]), 2)

    def test_numbers_expand(self):
        # norm-v2 expands digits: "2" and "два" align as a match.
        result = pe.score_text("два раза", "2 раза")
        self.assertEqual(result["errors"], 0)


class BoundaryDiagnosticsTests(unittest.TestCase):
    def test_missing_last_word_of_first_reference(self):
        entries = [
            {"id": "a", "reference": "один два три"},
            {"id": "b", "reference": "четыре пять"},
        ]
        finals = [{"text": "один два"}, {"text": "четыре пять"}]
        diag = pe.boundary_diagnostics(entries, finals)
        self.assertEqual(diag["boundary_deletions"], 1)
        self.assertEqual(diag["boundary_deletion_examples"], ["три"])

    def test_missing_first_word_of_second_reference(self):
        entries = [
            {"id": "a", "reference": "один два"},
            {"id": "b", "reference": "три четыре"},
        ]
        finals = [{"text": "один два"}, {"text": "четыре"}]
        diag = pe.boundary_diagnostics(entries, finals)
        self.assertEqual(diag["boundary_deletions"], 1)
        self.assertEqual(diag["boundary_deletion_examples"], ["три"])

    def test_insertion_at_split_point(self):
        entries = [
            {"id": "a", "reference": "один два"},
            {"id": "b", "reference": "три четыре"},
        ]
        finals = [{"text": "один два"}, {"text": "и три четыре"}]
        diag = pe.boundary_diagnostics(entries, finals)
        self.assertEqual(diag["boundary_insertions"], 1)
        self.assertEqual(diag["boundary_insertion_examples"], ["и"])

    def test_mid_utterance_deletion_not_at_boundary(self):
        entries = [{"id": "a", "reference": "один два три четыре пять"}]
        finals = [{"text": "один два четыре пять"}]
        diag = pe.boundary_diagnostics(entries, finals)
        self.assertEqual(diag["boundary_deletions"], 0)

    def test_perfect_pipeline_has_no_boundary_damage(self):
        entries = [
            {"id": "a", "reference": "один два"},
            {"id": "b", "reference": "три четыре"},
        ]
        finals = [{"text": "один два"}, {"text": "три четыре"}]
        diag = pe.boundary_diagnostics(entries, finals)
        self.assertEqual(diag["boundary_deletions"], 0)
        self.assertEqual(diag["boundary_insertions"], 0)
        self.assertEqual(diag["finals"], 2)
        self.assertEqual(diag["references"], 2)


class ConfigParsingTests(unittest.TestCase):
    def test_parse_config_types(self):
        config = rc.parse_config(
            ["min_silence_ms=700", "enter_threshold=0.5", "language=ru", "flag=true"]
        )
        self.assertEqual(
            config,
            {
                "min_silence_ms": 700,
                "enter_threshold": 0.5,
                "language": "ru",
                "flag": True,
            },
        )

    def test_parse_config_rejects_missing_separator(self):
        with self.assertRaises(SystemExit):
            rc.parse_config(["novalue"])

    def test_grid_parsing(self):
        self.assertEqual(pe.parse_grid("500,700,900"), [500, 700, 900])
        self.assertEqual(pe.parse_grid("0.2"), [0.2])

    def test_sweep_configs_cartesian(self):
        configs = pe.sweep_configs({"min_silence_ms": [500, 700], "post_roll_ms": [240]})
        self.assertEqual(
            configs,
            [
                {"min_silence_ms": 500, "post_roll_ms": 240},
                {"min_silence_ms": 700, "post_roll_ms": 240},
            ],
        )


class WavIOTests(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        samples = [0, 1000, -2000, 32767, -32768]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.wav")
            pe.write_wav(path, samples)
            loaded, rate, channels = pe.read_wav_i16(path)
            self.assertEqual(loaded, samples)
            self.assertEqual(rate, 16000)
            self.assertEqual(channels, 1)

    def test_float_wav_bytes_roundtrip(self):
        floats = [0.0, 0.5, -0.25, 1.0]
        payload = pe.write_wav_bytes(floats)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(payload)
            tmp_path = fh.name
        try:
            samples, rate, _ = rc.read_wav(tmp_path)
            self.assertEqual(rate, 16000)
            self.assertAlmostEqual(samples[1], 0.5, places=4)
        finally:
            os.unlink(tmp_path)

    def test_read_wav_rejects_wrong_width(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "w8.wav")
            with wave.open(path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(1)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x01\x02")
            with self.assertRaises(ValueError):
                pe.read_wav_i16(path)

    def test_encode_pcm_f32le(self):
        payload = rc.encode_pcm([0.0, 1.0, -1.0], "f32le")
        self.assertEqual(len(payload), 12)
        self.assertEqual(struct.unpack("<3f", payload), (0.0, 1.0, -1.0))

    def test_encode_pcm_s16le(self):
        payload = rc.encode_pcm([0, -32768, 32767], "s16le")
        self.assertEqual(struct.unpack("<3h", payload), (0, -32768, 32767))

    def test_encode_pcm_unknown_format(self):
        with self.assertRaises(ValueError):
            rc.encode_pcm([0.0], "f64le")


class MakeAudioIntegrationTests(unittest.TestCase):
    """make-audio CLI against a synthetic mini-manifest (no corpus, no daemon)."""

    def _make_dataset(self, tmp: str) -> str:
        dataset = os.path.join(tmp, "ds")
        os.makedirs(os.path.join(dataset, "audio", "rec"), exist_ok=True)
        paths = []
        for index, text in enumerate(("один два", "три четыре", "пять шесть")):
            rel = f"audio/rec/rec-{index:04d}.wav"
            full = os.path.join(dataset, rel)
            pe.write_wav(full, [8000 if i % 2 == 0 else -8000 for i in range(16000)])
            paths.append((rel, text))
        with open(os.path.join(dataset, "manifest.tsv"), "w", encoding="utf-8") as fh:
            for rel, text in paths:
                fh.write(f"{rel}\t{text}\n")
        with open(os.path.join(dataset, "metadata.jsonl"), "w", encoding="utf-8") as fh:
            for index, _ in enumerate(paths):
                fh.write(
                    json.dumps(
                        {
                            "segment_id": f"rec-{index:04d}",
                            "recording_id": "rec",
                            "split": "dev",
                            "speaker_id": f"rec/s{index}",
                            "duration_s": 1.0,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return dataset

    def test_make_audio_writes_wav_and_sidecar(self):
        import json as json_mod

        with tempfile.TemporaryDirectory() as tmp:
            dataset = self._make_dataset(tmp)
            out = os.path.join(tmp, "window.wav")
            code = pe.cmd_make_audio(
                [
                    "--dataset", dataset,
                    "--recording", "rec",
                    "--start", "0",
                    "--count", "3",
                    "--gap-ms", "500",
                    "--out", out,
                ]
            )
            self.assertEqual(code, 0)
            sidecar = os.path.splitext(out)[0] + ".jsonl"
            with open(sidecar, encoding="utf-8") as fh:
                entries = [json.loads(line) for line in fh]
            self.assertEqual(len(entries), 3)
            self.assertEqual([e["gap_before_ms"] for e in entries], [0, 500, 500])
            # Default lead 200 ms: speech blocks are [200,1200], [1700,2700],
            # [3200,4200]; trailing 1200 ms silence ends the window at 5400 ms.
            self.assertEqual(entries[1]["start_ms"], 1700)
            self.assertEqual(entries[2]["start_ms"], 3200)
            samples, rate, _ = pe.read_wav_i16(out)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(samples), pe.ms_to_samples(5400))
            self.assertEqual(
                [e["reference"] for e in entries],
                ["один два", "три четыре", "пять шесть"],
            )

    def test_make_audio_gaps_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = self._make_dataset(tmp)
            out = os.path.join(tmp, "window.wav")
            self.assertEqual(
                pe.cmd_make_audio(
                    [
                        "--dataset", dataset,
                        "--recording", "rec",
                        "--count", "3",
                        "--gaps", "100,900",
                        "--gap-ms", "0",
                        "--lead-ms", "0",
                        "--trail-ms", "0",
                        "--out", out,
                    ]
                ),
                0,
            )
            with open(os.path.splitext(out)[0] + ".jsonl", encoding="utf-8") as fh:
                entries = [json.loads(line) for line in fh]
            self.assertEqual([e["gap_before_ms"] for e in entries], [0, 100, 900])
            samples, _, _ = pe.read_wav_i16(out)
            self.assertEqual(len(samples), pe.ms_to_samples(3000 + 1000))
            # Silence in both gaps.
            self.assertTrue(
                all(v == 0 for v in samples[pe.ms_to_samples(1000):pe.ms_to_samples(1100)])
            )
            self.assertTrue(
                all(v == 0 for v in samples[pe.ms_to_samples(2100):pe.ms_to_samples(3000)])
            )

    def test_start_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = self._make_dataset(tmp)
            with self.assertRaises(ValueError):
                pe.select_window(dataset, "rec", start=5, count=2)


if __name__ == "__main__":
    unittest.main()
