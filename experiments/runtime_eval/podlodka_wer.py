"""Runtime WER on the local Podlodka clips, comparable with the file-engine baseline.

The batch baseline in `experiments/corpus/local/engines/batch-baseline-podlodka.json`
transcribes the same clips with file engines (FluidAudio / wavo). This script sends the
same WAVs through the daemon, scores them with the asr_eval norm-v2 scorer, and prints
the delta, so "runtime vs file" is a measured number on identical audio instead of an
assumption. The reference texts are `unverified` (dataset `transcription` field), so the
artifact records that status and must not be quoted as a benchmark.

Stdlib only; scoring is reused from `experiments/asr_eval`, never re-implemented.
Does not start a daemon: point `--url`/`--token` at a running one.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTS_DIR = os.path.dirname(HERE)
ASR_EVAL_DIR = os.path.join(EXPERIMENTS_DIR, "asr_eval")
CORPUS_LOCAL = os.path.join(EXPERIMENTS_DIR, "corpus", "local")
REFS = os.path.join(CORPUS_LOCAL, "transcripts", "refs.jsonl")
FILE_BASELINE = os.path.join(CORPUS_LOCAL, "engines", "batch-baseline-podlodka.json")

for path in (EXPERIMENTS_DIR, ASR_EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import wer as wer_mod  # noqa: E402

from runtime_eval import runtime_client as rc  # noqa: E402
from runtime_eval.pipeline_eval import machine_info, sha256_file  # noqa: E402


def load_refs(path: str) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries





def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runtime_eval.podlodka_wer",
        description="Score the daemon's batch transcription against the local Podlodka refs",
    )
    parser.add_argument("--url", default=os.environ.get("SPEECH_RUNTIME_URL", rc.DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("SPEECH_RUNTIME_TOKEN"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--refs", default=REFS)
    parser.add_argument("--corpus-root", default=CORPUS_LOCAL)
    parser.add_argument("--baseline", default=FILE_BASELINE)
    parser.add_argument("--language", default=None)
    parser.add_argument("--json", required=True, help="artifact output path")
    args = parser.parse_args(argv)
    run_started = datetime.datetime.now(datetime.timezone.utc)

    entries = load_refs(args.refs)
    if not entries:
        raise SystemExit(f"{args.refs}: no reference entries")

    client = rc.RuntimeClient(args.url, args.token)
    loaded = client.health().get("worker", {}).get("models") or []
    names = {
        entry.get("model")
        for entry in loaded
        if isinstance(entry, dict) and entry.get("loaded")
    }
    load_ms = None
    if args.model not in names:
        started = datetime.datetime.now()
        reply = client.load(args.model)
        load_ms = int((datetime.datetime.now() - started).total_seconds() * 1000)
        if not reply.get("ok", True):
            raise SystemExit(f"load failed: {reply!r}")

    rows = []
    items = []
    for entry in entries:
        wav_path = os.path.join(args.corpus_root, entry["file"])
        with open(wav_path, "rb") as fh:
            wav_bytes = fh.read()
        reply = client.transcribe_wav(wav_bytes, args.model, language=args.language)
        hypothesis = (reply.get("text") or "").strip()
        scored = wer_mod.score_pair(entry["ref"], hypothesis)
        rows.append({
            "id": entry["segment_id"],
            "wav": entry["file"],
            "sha256": sha256_file(wav_path),
            "bytes": len(wav_bytes),
            "annotation_status": entry.get("annotation_status", "unknown"),
            "wer": (scored["errors"] / scored["words"]) if scored["words"] else None,
            "cer": (
                (scored["char_errors"] / scored["chars"]) if scored["chars"] else None
            ),
            "words": scored["words"],
            "errors": scored["errors"],
            "chars": scored["chars"],
            "char_errors": scored["char_errors"],
            "substitutions": scored["substitutions"],
            "deletions": scored["deletions"],
            "insertions": scored["insertions"],
            "asr_ms": reply.get("asr_ms"),
            "queue_ms": reply.get("queue_ms"),
            "reference": entry["ref"],
            "hypothesis": hypothesis,
        })
        items.append({"id": entry["segment_id"], "reference": entry["ref"], "hypothesis": hypothesis})

    total = wer_mod.corpus(items)
    artifact = {
        "schema": "runtime-podlodka-wer-v1",
        "argv": list(argv if argv is not None else sys.argv[1:]),
        "started_utc": run_started.isoformat(),
        "machine": machine_info(),
        "model": args.model,
        "load_ms": load_ms,
        "corpus": "experiments/corpus/local (5 clips, unverified refs, dev-only)",
        "results": {
            "wer": total["wer"],
            "cer": total["cer"],
            "words": total["words"],
            "errors": total["errors"],
            "substitutions": total["substitutions"],
            "deletions": total["deletions"],
            "insertions": total["insertions"],
        },
        "per_segment": rows,
        "file_baseline": None,
    }
    if os.path.exists(args.baseline):
        with open(args.baseline, encoding="utf-8") as fh:
            base = json.load(fh)
        base_stems = set(base.get("segments") or [])
        stems = [os.path.splitext(os.path.basename(row["wav"]))[0] for row in rows]
        common = [item for item, stem in zip(items, stems) if stem in base_stems]
        common_stems = [stem for stem in stems if stem in base_stems]
        subset = wer_mod.corpus(common) if common else None
        artifact["file_baseline"] = {
            "path": os.path.relpath(args.baseline, EXPERIMENTS_DIR),
            "engines": base.get("results"),
            "words": base.get("words"),
            "note": base.get("note"),
            "subset": None,
        }
        if subset:
            artifact["file_baseline"]["subset"] = {
                "stems": sorted(common_stems),
                "words": subset["words"],
                "wer": subset["wer"],
                "cer": subset["cer"],
                "delta_wer": {
                    engine: subset["wer"] - row["wer"]
                    for engine, row in (base.get("results") or {}).items()
                },
                "note": "delta = runtime WER on the baseline's own clips minus file-engine WER",
            }
    artifact["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"runtime {args.model}: WER {total['wer']:.2%} CER {total['cer']:.2%} "
          f"({total['words']} words, {len(rows)} clips)")
    subset = (artifact["file_baseline"] or {}).get("subset")
    if subset:
        print(f"same-audio subset ({len(subset['stems'])} clips, {subset['words']} words): "
              f"runtime WER {subset['wer']:.2%}")
        for engine, delta in sorted(subset["delta_wer"].items()):
            file_wer = artifact["file_baseline"]["engines"][engine]["wer"]
            print(f"  vs {engine}: file {file_wer:.2%} -> delta {delta:+.2%}")
    print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())