#!/usr/bin/env python3
"""Считает задержки по JSON потокового прогона `fluid-asr stream-nemotron`.

Что измеряется (и чего не измеряется):

- `first_partial_s` — от начала подачи до первого непустого partial. Включает
  собственную задержку тира (560/1120/2240 мс) и время вычислений.
- `word_latency_s` — для каждого токена финального текста с известным временем
  конца в аудио: момент первого partial, где этот токен встретился, минус конец
  токена в аудио. Это задержка появления слова, а не задержка «правильного»
  слова: при пересмотрах partial мог содержать другой текст.
- `final_tail_s` — время `finish()` после последнего сэмпла.
- `rtf` — wall / audio (при подаче по часам он ≈ 1 по построению).

    python3 experiments/runners/analyze_streaming.py --json run.json
"""

from __future__ import annotations

import argparse
import json
import statistics


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def token_latencies(segment: dict) -> list[float]:
    """Последовательное сопоставление: каждый следующий токен ищется после
    предыдущего. Наивный substring-поиск засчитывал частые слова («и», «в»)
    по первому partial, где они случайно встретились."""
    partials = [(p["at_s"], p["text"].lower()) for p in segment.get("partials", []) if p["text"]]
    out: list[float] = []
    cursor = {id(text): 0 for _, text in partials}
    for token in segment.get("token_timings", []):
        piece = token["text"].strip().lower()
        if len(piece) < 2:
            continue
        for at, text in partials:
            position = text.find(piece, cursor[id(text)])
            if position >= 0:
                cursor[id(text)] = position + len(piece)
                out.append(at - token["end"])
                break
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    args = parser.parse_args(argv)

    data = json.load(open(args.json, encoding="utf-8"))
    rows = []
    all_word = []
    for segment in data["segments"]:
        latencies = token_latencies(segment)
        all_word.extend(latencies)
        first = next((p["at_s"] for p in segment.get("partials", []) if p["text"].strip()), None)
        rows.append({
            "segment_id": segment["segment_id"],
            "audio_s": segment["audio_s"],
            "rtf": segment["rtf"],
            "first_partial_s": first,
            "final_tail_s": segment["finish_s"],
            "partial_count": segment.get("partial_count", 0),
            "word_latency_p50": percentile(latencies, 0.5),
            "word_latency_p95": percentile(latencies, 0.95),
            "words_measured": len(latencies),
        })

    summary = {
        "realtime": data.get("realtime"),
        "step_ms": data.get("step_ms"),
        "model_dir": data["model_dir"],
        "load_s": data["load_s"],
        "segments": len(rows),
        "first_partial_p50": percentile([r["first_partial_s"] for r in rows if r["first_partial_s"]], 0.5),
        "first_partial_p95": percentile([r["first_partial_s"] for r in rows if r["first_partial_s"]], 0.95),
        "final_tail_p50": percentile([r["final_tail_s"] for r in rows], 0.5),
        "final_tail_p95": percentile([r["final_tail_s"] for r in rows], 0.95),
        "word_latency_p50": percentile(all_word, 0.5),
        "word_latency_p95": percentile(all_word, 0.95),
        "word_latency_mean": statistics.fmean(all_word) if all_word else None,
        "words_measured": len(all_word),
        "per_segment": rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
