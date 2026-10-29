#!/usr/bin/env python3
"""Батч-прогон GigaAM на списке WAV: TSV гипотез и JSON таймингов.

Модель берётся из пакета `gigaam` (0.2.0), веса скачиваются в ~/.cache/gigaam.
CPU-инференс (torch), без выбора потока — сравнение должно быть одинаковым
для всех прогонов на этой машине.

    python3 experiments/runners/gigaam_batch.py \
        --model v3_e2e_rnnt --files list.txt \
        --out-tsv hyp.tsv --out-json run.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave


def duration(path: str) -> float:
    """Длительность через soundfile: FLEURS публикует float32 WAV, который
    стандартный модуль wave не читает."""
    import soundfile

    return float(soundfile.info(path).duration)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="v3_e2e_rnnt")
    parser.add_argument("--files", required=True)
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)

    import gigaam
    import torchaudio

    files = [line.strip() for line in open(args.files, encoding="utf-8") if line.strip()]
    load_start = time.perf_counter()
    model = gigaam.load_model(args.model, fp16_encoder=False, device="cpu")
    load_s = time.perf_counter() - load_start

    rows, timings = [], []
    audio_s = inference_s = 0.0
    for path in files:
        start = time.perf_counter()
        length = duration(path)
        if length <= 24.0:
            text = model.transcribe(path).text
        else:
            # Пакет не принимает файлы > 25 с. Режем на окна по 20 с и склеиваем
            # текст; это отдельная обработка, а не «transcribe на том же входе».
            waveform, sample_rate = torchaudio.load(path)
            window = 20 * sample_rate
            parts = []
            for offset in range(0, waveform.shape[1], window):
                chunk_path = f"/tmp/gigaam-chunk.wav"
                import torchaudio as ta
                ta.save(chunk_path, waveform[:, offset:offset + window], sample_rate)
                parts.append(model.transcribe(chunk_path).text)
            text = " ".join(parts)
        seconds = time.perf_counter() - start
        seg_id = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        rows.append(f"{seg_id}\t{' '.join(text.split())}")
        audio_s += length
        inference_s += seconds
        timings.append({"segment_id": seg_id, "inference_s": seconds, "audio_s": length})
        print(f"done {seg_id}", file=sys.stderr)

    with open(args.out_tsv, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    with open(args.out_json, "w", encoding="utf-8") as fh:
        json.dump({
            "status": "unverified_hypotheses_not_benchmark",
            "model": args.model,
            "engine": "gigaam 0.2.0 + torch CPU fp32",
            "load_s": load_s,
            "audio_s": audio_s,
            "inference_s": inference_s,
            "rtf": inference_s / audio_s if audio_s else None,
            "segments": timings,
        }, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
