import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli

SMOKE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "smoke")

class PreflightCLI(unittest.TestCase):
    def test_preflight_rejects_unverified_smoke_data(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["preflight", "--dataset", SMOKE]), 1)

    def test_benchmark_scoring_rejects_unverified_data(self):
        hyp = os.path.join(SMOKE, "hyp-runner-a.tsv")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(["score", "--dataset", SMOKE, "--split", "dev", "--hypotheses", hyp, "--benchmark"]), 0)

    def test_benchmark_compare_rejects_unverified_data(self):
        hyp = os.path.join(SMOKE, "hyp-runner-a.tsv")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(["compare", "--dataset", SMOKE, "--split", "dev", "--a", hyp, "--b", hyp, "--benchmark"]), 0)
