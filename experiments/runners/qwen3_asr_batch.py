#!/usr/bin/env python3
"""Батч-прогон Qwen3-ASR (MLX) на списке WAV.

Модель по умолчанию `Qwen/Qwen3-ASR-0.6B` в 8-bit сборке moona3k; сессия
создаётся один раз, чтобы измерять инференс, а не загрузку. Контекст
(`--context`) — тот же механизм, что официальный `context` Qwen3-ASR.

    python3 experiments/runners/qwen3_asr_batch.py \
        --model mlx-community/Qwen3-ASR-0.6B-8bit --files list.txt \
        --out-tsv hyp.tsv --out-json run.json [--context "kubectl Helm"]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave


def duration(path: str) -> float:
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-0.6B")
    parser.add_argument("--files", required=True)
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--context", default=None)
    parser.add_argument("--language", default="Russian")
    args = parser.parse_args(argv)

    from mlx_qwen3_asr import Session

    files = [line.strip() for line in open(args.files, encoding="utf-8") if line.strip()]
    load_start = time.perf_counter()
    session = Session(model=args.model)
    load_s = time.perf_counter() - load_start

    rows, timings = [], []
    audio_s = inference_s = 0.0
    for path in files:
        start = time.perf_counter()
        result = session.transcribe(path, context=args.context, language=args.language)
        seconds = time.perf_counter() - start
        seg_id = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        rows.append(f"{seg_id}\t{' '.join(result.text.split())}")
        length = duration(path)
        audio_s += length
        inference_s += seconds
        timings.append({"segment_id": seg_id, "inference_s": seconds, "audio_s": length,
                        "language": getattr(result, "language", None)})
        print(f"done {seg_id}", file=sys.stderr)

    with open(args.out_tsv, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump({
            "status": "unverified_hypotheses_not_benchmark",
            "model": args.model,
            "engine": "mlx-qwen3-asr",
            "context": args.context,
            "language": args.language,
            "load_s": load_s,
            "audio_s": audio_s,
            "inference_s": inference_s,
            "rtf": inference_s / audio_s if audio_s else None,
            "segments": timings,
        }, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
