import contextlib
import io
import hashlib
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dataset
import cli

class CorpusPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = []
        self.references = {"dev1": "Кластер Kubernetes", "test1": "Проверить PostgreSQL"}
        for i, (name, split) in enumerate((("dev1", "dev"), ("test1", "test"))):
            path = self.root / (name + ".wav")
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(bytes([i, 0]) * 160)
            self.rows.append({"segment_id": name, "recording_id": name, "register": "R1", "speaker_id": name, "mic_class": "headset", "noise_class": "none", "duration_s": 0.01, "split": split, "source": "test recording", "license": "consent", "rights_status": "verified", "rights_evidence": "consent record 1", "rights_reviewer": "reviewer", "transcript_status": "verified", "transcript_reviewer": "reviewer", "transcript_sha256": hashlib.sha256(self.references[name].encode()).hexdigest(), "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.write_data()

    def write_data(self):
        (self.root / "manifest.tsv").write_text("".join(f"{key}.wav\t{value}\n" for key, value in self.references.items()), encoding="utf-8")
        (self.root / "metadata.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows), encoding="utf-8")

    def check(self):
        return dataset.benchmark_preflight(str(self.root))

    def test_accepted_only_with_evidence_and_verified_integrity(self):
        report = self.check()
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["split_counts"], {"dev": 1, "test": 1})

    def test_unverified_transcript_and_missing_rights_rejected(self):
        self.rows[0]["transcript_status"] = "unverified"
        self.rows[1]["rights_evidence"] = ""
        self.write_data()
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertTrue(any("transcript_status" in error for error in report["errors"]))
        self.assertTrue(any("rights_evidence" in error for error in report["errors"]))

    def test_edited_reference_and_audio_rejected(self):
        self.references["dev1"] += " изменён"
        (self.root / "test1.wav").write_bytes(b"bad")
        self.write_data()
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertTrue(any("transcript_sha256" in error for error in report["errors"]))
        self.assertTrue(any("audio_sha256" in error for error in report["errors"]))

    def test_unknown_license_rejected(self):
        self.rows[0]["license"] = "unknown"
        self.write_data()
        self.assertFalse(self.check()["ok"])

    def test_missing_split_and_speaker_leak_rejected(self):
        self.rows[1]["split"] = "dev"
        self.write_data()
        self.assertFalse(self.check()["ok"])
        self.rows[1]["split"] = "test"
        self.rows[1]["speaker_id"] = self.rows[0]["speaker_id"]
        self.write_data()
        self.assertFalse(self.check()["ok"])

    def test_missing_audio_and_unmatched_metadata_rejected(self):
        (self.root / "dev1.wav").unlink()
        self.rows[1]["segment_id"] = "other"
        self.write_data()
        self.assertFalse(self.check()["ok"])

    def test_duplicate_audio_cannot_cross_split(self):
        same = (self.root / "dev1.wav").read_bytes()
        (self.root / "test1.wav").write_bytes(same)
        self.rows[1]["audio_sha256"] = hashlib.sha256(same).hexdigest()
        self.write_data()
        self.assertFalse(self.check()["ok"])

    def test_duration_mismatch_is_rejected(self):
        self.rows[0]["duration_s"] = 5.0
        self.write_data()
        self.assertFalse(self.check()["ok"])

    def test_corrupt_wav_rejected_even_if_sha_matches(self):
        path = self.root / "dev1.wav"
        path.write_bytes(b"not a wave file")
        self.rows[0]["audio_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_data()
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertTrue(any("WAV" in error for error in report["errors"]))

    def test_missing_verification_identity_rejected(self):
        self.rows[0]["transcript_reviewer"] = "  "
        self.rows[1]["rights_status"] = "unverified"
        self.write_data()
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertTrue(any("transcript_reviewer" in error for error in report["errors"]))
        self.assertTrue(any("rights_status" in error for error in report["errors"]))

    def test_benchmark_score_with_verified_synthetic_fixture(self):
        hyp = self.root / "hyp.tsv"
        hyp.write_text("dev1\tКластер Kubernetes\ntest1\tПроверить PostgreSQL\n", encoding="utf-8")
        out = self.root / "result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["score", "--dataset", str(self.root), "--split", "dev", "--benchmark", "--hypotheses", str(hyp), "--json", str(out)])
        self.assertEqual(code, 0)
        result = json.loads(out.read_text())
        self.assertEqual(result["utterances"], 1)
        self.assertTrue(result["provenance"]["parameters"]["benchmark"])
