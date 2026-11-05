"""Full-pipeline evaluation: audio -> VAD/endpointing -> GigaAM vs no-VAD batch.

Subcommands:

- `make-audio` — build a long-window WAV from consecutive corpus utterances
  (`experiments/corpus/local/veche-prepared/simple/manifest.tsv`, dev split),
  with a configurable silence schedule between them, plus a JSONL of reference
  texts and audio offsets.
- `run` — stream the built WAV through the daemon at `--realtime` speed, collect
  `final` events, concatenate them, and score against the concatenated
  references with the asr_eval WER/CER (norm-v2). Also scores (a) a batch
  transcription of the same concatenated WAV and (b) batch transcription of each
  reference utterance in isolation (upper bound isolating VAD damage), plus
  boundary diagnostics via the asr_eval word alignment.
- `sweep` — run the same audio over a grid of endpointing configs and emit a
  markdown table + JSON artifact.

Stdlib only. Text scoring is reused from `experiments/asr_eval` (norm/wer/align);
this file never re-implements normalisation or alignment and never starts a
daemon: `run`/`sweep` require an already running daemon reachable via HTTP.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import platform
import statistics
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTS_DIR = os.path.dirname(HERE)
CORPUS_SIMPLE = os.path.join(
    EXPERIMENTS_DIR, "corpus", "local", "veche-prepared", "simple"
)
ASR_EVAL_DIR = os.path.join(EXPERIMENTS_DIR, "asr_eval")

# The scorer modules live in experiments/asr_eval and import each other by
# top-level names (`import align`), so the directory itself goes on sys.path.
for path in (EXPERIMENTS_DIR, ASR_EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import align as align_mod  # noqa: E402
import norm as norm_mod  # noqa: E402
import wer as wer_mod  # noqa: E402

from runtime_eval import runtime_client as rc  # noqa: E402

SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Audio window construction (pure, offline)
# ---------------------------------------------------------------------------


def read_wav_i16(path: str) -> tuple[list[int], int, int]:
    """16-bit PCM WAV -> (samples, rate, channels). Raises on other widths."""
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM WAV")
        frames = wav.readframes(wav.getnframes())
    if channels != 1:
        raise ValueError(f"{path}: expected mono WAV")
    return list(struct.unpack(f"<{len(frames) // 2}h", frames)), rate, channels


def write_wav(path: str, samples: list[int], sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def write_wav_bytes(samples: list[float], sample_rate: int = SAMPLE_RATE) -> bytes:
    """Floats in [-1, 1] -> WAV bytes (16-bit mono), for one-shot transcription."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(
            struct.pack(
                f"<{len(samples)}h",
                *(max(-32768, min(32767, int(round(v * 32768.0)))) for v in samples),
            )
        )
    return buffer.getvalue()


def resolve_gaps(gap_ms: int, gaps: list[int] | None, junctions: int) -> list[int]:
    """Silence between consecutive utterances: `--gaps` schedule or flat `--gap-ms`.

    A `--gaps` list must contain exactly `junctions` entries or a single entry
    that applies to every junction; anything else is an input error, not a guess.
    """
    if gaps is None:
        return [gap_ms] * junctions
    if len(gaps) == 1:
        return [gaps[0]] * junctions
    if len(gaps) != junctions:
        raise ValueError(
            f"--gaps has {len(gaps)} entries but the window has {junctions} junction(s)"
        )
    return list(gaps)


def build_window(
    utterances: list[dict],
    gap_ms: int,
    lead_ms: int = 0,
    trail_ms: int = 0,
    gaps: list[int] | None = None,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[list[int], list[dict]]:
    """Concatenate `utterances` ({"id", "reference", "samples"}) with silence.

    Returns (samples, entries); entries carry audio-stream millisecond offsets:
    `start_ms`/`end_ms` of the speech, `gap_before_ms` of the silence in front,
    and `duration_ms == end_ms - start_ms`. Pure: no I/O, deterministic.
    """
    if not utterances:
        raise ValueError("window needs at least one utterance")
    schedule = resolve_gaps(gap_ms, gaps, len(utterances) - 1)
    samples: list[int] = [0] * ms_to_samples(lead_ms, sample_rate)
    entries: list[dict] = []
    position_ms = lead_ms
    for index, utt in enumerate(utterances):
        gap_before = 0 if index == 0 else schedule[index - 1]
        if gap_before:
            samples.extend([0] * ms_to_samples(gap_before, sample_rate))
            position_ms += gap_before
        utt_samples = utt["samples"]
        start_ms = position_ms
        samples.extend(utt_samples)
        duration_ms = samples_to_ms(len(utt_samples), sample_rate)
        position_ms += duration_ms
        entries.append(
            {
                "index": index,
                "id": utt["id"],
                "reference": utt["reference"],
                "start_ms": start_ms,
                "end_ms": position_ms,
                "duration_ms": duration_ms,
                "gap_before_ms": gap_before,
            }
        )
    samples.extend([0] * ms_to_samples(trail_ms, sample_rate))
    return samples, entries


def ms_to_samples(ms: int, sample_rate: int = SAMPLE_RATE) -> int:
    return int(round(ms * sample_rate / 1000.0))


def samples_to_ms(count: int, sample_rate: int = SAMPLE_RATE) -> int:
    return int(round(count * 1000 / sample_rate))


def load_manifest(dataset_dir: str) -> list[dict]:
    """manifest.tsv rows (`path<TAB>reference`) in file order, ids from filenames."""
    rows: list[dict] = []
    manifest = os.path.join(dataset_dir, "manifest.tsv")
    with open(manifest, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise ValueError(f"{manifest}:{lineno}: expected path<TAB>reference")
            rel_path, reference = parts[0].strip(), parts[1].strip()
            seg_id = os.path.splitext(os.path.basename(rel_path))[0]
            rows.append(
                {
                    "id": seg_id,
                    "path": os.path.join(dataset_dir, rel_path),
                    "reference": reference,
                }
            )
    return rows


def load_split_map(dataset_dir: str) -> dict[str, dict]:
    """segment_id -> metadata row (recording_id, split, ...)."""
    out: dict[str, dict] = {}
    with open(os.path.join(dataset_dir, "metadata.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            out[row["segment_id"]] = row
    return out


def select_window(
    dataset_dir: str, recording: str, start: int, count: int
) -> list[dict]:
    """Consecutive dev-split utterances of one recording, manifest order."""
    manifest = load_manifest(dataset_dir)
    split_map = load_split_map(dataset_dir)
    rows = []
    for row in manifest:
        meta = split_map.get(row["id"])
        if meta is None:
            raise ValueError(f"{row['id']}: no metadata.jsonl row")
        if meta.get("recording_id") != recording or meta.get("split") != "dev":
            continue
        rows.append(row)
    if not rows:
        raise ValueError(f"no dev utterances for recording {recording!r}")
    if start < 0 or start >= len(rows):
        raise ValueError(f"--start {start} out of range 0..{len(rows) - 1}")
    if count < 1:
        raise ValueError("--count must be >= 1")
    return rows[start : start + count]
def cmd_make_audio(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline_eval make-audio",
        description="Concatenate corpus utterances into one WAV with silence gaps",
    )
    parser.add_argument("--dataset", default=CORPUS_SIMPLE)
    parser.add_argument("--recording", default="2-one-to-one", help="recording_id")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--gap-ms", type=int, default=700)
    parser.add_argument(
        "--gaps", default=None, help="comma-separated per-junction silence, ms"
    )
    parser.add_argument(
        "--lead-ms",
        type=int,
        default=None,
        help="silence before the first file (default: 200 dataset, 0 with --wavs)",
    )
    parser.add_argument(
        "--trail-ms",
        type=int,
        default=None,
        help="silence after the last file (default: 1200 dataset, 0 with --wavs)",
    )
    parser.add_argument(
        "--wavs",
        default=None,
        help=(
            "comma-separated WAV paths: concatenate whole files verbatim with "
            "--gap-ms between them (long-audio mode); dataset/start/count ignored"
        ),
    )
    parser.add_argument("--out", required=True, help="output WAV path")

    args = parser.parse_args(argv)
    gaps = [int(v) for v in args.gaps.split(",")] if args.gaps else None
    verbatim = [p for p in (args.wavs.split(",") if args.wavs else []) if p]
    utterances = []
    if verbatim:
        for path in verbatim:
            if not os.path.exists(path):
                raise ValueError(f"{path}: not found")
            samples, rate, _ = read_wav_i16(path)
            if rate != SAMPLE_RATE:
                raise ValueError(f"{path}: {rate} Hz, expected {SAMPLE_RATE} Hz")
            utterances.append(
                {"id": os.path.basename(path), "reference": None, "samples": samples}
            )
    else:
        for row in select_window(args.dataset, args.recording, args.start, args.count):
            samples, rate, _ = read_wav_i16(row["path"])
            if rate != SAMPLE_RATE:
                raise ValueError(f"{row['path']}: {rate} Hz, expected {SAMPLE_RATE} Hz")
            utterances.append(
                {"id": row["id"], "reference": row["reference"], "samples": samples}
            )
    lead_default, trail_default = (0, 0) if verbatim else (200, 1200)
    lead_ms = lead_default if args.lead_ms is None else args.lead_ms
    trail_ms = trail_default if args.trail_ms is None else args.trail_ms
    samples, entries = build_window(
        utterances, args.gap_ms, lead_ms, trail_ms, gaps
    )
    write_wav(args.out, samples)
    sidecar = os.path.splitext(args.out)[0] + ".jsonl"
    with open(sidecar, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    total_ms = samples_to_ms(len(samples))
    print(
        f"wrote {args.out} ({total_ms} ms, {len(entries)} utterances) and {sidecar}"
    )
    return 0


# ---------------------------------------------------------------------------
# Scoring (reuses asr_eval norm/wer/align; no local re-implementation)
# ---------------------------------------------------------------------------


def score_text(reference: str, hypothesis: str) -> dict:
    return wer_mod.score_pair(reference, hypothesis)


def corpus_wer(items: list[dict]) -> dict:
    """asr_eval corpus scoring for [{"id", "reference", "hypothesis"}, ...]."""
    return wer_mod.corpus(items)


def concatenated_reference(entries: list[dict]) -> str:
    """Space-joined reference texts, one utterance after another (batch ref)."""
    return " ".join(entry["reference"].strip() for entry in entries).strip()


def concatenated_hypothesis(finals: list[dict]) -> str:
    """Space-joined final texts in emitted order (VAD pipeline hypothesis)."""
    texts = [(final.get("text") or "").strip() for final in finals]
    return " ".join(text for text in texts if text)


def _norm_tokens(text: str) -> list[str]:
    return norm_mod.tokens(text)


def boundary_diagnostics(entries: list[dict], finals: list[dict]) -> dict:
    """Word-level boundary damage via the asr_eval alignment (align.align).

    Concatenation can only hurt boundaries where the VAD split disagrees with
    the reference join: missing first/last words of a reference become deletions
    in the concatenated alignment, hallucinated words at split points become
    insertions adjacent to a reference boundary. Deletions/insertions far from
    any reference boundary are not attributed to endpointing.
    """
    reference = concatenated_reference(entries)
    hypothesis = concatenated_hypothesis(finals)
    ref_tokens = _norm_tokens(reference)
    hyp_tokens = _norm_tokens(hypothesis)
    ops, _ = align_mod.align(ref_tokens, hyp_tokens)
    # Word index in the concatenated reference where each utterance starts/ends.
    starts: set[int] = set()
    ends: set[int] = set()
    cursor = 0
    for entry in entries:
        tokens = _norm_tokens(entry["reference"])
        if tokens:
            starts.add(cursor)
            ends.add(cursor + len(tokens) - 1)
        cursor += len(tokens)

    boundary_deletions = 0
    deletion_examples: list[str] = []
    boundary_insertions = 0
    insertion_examples: list[str] = []
    for pos, (op, ref_idx, hyp_idx) in enumerate(ops):
        if op == "del" and ref_idx is not None and (ref_idx in starts or ref_idx in ends):
            boundary_deletions += 1
            deletion_examples.append(ref_tokens[ref_idx])
            continue
        if op == "ins":
            prev_ref = None
            for k in range(pos - 1, -1, -1):
                if ops[k][1] is not None:
                    prev_ref = ops[k][1]
                    break
            next_ref = None
            for k in range(pos + 1, len(ops)):
                if ops[k][1] is not None:
                    next_ref = ops[k][1]
                    break
            if (prev_ref is not None and prev_ref in ends) or (
                next_ref is not None and next_ref in starts
            ):
                boundary_insertions += 1
                if hyp_idx is not None:
                    insertion_examples.append(hyp_tokens[hyp_idx])
    return {
        "references": len(entries),
        "finals": len(finals),
        "boundary_deletions": boundary_deletions,
        "boundary_deletion_examples": deletion_examples[:20],
        "boundary_insertions": boundary_insertions,
        "boundary_insertion_examples": insertion_examples[:20],
        "insertions_total": sum(1 for op, _, _ in ops if op == "ins"),
    }

# ---------------------------------------------------------------------------
# Live runs (require an already running daemon; no daemon is started here)
# ---------------------------------------------------------------------------


def load_sidecar(path: str) -> list[dict]:
    """JSONL written by `make-audio`: reference texts with audio offsets."""
    entries: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def run_stream(
    client: rc.RuntimeClient,
    wav_path: str,
    model: str,
    config: dict,
    realtime: float,
) -> dict:
    """One streaming pass; returns finals plus the harness-side timings."""
    return rc.stream_file(client, wav_path, model, config, realtime)


def batch_transcribe(
    client: rc.RuntimeClient, wav_bytes: bytes, model: str, language: str | None
) -> dict:
    return client.transcribe_wav(wav_bytes, model, language, priority="batch")


def latency_stats(finals: list[dict]) -> dict:
    """Mean/p50 of the latency stages the contract reports per final."""

    def stats(key: str) -> dict:
        values = [final[key] for final in finals if final.get(key) is not None]
        if not values:
            return {"n": 0, "mean_ms": None, "p50_ms": None}
        return {
            "n": len(values),
            "mean_ms": round(statistics.fmean(values), 1),
            "p50_ms": round(statistics.median(values), 1),
        }

    return {
        "asr_ms": stats("asr_ms"),
        "queue_ms": stats("queue_ms"),
        "e2e_ms": stats("e2e_ms"),
    }


def utterance_duration_stats(finals: list[dict]) -> dict:
    durations = [
        (final["end_ms"] - final["start_ms"])
        for final in finals
        if final.get("end_ms") is not None and final.get("start_ms") is not None
    ]
    if not durations:
        return {"n": 0, "mean_ms": None, "p50_ms": None}
    return {
        "n": len(durations),
        "mean_ms": round(statistics.fmean(durations), 1),
        "p50_ms": round(statistics.median(durations), 1),
    }


def machine_info() -> dict:
    return {
        "machine": platform.machine(),
        "node": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "processor": platform.processor(),
        "python": platform.python_version(),
    }


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_header(argv: list[str], command: str) -> dict:
    return {
        "schema": "runtime-pipeline-eval-v1",
        "command": command,
        "argv": list(argv),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "finished_utc": None,
        "machine": machine_info(),
    }


def write_artifact(path: str, artifact: dict) -> None:
    artifact["finished_utc"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def evaluate_run(
    client: rc.RuntimeClient,
    wav_path: str,
    entries: list[dict],
    model: str,
    config: dict,
    realtime: float,
    language: str | None,
    baselines: bool = True,
) -> dict:
    """Streaming pass + scores + batch baselines. All numbers are measured."""
    stream_result = run_stream(client, wav_path, model, config, realtime)
    finals = stream_result["finals"]
    reference = concatenated_reference(entries)
    hypothesis = concatenated_hypothesis(finals)
    stream_score = corpus_wer(
        [{"id": "concat", "reference": reference, "hypothesis": hypothesis}]
    )["per_utterance"][0]

    result: dict = {
        "model": model,
        "config": config,
        "realtime": realtime,
        "audio": {
            "path": wav_path,
            "sha256": sha256_file(wav_path),
            "audio_ms": stream_result["audio_ms"],
            "wall_ms": stream_result["wall_ms"],
            "utterances": len(entries),
        },
        "stream": {
            "finals": len(finals),
            "ended": stream_result["ended"],
            "wer": stream_score["errors"] / stream_score["words"]
            if stream_score["words"]
            else None,
            "cer": stream_score["char_errors"] / stream_score["chars"]
            if stream_score["chars"]
            else None,
            "per_final": finals,
            "latency": latency_stats(finals),
            "utterance_duration_ms": utterance_duration_stats(finals),
        },
        "boundary": boundary_diagnostics(entries, finals),
    }

    if baselines:
        with open(wav_path, "rb") as fh:
            wav_bytes = fh.read()
        batch_reply = batch_transcribe(client, wav_bytes, model, language)
        batch_text = batch_reply.get("text", "")
        batch_score = score_text(reference, batch_text)
        result["batch_concat"] = {
            "text": batch_text,
            "asr_ms": batch_reply.get("asr_ms"),
            "wall_ms": batch_reply.get("wall_ms"),
            "audio_ms": batch_reply.get("audio_ms"),
            "wer": batch_score["errors"] / batch_score["words"]
            if batch_score["words"]
            else None,
            "cer": batch_score["char_errors"] / batch_score["chars"]
            if batch_score["chars"]
            else None,
            "counts": {
                "substitutions": batch_score["substitutions"],
                "deletions": batch_score["deletions"],
                "insertions": batch_score["insertions"],
            },
        }

        # Per-utterance upper bound: transcribe each reference utterance file.
        dataset = None
        for candidate in (os.environ.get("RUNTIME_EVAL_DATASET"), CORPUS_SIMPLE):
            if candidate and os.path.isdir(candidate):
                dataset = candidate
                break
        per_utt_rows: list[dict] = []
        if dataset:
            manifest = {row["id"]: row for row in load_manifest(dataset)}
            for entry in entries:
                row = manifest.get(entry["id"])
                if row is None or not os.path.isfile(row["path"]):
                    continue
                with open(row["path"], "rb") as fh:
                    reply = batch_transcribe(client, fh.read(), model, language)
                per_utt_rows.append(
                    {
                        "id": entry["id"],
                        "reference": entry["reference"],
                        "hypothesis": reply.get("text", ""),
                    }
                )
        if per_utt_rows:
            per_utt_corpus = corpus_wer(per_utt_rows)
            result["batch_per_utterance"] = {
                "n": len(per_utt_rows),
                "wer": per_utt_corpus["wer"],
                "cer": per_utt_corpus["cer"],
                "counts": {
                    "substitutions": per_utt_corpus["substitutions"],
                    "deletions": per_utt_corpus["deletions"],
                    "insertions": per_utt_corpus["insertions"],
                },
            }
    return result


def cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline_eval run",
        description="Stream a built WAV and score the full pipeline vs batch",
    )
    parser.add_argument("--wav", required=True, help="WAV from make-audio")
    parser.add_argument(
        "--sidecar",
        default=None,
        help="reference JSONL (default: WAV path with .jsonl)",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--realtime", type=float, default=8.0)
    parser.add_argument("--language", default=None)
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="k=v",
        help="stream config entry, e.g. --config min_silence_ms=700",
    )
    parser.add_argument(
        "--no-baselines", action="store_true", help="skip batch transcriptions"
    )
    parser.add_argument("--json", required=True, help="artifact output path")
    parser.add_argument(
        "--url", default=os.environ.get("SPEECH_RUNTIME_URL", rc.DEFAULT_BASE_URL)
    )
    parser.add_argument("--token", default=os.environ.get("SPEECH_RUNTIME_TOKEN"))

    args = parser.parse_args(argv)
    sidecar = args.sidecar or os.path.splitext(args.wav)[0] + ".jsonl"
    entries = load_sidecar(sidecar)
    if not entries:
        raise SystemExit(f"{sidecar}: no reference entries")
    config = rc.parse_config(args.config)
    if args.language:
        config["language"] = args.language
    client = rc.RuntimeClient(args.url, args.token)
    artifact = artifact_header(argv, "run")
    artifact.update(
        evaluate_run(
            client,
            args.wav,
            entries,
            args.model,
            config,
            args.realtime,
            args.language,
            baselines=not args.no_baselines,
        )
    )
    write_artifact(args.json, artifact)
    stream = artifact["stream"]
    batch = artifact.get("batch_concat", {})
    per_utt = artifact.get("batch_per_utterance", {})
    print(
        f"stream WER {fmt_pct(stream['wer'])} CER {fmt_pct(stream['cer'])} "
        f"finals {stream['finals']}/{artifact['boundary']['references']} refs"
    )
    if batch:
        print(f"batch(concat) WER {fmt_pct(batch['wer'])} CER {fmt_pct(batch['cer'])}")
    if per_utt:
        print(
            f"batch(per-utt) WER {fmt_pct(per_utt['wer'])} "
            f"CER {fmt_pct(per_utt['cer'])}"
        )
    print(f"artifact: {args.json}")
    return 0


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def parse_grid(spec: str) -> list:
    """`A,B,C` list of typed values; single value becomes a one-entry list."""
    values = [
        rc.coerce_value(part.strip()) for part in spec.split(",") if part.strip()
    ]
    if not values:
        raise SystemExit(f"empty grid: {spec!r}")
    return values


def sweep_configs(grid: dict[str, list]) -> list[dict]:
    """Cartesian product of per-key grids; keys sorted, later keys fastest."""
    configs: list[dict] = [{}]
    for key in sorted(grid):
        configs = [
            {**config, key: value} for config in configs for value in grid[key]
        ]
    return configs


def cmd_sweep(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline_eval sweep",
        description="Sweep endpointing configs over one audio window",
    )
    parser.add_argument("--wav", required=True)
    parser.add_argument("--sidecar", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--realtime", type=float, default=8.0)
    parser.add_argument("--language", default=None)
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="k=v1,v2",
        help="config grid entry, e.g. --config min_silence_ms=500,700,900",
    )
    parser.add_argument("--no-baselines", action="store_true")
    parser.add_argument("--json", required=True)
    parser.add_argument("--markdown", default=None, help="markdown table path")
    parser.add_argument(
        "--url", default=os.environ.get("SPEECH_RUNTIME_URL", rc.DEFAULT_BASE_URL)
    )
    parser.add_argument("--token", default=os.environ.get("SPEECH_RUNTIME_TOKEN"))

    args = parser.parse_args(argv)
    sidecar = args.sidecar or os.path.splitext(args.wav)[0] + ".jsonl"
    entries = load_sidecar(sidecar)
    if not entries:
        raise SystemExit(f"{sidecar}: no reference entries")
    grid = {
        key: parse_grid(spec)
        for key, spec in (kv.split("=", 1) for kv in args.config)
    }
    if not grid:
        grid = {"min_silence_ms": [700]}
    configs = sweep_configs(grid)
    if args.language:
        for config in configs:
            config["language"] = args.language
    client = rc.RuntimeClient(args.url, args.token)
    artifact = artifact_header(argv, "sweep")
    artifact["grid"] = {key: list(values) for key, values in grid.items()}
    artifact["results"] = []
    for config in configs:
        result = evaluate_run(
            client,
            args.wav,
            entries,
            args.model,
            config,
            args.realtime,
            args.language,
            baselines=not args.no_baselines,
        )
        artifact["results"].append(result)
        print(f"sweep {result['config']}: WER {fmt_pct(result['stream']['wer'])} "
              f"CER {fmt_pct(result['stream']['cer'])} "
              f"finals {result['stream']['finals']}")
    if not args.no_baselines:
        # Baselines are audio properties, not config properties: measure once.
        artifact["baseline"] = artifact["results"][0].get("batch_concat")
        artifact["baseline_per_utterance"] = artifact["results"][0].get(
            "batch_per_utterance"
        )
        for result in artifact["results"]:
            result.pop("batch_concat", None)
            result.pop("batch_per_utterance", None)
    write_artifact(args.json, artifact)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(sweep_markdown(artifact))
        print(f"markdown: {args.markdown}")
    print(f"artifact: {args.json}")
    return 0


def sweep_markdown(artifact: dict) -> str:
    """Markdown table: config | WER | CER | finals | durations | latency stages."""
    grid_keys = sorted(artifact.get("grid", {}))
    columns = [
        "WER",
        "CER",
        "finals",
        "refs",
        "utt mean/p50 ms",
        "asr mean/p50 ms",
        "queue mean/p50 ms",
        "e2e mean/p50 ms",
        "boundary del",
        "boundary ins",
    ]
    header = grid_keys if grid_keys else ["config"]
    lines = [
        "# Sweep: " + ", ".join(f"{key}={artifact['grid'][key]}" for key in grid_keys),
        "",
        "| " + " | ".join(header + columns) + " |",
        "|" + "---|" * (len(header) + len(columns)),
    ]
    for result in artifact["results"]:
        stream = result["stream"]
        boundary = result["boundary"]
        latency = stream["latency"]
        if grid_keys:
            config_cell = " ".join(str(result["config"][key]) for key in grid_keys)
        else:
            config_cell = json.dumps(result["config"], ensure_ascii=False)
        row = [
            config_cell,
            fmt_pct(stream["wer"]),
            fmt_pct(stream["cer"]),
            str(stream["finals"]),
            str(boundary["references"]),
            fmt_ms_pair(stream["utterance_duration_ms"]),
            fmt_ms_pair(latency["asr_ms"]),
            fmt_ms_pair(latency["queue_ms"]),
            fmt_ms_pair(latency["e2e_ms"]),
            str(boundary["boundary_deletions"]),
            str(boundary["boundary_insertions"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    baseline = artifact.get("baseline")
    if baseline:
        lines += [
            "",
            f"Baseline batch (concatenated audio): WER {fmt_pct(baseline['wer'])}, "
            f"CER {fmt_pct(baseline['cer'])}.",
        ]
    per_utt = artifact.get("baseline_per_utterance")
    if per_utt:
        lines.append(
            f"Baseline batch (each reference utterance alone): WER "
            f"{fmt_pct(per_utt['wer'])}, CER {fmt_pct(per_utt['cer'])} "
            f"({per_utt['n']} utterances)."
        )
    return "\n".join(lines) + "\n"


def fmt_ms_pair(stats: dict) -> str:
    if stats.get("n"):
        return f"{stats['mean_ms']:.0f}/{stats['p50_ms']:.0f}"
    return "-"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m runtime_eval.pipeline_eval make-audio|run|sweep ...",
            file=sys.stderr,
        )
        return 2
    command, rest = argv[0], argv[1:]
    if command == "make-audio":
        return cmd_make_audio(rest)
    if command == "run":
        return cmd_run(rest)
    if command == "sweep":
        return cmd_sweep(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
