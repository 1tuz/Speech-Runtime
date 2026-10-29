#!/usr/bin/env python3
"""Замена терминов по эмбеддингам с отказом от замены при неуверенности.

В отличие от `correction_eval.py` (символьное расстояние по всему словарю),
здесь кандидат выбирается по смыслу: эмбеддинг реплики сравнивается с
эмбеддингами терминов, а замена делается только для токена, который

1) не является уже точной формой словаря,
2) встречается в позиции, где эталон содержит термин (проверяется только при
   оценке, правило этого не видит),
3) имеет косинус выше порога и отрыв от второго кандидата больше margin.

Порог замораживается на dev и применяется к test без изменений.

    python3 experiments/runners/correction_eval_embed.py \
        --dataset .../simple --split dev --hypotheses .../qwen-dev-empty.tsv \
        --model .../qwen3-embedding-0.6b-4bit-dwq --threshold 0.5
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


def build_term_texts(vocab, glossary_path: str) -> list[str]:
    definitions = {}
    if os.path.exists(glossary_path):
        registry = json.load(open(glossary_path, encoding="utf-8"))
        definitions = {e["canonical_term"].lower(): e["ru_definition"][:300] for e in registry["entries"]}
    texts = []
    for term in vocab.terms:
        definition = definitions.get(term.id.lower(), "")
        texts.append(f"{' / '.join(term.accept)}. {definition}".strip())
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--hypotheses", required=True)
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--glossary", default=os.path.join(REPO, "glossary", "registry.json"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--margin", type=float, default=0.05)
    parser.add_argument("--min-length", type=int, default=4)
    parser.add_argument("--local-similarity", type=float, default=0.5,
                        help="минимальное строковое сходство токена с формой: "
                             "эмбеддинг выбирает термин, строковое сходство локализует токен")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    import mlx.core as mx
    import mlx_embeddings

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

    model, tokenizer = mlx_embeddings.load(args.model)
    term_texts = build_term_texts(vocab, args.glossary)
    term_ids = [term.id for term in vocab.terms]
    term_vectors = mlx_embeddings.generate(model, tokenizer, term_texts).text_embeds
    term_vectors = term_vectors / mx.linalg.norm(term_vectors, axis=1, keepdims=True)

    exact_forms = set()
    for term in vocab.terms:
        for form in list(term.accept) + list(term.accept_translit):
            exact_forms.add(form.lower())

    segments = [hyps[sid] for sid in selected]
    vectors = mlx_embeddings.generate(model, tokenizer, segments).text_embeds
    vectors = vectors / mx.linalg.norm(vectors, axis=1, keepdims=True)
    similarity = (vectors @ term_vectors.T).tolist()

    fixed = false_replacements = wrong_replacements = corrections = words = 0
    wer_before = wer_after = ref_units = 0
    examples = []
    for row, seg_id in zip(similarity, selected):
        ref_tokens = norm.tokens(refs[seg_id])
        hyp_tokens = norm.tokens(hyps[seg_id])
        order = sorted(range(len(term_ids)), key=lambda index: -row[index])
        top, second = order[0], order[1]
        best_score, second_score = row[top], row[second]
        corrected = list(hyp_tokens)
        if best_score >= args.threshold and (best_score - second_score) >= args.margin:
            target = vocab.by_id[term_ids[top]]
            form = target.accept[0].lower()
            # Эмбеддинг даёт кандидата, локализация — строковое сходство.
            # Заменяется не более одного токена: тот, что ближе всего к форме.
            best_index, best_similarity = None, 0.0
            for index, token in enumerate(hyp_tokens):
                if len(token) < args.min_length or not token.isalpha():
                    continue
                if token in exact_forms or token == form:
                    continue
                similarity_score = 1.0 - levenshtein(token, form) / max(len(token), len(form), 1)
                if similarity_score > best_similarity:
                    best_index, best_similarity = index, similarity_score
            if best_index is not None and best_similarity >= args.local_similarity:
                corrections += 1
                if len(examples) < 12:
                    examples.append({"segment_id": seg_id, "from": hyp_tokens[best_index],
                                     "to": target.accept[0], "score": round(best_score, 3),
                                     "margin": round(best_score - second_score, 3),
                                     "local_similarity": round(best_similarity, 3)})
                corrected[best_index] = form

        changed = {i for i, (a, b) in enumerate(zip(hyp_tokens, corrected)) if a != b}
        ops, _ = align.align(ref_tokens, corrected)
        for op, ref_index, hyp_index in ops:
            if op != align.SUB or hyp_index not in changed:
                continue
            original = hyp_tokens[hyp_index]
            new = corrected[hyp_index]
            ref_token = ref_tokens[ref_index]
            if original == ref_token:
                false_replacements += 1
            elif new == ref_token:
                fixed += 1
            else:
                wrong_replacements += 1
        before_ops, _ = align.align(ref_tokens, hyp_tokens)
        after_ops, _ = align.align(ref_tokens, corrected)
        b_sub, b_del, b_ins = align.edit_counts(before_ops)
        a_sub, a_del, a_ins = align.edit_counts(after_ops)
        wer_before += b_sub + b_del + b_ins
        wer_after += a_sub + a_del + a_ins
        ref_units += len(ref_tokens)
        words += len(corrected)

    report = {
        "split": args.split,
        "threshold": args.threshold,
        "margin": args.margin,
        "local_similarity": args.local_similarity,
        "model": os.path.basename(args.model.rstrip("/")),
        "segments": len(selected),
        "words": words,
        "corrections": corrections,
        "fixed_errors": fixed,
        "false_replacements": false_replacements,
        "wrong_replacements": wrong_replacements,
        "false_replacements_per_1000_words": false_replacements / max(words, 1) * 1000,
        "wer_before": wer_before / max(ref_units, 1),
        "wer_after": wer_after / max(ref_units, 1),
        "examples": examples,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
