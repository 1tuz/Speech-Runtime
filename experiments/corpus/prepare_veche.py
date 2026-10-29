#!/usr/bin/env python3
"""Готовит Veche-bench к скореру: режет реплики, пишет манифест, метаданные и RTTM.

Veche-bench — человеческая начитка сценариев, собранная в 9 совещаний и
опубликованная в четырёх вариантах сложности (`data_raw`, `data_simple`,
`data_medium`, `data_hard`). Варианты — не независимые корпуса: все четыре
версии одной встречи принадлежат одной группе при разбиении и анализе.

Что делает скрипт:
- читает `<variant>/<meeting>/<meeting>.csv` (реплики с таймингами и текстом);
- режет `<meeting>.wav` по границам реплик в `audio/<meeting>/<id>.wav`;
- пишет `manifest.tsv` (путь<TAB>референс), `metadata.jsonl` и `rttm/<variant>.rttm`.

Референс — текст реплики из CSV как есть. Это сценарный текст, а не результат
прослушивания: скрипт не присваивает `transcript_status=verified` и
`rights_status=verified`, поэтому preflight бенчмарка на таком наборе
сознательно не пройдёт. Скоринг запускается в режиме разработки.

Использование:
    python3 experiments/corpus/prepare_veche.py \
        --source experiments/corpus/local/veche-bench \
        --variant data_simple \
        --out experiments/corpus/local/veche-prepared/simple \
        --terms experiments/asr_eval/terms.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import wave

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "asr_eval"))

import norm  # noqa: E402
import terms as terms_mod  # noqa: E402

SOURCE_URL = "https://huggingface.co/datasets/artem-tish/veche-bench"
REVISION = "a0ab08d6887b64995d076b475a87310953a51138"
LICENSE = "CC BY 4.0"

# Разбиение по встречам, а не по вариантам сложности: варианты одной встречи —
# одна группа. dev оставлен для отладки пайплайна, настройка на нём запрещена.
DEV_MEETINGS = ("2-one-to-one", "5-it-product", "7-bi-platform")

NOISE = {"data_raw": "clean", "data_simple": "clean", "data_medium": "overlap", "data_hard": "noise"}


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_meeting_wav(path: str) -> tuple[wave._wave_params, bytes]:
    with wave.open(path, "rb") as wav:
        params = wav.getparams()
        if params.nchannels != 1 or params.sampwidth != 2:
            raise SystemExit(f"{path}: ожидался mono PCM s16, получено {params}")
        return params, wav.readframes(params.nframes)


def slice_frames(frames: bytes, framerate: int, start: float, end: float) -> bytes:
    first = max(0, int(round(start * framerate)))
    last = max(first, int(round(end * framerate)))
    return frames[first * 2:last * 2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="каталог клона veche-bench")
    parser.add_argument("--variant", default="data_simple", choices=sorted(NOISE))
    parser.add_argument("--out", required=True)
    parser.add_argument("--terms", default=os.path.join(REPO, "asr_eval", "terms.json"))
    parser.add_argument("--dev-meetings", default=",".join(DEV_MEETINGS))
    parser.add_argument(
        "--min-duration",
        type=float,
        default=0.35,
        help="короче этого реплики отбрасываются: Parakeet требует >= 0.3 с, "
             "и все модели должны видеть одинаковый набор",
    )
    args = parser.parse_args(argv)

    dev_meetings = {name.strip() for name in args.dev_meetings.split(",") if name.strip()}
    vocab = terms_mod.Vocabulary.from_path(args.terms)
    variant_dir = os.path.join(args.source, args.variant)
    meetings = sorted(entry for entry in os.listdir(variant_dir) if os.path.isdir(os.path.join(variant_dir, entry)))

    # Старые нарезки удаляются: иначе после смены --min-duration в каталоге
    # остаются файлы, которых нет в манифесте, и прогон уходит по чужому списку.
    audio_root = os.path.join(args.out, "audio")
    if os.path.isdir(audio_root):
        shutil.rmtree(audio_root)
    os.makedirs(audio_root)
    os.makedirs(os.path.join(args.out, "rttm"), exist_ok=True)

    manifest_lines: list[str] = []
    metadata_rows: list[dict] = []
    rttm_lines: list[str] = []
    total_seconds = 0.0
    skipped_short = 0

    for meeting in meetings:
        wav_path = os.path.join(variant_dir, meeting, f"{meeting}.wav")
        csv_path = os.path.join(variant_dir, meeting, f"{meeting}.csv")
        params, frames = read_meeting_wav(wav_path)
        audio_dir = os.path.join(args.out, "audio", meeting)
        os.makedirs(audio_dir, exist_ok=True)
        split = "dev" if meeting in dev_meetings else "test"

        with open(csv_path, encoding="utf-8", newline="") as fh:
            replicas = list(csv.DictReader(fh))

        for replica in replicas:
            index = int(replica["replica_idx"])
            start = float(replica["start"])
            end = float(replica["end"])
            text = " ".join(replica["text"].split())
            if not text:
                continue
            chunk = slice_frames(frames, params.framerate, start, end)
            if len(chunk) / 2 / params.framerate < args.min_duration:
                skipped_short += 1
                continue
            seg_id = f"{meeting}-{index:04d}"
            rel_path = os.path.join("audio", meeting, f"{seg_id}.wav")
            abs_path = os.path.join(args.out, rel_path)
            with wave.open(abs_path, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(params.framerate)
                out.writeframes(chunk)
            duration = len(chunk) / 2 / params.framerate
            total_seconds += duration

            speaker = str(replica["speaker_id"]).strip()
            occurrences = vocab.occurrences(norm.tokens(text))
            metadata_rows.append({
                "segment_id": seg_id,
                "recording_id": meeting,
                "register": "R2",
                "speaker_id": f"{meeting}/s{speaker}",
                "mic_class": "phone",
                "noise_class": NOISE[args.variant],
                "duration_s": round(duration, 3),
                "split": split,
                "source": f"{SOURCE_URL} rev {REVISION[:8]} / {args.variant} / {meeting} / replica {index}",
                "license": LICENSE,
                "rights_evidence": f"{SOURCE_URL}/blob/{REVISION}/README.md (card: license cc-by-4.0 + ethical use)",
                "rights_status": "card_checked_no_human_signoff",
                "rights_reviewer": "pending",
                "transcript_status": "scenario_text_not_listened",
                "transcript_reviewer": "pending",
                "transcript_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "audio_sha256": sha256_file(abs_path),
                "terms": [
                    {"term_id": occ.term_id, "start": occ.start, "end": occ.end, "form_kind": occ.form_kind}
                    for occ in occurrences
                ],
                "notes": "mic_class и register назначены скриптом, не проверены прослушиванием",
            })
            manifest_lines.append(f"{rel_path}\t{text}")
            rttm_lines.append(
                f"SPEAKER {meeting} 1 {start:.3f} {duration:.3f} <NA> <NA> S{speaker} <NA> <NA>"
            )

    with open(os.path.join(args.out, "manifest.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(manifest_lines) + "\n")
    with open(os.path.join(args.out, "metadata.jsonl"), "w", encoding="utf-8") as fh:
        for row in metadata_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "rttm", f"{args.variant}.rttm"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rttm_lines) + "\n")

    per_split = {}
    for row in metadata_rows:
        per_split[row["split"]] = per_split.get(row["split"], 0) + 1
    print(json.dumps({
        "variant": args.variant,
        "meetings": len(meetings),
        "segments": len(metadata_rows),
        "seconds": round(total_seconds, 1),
        "minutes": round(total_seconds / 60, 1),
        "per_split": per_split,
        "term_occurrences": sum(len(row["terms"]) for row in metadata_rows),
        "dev_meetings": sorted(dev_meetings & set(meetings)),
        "skipped_shorter_than_s": args.min_duration,
        "skipped_count": skipped_short,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
