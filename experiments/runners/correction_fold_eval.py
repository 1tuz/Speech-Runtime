#!/usr/bin/env python3
"""Считает WER до/после корректора на одном совещании (holdout-фолд).

    python3 experiments/runners/correction_fold_eval.py \
        --pairs .../pairs.jsonl --baseline-tsv .../system-dev.tsv \
        --dataset .../veche-prepared/simple --holdout 5-it-product \
        --work-dir /tmp/fold-5-it-product --corrected-tsv /tmp/after.tsv \
        --out-json /tmp/report.json

Собирает в `--work-dir` мини-датасет только из сегментов совещания
`--holdout`, чтобы `correction_report.py` посчитал метрики по одному
совещанию, а не по всему сплиту. Если `--corrected-tsv` уже существует,
генерация коррекции пропускается (можно гонять отчёт по готовым TSV).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNERS = os.path.join(REPO, "runners")


def load_tsv(path: str) -> dict[str, str]:
    rows = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if line:
            segment_id, text = line.split("\t", 1)
            rows[segment_id] = text
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pairs", required=True, help="pairs.jsonl от correction_pairs.py")
    parser.add_argument("--baseline-tsv", required=True, help="гипотезы системы до коррекции")
    parser.add_argument("--dataset", required=True, help="корень veche-prepared/simple")
    parser.add_argument("--holdout", required=True, help="recording_id совещания")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--corrected-tsv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--adapter", default=None, help="путь к LoRA-адаптеру (или ничего)")
    parser.add_argument("--split", default="dev")
    args = parser.parse_args(argv)

    os.makedirs(args.work_dir, exist_ok=True)
    dataset_out = os.path.join(args.work_dir, "dataset")
    os.makedirs(dataset_out, exist_ok=True)

    # 1. Пары только holdout-совещания.
    pairs = [json.loads(line) for line in open(args.pairs, encoding="utf-8") if line.strip()]
    heldout_pairs = [row for row in pairs if row.get("meeting") == args.holdout]
    if not heldout_pairs:
        raise SystemExit(f"в {args.pairs} нет пар для совещания {args.holdout}")
    heldout_ids = [row["segment_id"] for row in heldout_pairs]
    systems = sorted({row["system"] for row in heldout_pairs})
    if len(systems) != 1:
        raise SystemExit(f"в holdout-пары попали несколько систем: {systems}")
    heldout_pairs_path = os.path.join(args.work_dir, "heldout-pairs.jsonl")
    with open(heldout_pairs_path, "w", encoding="utf-8") as fh:
        for row in heldout_pairs:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 2. Мини-датасет: только сегменты этого совещания, исходные пути и сплиты.
    meta_rows = [json.loads(line) for line in open(os.path.join(args.dataset, "metadata.jsonl"), encoding="utf-8") if line.strip()]
    meeting_meta = [row for row in meta_rows if row.get("recording_id") == args.holdout]
    if not meeting_meta:
        raise SystemExit(f"в {args.dataset}/metadata.jsonl нет сегментов {args.holdout}")
    with open(os.path.join(dataset_out, "metadata.jsonl"), "w", encoding="utf-8") as fh:
        for row in meeting_meta:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    meeting_ids = {row["segment_id"] for row in meeting_meta}
    missing = sorted(set(heldout_ids) - meeting_ids)
    if missing:
        raise SystemExit(f"нет метаданных для {len(missing)} сегментов пар, первый {missing[0]}")
    kept = 0
    with open(os.path.join(dataset_out, "manifest.tsv"), "w", encoding="utf-8") as out:
        for line in open(os.path.join(args.dataset, "manifest.tsv"), encoding="utf-8"):
            line = line.rstrip("\n")
            if not line:
                continue
            path, text = line.split("\t", 1)
            segment_id = os.path.splitext(os.path.basename(path))[0]
            if segment_id in meeting_ids:
                out.write(f"{path}\t{text}\n")  # путь и текст — как в исходном манифесте
                kept += 1
    if kept != len(meeting_ids):
        raise SystemExit(f"в манифесте нашлось {kept} сегментов из {len(meeting_ids)}")

    # 3. Гипотезы «до» — только сегменты совещания, в порядке baseline-tsv.
    baseline = load_tsv(args.baseline_tsv)
    before_path = os.path.join(args.work_dir, "before.tsv")
    with open(before_path, "w", encoding="utf-8") as fh:
        for segment_id, text in baseline.items():
            if segment_id in meeting_ids:
                fh.write(f"{segment_id}\t{text}\n")

    # 4. Коррекция, если готового TSV ещё нет.
    if not os.path.isfile(args.corrected_tsv):
        apply_json = os.path.join(args.work_dir, "apply.json")
        cmd = [sys.executable, os.path.join(RUNNERS, "correction_apply.py"),
               "--pairs", heldout_pairs_path, "--out-tsv", args.corrected_tsv,
               "--out-json", apply_json]
        if args.adapter:
            cmd += ["--adapter", args.adapter]
        os.makedirs(os.path.dirname(os.path.abspath(args.corrected_tsv)), exist_ok=True)
        subprocess.run(cmd, check=True)

    # 5. Отчёт по мини-датасету (stdout перехватываем: финальный JSON печатаем сами).
    report = subprocess.run(
        [sys.executable, os.path.join(RUNNERS, "correction_report.py"),
         "--dataset", dataset_out, "--before", before_path, "--after", args.corrected_tsv,
         "--split", args.split, "--out", args.out_json],
        check=True, capture_output=True, text=True)
    sys.stderr.write(report.stderr)
    with open(args.out_json, encoding="utf-8") as fh:
        result = json.load(fh)
    result["meeting"] = args.holdout
    result["system"] = systems[0]
    result["adapter"] = args.adapter
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
