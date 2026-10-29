#!/usr/bin/env python3
"""Собирает воспроизводимые результаты экспериментов в отслеживаемый каталог.

Аудио, веса и полные гипотезы остаются в `experiments/corpus/local/` (вне Git);
сюда попадают метрики, тайминги и хэши, по которым можно сверить прогон.

    python3 experiments/runners/collect_results.py --out experiments/pilot/results
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil

SIMPLE = "experiments/corpus/local/veche-prepared/simple/runs"
SCORES = os.path.join(SIMPLE, "scores")
FLEURS = "experiments/corpus/local/fleurs-test"
MEDIUM = "experiments/corpus/local/veche-prepared/medium"
HARD = "experiments/corpus/local/veche-prepared/hard"
LORA = "experiments/corpus/local/embedding/lora/report.json"
EMBED = "experiments/corpus/local/embedding/results.json"

SOURCES = [
    (SCORES, "scores"),
    (os.path.join(SIMPLE, "der.json"), "der"),
    (os.path.join(MEDIUM, "der.json"), "der"),
    (os.path.join(HARD, "der.json"), "der"),
    (os.path.join(SIMPLE, "stream560-latency.json"), "streaming"),
    (os.path.join(SIMPLE, "stream1120-latency.json"), "streaming"),
    (os.path.join(SIMPLE, "stream2240-latency.json"), "streaming"),
    (os.path.join(SIMPLE, "correction-char-t0.25.json"), "correction"),
    (os.path.join(SIMPLE, "correction-char-t0.3.json"), "correction"),
    (os.path.join(SIMPLE, "correction-char-t0.4.json"), "correction"),
    (os.path.join(SIMPLE, "correction-embed-t0.5.json"), "correction"),
    (os.path.join(SIMPLE, "correction-embed-t0.6.json"), "correction"),
    (os.path.join(SIMPLE, "scores", "qwen-dev-empty.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "qwen-dev-relevant.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "qwen-dev-distractor.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "nemotron-dev-none.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "nemotron-dev-vocab2.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "parakeet-boost-plain-dev.json"), "adaptation"),
    (os.path.join(SIMPLE, "scores", "parakeet-boost-vocab-dev.json"), "adaptation"),
    (os.path.join(MEDIUM, "runs-parakeet-test.json"), "variants"),
    (os.path.join(MEDIUM, "runs-gigaam-test.json"), "variants"),
    (os.path.join(HARD, "runs-parakeet-test.json"), "variants"),
    (os.path.join(HARD, "runs-gigaam-test.json"), "variants"),
    (os.path.join(FLEURS, "runs-parakeet-test.json"), "fleurs"),
    (os.path.join(FLEURS, "runs-gigaam-test.json"), "fleurs"),
    (LORA, "embedding"),
    (EMBED, "embedding"),
]


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="experiments/pilot/results")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    manifest = []
    for source, group in SOURCES:
        if os.path.isdir(source):
            for name in sorted(os.listdir(source)):
                if not name.endswith(".json"):
                    continue
                src = os.path.join(source, name)
                dst_dir = os.path.join(args.out, group)
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copyfile(src, os.path.join(dst_dir, name))
                manifest.append({"group": group, "file": f"{group}/{name}",
                                 "source": src, "sha256": sha256(src)})
            continue
        if not os.path.isfile(source):
            continue
        dst_dir = os.path.join(args.out, group)
        os.makedirs(dst_dir, exist_ok=True)
        # Имена вида runs-parakeet-test.json повторяются у medium и hard, поэтому
        # к имени добавляется родительский каталог: иначе один файл затирает другой.
        parent = os.path.basename(os.path.dirname(source))
        name = f"{parent}-{os.path.basename(source)}"
        shutil.copyfile(source, os.path.join(dst_dir, name))
        manifest.append({"group": group, "file": f"{group}/{name}",
                         "source": source, "sha256": sha256(source)})

    with open(os.path.join(args.out, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump({"files": manifest}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"files": len(manifest), "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
