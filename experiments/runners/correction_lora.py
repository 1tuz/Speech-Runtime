#!/usr/bin/env python3
"""Обучение LoRA-корректора Qwen3-0.6B на парах dev Veche-bench.

Разбиение по встречам, а не по репликам: проверочная встреча не встречается в
обучении ни одной репликой. Поэтому три встречи дают три фолда
(`--holdout 2-one-to-one` и т. д.) и отдельный прогон `--holdout none` — модель
на всём dev для замера задержки и памяти; её WER на dev — обучающий, не отчётный.

    python3 experiments/runners/correction_lora.py \
        --pairs experiments/corpus/local/correction/gigaam/pairs.jsonl \
        --out experiments/corpus/local/correction/gigaam-lora-fold-5-it-product \
        --holdout 5-it-product --epochs 3

Движок выбирается сам: Unsloth, если он импортируется и видит Apple Silicon,
иначе MLX (`mlx_lm.lora`). Оба — LoRA; выбор фиксируется в `run.json`, чтобы
числа не смешивались.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSTRUCTION = (
    "Исправь текст, распознанный с русской речи. Верни только исправленный текст "
    "без пояснений. Сохрани смысл и порядок слов; латинские названия технологий, "
    "команды и аббревиатуры пиши латиницей."
)


def load_pairs(path: str) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def prompt_for(hyp: str) -> str:
    return f"{INSTRUCTION}\nТекст: {hyp}\nИсправление:"


def write_completions(pairs: list[dict], path: str) -> None:
    """Формат `mlx_lm.lora`: prompt = инструкция + гипотеза, completion = референс.

    Пробел в начале completion обязателен: без него шаблон склеивает последний
    токен промпта с первым токеном ответа.
    """
    with open(path, "w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(json.dumps({
                "prompt": prompt_for(pair["hyp"]),
                "completion": " " + pair["ref"],
            }, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--holdout", default="none", help="встреча для проверки или none")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=16)
    parser.add_argument("--lr", default="5e-5")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--engine", default="auto", choices=["auto", "unsloth", "mlx"])
    args = parser.parse_args(argv)

    pairs = load_pairs(args.pairs)
    if args.holdout == "none":
        train, valid = pairs, pairs[:1]
    else:
        train = [p for p in pairs if p["meeting"] != args.holdout]
        valid = [p for p in pairs if p["meeting"] == args.holdout]
        if not valid:
            raise SystemExit(f"{args.holdout}: во встречах пар нет — {sorted({p['meeting'] for p in pairs})}")

    work = os.path.join(args.out, "data")
    os.makedirs(work, exist_ok=True)
    write_completions(train, os.path.join(work, "train.jsonl"))
    write_completions(valid, os.path.join(work, "valid.jsonl"))

    engine = args.engine
    if engine == "auto":
        engine = "mlx"
        try:
            import unsloth  # noqa: F401
            engine = "unsloth"
        except Exception:
            pass

    steps_per_epoch = max(1, len(train) // (args.batch_size * args.grad_accum))
    run = {
        "status": "experiment_not_benchmark",
        "engine": engine,
        "model": args.model,
        "holdout_meeting": args.holdout,
        "pairs_train": len(train),
        "pairs_valid": len(valid),
        "epochs": args.epochs,
        "rank": args.rank,
        "alpha": args.alpha,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "grad_accum": args.grad_accum,
        "max_seq_length": args.max_seq_length,
        "steps_per_epoch": steps_per_epoch,
        "instruction": INSTRUCTION,
    }

    if engine == "unsloth":
        # Unsloth на Apple Silicon работает через MLX-бэкенд (DEVICE_TYPE=="mlx").
        # Текстовая модель, LoRA, маска промпта: loss считается только по
        # completion, как и в mlx_lm-ветке ниже.
        from datasets import Dataset
        from unsloth import FastLanguageModel
        from unsloth_zoo.mlx.trainer import MLXTrainingConfig, MLXTrainer

        adapter_path = os.path.join(args.out, "adapter")
        os.makedirs(adapter_path, exist_ok=True)

        model, tokenizer = FastLanguageModel.from_pretrained(
            args.model,
            max_seq_length=args.max_seq_length,
            load_in_4bit=True,
        )
        model = FastLanguageModel.get_peft_model(
            model,
            r=args.rank,
            lora_alpha=args.alpha,
            lora_dropout=0.0,
            max_seq_length=args.max_seq_length,
        )

        def as_dataset(items: list[dict]) -> "Dataset":
            return Dataset.from_list([
                {"prompt": prompt_for(p["hyp"]), "completion": " " + p["ref"]}
                for p in items
            ])

        train_ds, valid_ds = as_dataset(train), as_dataset(valid)
        steps_per_epoch = max(1, len(train) // (args.batch_size * args.grad_accum))
        config = MLXTrainingConfig(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs,
            max_steps=-1,
            learning_rate=float(args.lr),
            lr_scheduler_type="linear",
            warmup_steps=min(5, steps_per_epoch),
            logging_steps=5,
            output_dir=adapter_path,
            report_to="none",
            save_steps=0,
            seed=0,
            max_seq_length=args.max_seq_length,
            dataset_text_field="text",
            train_on_completions=True,
            gradient_checkpointing=True,
        )
        trainer = MLXTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=train_ds,
            eval_dataset=valid_ds if args.holdout != "none" else None,
            args=config,
        )
        trainer.train()
        trainer.save_model(adapter_path)

        run["adapter"] = adapter_path
        run["train_exit"] = 0
        with open(os.path.join(args.out, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh, ensure_ascii=False, indent=2)
        print(json.dumps({k: v for k, v in run.items() if k != "instruction"},
                         ensure_ascii=False, indent=2))
        return 0

    config = {
        "model": args.model,
        "train": True,
        "data": os.path.abspath(work),
        "seed": 0,
        "num_layers": 16,
        "batch_size": args.batch_size,
        "iters": steps_per_epoch * args.epochs,
        "val_batches": 5,
        "learning_rate": float(args.lr),
        "steps_per_report": 10,
        "steps_per_eval": max(10, steps_per_epoch),
        "save_every": steps_per_epoch * args.epochs,
        "adapter_path": os.path.abspath(os.path.join(args.out, "adapter")),
        "max_seq_length": args.max_seq_length,
        "grad_checkpoint": True,
        "mask_prompt": True,
        "lora_parameters": {"rank": args.rank, "scale": args.alpha / args.rank, "dropout": 0.0},
    }
    config_path = os.path.join(args.out, "lora_config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)

    os.makedirs(config["adapter_path"], exist_ok=True)
    cmd = [sys.executable, "-m", "mlx_lm.lora", "-c", config_path]
    print(" ".join(cmd), file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    (open(os.path.join(args.out, "train.log"), "w", encoding="utf-8")
     .write(result.stdout + "\n--- stderr ---\n" + result.stderr))
    run["train_exit"] = result.returncode
    run["train_tail"] = (result.stdout + result.stderr).strip().splitlines()[-12:]
    with open(os.path.join(args.out, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
