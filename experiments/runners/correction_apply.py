#!/usr/bin/env python3
"""Применяет LoRA-корректор к гипотезам ASR и меряет задержку и память.

    python3 experiments/runners/correction_apply.py \
        --pairs experiments/corpus/local/correction/gigaam/pairs.jsonl \
        --adapter experiments/corpus/local/correction/gigaam-lora-fold-5-it-product/adapter \
        --out-tsv /tmp/gigaam-corrected.tsv \
        --out-json /tmp/gigaam-corrected.json

Генерация жадная (temperature 0) и последовательная: задержка на реплику —
это то, что получит клиент, а не пропускная способность батча. Пиковая память
берётся из счётчика MLX (`mx.get_peak_memory`), то есть только вычисления;
RSS процесса включает веса и Python-рантайм и пишется отдельно.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import time

import mlx.core as mx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--instruction-file", default=None)
    args = parser.parse_args(argv)

    sys_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rows = [json.loads(line) for line in open(args.pairs, encoding="utf-8")]
    if args.limit:
        rows = rows[:args.limit]

    instruction = (
        "Исправь текст, распознанный с русской речи. Верни только исправленный текст "
        "без пояснений. Сохрани смысл и порядок слов; латинские названия технологий, "
        "команды и аббревиатуры пиши латиницей."
    )
    if args.instruction_file:
        instruction = open(args.instruction_file, encoding="utf-8").read().strip()

    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    # mlx_lm >= 0.30 принимает сэмплер вместо `temp`; temp=0 -> argmax.
    greedy = make_sampler(temp=0.0)

    load_start = time.perf_counter()
    model, tokenizer = load(args.model, adapter_path=args.adapter)
    load_s = time.perf_counter() - load_start
    mx.reset_peak_memory()

    out_rows, timings = [], []
    inference_s = 0.0
    for row in rows:
        prompt = f"{instruction}\nТекст: {row['hyp']}\nИсправление:"
        budget = max(32, int(len(tokenizer.encode(row["ref"])) * 1.6) + 16)
        start = time.perf_counter()
        text = generate(model, tokenizer, prompt=prompt, max_tokens=budget,
                        verbose=False, sampler=greedy)
        elapsed = time.perf_counter() - start
        inference_s += elapsed
        text = " ".join(text.strip().split())
        out_rows.append(f"{row['segment_id']}\t{text}")
        timings.append({"segment_id": row["segment_id"], "s": round(elapsed, 4)})

    with open(args.out_tsv, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_rows) + "\n")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "status": "experiment_not_benchmark",
        "model": args.model,
        "adapter": args.adapter,
        "segments": len(rows),
        "load_s": round(load_s, 3),
        "inference_s": round(inference_s, 3),
        "latency_s": {
            "mean": round(inference_s / max(1, len(rows)), 4),
            "p50": round(sorted(t["s"] for t in timings)[len(timings) // 2], 4) if timings else None,
            "max": max((t["s"] for t in timings), default=None),
        },
        "mlx_peak_memory_bytes": mx.get_peak_memory(),
        "max_rss_bytes": usage.ru_maxrss,
        "timings": timings,
    }
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "timings"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
