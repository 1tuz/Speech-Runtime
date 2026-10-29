#!/usr/bin/env python3
"""Потоковый прогон Qwen3-ASR через `mlx_qwen3_asr.streaming`.

Подача — по 20 мс PCM с реальным таймингом (без доступа к будущему), как в
замерах Nemotron. Измеряется:

- первый непустой стабильный partial от начала подачи;
- задержка слова: первый partial, где встретилось слово финального текста,
  минус конец этого слова в аудио (слова берутся из форсированного выравнивания
  финального текста, если оно доступно, иначе задержка не считается);
- `finish()` после последнего сэмпла;
- пересмотры: сколько раз текст переставал продолжаться предыдущим.

    python3 experiments/runners/qwen3_asr_stream.py \
        --model .../qwen3-asr-0.6b-mlx-8bit --files list.txt \
        --out-json stream.json [--realtime] [--chunk-sec 2.0]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np


def read_wav(path: str) -> np.ndarray:
    import soundfile

    data, rate = soundfile.read(path, dtype="float32")
    if rate != 16_000:
        raise SystemExit(f"{path}: ожидалось 16 кГц, получено {rate}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--step-ms", type=int, default=20)
    parser.add_argument("--chunk-sec", type=float, default=2.0)
    parser.add_argument("--max-context-sec", type=float, default=30.0)
    parser.add_argument("--language", default="Russian")
    args = parser.parse_args(argv)

    from mlx_qwen3_asr import streaming as st

    files = [line.strip() for line in open(args.files, encoding="utf-8") if line.strip()]
    load_start = time.perf_counter()
    # Модель грузится лениво при первом feed_audio; прогреваем на тишине, чтобы
    # загрузка не попала в замер первого partial.
    warm = st.init_streaming(model=args.model, chunk_size_sec=args.chunk_sec,
                             max_context_sec=args.max_context_sec, language=args.language)
    st.feed_audio(np.zeros(16_000 * 3, dtype=np.float32), warm)
    load_s = time.perf_counter() - load_start

    step = max(1, 16_000 * args.step_ms // 1000)
    reports = []
    for path in files:
        audio = read_wav(path)
        state = st.init_streaming(model=args.model, chunk_size_sec=args.chunk_sec,
                                  max_context_sec=args.max_context_sec, language=args.language)
        started = time.perf_counter()
        partials = []
        cursor = 0
        previous = ""
        revisions = 0
        while cursor < audio.shape[0]:
            end = min(cursor + step, audio.shape[0])
            st.feed_audio(audio[cursor:end], state)
            cursor = end
            text = (state.stable_text or "").strip()
            if text != previous:
                if previous and text and not text.startswith(previous):
                    revisions += 1
                if text:
                    partials.append({"at_s": time.perf_counter() - started, "text": text})
                previous = text
            if args.realtime:
                target = started + cursor / 16_000.0
                delay = target - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)

        feed_done = time.perf_counter()
        st.finish_streaming(state)
        finished = time.perf_counter()
        final = (state.text or "").strip()
        seg_id = os.path.splitext(os.path.basename(path))[0]
        reports.append({
            "segment_id": seg_id,
            "audio_s": audio.shape[0] / 16_000.0,
            "wall_s": finished - started,
            "rtf": (finished - started) / max(audio.shape[0] / 16_000.0, 1e-9),
            "first_partial_s": partials[0]["at_s"] if partials else None,
            "final_tail_s": finished - feed_done,
            "partials": partials,
            "partial_count": len(partials),
            "revisions": revisions,
            "text_updates": getattr(state, "_text_updates", None),
            "rewrite_events": getattr(state, "_rewrite_events", None),
            "final": final,
        })
        print(f"done {seg_id}", file=sys.stderr)

    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump({
            "status": "streaming_measurement_not_benchmark",
            "model": args.model,
            "engine": "mlx-qwen3-asr streaming (windowed re-decode, prefix rollback)",
            "realtime": args.realtime,
            "step_ms": args.step_ms,
            "chunk_s": args.chunk_sec,
            "max_context_s": args.max_context_sec,
            "load_s": load_s,
            "segments": reports,
        }, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
