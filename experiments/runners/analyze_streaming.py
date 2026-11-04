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


def words_from_pieces(segment: dict) -> list[dict]:
    """Склеивает sentencepiece-куски в слова.

    `token_timings` — куски (`▁Да`, `в`, `айте`), а не слова. Границу слова даёт
    кусок, начинающийся с `▁`. Отдельный кусок `▁` (маркер пробела без букв)
    тоже граница: следующий кусок открывает новое слово, иначе «начнем» и
    «статус» склеиваются в одно.
    """
    words: list[dict] = []
    for piece in segment.get("token_timings", []):
        text = piece["text"]
        starts_word = text.startswith("\u2581")
        cleaned = text.replace("\u2581", "").strip()
        if starts_word:
            words.append({"text": cleaned, "start": piece["start"], "end": piece["end"]})
        elif not words:
            words.append({"text": cleaned, "start": piece["start"], "end": piece["end"]})
        else:
            words[-1]["text"] += cleaned
            words[-1]["end"] = piece["end"]
    return [word for word in words if word["text"]]


def word_latencies(segment: dict) -> list[float]:
    """Задержка появления слова: первый partial с этим словом минус конец слова.

    Сопоставление последовательное: следующее слово ищется после предыдущего,
    чтобы частые слова не засчитывались по случайному раннему совпадению.
    """
    partials = [(p["at_s"], p["text"].lower()) for p in segment.get("partials", []) if p["text"]]
    out: list[float] = []
    cursors = [0] * len(partials)
    for word in words_from_pieces(segment):
        piece = word["text"].strip().lower()
        if len(piece) < 2:
            continue
        for index, (at, text) in enumerate(partials):
            position = text.find(piece, cursors[index])
            if position >= 0:
                cursors[index] = position + len(piece)
                out.append(at - word["end"])
                break
    return out


def revision_stats(segment: dict) -> dict:
    """Считает пересмотры уже показанного текста и момент подтверждения.

    Пересмотр — partial, где самое длинное общее начало с предыдущим partial
    стало короче: значит выданные слова были заменены. Это не то же самое, что
    `stableTokenCount`: здесь считается видимый клиенту текст.
    """
    partials = [p for p in segment.get("partials", []) if p["text"].strip()]
    revisions = 0
    previous: list[str] = []
    confirmed_at = None
    for partial in partials:
        words = partial["text"].split()
        common = 0
        for left, right in zip(previous, words):
            if left != right:
                break
            common += 1
        if common < len(previous) and previous:
            revisions += 1
        previous = words
        if partial.get("is_confirmed") and confirmed_at is None:
            confirmed_at = partial["at_s"]
    return {
        "revisions": revisions,
        "updates": len(partials),
        "first_confirmed_s": confirmed_at,
    }


def _edit_distance(ref: list[str], hyp: list[str]) -> tuple[int, int, int, int]:
    """Левенштейн по словам с восстановлением S/D/I."""
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    i, j = n, m
    sub = dele = ins = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            sub += 1
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return sub, dele, ins, dp[n][m]


def stream_vs_final(segment: dict) -> dict | None:
    """Расхождение видимого потока и финального текста (только оконный режим).

    Видимый текст — конкатенация текстов partial'ов: клиент показывает именно её.
    Финальный текст движок собирает из подтверждённого состояния, поэтому
    разница показывает, что теряется или дублируется на стыках окон.
    """
    if not is_window_mode(segment):
        return None
    visible = " ".join(p["text"].strip() for p in segment.get("partials", [])
                       if p["text"].strip())
    ref = segment.get("final", "").split()
    hyp = visible.split()
    if not ref:
        return None
    sub, dele, ins, errors = _edit_distance(ref, hyp)
    return {"errors": errors, "substitutions": sub, "deletions": dele,
            "insertions": ins, "words": len(ref), "visible_words": len(hyp)}


def is_window_mode(segment: dict) -> bool:
    """Движок отдаёт текст окном целиком, а не дописывает partial."""
    return not segment.get("token_timings") and any(
        partial.get("token_timings") for partial in segment.get("partials", [])
    )


def window_lags(segment: dict) -> list[float]:
    """Отставание текста от аудио: `at_s` минус конец последнего токена окна.

    Так ведёт себя `SlidingWindowAsrManager`: каждое обновление заменяет окно, а
    не продолжает предыдущее, поэтому задержку отдельного слова по
    последовательным partial не измерить — измеряется возраст выданного текста.
    """
    lags: list[float] = []
    for partial in segment.get("partials", []):
        timings = partial.get("token_timings") or []
        if not partial["text"].strip() or not timings:
            continue
        lags.append(partial["at_s"] - timings[-1]["end"])
    return lags


def _aggregate_stream_final(rows: list[dict]) -> dict | None:
    items = [r["stream_vs_final"] for r in rows if r.get("stream_vs_final")]
    if not items:
        return None
    errors = sum(i["errors"] for i in items)
    words = sum(i["words"] for i in items)
    return {
        "segments": len(items),
        "errors": errors,
        "words": words,
        "wer": errors / words if words else None,
        "substitutions": sum(i["substitutions"] for i in items),
        "deletions": sum(i["deletions"] for i in items),
        "insertions": sum(i["insertions"] for i in items),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    args = parser.parse_args(argv)

    data = json.load(open(args.json, encoding="utf-8"))
    segments = data.get("segments")
    if not isinstance(segments, list):
        raise SystemExit(
            "--json ожидает JSON прогона (`segments` — список сегментов), "
            "а не сводку этого же анализатора"
        )
    rows = []
    all_word = []
    all_lag = []
    for segment in segments:
        window = is_window_mode(segment)
        latencies = [] if window else word_latencies(segment)
        all_word.extend(latencies)
        lags = window_lags(segment) if window else []
        all_lag.extend(lags)
        first = next((p["at_s"] for p in segment.get("partials", []) if p["text"].strip()), None)
        rev = revision_stats(segment)
        stream_final = stream_vs_final(segment)
        rows.append({
            "segment_id": segment["segment_id"],
            "stream_vs_final": stream_final,
            "audio_s": segment["audio_s"],
            "rtf": segment["rtf"],
            "first_partial_s": first,
            "first_confirmed_s": rev["first_confirmed_s"],
            "revisions": None if window else rev["revisions"],
            "updates": rev["updates"],
            "final_tail_s": segment["finish_s"],
            "partial_count": segment.get("partial_count", 0),
            "word_latency_p50": percentile(latencies, 0.5),
            "word_latency_p95": percentile(latencies, 0.95),
            "words_measured": len(latencies),
            "text_lag_p50": percentile(lags, 0.5),
            "text_lag_p95": percentile(lags, 0.95),
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
        "text_lag_p50": percentile(all_lag, 0.5),
        "text_lag_p95": percentile(all_lag, 0.95),
        "first_confirmed_p50": percentile([r["first_confirmed_s"] for r in rows if r["first_confirmed_s"]], 0.5),
        "revisions_total": sum(r["revisions"] for r in rows if r["revisions"] is not None),
        "updates_total": sum(r["updates"] for r in rows),
        "words_measured": len(all_word),
        "stream_vs_final": _aggregate_stream_final(rows),
        "revisions_note": ("оконный режим не отдаёт пересмотры народу: каждый "
                           "partial несёт только текст текущего чанка"),
        "per_segment": rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
