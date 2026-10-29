#!/usr/bin/env python3
"""Готовит FLEURS `ru_ru` test к скореру: манифест и метаданные без аудио-копий.

FLEURS — независимый общелингвистический контроль: он показывает, деградирует ли
обычный русский после адаптации к IT. Это не IT-корпус и не доказательство
отсутствия этих данных в предобучении. Test не используется для обучения.

    python3 experiments/corpus/prepare_fleurs.py \
        --tsv experiments/corpus/local/extra/fleurs/ru_ru-test.tsv \
        --audio experiments/corpus/local/extra/fleurs/audio/test \
        --out experiments/corpus/local/fleurs-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import wave

SOURCE = "https://huggingface.co/datasets/google/fleurs"
REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    manifest, metadata = [], []
    missing = 0
    for line in open(args.tsv, encoding="utf-8"):
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 6:
            continue
        speaker, filename, text = fields[0], fields[1], fields[2]
        path = os.path.join(args.audio, filename)
        if not os.path.isfile(path):
            missing += 1
            continue
        with wave.open(path, "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        seg_id = os.path.splitext(filename)[0]
        # FLEURS-аудио уже лежит рядом с манифестом; путь относительный, чтобы
        # манифест не зависел от абсолютного расположения корпуса.
        manifest.append(f"audio/test/{filename}\t{text}")
        metadata.append({
            "segment_id": seg_id,
            "recording_id": f"fleurs-{speaker}",
            "register": "R3",
            "speaker_id": f"fleurs-{speaker}",
            "mic_class": "phone",
            "noise_class": "clean",
            "duration_s": round(duration, 3),
            "split": "test",
            "source": f"{SOURCE} rev {REVISION[:8]} / ru_ru / test / {filename}",
            "license": "CC BY 4.0",
            "rights_evidence": f"{SOURCE}/blob/{REVISION}/README.md",
            "rights_status": "card_checked_no_human_signoff",
            "rights_reviewer": "pending",
            "transcript_status": "official_transcript_not_listened",
            "transcript_reviewer": "pending",
            "transcript_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "terms": [],
            "notes": "register/mic_class — заглушки скрипта; FLEURS не IT-корпус",
        })

    with open(os.path.join(args.out, "manifest.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(manifest) + "\n")
    with open(os.path.join(args.out, "metadata.jsonl"), "w", encoding="utf-8") as fh:
        for row in metadata:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"segments": len(manifest), "missing_audio": missing,
                      "seconds": round(sum(r["duration_s"] for r in metadata), 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
