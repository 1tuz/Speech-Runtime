"""Batch-slicing quality harness: text/token stability across code changes.

Subcommands:

- `run --wav ... --model ... --json out.json [--repeat N] [--language ru]` —
  one (or N repeated) batch transcriptions of the same WAV through
  `/v1/audio/transcriptions` with `priority=batch`. The artifact stores the
  text, the token stream (`text`, `start_ms`) as reported by the daemon, the
  timings (`asr_ms`/`wall_ms`/`queue_ms`), the WAV SHA-256, machine and git
  info, plus the exact command/argv. Repeats are stored as a list of runs.
- `compare --json-before a.json --json-after b.json --json out.json` — the
  offline, pure comparison `compare_batch_runs(before, after)`: WER of the
  after-text against the before-text via the asr_eval scorer (norm-v2), token
  diff from the alignment of the norm tokens (deletions = words lost,
  insertions = words duplicated/hallucinated), and the timing deltas
  (`asr_ms`, `wall_ms`). No daemon and no audio are needed to compare.

The daemon is never started here: `run` requires an already running daemon.
Under-load latency is measured by `soak.py multiclient --batch`; this file
deliberately does not duplicate it.

Stdlib only; scoring is reused from `experiments/asr_eval`, never
re-implemented.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPERIMENTS_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(EXPERIMENTS_DIR)
ASR_EVAL_DIR = os.path.join(EXPERIMENTS_DIR, "asr_eval")

# asr_eval modules import each other by top-level names (`import align`).
for path in (EXPERIMENTS_DIR, ASR_EVAL_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import align as align_mod  # noqa: E402
import norm as norm_mod  # noqa: E402
import wer as wer_mod  # noqa: E402

from runtime_eval import runtime_client as rc  # noqa: E402
from runtime_eval.environment import git_state  # noqa: E402
from runtime_eval.pipeline_eval import machine_info, sha256_file  # noqa: E402

SCHEMA = "runtime-batch-slice-eval-v1"


# ---------------------------------------------------------------------------
# Pure comparison (offline, unit-tested)
# ---------------------------------------------------------------------------


def compare_batch_runs(before: dict, after: dict) -> dict:
    """Compare two `run` artifacts of the same WAV across a code change.

    `before`/`after` are dicts as written by `cmd_run` (the top-level artifact
    or a single run record). The comparison reuses the asr_eval scorer and
    alignment on the normalised texts: WER treats the before-text as the
    reference, deletions are words lost by the change, insertions are words
    the new code adds (duplicates/hallucinations). Timings are deltas of the
    mean over repeats; None whenever either side did not measure them.
    Pure: no I/O, no daemon.
    """
    before_run = _single_run(before)
    after_run = _single_run(after)
    before_text = (before_run.get("text") or "").strip()
    after_text = (after_run.get("text") or "").strip()

    score = wer_mod.score_pair(before_text, after_text)
    ref_tokens = norm_mod.tokens(before_text)
    hyp_tokens = norm_mod.tokens(after_text)
    ops, _ = align_mod.align(ref_tokens, hyp_tokens)
    deletions = [ref_tokens[ref_idx] for op, ref_idx, _ in ops
                 if op == "del" and ref_idx is not None]
    insertions = [hyp_tokens[hyp_idx] for op, _, hyp_idx in ops
                  if op == "ins" and hyp_idx is not None]

    return {
        "before_sha256": before_run.get("sha256"),
        "after_sha256": after_run.get("sha256"),
        "same_audio": (
            None
            if before_run.get("sha256") is None or after_run.get("sha256") is None
            else before_run["sha256"] == after_run["sha256"]
        ),
        "text": {
            "before": before_text,
            "after": after_text,
            "identical": before_text == after_text,
            "wer": (score["errors"] / score["words"]) if score["words"] else None,
            "words": score["words"],
            "errors": score["errors"],
            "substitutions": score["substitutions"],
            "deletions": score["deletions"],
            "insertions": score["insertions"],
            "lost_word_examples": deletions[:20],
            "added_word_examples": insertions[:20],
        },
        "tokens": {
            "before": len(before_run.get("tokens") or []),
            "after": len(after_run.get("tokens") or []),
        },
        "timing": {
            "asr_ms": _mean_delta(before, after, "asr_ms"),
            "wall_ms": _mean_delta(before, after, "wall_ms"),
            "queue_ms": _mean_delta(before, after, "queue_ms"),
            "note": "mean after minus mean before over the run repeats; None when unmeasured",
        },
    }


def _single_run(artifact: dict) -> dict:
    """One run record: the artifact itself (single run) or its first repeat.

    `run` writes either a flat run record (one repeat) or `{"runs": [...]}`;
    a missing key is preserved as None so the comparison reports UNMEASURED
    instead of guessing.
    """
    runs = artifact.get("runs")
    if isinstance(runs, list) and runs:
        return runs[0]
    return artifact


def _mean_delta(before: dict, after: dict, key: str) -> float | None:
    """Mean(after) - mean(before) for one timing key over the run repeats."""
    before_mean = _mean_runs(before, key)
    after_mean = _mean_runs(after, key)
    if before_mean is None or after_mean is None:
        return None
    return round(after_mean - before_mean, 1)


def _mean_runs(artifact: dict, key: str) -> float | None:
    """Mean of `key` over the runs of an artifact (single run or repeats)."""
    runs = artifact.get("runs")
    if not isinstance(runs, list) or not runs:
        runs = [artifact]
    values = [run.get(key) for run in runs]
    values = [value for value in values if isinstance(value, (int, float))]
    if not values:
        return None
    return statistics.fmean(values)


def _norm_tokens(text: str) -> list[str]:
    return norm_mod.tokens(text)


# ---------------------------------------------------------------------------
# Live run (requires an already running daemon; no daemon is started here)
# ---------------------------------------------------------------------------


def run_batch(
    client: rc.RuntimeClient,
    wav_path: str,
    model: str,
    language: str | None,
    repeat: int = 1,
) -> list[dict]:
    """`repeat` batch transcriptions of the same WAV; one record per repeat."""
    with open(wav_path, "rb") as fh:
        wav_bytes = fh.read()
    runs: list[dict] = []
    for index in range(repeat):
        reply = client.transcribe_wav(wav_bytes, model, language, priority="batch")
        tokens = reply.get("tokens") or []
        runs.append(
            {
                "repeat": index,
                "text": reply.get("text", ""),
                "tokens": [
                    {
                        "text": token.get("text", ""),
                        "start_ms": token.get("start_ms"),
                    }
                    for token in tokens
                    if isinstance(token, dict)
                ],
                "asr_ms": reply.get("asr_ms"),
                "wall_ms": reply.get("wall_ms"),
                "queue_ms": reply.get("queue_ms"),
                "audio_ms": reply.get("audio_ms"),
            }
        )
    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="batch_slice_eval run",
        description="Batch-transcribe one WAV N times and store the artifact",
    )
    parser.add_argument("--wav", required=True, help="16 kHz mono PCM WAV")
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeat", type=int, default=1, help="number of runs")
    parser.add_argument("--language", default=None)
    parser.add_argument("--json", required=True, help="artifact output path")
    parser.add_argument(
        "--url", default=os.environ.get("SPEECH_RUNTIME_URL", rc.DEFAULT_BASE_URL)
    )
    parser.add_argument("--token", default=os.environ.get("SPEECH_RUNTIME_TOKEN"))
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be >= 1")

    client = rc.RuntimeClient(args.url, args.token)
    runs = run_batch(client, args.wav, args.model, args.language, args.repeat)
    artifact = artifact_header(argv, "run")
    artifact.update(
        {
            "model": args.model,
            "language": args.language,
            "repeat": args.repeat,
            "audio": {
                "path": args.wav,
                "sha256": sha256_file(args.wav),
            },
            "runs": runs,
        }
    )
    write_artifact(args.json, artifact)
    first = runs[0]
    print(
        f"batch {args.model}: {args.repeat} run(s), "
        f"{len(first['tokens'])} tokens, "
        f"asr_ms {first['asr_ms']}"
    )
    print(f"artifact: {args.json}")
    return 0


def cmd_compare(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="batch_slice_eval compare",
        description="Offline comparison of two run artifacts of the same WAV",
    )
    parser.add_argument("--json-before", required=True)
    parser.add_argument("--json-after", required=True)
    parser.add_argument("--json", required=True, help="artifact output path")
    args = parser.parse_args(argv)

    with open(args.json_before, encoding="utf-8") as fh:
        before = json.load(fh)
    with open(args.json_after, encoding="utf-8") as fh:
        after = json.load(fh)
    comparison = compare_batch_runs(before, after)
    artifact = artifact_header(argv, "compare")
    artifact.update(
        {
            "before": {"path": args.json_before, "model": before.get("model")},
            "after": {"path": args.json_after, "model": after.get("model")},
            "comparison": comparison,
        }
    )
    write_artifact(args.json, artifact)
    text = comparison["text"]
    timing = comparison["timing"]
    print(
        f"identical={text['identical']} WER {fmt_pct(text['wer'])} "
        f"del {text['deletions']} ins {text['insertions']}"
    )
    print(
        f"delta asr_ms {timing['asr_ms']} wall_ms {timing['wall_ms']} "
        f"queue_ms {timing['queue_ms']}"
    )
    if comparison["same_audio"] is False:
        print(
            "WARNING: different audio (sha256 mismatch); "
            "the comparison measures audio, not the code change",
            file=sys.stderr,
        )
    print(f"artifact: {args.json}")
    return 0


def artifact_header(argv: list[str], command: str) -> dict:
    return {
        "schema": SCHEMA,
        "command": command,
        "argv": list(argv),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "finished_utc": None,
        "machine": machine_info(),
        "git": git_state(),
    }


def write_artifact(path: str, artifact: dict) -> None:
    artifact["finished_utc"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m runtime_eval.batch_slice_eval run|compare ...",
            file=sys.stderr,
        )
        return 2
    command, rest = argv[0], argv[1:]
    if command == "run":
        return cmd_run(rest)
    if command == "compare":
        return cmd_compare(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
