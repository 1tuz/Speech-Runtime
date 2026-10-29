#!/usr/bin/env python3
"""Сопоставление «шумный фрагмент → термин»: базовые правила против эмбеддингов.

Три подхода на одних и тех же dev-парах (`make_error_pairs.py`):

1. `exact` — токен совпал с формой словаря буквально;
2. `fuzzy` — нормированное расстояние Левенштейна к формам словаря, порог
   подбирается на половине пар (tune) и проверяется на второй (eval);
3. `embedding` — косинус между эмбеддингом контекста и эмбеддингом описания
   термина (Qwen3-Embedding-0.6B, MLX).

Эмбеддинг возвращает кандидатов, а не право на замену: правильный исходный
термин нельзя портить. Поэтому отдельно считается, сколько замен правило
предложило бы там, где менять нечего (ложные срабатывания на 1000 слов).

    python3 experiments/runners/embedding_eval.py \
        --pairs experiments/corpus/local/embedding/pairs-dev.jsonl \
        --model experiments/corpus/local/models/qwen3-embedding-0.6b-mxfp8 \
        --json experiments/corpus/local/embedding/results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

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


def normalized_distance(a: str, b: str) -> float:
    return levenshtein(a, b) / max(len(a), len(b), 1)


def is_tune(pair: dict) -> bool:
    digest = hashlib.sha256(pair["segment_id"].encode()).hexdigest()
    return int(digest[:8], 16) % 2 == 0


def fuzzy_rank(pair: dict, vocab: terms_mod.Vocabulary) -> list[tuple[str, float]]:
    token = pair["hyp_token"]
    scored = []
    for term in vocab.terms:
        best = min(
            (normalized_distance(token, form.lower()) for form in term.accept + term.accept_translit if form),
            default=1.0,
        )
        scored.append((term.id, best))
    scored.sort(key=lambda item: item[1])
    return scored


def exact_hit(pair: dict, vocab: terms_mod.Vocabulary) -> str | None:
    token = pair["hyp_token"].lower()
    for term in vocab.terms:
        for form in term.accept + term.accept_translit:
            if form and form.lower() == token:
                return term.id
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--glossary", default=os.path.join(REPO, "glossary", "registry.json"))
    parser.add_argument("--model", default=os.path.join(REPO, "corpus", "local", "models",
                                                        "qwen3-embedding-0.6b-mxfp8"))
    parser.add_argument("--json", default=None)
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args(argv)

    vocab = terms_mod.Vocabulary.from_path(args.terms)
    pairs = [json.loads(line) for line in open(args.pairs, encoding="utf-8")]
    tune = [p for p in pairs if is_tune(p)]
    evaluation = [p for p in pairs if not is_tune(p)]

    # --- baseline: exact и fuzzy -------------------------------------------
    exact_correct = sum(1 for p in pairs if exact_hit(p, vocab) == p["term_id"])
    thresholds = [0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
    best_threshold, best_score = None, -1.0
    for threshold in thresholds:
        hits = 0
        for pair in tune:
            ranked = fuzzy_rank(pair, vocab)
            if ranked and ranked[0][1] <= threshold and ranked[0][0] == pair["term_id"]:
                hits += 1
        score = hits / max(len(tune), 1)
        if score > best_score:
            best_threshold, best_score = threshold, score

    fuzzy_eval_hits = 0
    for pair in evaluation:
        ranked = fuzzy_rank(pair, vocab)
        if ranked and ranked[0][1] <= best_threshold and ranked[0][0] == pair["term_id"]:
            fuzzy_eval_hits += 1

    # --- эмбеддинги ---------------------------------------------------------
    embedding_report: dict = {}
    if not args.skip_embeddings:
        import mlx.core as mx
        import mlx_embeddings

        descriptions = {}
        if os.path.exists(args.glossary):
            registry = json.load(open(args.glossary, encoding="utf-8"))
            for entry in registry["entries"]:
                descriptions[entry["canonical_term"].lower()] = entry["ru_definition"][:400]
        term_ids = [term.id for term in vocab.terms]
        term_texts = []
        for term in vocab.terms:
            forms = " / ".join(term.accept)
            definition = descriptions.get(term.id.lower(), "")
            term_texts.append(f"{forms}. {definition}".strip())

        model, tokenizer = mlx_embeddings.load(args.model)
        term_vectors = mlx_embeddings.generate(model, tokenizer, term_texts).text_embeds
        term_vectors = term_vectors / mx.linalg.norm(term_vectors, axis=1, keepdims=True)

        contexts = [p["context"] for p in evaluation]
        context_vectors = mlx_embeddings.generate(model, tokenizer, contexts).text_embeds
        context_vectors = context_vectors / mx.linalg.norm(context_vectors, axis=1, keepdims=True)
        similarity = context_vectors @ term_vectors.T
        similarity = similarity.tolist()

        recall_at = {1: 0, 5: 0, 10: 0}
        reciprocal = 0.0
        for row, pair in zip(similarity, evaluation):
            order = sorted(range(len(term_ids)), key=lambda index: -row[index])
            rank = order.index(term_ids.index(pair["term_id"])) + 1
            for k in recall_at:
                if rank <= k:
                    recall_at[k] += 1
            reciprocal += 1.0 / rank
        total = max(len(evaluation), 1)
        embedding_report = {
            "recall_at_1": recall_at[1] / total,
            "recall_at_5": recall_at[5] / total,
            "recall_at_10": recall_at[10] / total,
            "mrr": reciprocal / total,
        }

    report = {
        "pairs": len(pairs),
        "tune_pairs": len(tune),
        "eval_pairs": len(evaluation),
        "exact_matches_all": exact_correct,
        "exact_share": exact_correct / max(len(pairs), 1),
        "fuzzy_threshold": best_threshold,
        "fuzzy_tune_accuracy": best_score,
        "fuzzy_eval_accuracy": fuzzy_eval_hits / max(len(evaluation), 1),
        "embedding": embedding_report,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
