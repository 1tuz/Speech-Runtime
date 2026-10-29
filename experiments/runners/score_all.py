#!/usr/bin/env python3
"""Собирает гипотезы всех систем в стандартный TSV и считает скорер по сплитам.

Whisper пишет JSONL с полем `file`, остальные раннеры — сразу TSV. Скрипт
приводит всё к `<id>\\t<text>`, зовёт `experiments/asr_eval/cli.py score` и
печатает сводную таблицу. Ничего не пересчитывает сам: единственный скорер —
существующий стенд.

    python3 experiments/runners/score_all.py --run-dir DIR [--split test]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# Каталог experiments: раннеры лежат в нём двумя уровнями ниже.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "asr_eval", "cli.py")
TERMS = os.path.join(REPO, "asr_eval", "terms.json")


def whisper_jsonl_to_tsv(jsonl_path: str, tsv_path: str) -> int:
    rows = []
    for line in open(jsonl_path, encoding="utf-8"):
        line = line.strip()
        if not line or '"file"' not in line:
            continue
        record = json.loads(line)
        seg_id = os.path.splitext(os.path.basename(record["file"]))[0]
        rows.append(f"{seg_id}\t{' '.join(record['text'].split())}")
    with open(tsv_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--terms", default=TERMS)
    parser.add_argument("--json-dir", default=None)
    args = parser.parse_args(argv)

    systems: dict[str, str] = {}
    for name in sorted(os.listdir(args.run_dir)):
        path = os.path.join(args.run_dir, name)
        if name.startswith("whisper") and name.endswith(".jsonl"):
            tsv = path[:-6] + ".tsv"
            count = whisper_jsonl_to_tsv(path, tsv)
            systems[name[:-6]] = tsv
            print(f"{name}: {count} строк", file=sys.stderr)
        elif name.endswith(".tsv") and not name.endswith("-test.tsv"):
            systems[name[:-4]] = path

    out_dir = args.json_dir or os.path.join(args.run_dir, "scores")
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for name, tsv in systems.items():
        target = os.path.join(out_dir, f"{name}-{args.split}.json")
        cmd = [sys.executable, CLI, "score", "--dataset", args.dataset, "--split", args.split,
               "--hypotheses", tsv, "--terms", args.terms, "--json", target]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0:
            print(f"{name}: ошибка {run.stderr[-300:]}", file=sys.stderr)
            continue
        report = json.load(open(target, encoding="utf-8"))
        results[name] = {
            "wer": report["wer"], "cer": report["cer"],
            "s": report["substitutions"], "d": report["deletions"], "i": report["insertions"],
            "term_recall": report["terms"]["recall_any"],
            "term_recall_exact": report["terms"]["recall_exact"],
            "latin_form": report["terms"]["latin_form_accuracy"],
            "term_n": report["terms"]["occurrences"],
            "words": report["words"],
        }

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
