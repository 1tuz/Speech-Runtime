#!/usr/bin/env python3
"""Сравнивает WER до и после корректора и считает ложные исправления.

    python3 experiments/runners/correction_report.py \
        --dataset .../veche-prepared/simple --history /tmp/hyp-before.tsv \
        --corrected /tmp/hyp-after.tsv --out /tmp/report.json

«Исправление» — позиция, которая в исходной гипотезе была ошибкой
(sub/del/ins), а после коррекции стала совпадением. «Ложное исправление» —
позиция, которая была верной и стала ошибкой. Обе величины считаются по
нормализованным токенам (`norm-v2`) относительно референса, а не по диффу
двух гипотез: дифф не знает, какая версия права.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

import align  # noqa: E402
import norm  # noqa: E402
import wer  # noqa: E402


def load_tsv(path: str) -> dict[str, str]:
    rows = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line:
            segment_id, text = line.split("\t", 1)
            rows[segment_id] = text
    return rows


def op_counts(ref: list[str], hyp: list[str]) -> dict[str, int]:
    ops, _ = align.align(ref, hyp)
    counts = {"match": 0, "sub": 0, "del": 0, "ins": 0}
    for op, _, _ in ops:
        counts[op] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    refs = {}
    for line in open(os.path.join(args.dataset, "manifest.tsv"), encoding="utf-8"):
        path, text = line.rstrip("\n").split("\t", 1)
        refs[os.path.splitext(os.path.basename(path))[0]] = text
    meta = {}
    for line in open(os.path.join(args.dataset, "metadata.jsonl"), encoding="utf-8"):
        row = json.loads(line)
        meta[row["segment_id"]] = row

    before = load_tsv(args.before)
    after = load_tsv(args.after)
    ids = sorted(sid for sid, row in meta.items() if row["split"] == args.split)
    missing = [sid for sid in ids if sid not in before or sid not in after]
    if missing:
        raise SystemExit(f"нет гипотез для {len(missing)} сегментов, первый {missing[0]}")

    utterances_before = [{"id": sid, "reference": refs[sid], "hypothesis": before[sid]} for sid in ids]
    utterances_after = [{"id": sid, "reference": refs[sid], "hypothesis": after[sid]} for sid in ids]
    corpus_before = wer.corpus(utterances_before)
    corpus_after = wer.corpus(utterances_after)

    fixes = regressions = unchanged_segments = changed_segments = 0
    inserted_bad = deleted_good = 0
    deltas = []
    for sid in ids:
        ref = norm.tokens(refs[sid])
        hyp_before = norm.tokens(before[sid])
        hyp_after = norm.tokens(after[sid])
        if hyp_before == hyp_after:
            unchanged_segments += 1
            continue
        changed_segments += 1
        ops_before = op_counts(ref, hyp_before)
        ops_after = op_counts(ref, hyp_after)
        seg_fixes = (ops_before["sub"] + ops_before["del"] + ops_before["ins"]) - (
            ops_after["sub"] + ops_after["del"] + ops_after["ins"])
        if seg_fixes > 0:
            fixes += seg_fixes
        else:
            regressions += -seg_fixes
        # Вставки и удаления считаются отдельно: они меняют длину текста, а не форму.
        inserted_bad += max(0, ops_after["ins"] - ops_before["ins"])
        deleted_good += max(0, ops_after["del"] - ops_before["del"])
        deltas.append({"segment_id": sid, "errors_before": ops_before["sub"] + ops_before["del"] + ops_before["ins"],
                       "errors_after": ops_after["sub"] + ops_after["del"] + ops_after["ins"]})

    deltas_sorted = sorted(deltas, key=lambda row: row["errors_after"] - row["errors_before"])
    report = {
        "status": "experiment_not_benchmark",
        "split": args.split,
        "segments": len(ids),
        "before": {k: corpus_before[k] for k in ("wer", "cer", "errors", "words", "substitutions", "deletions", "insertions")},
        "after": {k: corpus_after[k] for k in ("wer", "cer", "errors", "words", "substitutions", "deletions", "insertions")},
        "wer_delta": corpus_after["wer"] - corpus_before["wer"],
        "changed_segments": changed_segments,
        "unchanged_segments": unchanged_segments,
        "error_positions_fixed": fixes,
        "error_positions_added": regressions,
        "extra_insertions": inserted_bad,
        "extra_deletions": deleted_good,
        "worst_segments": deltas_sorted[:5],
        "best_segments": deltas_sorted[-5:],
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
