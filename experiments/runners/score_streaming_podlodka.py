#!/usr/bin/env python3
"""Считает WER/CER и задержки для потоковых прогонов на записях Podlodka.

Референсы лежат в `experiments/corpus/local/transcripts/refs.jsonl` (сценарный
текст, не прослушан). Прогоны `stream-parakeet` пишут JSON с `segments[].final`
(накопленный потоковый текст) и `segments[].partials`. Скрипт собирает из них
временный набор в формате `asr_eval`, зовёт единственный скорер стенда и рядом
печатает задержки из `analyze_streaming.py`, чтобы точность и задержка читались
по одному прогону.

    python3 experiments/runners/score_streaming_podlodka.py \
        --run-dir experiments/corpus/local/streaming/ultra \
        --out experiments/corpus/local/streaming/ultra/summary.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "asr_eval", "cli.py")
ANALYZE = os.path.join(REPO, "runners", "analyze_streaming.py")
REFS = os.path.join(REPO, "corpus", "local", "transcripts", "refs.jsonl")


def load_refs(path: str) -> dict[str, dict]:
    records = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            name = os.path.basename(record["file"])
            records[os.path.splitext(name)[0]] = {
                "audio": name, "ref": record["ref"], "segment_id": record["segment_id"],
            }
    return records


def build_dataset(refs: dict[str, dict], run: dict, out_dir: str) -> list[str]:
    """Набор содержит только те записи, что есть в прогоне: иначе скорер ругается."""
    durations = {seg["segment_id"]: seg["audio_s"] for seg in run["segments"]}
    selected = [refs[key] for key in durations if key in refs]
    missing = set(durations) - set(refs)
    if missing:
        raise SystemExit(f"нет референсов для {sorted(missing)[:3]}")
    with open(os.path.join(out_dir, "manifest.tsv"), "w", encoding="utf-8") as manifest, \
            open(os.path.join(out_dir, "metadata.jsonl"), "w", encoding="utf-8") as metadata:
        for item in sorted(selected, key=lambda row: row["audio"]):
            seg_id = os.path.splitext(item["audio"])[0]
            manifest.write(f"audio/{item['audio']}\t{' '.join(item['ref'].split())}\n")
            metadata.write(json.dumps({
                "segment_id": seg_id, "recording_id": seg_id, "register": "R2",
                "speaker_id": seg_id, "mic_class": "builtin", "noise_class": "clean",
                "duration_s": durations[seg_id], "split": "dev",
            }, ensure_ascii=False) + "\n")
    return [os.path.splitext(item["audio"])[0] for item in selected]


def machine_label() -> str:
    """Возвращает «чип, RAM, macOS» текущей машины."""
    import subprocess

    def out(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except Exception:
            return "?"

    chip = out(["sysctl", "-n", "machdep.cpu.brand_string"])
    try:
        ram = f"{int(out(['sysctl', '-n', 'hw.memsize'])) / 1073741824:.0f} GB"
    except ValueError:
        ram = "?"
    return f"{chip}, {ram}, macOS {out(['sw_vers', '-productVersion'])}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--refs", default=REFS)
    parser.add_argument("--out", default=None)
    parser.add_argument("--machine", default=None,
                        help="метка машины; по умолчанию определяется из системы")
    args = parser.parse_args(argv)

    refs = load_refs(args.refs)
    rows = []
    for path in sorted(glob.glob(os.path.join(args.run_dir, "*.json"))):
        if path.endswith("summary.json") or path.endswith("-score.json"):
            continue
        run = json.load(open(path, encoding="utf-8"))
        if "segments" not in run or not run["segments"]:
            continue
        name = os.path.splitext(os.path.basename(path))[0]
        with tempfile.TemporaryDirectory() as tmp:
            build_dataset(refs, run, tmp)
            hyp = os.path.join(tmp, "hyp.tsv")
            with open(hyp, "w", encoding="utf-8") as fh:
                for seg in run["segments"]:
                    fh.write(f"{seg['segment_id']}\t{' '.join(seg['final'].split())}\n")
            score_path = os.path.join(args.run_dir, f"{name}-score.json")
            result = subprocess.run(
                [sys.executable, CLI, "score", "--dataset", tmp, "--split", "dev",
                 "--hypotheses", hyp, "--json", score_path],
                capture_output=True, text=True)
            if result.returncode:
                print(f"{name}: скорер упал: {result.stderr.strip()[-300:]}", file=sys.stderr)
                continue
            score = json.load(open(score_path, encoding="utf-8"))
        analysis = subprocess.run([sys.executable, ANALYZE, "--json", path],
                                  capture_output=True, text=True, check=True)
        timing = json.loads(analysis.stdout)
        config = run["config"]
        rows.append({
            "config": name,
            "chunk_s": config["chunk_s"], "right_s": config["right_s"],
            "hypothesis_s": config["hypothesis_s"], "left_s": config["left_s"],
            "segments": len(run["segments"]),
            "first_partial_p50": timing["first_partial_p50"],
            "first_partial_p95": timing["first_partial_p95"],
            "text_lag_p50": timing["text_lag_p50"], "text_lag_p95": timing["text_lag_p95"],
            "updates_total": timing["updates_total"], "revisions_total": timing["revisions_total"],
            "stream_vs_final": timing.get("stream_vs_final"),
            "wer": score["wer"], "cer": score["cer"], "substitutions": score["substitutions"],
            "deletions": score["deletions"], "insertions": score["insertions"],
            "words": score["words"], "score": f"{name}-score.json",
        })

    summary = {
        "status": "experiment_not_benchmark",
        "note": ("Podlodka-записи из corpus/local/audio; референсы — сценарный текст, а не "
                 "прослушанная речь; гипотеза — накопленный потоковый текст (`final`)."),
        "machine": args.machine or machine_label(),
        "model": "parakeet-ultra (FluidAudio SlidingWindowAsrManager, ANE)",
        "realtime_feed": True,
        "rows": rows,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
