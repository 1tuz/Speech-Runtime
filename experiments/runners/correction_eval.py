#!/usr/bin/env python3
"""Оценка правил замены терминов: точность исправлений и ложные замены.

Правило получает только текст гипотезы (никаких референсов) и словарь; порог
заморожен по dev до прогона на test. Затем выравниванием проверяется:

- `fixed` — токен был ошибкой (в референсе стоит термин) и заменён правильно;
- `false_replacement` — токен уже совпадал с референсом, но правило его
  переписало (порча правильного текста);
- изменение WER и term-exact до/после.

    python3 experiments/runners/correction_eval.py \
        --dataset .../simple --split test \
        --hypotheses .../qwen3-asr-fp16-hypotheses.tsv --threshold 0.4
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
import terms as terms_mod  # noqa: E402


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def best_terms(token: str, vocab: terms_mod.Vocabulary) -> list[tuple[str, float, str]]:
    scored = []
    for term in vocab.terms:
        best, best_form = 1.0, ""
        for form in list(term.accept) + list(term.accept_translit):
            candidate = form.lower()
            if not candidate:
                continue
            distance = levenshtein(token, candidate) / max(len(token), len(candidate), 1)
            if distance < best:
                best, best_form = distance, term.accept[0]
        if best_form:
            scored.append((term.id, best, best_form))
    scored.sort(key=lambda item: item[1])
    return scored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--hypotheses", required=True)
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--margin", type=float, default=0.1)
    parser.add_argument("--min-length", type=int, default=4)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    vocab = terms_mod.Vocabulary.from_path(args.terms)
    refs = {}
    for line in open(os.path.join(args.dataset, "manifest.tsv"), encoding="utf-8"):
        path, text = line.rstrip("\n").split("\t", 1)
        refs[os.path.splitext(os.path.basename(path))[0]] = text
    meta = {}
    for line in open(os.path.join(args.dataset, "metadata.jsonl"), encoding="utf-8"):
        row = json.loads(line)
        meta[row["segment_id"]] = row
    hyps = {}
    for line in open(args.hypotheses, encoding="utf-8"):
        seg_id, text = line.rstrip("\n").split("\t", 1)
        hyps[seg_id] = text

    selected = [sid for sid in refs if meta[sid]["split"] == args.split]
    words = 0
    fixed = false_replacements = wrong_replacements = corrections = 0
    guessed = 0
    wer_before = wer_after = 0
    ref_units = 0
    replaced_examples = []

    for seg_id in selected:
        ref_tokens = norm.tokens(refs[seg_id])
        hyp_tokens = norm.tokens(hyps[seg_id])
        # Правило видит только гипотезу.
        corrected = list(hyp_tokens)
        for index, token in enumerate(hyp_tokens):
            if len(token) < args.min_length or not token.isalpha():
                continue
            candidates = best_terms(token, vocab)
            if not candidates:
                continue
            term_id, distance, form = candidates[0]
            runner_up = candidates[1][1] if len(candidates) > 1 else 1.0
            if distance <= args.threshold and (runner_up - distance) >= args.margin:
                guessed += 1
                if form.lower() != token:
                    corrected[index] = form.lower()
                    corrections += 1
                    if len(replaced_examples) < 15:
                        replaced_examples.append({"segment_id": seg_id, "from": token, "to": form})

        # Оцениваем только фактически изменённые токены и только по исходному
        # выравниванию: после замены выравнивание может сдвинуться, и индекс
        # гипотезы перестаёт соответствовать тому же слову.
        changed = [i for i, (a, b) in enumerate(zip(hyp_tokens, corrected)) if a != b]
        if changed:
            _, ref_to_hyp = align.align(ref_tokens, hyp_tokens)
            hyp_to_ref = {h: r for r, h in ref_to_hyp.items()}
            for index in changed:
                ref_index = hyp_to_ref.get(index)
                if ref_index is None:
                    wrong_replacements += 1
                    continue
                original = hyp_tokens[index]
                new = corrected[index]
                ref_token = ref_tokens[ref_index]
                if original == ref_token:
                    false_replacements += 1
                elif new == ref_token:
                    fixed += 1
                else:
                    wrong_replacements += 1
        before, _ = align.align(ref_tokens, hyp_tokens)
        after, _ = align.align(ref_tokens, corrected)
        _, before_del, before_ins = align.edit_counts(before)
        before_sub, _, _ = align.edit_counts(before)
        after_sub, after_del, after_ins = align.edit_counts(after)
        wer_before += before_sub + before_del + before_ins
        wer_after += after_sub + after_del + after_ins
        ref_units += len(ref_tokens)
        words += len(corrected)

    report = {
        "split": args.split,
        "threshold": args.threshold,
        "margin": args.margin,
        "min_length": args.min_length,
        "segments": len(selected),
        "words": words,
        "corrections": corrections,
        "fixed_errors": fixed,
        "false_replacements": false_replacements,
        "wrong_replacements": wrong_replacements,
        "false_replacements_per_1000_words": false_replacements / max(words, 1) * 1000,
        "corrections_per_1000_words": corrections / max(words, 1) * 1000,
        "wer_before": wer_before / max(ref_units, 1),
        "wer_after": wer_after / max(ref_units, 1),
        "examples": replaced_examples,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
