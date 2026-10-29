#!/usr/bin/env python3
"""Короткий пилот LoRA для Qwen3-ASR-0.6B на реальных парах аудио-текст (dev).

Не претендует на обучение: проверяет, что формат входа, маска loss, русская
токенизация и языковой префикс совместимы, что LoRA реально меняет веса, что
адаптер сохраняется и перезагружается. Данные — только `dev` (три встречи
Veche-bench), `test` не участвует.

    python3 experiments/runners/asr_lora_smoke.py \
        --model .../qwen3-asr-0.6b-mlx-8bit \
        --dataset .../veche-prepared/simple --out .../asr-lora --steps 20
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

import soundfile  # noqa: E402


def load_dev_pairs(dataset: str, limit: int) -> list[dict]:
    rows = [json.loads(line) for line in open(os.path.join(dataset, "metadata.jsonl"), encoding="utf-8")]
    refs = {}
    for line in open(os.path.join(dataset, "manifest.tsv"), encoding="utf-8"):
        path, text = line.rstrip("\n").split("\t", 1)
        refs[os.path.splitext(os.path.basename(path))[0]] = (path, text)
    dev = [row for row in rows if row["split"] == "dev"]
    random.Random(0).shuffle(dev)
    pairs = []
    for row in dev[:limit]:
        rel, text = refs[row["segment_id"]]
        audio, rate = soundfile.read(os.path.join(dataset, rel), dtype="float32")
        pairs.append({"audio": {"array": audio, "sampling_rate": rate}, "text": text})
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--pairs", type=int, default=24)
    parser.add_argument("--language", default="ru")
    args = parser.parse_args(argv)

    from mlx_tune import FastSTTModel, STTDataCollator, STTSFTConfig, STTSFTTrainer

    dataset = load_dev_pairs(args.dataset, args.pairs)
    model, processor = FastSTTModel.from_pretrained(model_name=args.model, max_seq_length=448)
    model = FastSTTModel.get_peft_model(model, r=8, lora_alpha=16,
                                        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
                                        finetune_encoder=False, finetune_decoder=True)

    collator = STTDataCollator(model=model, processor=processor,
                               language=args.language, task="transcribe")
    config = STTSFTConfig(per_device_train_batch_size=1, gradient_accumulation_steps=2,
                          warmup_steps=2, max_steps=args.steps, learning_rate=1e-4,
                          logging_steps=1, output_dir=args.out)
    trainer = STTSFTTrainer(model=model, processor=processor, data_collator=collator,
                            train_dataset=dataset, args=config)
    stats = trainer.train()
    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(os.path.join(args.out, "adapter"))

    report = {
        "model": args.model,
        "pairs": len(dataset),
        "steps": args.steps,
        "train_loss": stats.metrics.get("train_loss"),
        "adapter": os.path.join(args.out, "adapter"),
        "adapter_files": sorted(os.listdir(os.path.join(args.out, "adapter"))),
        "lora_trainable_params": sum(p.size for _, p in model.model.named_parameters()
                                     if "lora" in _) if hasattr(model, "model") else None,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    open(os.path.join(args.out, "report.json"), "w", encoding="utf-8").write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
