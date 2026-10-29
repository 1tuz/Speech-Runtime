#!/usr/bin/env python3
"""Собирает пары «наблюдавшаяся ошибка ASR → правильный термин» из dev-гипотез.

Источник пар — только dev: test остаётся нетронутым до финальной оценки. Для
каждой реплики гипотеза выравнивается с референсом (`align.py`), затем берутся
подстановки, попадающие в размеченное вхождение термина словаря `terms-v1`.

Выход: JSONL с полями `segment_id`, `system`, `ref_window`, `hyp_window`,
`hyp_token`, `ref_token`, `term_id`, `term_forms`, `context`. Это обучающий
материал для эмбеддингов и одновременно тест для базовых правил.

    python3 experiments/runners/make_error_pairs.py \
        --dataset experiments/corpus/local/veche-prepared/simple \
        --system gigaam=.../gigaam-v3-hypotheses.tsv \
        --system parakeet=.../parakeet-v3-nolang.tsv \
        --out experiments/corpus/local/embedding/pairs-dev.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Каталог experiments: раннеры лежат в нём двумя уровнями ниже.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

import align  # noqa: E402
import norm  # noqa: E402
import terms as terms_mod  # noqa: E402

WINDOW = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--system", action="append", required=True, help="name=path.tsv")
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    vocab = terms_mod.Vocabulary.from_path(args.terms)
    refs: dict[str, str] = {}
    for line in open(os.path.join(args.dataset, "manifest.tsv"), encoding="utf-8"):
        path, text = line.rstrip("\n").split("\t", 1)
        refs[os.path.splitext(os.path.basename(path))[0]] = text
    meta = {}
    for line in open(os.path.join(args.dataset, "metadata.jsonl"), encoding="utf-8"):
        row = json.loads(line)
        meta[row["segment_id"]] = row

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    written = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for spec in args.system:
            name, path = spec.split("=", 1)
            hypotheses = {}
            for line in open(path, encoding="utf-8"):
                seg_id, text = line.rstrip("\n").split("\t", 1)
                hypotheses[seg_id] = text
            for seg_id, hyp in hypotheses.items():
                row = meta.get(seg_id)
                if not row or row["split"] != "dev":
                    continue
                ref = refs[seg_id]
                ref_tokens = norm.tokens(ref)
                hyp_tokens = norm.tokens(hyp)
                occurrences = vocab.occurrences(ref_tokens)
                if not occurrences:
                    continue
                ops, ref_to_hyp = align.align(ref_tokens, hyp_tokens)
                for op, ref_index, hyp_index in ops:
                    if op != align.SUB:
                        continue
                    hit = next(
                        (occ for occ in occurrences if occ.start <= ref_index < occ.end),
                        None,
                    )
                    if hit is None:
                        continue
                    term = vocab.by_id[hit.term_id]
                    left = max(0, hyp_index - WINDOW)
                    right = min(len(hyp_tokens), hyp_index + WINDOW + 1)
                    out.write(json.dumps({
                        "segment_id": seg_id,
                        "system": name,
                        "ref_token": ref_tokens[ref_index],
                        "hyp_token": hyp_tokens[hyp_index],
                        "term_id": term.id,
                        "term_forms": list(term.accept),
                        "term_translit": list(term.accept_translit),
                        "context": " ".join(hyp_tokens[left:right]),
                        "ref_window": " ".join(ref_tokens[max(0, ref_index - WINDOW):ref_index + WINDOW + 1]),
                    }, ensure_ascii=False) + "\n")
                    written += 1
    print(json.dumps({"pairs": written, "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
