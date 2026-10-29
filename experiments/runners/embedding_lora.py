#!/usr/bin/env python3
"""LoRA для Qwen3-Embedding-0.6B на парах «шумный контекст → термин».

Обучающие пары — только dev (половина `pairs-dev.jsonl`). Вторая половина
используется для проверки до и после адаптера; test Veche не участвует.

    python3 experiments/runners/embedding_lora.py \
        --pairs experiments/corpus/local/embedding/pairs-dev.jsonl \
        --base experiments/corpus/local/models/qwen3-embedding-0.6b-4bit-dwq \
        --out experiments/corpus/local/embedding/lora
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


def is_tune(pair: dict) -> bool:
    digest = hashlib.sha256(pair["segment_id"].encode()).hexdigest()
    return int(digest[:8], 16) % 2 == 0


def evaluate(model, tokenizer, pairs, term_texts, term_ids) -> dict:
    import mlx.core as mx
    from mlx_tune import FastEmbeddingModel

    FastEmbeddingModel.for_inference(model)
    queries = model.encode([p["context"] for p in pairs])
    docs = model.encode(term_texts)
    queries = queries / mx.linalg.norm(queries, axis=1, keepdims=True)
    docs = docs / mx.linalg.norm(docs, axis=1, keepdims=True)
    similarity = (queries @ docs.T).tolist()
    recall = {1: 0, 5: 0, 10: 0}
    reciprocal = 0.0
    for row, pair in zip(similarity, pairs):
        order = sorted(range(len(term_ids)), key=lambda index: -row[index])
        rank = order.index(term_ids.index(pair["term_id"])) + 1
        for k in recall:
            if rank <= k:
                recall[k] += 1
        reciprocal += 1.0 / rank
    total = max(len(pairs), 1)
    return {
        "recall_at_1": recall[1] / total,
        "recall_at_5": recall[5] / total,
        "recall_at_10": recall[10] / total,
        "mrr": reciprocal / total,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--glossary", default=os.path.join(REPO, "glossary", "registry.json"))
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--steps", type=int, default=60)
    args = parser.parse_args(argv)

    from mlx_tune import (
        EmbeddingDataCollator,
        EmbeddingSFTConfig,
        EmbeddingSFTTrainer,
        FastEmbeddingModel,
    )

    vocab = terms_mod.Vocabulary.from_path(args.terms)
    registry = json.load(open(args.glossary, encoding="utf-8")) if os.path.exists(args.glossary) else {"entries": []}
    definitions = {entry["canonical_term"].lower(): entry["ru_definition"][:300] for entry in registry["entries"]}

    term_texts = []
    for term in vocab.terms:
        forms = " / ".join(term.accept)
        definition = definitions.get(term.id.lower(), "")
        term_texts.append(f"{forms}. {definition}".strip())
    term_ids = [term.id for term in vocab.terms]

    pairs = [json.loads(line) for line in open(args.pairs, encoding="utf-8")]
    train = [p for p in pairs if is_tune(p)]
    evaluation = [p for p in pairs if not is_tune(p)]

    dataset = []
    for pair in train:
        definition = definitions.get(pair["term_id"].lower(), "")
        positive = f"{' / '.join(pair['term_forms'])}. {definition}".strip()
        dataset.append({"anchor": pair["context"], "positive": positive})

    model, tokenizer = FastEmbeddingModel.from_pretrained(
        model_name=args.base, max_seq_length=512, pooling_strategy="last_token")
    before = evaluate(model, tokenizer, evaluation, term_texts, term_ids)

    model = FastEmbeddingModel.get_peft_model(model, r=8, lora_alpha=16)
    collator = EmbeddingDataCollator(model=model, tokenizer=tokenizer,
                                     anchor_column="anchor", positive_column="positive",
                                     max_seq_length=512)
    config = EmbeddingSFTConfig(output_dir=args.out, per_device_train_batch_size=8,
                                learning_rate=2e-5, max_steps=args.steps, temperature=0.05,
                                loss_type="infonce", logging_steps=10, max_seq_length=512)
    trainer = EmbeddingSFTTrainer(model=model, tokenizer=tokenizer, data_collator=collator,
                                  train_dataset=dataset, args=config)
    result = trainer.train()

    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(os.path.join(args.out, "adapter"))
    after = evaluate(model, tokenizer, evaluation, term_texts, term_ids)

    # Повторная загрузка адаптера: без этого «улучшение» может быть эффектом
    # грязного состояния процесса.
    reloaded, tok2 = FastEmbeddingModel.from_pretrained(
        model_name=args.base, max_seq_length=512, pooling_strategy="last_token")
    reloaded = FastEmbeddingModel.get_peft_model(reloaded, r=8, lora_alpha=16)
    reloaded._apply_lora()
    reloaded.load_adapter(os.path.join(args.out, "adapter"))
    reload_after = evaluate(reloaded, tok2, evaluation, term_texts, term_ids)

    report = {
        "train_pairs": len(dataset),
        "eval_pairs": len(evaluation),
        "steps": args.steps,
        "train_loss": result.metrics.get("train_loss"),
        "before": before,
        "after": after,
        "after_reload": reload_after,
        "adapter": os.path.join(args.out, "adapter"),
        "adapter_sha256": hashlib.sha256(
            open(os.path.join(args.out, "adapter", "adapters.safetensors"), "rb").read()
        ).hexdigest() if os.path.exists(os.path.join(args.out, "adapter", "adapters.safetensors")) else None,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    open(os.path.join(args.out, "report.json"), "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
