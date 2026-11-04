"""Offline unit tests for the pure helpers of runtime_eval.soak.

No daemon is started and no model is loaded: percentile math, RSS and
`phys_footprint` parsing, WAV slicing/wrapping, and the assertion collector
are checked against tiny fixed inputs. Network-bearing scenarios are out of
scope here.

Run from experiments/:
    python3 -m unittest runtime_eval.tests.test_soak
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

from runtime_eval import soak  # noqa: E402


class PercentileTests(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertIsNone(soak.percentile([], 50))
        self.assertEqual(soak.percentile([7], 50), 7)
        self.assertEqual(soak.percentile([7], 0), 7)
        self.assertEqual(soak.percentile([7], 100), 7)

    def test_nearest_rank(self):
        values = list(range(1, 101))  # 1..100
        self.assertEqual(soak.percentile(values, 50), 50)
        self.assertEqual(soak.percentile(values, 95), 95)
        self.assertEqual(soak.percentile(values, 100), 100)
        self.assertEqual(soak.percentile(values, 0), 1)

    def test_small_samples(self):
        self.assertEqual(soak.percentile([10, 20, 30, 40], 50), 20)
        self.assertEqual(soak.percentile([10, 20, 30, 40], 95), 40)
        self.assertEqual(soak.percentile([5, 1, 3], 50), 3)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            soak.percentile([1, 2, 3], -1)
        with self.assertRaises(ValueError):
            soak.percentile([1, 2, 3], 101)

    def test_stats_shape(self):
        stats = soak.percentile_stats([100, 200, 300, 400])
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["mean"], 250.0)
        self.assertEqual(stats["p50"], 200)
        self.assertEqual(stats["p95"], 400)
        empty = soak.percentile_stats([])
        self.assertEqual(empty, {"n": 0, "mean": None, "p50": None, "p95": None})


class RssParsingTests(unittest.TestCase):
    def test_ps_output(self):
        self.assertEqual(soak.parse_rss_kib("  46512 \n"), 46512)
        self.assertEqual(soak.parse_rss_kib("46512"), 46512)

    def test_gone_process(self):
        self.assertIsNone(soak.parse_rss_kib(""))
        self.assertIsNone(soak.parse_rss_kib("\n\n"))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            soak.parse_rss_kib("not-a-number")

    def test_footprint_plain_form(self):
        out = "phys_footprint:  22020096\nsomething: 1\n"
        self.assertEqual(soak.parse_footprint_bytes(out), 22020096)

    def test_footprint_json_form(self):
        out = '{\n  "phys_footprint": 3145728,\n  "rss": 1\n}'
        self.assertEqual(soak.parse_footprint_bytes(out), 3145728)

    def test_footprint_missing_key(self):
        self.assertIsNone(soak.parse_footprint_bytes("rss: 123\n"))
        self.assertIsNone(soak.parse_footprint_bytes(""))
        self.assertIsNone(soak.parse_footprint_bytes("phys_footprint: n/a"))


class SliceTests(unittest.TestCase):
    def test_slice_within_bounds(self):
        samples = list(range(16000))  # 1 s
        # 250 ms window from 100 ms -> sample offsets [1600, 4000)
        self.assertEqual(soak.slice_samples(samples, 16000, 100, 250), samples[1600:4000])
        self.assertEqual(soak.slice_samples(samples, 16000, 0, 50), samples[:800])
        self.assertEqual(soak.slice_samples(samples, 16000, 950, 1050), samples[15200:])

    def test_slice_clamps(self):
        samples = [1.0, 2.0, 3.0]
        self.assertEqual(soak.slice_samples(samples, 16000, -100, 10_000), samples)
        self.assertEqual(soak.slice_samples(samples, 16000, 10_000, 20_000), [])

    def test_chunking(self):
        samples = list(range(3200))  # 200 ms at 16 kHz
        chunks = list(soak.chunk_samples(samples, 16000, 50))
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[1][0], 50)
        self.assertEqual(len(chunks[0][1]), 800)
        self.assertEqual(chunks[-1][1], samples[2400:])

    def test_chunk_rejects_bad_step(self):
        with self.assertRaises(ValueError):
            list(soak.chunk_samples([1, 2, 3], 16000, 0))

    def test_wrap_slice_same_pass(self):
        samples = [1, 2, 3, 4]
        self.assertEqual(soak.wrap_slice(samples, 0, 3), [1, 2, 3])
        self.assertEqual(soak.wrap_slice(samples, 1, 4), [2, 3, 4])

    def test_wrap_slice_crosses_boundary(self):
        samples = [1, 2, 3, 4]
        self.assertEqual(soak.wrap_slice(samples, 2, 7), [3, 4, 1, 2, 3])
        self.assertEqual(soak.wrap_slice(samples, 4, 8), [1, 2, 3, 4])
        self.assertEqual(soak.wrap_slice(samples, 3, 9), [4, 1, 2, 3, 4, 1])

    def test_wrap_slice_edges(self):
        self.assertEqual(soak.wrap_slice([], 0, 10), [])
        self.assertEqual(soak.wrap_slice([1], 2, 5), [1, 1, 1])
        self.assertEqual(soak.wrap_slice([1, 2], 5, 5), [])
        self.assertEqual(soak.wrap_slice([1, 2], -1, 3), [])


class AssertionLogicTests(unittest.TestCase):
    def test_collects_pass_and_fail(self):
        check = soak.Assertions()
        self.assertTrue(check.check("ok_thing", 1 == 1, "yes"))
        self.assertFalse(check.check("bad_thing", False, "no"))
        self.assertTrue(check.check("truthy_string", "detail"))
        self.assertEqual(len(check.results), 3)
        by_name = {r["name"]: r for r in check.results}
        self.assertTrue(by_name["ok_thing"]["pass"])
        self.assertFalse(by_name["bad_thing"]["pass"])
        self.assertEqual(by_name["bad_thing"]["detail"], "no")
        self.assertFalse(check.all_passed)

    def test_all_passed(self):
        check = soak.Assertions()
        check.check("a", True, "1")
        check.check("b", [1], "non-empty")
        self.assertTrue(check.all_passed)

    def test_empty_never_passes(self):
        self.assertFalse(soak.Assertions().all_passed)

    def test_results_are_json_serialisable(self):
        import json

        check = soak.Assertions()
        check.check("a", True, "detail")
        check.check("b", False, None)
        json.dumps(check.results)


class GrowthAndStarvationTests(unittest.TestCase):
    def test_growth_ok_within_limit(self):
        samples = [
            {"worker": {"rss_kib": 100 * 1024}},
            {"worker": {"rss_kib": 150 * 1024}},
        ]
        self.assertTrue(soak._growth_ok(samples, "worker", "rss_kib", 100))
        self.assertFalse(soak._growth_ok(samples, "worker", "rss_kib", 10))

    def test_growth_unmeasurable_fails(self):
        self.assertFalse(soak._growth_ok([], "worker", "rss_kib", 1))
        self.assertFalse(
            soak._growth_ok([{"worker": {"rss_kib": None}}], "worker", "rss_kib", 1)
        )

    def test_no_starvation(self):
        interactive = {"p95": 300}
        batch = {"p50": 100}
        self.assertTrue(soak._no_starvation(interactive, batch))
        self.assertFalse(soak._no_starvation({"p95": 5000}, {"p50": 100}))
        # Unmeasurable sides never claim starvation.
        self.assertTrue(soak._no_starvation({"p95": None}, {"p50": 100}))
        self.assertTrue(soak._no_starvation({"p95": 5000}, {"p50": None}))
        self.assertTrue(soak._no_starvation({"p95": 5000}, {"p50": 0}))


class ArtifactTests(unittest.TestCase):
    def test_header_and_write(self):
        import json
        import tempfile

        header = soak.artifact_header(["soak", "cancel", "--wav", "x.wav"], "cancel")
        self.assertEqual(header["schema"], "runtime-soak-v1")
        self.assertEqual(header["command"], "cancel")
        self.assertEqual(header["argv"], ["soak", "cancel", "--wav", "x.wav"])
        self.assertIsNone(header["finished_utc"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "artifact.json")
            soak.write_artifact(path, header)
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        self.assertIsNotNone(loaded["finished_utc"])


class ParserTests(unittest.TestCase):
    def test_every_scenario_has_flags(self):
        parser = soak.build_parser()
        for scenario in soak.SCENARIOS:
            args = parser.parse_args([scenario, "--wav", "x.wav"])
            self.assertEqual(args.scenario, scenario)
            self.assertTrue(args.wav)
            self.assertTrue(args.url)
            self.assertTrue(args.token_file)

    def test_two_models_has_no_model_flag(self):
        args = soak.build_parser().parse_args(["two-models", "--wav", "x.wav"])
        self.assertFalse(hasattr(args, "model"))

    def test_defaults(self):
        args = soak.build_parser().parse_args(["multiclient", "--wav", "x.wav"])
        self.assertEqual(args.clients, 4)
        self.assertEqual(args.speed, 1.0)
        self.assertFalse(args.batch)
        args = soak.build_parser().parse_args(["memory", "--wav", "x.wav"])
        self.assertEqual(args.clients, 2)
        args = soak.build_parser().parse_args(["long-session", "--wav", "x.wav"])
        self.assertEqual(args.minutes, 10.0)
        self.assertEqual(args.model, "gigaam")


if __name__ == "__main__":
    unittest.main()
