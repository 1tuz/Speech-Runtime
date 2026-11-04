#!/usr/bin/env python3
"""Собирает пары «транскрипция ASR → правильный текст» для обучения корректора.

Источник пар — только dev Veche-bench (три встречи). Test не используется: по
условию задачи корректор на нём не обучается. Референс — сценарный текст, а не
результат прослушивания, поэтому пары наследуют `transcript_status` корпуса и в
отчёте помечаются как непроверенные.

    python3 experiments/runners/correction_pairs.py \
        --dataset experiments/corpus/local/veche-prepared/simple \
        --system gigaam=.../gigaam-v3-dev.tsv \
        --system parakeet=.../parakeet-ultra-dev.tsv \
        --out-dir experiments/corpus/local/correction

Пишет `<system>/pairs.jsonl` (segment_id, meeting, hyp, ref) и сводку по числу
пар, токенов и вхождений терминов. Разбиение на обучение/проверку делает
`correction_lora.py`: оно должно быть по встречам, а не по репликам.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

import norm  # noqa: E402
import terms as terms_mod  # noqa: E402


def load_dataset(dataset: str) -> tuple[dict[str, str], dict[str, dict]]:
    refs: dict[str, str] = {}
    for line in open(os.path.join(dataset, "manifest.tsv"), encoding="utf-8"):
        path, text = line.rstrip("\n").split("\t", 1)
        refs[os.path.splitext(os.path.basename(path))[0]] = text
    meta: dict[str, dict] = {}
    for line in open(os.path.join(dataset, "metadata.jsonl"), encoding="utf-8"):
        row = json.loads(line)
        meta[row["segment_id"]] = row
    return refs, meta


def load_tsv(path: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        segment_id, text = line.split("\t", 1)
        rows[segment_id] = text
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--system", action="append", required=True, help="name=path.tsv")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)

    vocab = terms_mod.Vocabulary.from_path(args.terms)
    refs, meta = load_dataset(args.dataset)
    wanted = {sid for sid, row in meta.items() if row["split"] == args.split}

    summary = {}
    for spec in args.system:
        name, path = spec.split("=", 1)
        hypotheses = load_tsv(path)
        missing = sorted(wanted - set(hypotheses))
        if missing:
            raise SystemExit(f"{name}: нет гипотез для {len(missing)} сегментов, первый {missing[0]}")

        pairs = []
        hyp_tokens = ref_tokens = 0
        term_hits = 0
        for segment_id in sorted(wanted):
            ref = refs[segment_id]
            hyp = " ".join(hypotheses[segment_id].split())
            if not ref or not hyp:
                continue
            meeting = meta[segment_id]["recording_id"]
            pairs.append({"segment_id": segment_id, "system": name, "meeting": meeting, "hyp": hyp, "ref": ref})
            hyp_tokens += len(norm.tokens(hyp))
            ref_tokens += len(norm.tokens(ref))
            term_hits += len(vocab.occurrences(norm.tokens(ref)))

        out_dir = os.path.join(args.out_dir, name)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "pairs.jsonl"), "w", encoding="utf-8") as fh:
            for pair in pairs:
                fh.write(json.dumps(pair, ensure_ascii=False) + "\n")

        meetings = {}
        for pair in pairs:
            meetings[pair["meeting"]] = meetings.get(pair["meeting"], 0) + 1
        summary[name] = {
            "pairs": len(pairs),
            "meetings": meetings,
            "hyp_tokens": hyp_tokens,
            "ref_tokens": ref_tokens,
            "term_occurrences_in_ref": term_hits,
            "identical_pairs": sum(1 for p in pairs if p["hyp"] == p["ref"]),
        }

    print(json.dumps({"split": args.split, "systems": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
