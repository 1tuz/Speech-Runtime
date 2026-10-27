import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cli
import dataset

class Iteration1CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        with open(os.path.join(self.root, "manifest.tsv"), "w") as f:
            f.write("a.wav\tkubectl get pods -n prod\nb.wav\thelm\n")
        rows = [dict(segment_id="a", recording_id="r1", register="R1", speaker_id="s1", mic_class="headset", noise_class="none", duration_s=1, split="dev", commands=["kubectl get pods -n prod"]), dict(segment_id="b", recording_id="r2", register="R1", speaker_id="s2", mic_class="headset", noise_class="none", duration_s=1, split="test")]
        with open(os.path.join(self.root, "metadata.jsonl"), "w") as f:
            for row in rows: f.write(json.dumps(row) + "\n")
        self.hyp = os.path.join(self.root, "hyp.tsv")
        with open(self.hyp, "w") as f: f.write("a\tkubectl get pods n prod\nb\thelm\n")

    def test_mixed_split_requires_explicit_selection(self):
        with self.assertRaises(ValueError):
            dataset.load_scoring_data(self.root, None)

    def test_explicit_dev_selects_only_dev_and_command_flag_is_significant(self):
        out = os.path.join(self.root, "result.json")
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["score", "--dataset", self.root, "--hypotheses", self.hyp, "--split", "dev", "--json", out])
        self.assertEqual(code, 0)
        with open(out) as f: result = json.load(f)
        self.assertEqual(result["utterances"], 1)
        self.assertEqual(result["commands"]["total"], 1)
        self.assertEqual(result["commands"]["correct"], 0)
        self.assertEqual(result["commands"]["accuracy"], 0)
        self.assertEqual(result["provenance"]["split"], "dev")
        self.assertTrue(result["provenance"]["dataset_sha256"])
        self.assertTrue(result["scorer_version"])

    def test_wrong_metadata_id_rejected(self):
        with open(os.path.join(self.root, "metadata.jsonl"), "a") as f:
            f.write(json.dumps(dict(segment_id="extra", recording_id="r3", register="R1", speaker_id="s3", mic_class="headset", noise_class="none", duration_s=1, split="test")) + "\n")
        with self.assertRaises(ValueError): dataset.load_scoring_data(self.root, "dev")

    def test_command_occurrence_cannot_be_reused(self):
        result = cli.command_accuracy([{"reference":"kubectl get pods -n prod kubectl get pods -n prod", "hypothesis":"kubectl get pods -n prod", "commands":["kubectl get pods -n prod", "kubectl get pods -n prod"]}])
        self.assertEqual((result["total"], result["correct"]), (2, 1))

    def test_grouped_compare_writes_reproducible_provenance(self):
        out = os.path.join(self.root, "comparison.json")
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["compare", "--dataset", self.root, "--a", self.hyp, "--b", self.hyp, "--split", "dev", "--group-by", "recording_id", "--replications", "20", "--seed", "42", "--json", out])
        # Only one group cannot estimate a cluster interval.
        self.assertEqual(code, 2)

    def test_score_rejects_duplicate_report_ids(self):
        with self.assertRaises(SystemExit):
            cli._pairs_from_report(self._report_duplicate())

    def _report_duplicate(self):
        path = os.path.join(self.root, "report.json")
        with open(path, "w") as f:
            json.dump({"results": [{"utteranceResults": [{"id": "a", "reference": "x", "hypothesis": "x"}, {"id": "a", "reference": "y", "hypothesis": "y"}]}]}, f)
        return path

    def test_split_rejects_speaker_leak(self):
        path = os.path.join(self.root, "metadata.jsonl")
        with open(path) as f: rows = [json.loads(line) for line in f]
        rows[1]["speaker_id"] = rows[0]["speaker_id"]
        with open(path, "w") as f:
            for row in rows: f.write(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ValueError, "утечка"):
            dataset.load_scoring_data(self.root, "dev")

    def test_grouped_compare_records_seed_and_input_hashes(self):
        path = os.path.join(self.root, "metadata.jsonl")
        with open(path) as f: rows = [json.loads(line) for line in f]
        rows[1]["split"] = "dev"
        with open(path, "w") as f:
            for row in rows: f.write(json.dumps(row) + "\n")
        out = os.path.join(self.root, "comparison.json")
        args = ["compare", "--dataset", self.root, "--a", self.hyp, "--b", self.hyp, "--split", "dev", "--group-by", "recording_id", "--replications", "20", "--seed", "42", "--json", out]
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cli.main(args), 0)
        with open(out) as f: first = json.load(f)
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cli.main(args), 0)
        with open(out) as f: second = json.load(f)
        self.assertEqual(first, second)
        self.assertEqual(first["groups"], 2)
        self.assertEqual(first["provenance"]["parameters"]["seed"], 42)
        self.assertTrue(first["provenance"]["a_sha256"])

    def test_report_and_hypothesis_flags_conflict(self):
        path = os.path.join(self.root, "valid.json")
        with open(path, "w") as f:
            json.dump({"results": [{"utteranceResults": [{"id": "a", "reference": "x", "hypothesis": "x"}]}]}, f)
        with self.assertRaises(SystemExit):
            cli.main(["score", "--report", path, "--hypotheses", self.hyp])

    def test_command_result_is_visible_on_stdout(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["score", "--dataset", self.root, "--hypotheses", self.hyp, "--split", "dev"]), 0)
        self.assertIn("commands:", stdout.getvalue())
