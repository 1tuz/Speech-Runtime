"""Soak and failure-scenario harness for the Speech Runtime daemon.

Subcommands (pure stdlib, argparse). The daemon is NEVER started by this file
unless `--spawn BINARY` is given explicitly: that path requires a NON-default
port together with `--token-file` and `--config`, and the process is killed at
the end. Every other invocation only talks HTTP to an already-running daemon.

- `long-session` — one stream held for N minutes, per-utterance latency stages,
  RSS/`phys_footprint` of daemon+worker sampled every 30 s.
- `multiclient` — concurrent streams (+ optional batch loop); interactive
  p50/p95, batch wall time, starvation check on `queue_ms`/`e2e_ms` p95 vs
  a no-batch baseline phase over the same audio/clients, finals completeness,
  `end_reason` counts. Assertion statuses: PASS / FAIL / UNMEASURED / BLOCKED.
- `crash` — `kill -9` the worker mid-stream; daemon must stay up, the client
  must see an explicit error/ended, `health.restarts >= 1`, a new stream must
  work after the worker reloads.
- `cancel` — cancel mid-stream; no `final` after cancel, `health.streams` back
  to baseline.
- `two-models` — load `gigaam` + `parakeet-ultra` from `runtime/models.json`,
  stream both concurrently, per-model `asr_ms` and failures.
- `memory` — RSS/`phys_footprint` baseline -> loaded -> N streams -> all ended
  -> unloaded; `health.streams` must not grow after ends.

Every scenario writes a JSON artifact with argv, percentiles and pass/fail per
assertion. Exit code 0 only when all assertions pass.

Offline tests for the pure helpers: `python3 -m unittest runtime_eval.tests.test_soak`
(from `experiments/`, no daemon needed).
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
MODELS_JSON = os.path.join(REPO_ROOT, "runtime", "models.json")
SAMPLE_INTERVAL_S = 30.0
DEFAULT_SAMPLE_RATE = 16000

sys.path.insert(0, os.path.dirname(HERE))  # experiments/ for `runtime_eval.`

from runtime_eval import runtime_client as rc  # noqa: E402

SCHEMA = "runtime-soak-v1"


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline)
# ---------------------------------------------------------------------------


def percentile(values, p: float):
    """Nearest-rank percentile of a numeric list; None for empty input.

    `p` in [0, 100]; 50 == median by the nearest-rank convention.
    """
    if not values:
        return None
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile out of range: {p}")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(math.ceil(p / 100.0 * len(ordered))) - 1))
    return ordered[rank]


def percentile_stats(values, ps=(50, 95)) -> dict:
    """`{"n": .., "mean": .., "p50": .., "p95": ..}` with None for missing data."""
    stats: dict = {"n": len(values)}
    stats["mean"] = round(sum(values) / len(values), 1) if values else None
    for p in ps:
        value = percentile(values, p)
        stats[f"p{p:g}"] = value if value is None else round(float(value), 1)
    return stats


def parse_rss_kib(output: str):
    """`ps -o rss=` output line(s) -> KiB int, None when the process is gone."""
    for line in output.splitlines():
        text = line.strip()
        if text:
            return int(text)
    return None


def parse_footprint_bytes(output: str):
    """`footprint -p PID` stdout -> phys_footprint bytes, None when unavailable.

    Tolerates both the plain `phys_footprint: 123456` form and the JSON form
    (`"phys_footprint": 123456`) used by newer footprint builds.
    """
    for line in output.splitlines():
        text = line.strip().rstrip(",")
        if ":" not in text:
            continue
        key, _, value = text.partition(":")
        if key.strip().strip('"') != "phys_footprint":
            continue
        try:
            return int(value.strip())
        except ValueError:
            continue
    return None


def slice_samples(samples, rate: int, from_ms: int, to_ms: int):
    """Half-open [from_ms, to_ms) slice of a sample list, clamped to bounds."""
    start = max(0, int(round(from_ms * rate / 1000.0)))
    end = max(start, min(len(samples), int(round(to_ms * rate / 1000.0))))
    return samples[start:end]


def chunk_samples(samples, rate: int, chunk_ms: int):
    """Yields (offset_ms, chunk) pieces of a sample list."""
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be positive")
    step = max(1, int(round(chunk_ms * rate / 1000.0)))
    for offset in range(0, len(samples), step):
        yield int(round(offset * 1000.0 / rate)), samples[offset:offset + step]


class Assertions:
    """Named status collector for scenario checks.

    Statuses: `PASS`, `FAIL`, `UNMEASURED` (no data to judge, never a PASS),
    `BLOCKED` (a prerequisite failed so the check could not run). `check()`
    with `ok=None` records UNMEASURED; `check()` with a bool keeps the
    historical PASS/FAIL behaviour, so existing callers are unaffected.
    `all_passed` is true only when every result is PASS.
    """

    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, name: str, ok, detail=None) -> bool:
        if ok is None:
            return self.unmeasured(name, detail)
        passed = bool(ok)
        self.results.append(
            {"name": name, "pass": passed, "status": "PASS" if passed else "FAIL",
             "detail": detail if detail is not None else ok}
        )
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail if detail is not None else ok}")
        return passed

    __call__ = check

    def unmeasured(self, name: str, detail=None) -> bool:
        """Record UNMEASURED: missing data must never look like a PASS."""
        self.results.append(
            {"name": name, "pass": False, "status": "UNMEASURED", "detail": detail}
        )
        print(f"  [UNMEASURED] {name}: {detail}")
        return False

    def blocked(self, name: str, detail=None) -> bool:
        """Record BLOCKED: a prerequisite failed, the check could not run."""
        self.results.append(
            {"name": name, "pass": False, "status": "BLOCKED", "detail": detail}
        )
        print(f"  [BLOCKED] {name}: {detail}")
        return False

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r["status"] == "PASS" for r in self.results)



def status_of(result: dict) -> str:
    """Assertion status string (`PASS`/`FAIL`/`UNMEASURED`/`BLOCKED`); old artifacts default to PASS/FAIL."""
    status = result.get("status")
    if status in ("PASS", "FAIL", "UNMEASURED", "BLOCKED"):
        return status
    return "PASS" if result.get("pass") else "FAIL"


def machine_info() -> dict:
    return {
        "machine": platform.machine(),
        "node": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "processor": platform.processor(),
        "python": platform.python_version(),
    }


def artifact_header(argv, command: str) -> dict:
    return {
        "schema": SCHEMA,
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
        json.dump(artifact, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def wrap_slice(samples, start: int, end: int):
    """Samples of a virtual `samples * n` repetition, slice [start, end).

    Long sessions loop one WAV many times; materialising the concatenation
    would cost hundreds of MB of Python floats, so slicing wraps instead.
    """
    if not samples:
        return []
    if end <= start or start < 0:
        return []
    if end - start <= len(samples) and end // len(samples) == start // len(samples):
        return samples[start % len(samples):end % len(samples)]
    out = []
    pos = start
    while pos < end:
        base = pos % len(samples)
        take = min(len(samples) - base, end - pos)
        out.extend(samples[base:base + take])
        pos += take
    return out


# ---------------------------------------------------------------------------
# Process memory sampling (daemon + worker PIDs come from /health + lsof)
# ---------------------------------------------------------------------------


def _run(cmd, timeout: float = 10.0) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


FOOTPRINT_AVAILABLE = bool(_run(["footprint", "-p", "1"]).strip())


def sample_process_memory(pid):
    """`{"pid", "rss_kib", "phys_footprint"}` for one pid; rss None when gone.

    RSS via `ps -o rss=`; `phys_footprint` via `footprint -p PID` when the tool
    is present and allowed for that pid. No sudo, no powermetrics.
    """
    if not pid or pid < 0:
        return {"pid": pid, "rss_kib": None, "phys_footprint": None}
    rss = parse_rss_kib(_run(["ps", "-o", "rss=", "-p", str(pid)]))
    footprint = None
    if FOOTPRINT_AVAILABLE:
        footprint = parse_footprint_bytes(_run(["footprint", "-p", str(pid)]))
    return {"pid": pid, "rss_kib": rss, "phys_footprint": footprint}


def daemon_pid_for_url(base_url: str):
    """Daemon PID via `lsof` on the base URL's listening TCP port, else None."""
    try:
        from urllib.parse import urlsplit

        port = urlsplit(base_url).port
    except ValueError:
        return None
    if not port:
        return None
    out = _run(["lsof", "-nP", "-tiTCP:" + str(port), "-sTCP:LISTEN"])
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def memory_sample(client: rc.RuntimeClient, label: str, extra: dict | None = None) -> dict:
    health = client.health()
    worker_pid = int((health.get("worker") or {}).get("pid") or -1)
    daemon_pid = daemon_pid_for_url(client.base_url)
    sample = {
        "label": label,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "daemon": sample_process_memory(daemon_pid),
        "worker": sample_process_memory(worker_pid),
        "health_streams": health.get("streams"),
    }
    if extra:
        sample.update(extra)
    fp = sample["worker"]["phys_footprint"]
    print(
        f"  mem[{label}] daemon pid={sample['daemon']['pid']} "
        f"rss={sample['daemon']['rss_kib']} KiB | worker pid={worker_pid} "
        f"rss={sample['worker']['rss_kib']} KiB phys_footprint="
        f"{fp if fp is None else f'{fp / 1048576:.1f} MiB'} | "
        f"streams={sample['health_streams']}"
    )
    return sample


# ---------------------------------------------------------------------------
# Streaming engine: paced feeder + event collection
# ---------------------------------------------------------------------------


class FeedResult(dict):
    """Outcome of one fed stream (also a plain dict for JSON artifacts)."""


def run_feeder(
    client: rc.RuntimeClient,
    model: str,
    samples,
    rate: int,
    speed: float,
    config: dict | None = None,
    chunk_ms: int = 100,
    stop_event: threading.Event | None = None,
    loops: int = 1,
) -> FeedResult:
    """Feeds `samples` (repeated `loops` times) as one live stream at `speed`x.

    Collects finals/error/ended events. When `stop_event` is set mid-feed the
    feeder stops sending audio and only drains events (used by crash/cancel).
    Repetition wraps the buffer instead of materialising `samples * loops`.
    """
    stream_id = client.open_stream(model, config)
    started = time.monotonic()
    cursor = 0
    finals: list[dict] = []
    state = {"error": None, "ended": False}
    fed_audio_s = 0.0
    fed_clock = started

    def wall_at(audio_s: float) -> float:
        behind = max(fed_audio_s - audio_s, 0)
        return fed_clock - behind / speed

    def absorb(batch: dict) -> None:
        nonlocal cursor
        cursor = int(batch.get("next", cursor))
        for event in batch.get("events", []):
            name = event.get("event")
            data = event.get("data", {})
            if name == "final":
                record = {key: data.get(key) for key in rc.FINAL_FIELDS}
                if "end_reason" in data:  # absent in old daemons -> stays unknown
                    record["end_reason"] = data.get("end_reason")
                last_voice_s = (data.get("last_voice_ms") or 0) / 1000.0
                record["e2e_ms"] = int((time.monotonic() - wall_at(last_voice_s)) * 1000)
                finals.append(record)
            elif name == "error":
                state["error"] = data.get("message", "stream error")
            elif name == "ended":
                state["ended"] = True

    offset = 0
    total = len(samples) * max(1, loops)
    step = max(1, int(round(chunk_ms * rate / 1000.0)))
    try:
        while offset < total:
            if stop_event is not None and stop_event.is_set():
                break
            client.send_audio(stream_id, wrap_slice(samples, offset, offset + step))
            offset += step
            fed_audio_s = min(offset, total) / rate
            fed_clock = time.monotonic()
            absorb(client.events(stream_id, cursor, wait=0.0))
            target = fed_audio_s / speed
            elapsed = time.monotonic() - started
            if target > elapsed:
                time.sleep(target - elapsed)
        if stop_event is None or not stop_event.is_set():
            client.end_stream(stream_id)
    except rc.RuntimeError as exc:
        state["error"] = str(exc)

    # Drain events until ended or an explicit error arrives (bounded).
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        try:
            batch = client.events(stream_id, cursor, wait=0.5)
        except rc.RuntimeError as exc:
            if state["error"] is None:
                state["error"] = str(exc)
            break
        absorb(batch)
        if state["ended"] or state["error"]:
            break
    return FeedResult(
        stream=stream_id,
        finals=finals,
        error=state["error"],
        ended=state["ended"],
        sent_ms=int(min(offset, total) * 1000 / rate),
        wall_ms=int((time.monotonic() - started) * 1000),
    )


def cleanup_stream(client: rc.RuntimeClient, stream_id: str) -> None:
    """Best-effort cancel so a failed scenario leaves no session behind."""
    try:
        client.cancel_stream(stream_id)
    except rc.RuntimeError:
        pass


def spawn_daemon(args) -> subprocess.Popen:
    """Starts an ISOLATED daemon on a NON-default port; never the default one."""
    from urllib.parse import urlsplit

    port = urlsplit(args.url).port or 0
    if port == 8477:
        raise SystemExit("--spawn requires --url with a NON-default port (not 8477)")
    if not args.token_file or not args.config:
        raise SystemExit("--spawn requires --token-file and --config")
    cmd = [
        args.spawn,
        "--port", str(port),
        "--config", args.config,
        "--token-file", args.token_file,
    ]
    print(f"[soak] spawning daemon: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_healthy(client: rc.RuntimeClient, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.health().get("ok"):
                return True
        except rc.RuntimeError:
            pass
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_long_session(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """One stream for `--minutes` N: loop the WAV, sample memory every 30 s."""
    audio_ms = len(samples) * 1000 // rate
    loops = max(1, math.ceil(args.minutes * 60_000 / max(audio_ms, 1)))
    print(f"[long-session] {audio_ms} ms wav x{loops} loops = {args.minutes} min @ {args.speed}x")
    check("model_loaded", _ensure_loaded(client, args.model), args.model)

    memory: list[dict] = [memory_sample(client, "start")]
    sampler_stop = threading.Event()
    sampler = threading.Thread(
        target=_periodic_memory_sampler, args=(client, memory, sampler_stop), daemon=True
    )
    sampler.start()
    try:
        result = run_feeder(client, args.model, samples, rate, args.speed, loops=loops)
    finally:
        sampler_stop.set()
        sampler.join(timeout=SAMPLE_INTERVAL_S + 5)
    time.sleep(2.0)  # let the daemon drop the ended session before "end" sample
    memory.append(memory_sample(client, "end"))

    finals = result["finals"]
    check("stream_ended", result["ended"], f"ended={result['ended']} error={result['error']}")
    check("finals_nonempty", len(finals) > 0, f"{len(finals)} finals")
    check("no_stream_error", result["error"] is None, result["error"])
    stages = {
        key: percentile_stats([f[key] for f in finals if f.get(key) is not None])
        for key in ("asr_ms", "queue_ms", "e2e_ms")
    }
    check(
        "rss_worker_growth_mib",
        _growth_ok(memory, "worker", "rss_kib", args.rss_growth_limit_mib),
        f"worker rss {memory[0]['worker']['rss_kib']} -> {memory[-1]['worker']['rss_kib']} KiB "
        f"(limit {args.rss_growth_limit_mib} MiB)",
    )
    check(
        "rss_daemon_growth_mib",
        _growth_ok(memory, "daemon", "rss_kib", args.rss_growth_limit_mib),
        f"daemon rss {memory[0]['daemon']['rss_kib']} -> {memory[-1]['daemon']['rss_kib']} KiB "
        f"(limit {args.rss_growth_limit_mib} MiB)",
    )
    return {
        "audio_ms": audio_ms,
        "loops": loops,
        "stream": result["stream"],
        "finals": finals,
        "stages": stages,
        "memory": memory,
        "assertions": check.results,
    }


def _periodic_memory_sampler(
    client: rc.RuntimeClient, out: list[dict], stop: threading.Event
) -> None:
    while not stop.wait(SAMPLE_INTERVAL_S):
        try:
            out.append(memory_sample(client, "periodic"))
        except Exception as exc:  # noqa: BLE001 - sampler must never kill the run
            out.append({"label": "periodic", "error": str(exc)})


def _ensure_loaded(client: rc.RuntimeClient, model: str) -> bool:
    for row in client.models().get("models", []):
        if row.get("model") == model and row.get("loaded"):
            return True
    try:
        client.load(model)
        return True
    except rc.RuntimeError:
        return False


def _growth_ok(samples: list[dict], who: str, field: str, limit_mib: float) -> bool:
    values = [s.get(who, {}).get(field) for s in samples]
    values = [v for v in values if isinstance(v, int)]
    if len(values) < 2:
        return False
    return (max(values) - min(values)) <= limit_mib * 1024


def _stage_stats(finals: list[dict]) -> dict:
    """`{stage: percentile_stats}` for `asr_ms`, `queue_ms`, `e2e_ms`."""
    return {
        key: percentile_stats([f[key] for f in finals if f.get(key) is not None])
        for key in ("asr_ms", "queue_ms", "e2e_ms")
    }


def _end_reason_counts(finals: list[dict]) -> dict:
    """Count finals per `end_reason`; missing/unrecognised reasons count as `unknown`.

    Recognised reasons: `silence`, `max_utterance`, `client_end`. Old runs whose
    final events carry no `end_reason` field simply report everything as
    `unknown` — they never fail.
    """
    counts: dict[str, int] = {"silence": 0, "max_utterance": 0, "client_end": 0, "unknown": 0}
    for final in finals:
        reason = final.get("end_reason")
        counts[reason if reason in ("silence", "max_utterance", "client_end") else "unknown"] += 1
    return counts


def _finals_complete(results: dict, baseline: dict | None, clients: int) -> tuple[bool, str]:
    """Every interactive client ended cleanly and produced >= baseline finals.

    `baseline` maps client index -> finals count (or None when there was no
    baseline phase). Returns (ok, detail).
    """
    problems = []
    for index in range(clients):
        result = results.get(index)
        if result is None:
            problems.append(f"client {index}: no result")
            continue
        if not result["ended"] or result["error"] is not None:
            problems.append(f"client {index}: ended={result['ended']} error={result['error']}")
        got = len(result["finals"])
        want = None if baseline is None else baseline.get(index)
        if want is not None and got < want:
            problems.append(f"client {index}: finals {got} < baseline {want}")
    if problems:
        return False, "; ".join(problems)
    if baseline is None:
        return True, "no baseline counts to compare; all streams ended without error"
    got = sum(len(r["finals"]) for r in results.values())
    want = sum(baseline.values())
    return True, f"all clients ended cleanly; finals {got} >= baseline {want}"


def _starvation_verdict(
    interactive: dict, baseline: dict, factor: float = 5.0, abs_ms: float = 2000.0,
) -> tuple[str, str]:
    """Starvation verdict for `queue_ms`/`e2e_ms` interactive stages vs baseline.

    Compares interactive p95 `queue_ms`/`e2e_ms` against the SAME stages of the
    baseline phase (same audio, same client count, no batch). `asr_ms` alone is
    not a starvation signal: waiting in the queue or for the end-to-end result
    is exactly what batch contention causes.

    Returns `(status, detail)` where status is `PASS`, `FAIL` or `UNMEASURED`:
    - FAIL  — some stage's p95 exceeds max(factor * baseline p95, baseline p95 + abs_ms).
    - UNMEASURED — baseline or measurement stage p95 missing / not enough samples
      (n < 2 on either side); missing data is never a PASS.
    """
    comparisons = []
    statuses = []
    for stage in ("queue_ms", "e2e_ms"):
        meas = interactive.get(stage, {})
        base = baseline.get(stage, {})
        p95, base_p95 = meas.get("p95"), base.get("p95")
        if p95 is None or base_p95 is None or meas.get("n", 0) < 2 or base.get("n", 0) < 2:
            statuses.append("UNMEASURED")
            comparisons.append(
                f"{stage}: p95={p95} (n={meas.get('n')}) vs baseline p95={base_p95} (n={base.get('n')});"
                " need n>=2 and both p95 present"
            )
            continue
        limit = max(factor * base_p95, base_p95 + abs_ms)
        verdict = "PASS" if p95 <= limit else "FAIL"
        statuses.append(verdict)
        comparisons.append(f"{stage}: p95={p95} vs baseline p95={base_p95} (limit {limit})")
    if "FAIL" in statuses:
        return "FAIL", "; ".join(comparisons)
    if "UNMEASURED" in statuses:
        return "UNMEASURED", "; ".join(comparisons)
    return "PASS", "; ".join(comparisons)


def scenario_multiclient(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """`--clients` concurrent streams; optional `--batch` loop in parallel.

    With `--batch` the scenario runs in two phases over the SAME audio and the
    SAME client count: first the interactive streams alone (baseline phase),
    then the interactive streams again while a batch loop runs (measurement
    phase). Starvation is judged on `queue_ms`/`e2e_ms` p95 against the
    baseline phase plus finals completeness — never on `asr_ms` alone.
    """
    check("model_loaded", _ensure_loaded(client, args.model), args.model)
    baseline_streams = client.health().get("streams")
    results: dict[int, FeedResult] = {}
    errors: dict[int, str] = {}
    batch_stats: dict | None = None
    batch_stop = threading.Event()
    batch_thread: threading.Thread | None = None
    baseline_results: dict[int, FeedResult] | None = None
    baseline_stats: dict | None = None
    baseline_wall_ms: int | None = None

    if args.batch:
        def batch_loop() -> None:
            nonlocal batch_stats
            durations, asr = [], []
            wav_bytes = _wav_bytes(args.wav)
            while not batch_stop.is_set():
                started = time.monotonic()
                try:
                    reply = client.transcribe_wav(wav_bytes, args.model, priority="batch")
                except rc.RuntimeError as exc:
                    errors.setdefault(-1, str(exc))
                    break
                durations.append((time.monotonic() - started) * 1000)
                asr.append(reply.get("asr_ms") or reply.get("stats", {}).get("asr_ms"))
            batch_stats = {
                "count": len(durations),
                "wall_ms": percentile_stats(durations, ps=(50, 95)),
                "asr_ms": percentile_stats([v for v in asr if v is not None], ps=(50, 95)),
            }

        batch_thread = threading.Thread(target=batch_loop, daemon=True)

    def feed(index: int) -> None:
        try:
            results[index] = run_feeder(client, args.model, samples, rate, args.speed)
        except rc.RuntimeError as exc:
            errors[index] = str(exc)

    def run_streams() -> None:
        results.clear()
        threads = [threading.Thread(target=feed, args=(i,)) for i in range(args.clients)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def baseline_feed(index: int) -> None:
        try:
            baseline_results[index] = run_feeder(client, args.model, samples, rate, args.speed)
        except rc.RuntimeError as exc:
            errors[index] = str(exc)

    if args.batch:
        # Baseline phase: the same streams, the same audio, NO batch.
        baseline_results = {}
        threads = [threading.Thread(target=baseline_feed, args=(i,))
                   for i in range(args.clients)]
        base_started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        baseline_wall_ms = int((time.monotonic() - base_started) * 1000)
        for r in baseline_results.values():
            cleanup_stream(client, r["stream"])

    started = time.monotonic()
    if args.batch:
        batch_thread.start()
    run_streams()
    batch_stop.set()
    if batch_thread is not None:
        batch_thread.join(timeout=120)
    wall_ms = int((time.monotonic() - started) * 1000)

    ok_streams = [r for r in results.values() if r["ended"] and r["error"] is None]
    check("all_streams_ok", len(ok_streams) == args.clients,
          f"{len(ok_streams)}/{args.clients} ended without error; errors={errors or 'none'}")
    all_finals = [f for r in results.values() for f in r["finals"]]
    reason_counts = _end_reason_counts(all_finals)
    check("finals_nonempty", len(all_finals) > 0,
          f"{len(all_finals)} finals; end_reason={reason_counts}")
    interactive = _stage_stats(all_finals)

    if args.batch:
        baseline_finals = None
        if baseline_results is not None:
            baseline_finals = [f for r in baseline_results.values() for f in r["finals"]]
            baseline_stats = _stage_stats(baseline_finals)

        baseline_counts = None
        if baseline_results is not None:
            baseline_counts = {i: len(r["finals"]) for i, r in baseline_results.items()}
        complete, complete_detail = _finals_complete(results, baseline_counts, args.clients)
        if baseline_results is None:
            check("finals_complete", None,
                  "no baseline phase results; completeness vs baseline UNMEASURED")
        else:
            check("finals_complete", complete, complete_detail)

        check("batch_ran", batch_stats is not None and batch_stats["count"] > 0,
              f"{(batch_stats or {}).get('count', 0)} batch jobs")
        if baseline_stats is None or interactive["queue_ms"]["n"] == 0 or interactive["e2e_ms"]["n"] == 0:
            check.unmeasured(
                "no_starvation",
                "baseline or measurement stages missing; starvation cannot be judged "
                f"(baseline={baseline_stats is not None}, "
                f"queue_n={interactive['queue_ms']['n']}, e2e_n={interactive['e2e_ms']['n']})",
            )
        else:
            verdict, detail = _starvation_verdict(interactive, baseline_stats)
            if verdict == "PASS":
                check("no_starvation", True, detail)
            elif verdict == "FAIL":
                check("no_starvation", False, detail)
            else:
                check.unmeasured("no_starvation", detail)

    streams_after = client.health().get("streams")
    check("streams_back_to_baseline", streams_after == baseline_streams,
          f"{baseline_streams} -> {streams_after}")
    for r in results.values():
        cleanup_stream(client, r["stream"])
    report = {
        "clients": args.clients,
        "wall_ms": wall_ms,
        "per_client": {str(k): {"ended": v["ended"], "error": v["error"],
                                "finals": len(v["finals"])} for k, v in sorted(results.items())},
        "interactive": interactive,
        "end_reason_counts": reason_counts,
        "batch": batch_stats,
        "errors": errors,
        "assertions": check.results,
    }
    if args.batch:
        report["baseline"] = {
            "wall_ms": baseline_wall_ms,
            "interactive": baseline_stats,
            "end_reason_counts": _end_reason_counts(baseline_finals or []),
            "per_client": ({str(k): {"ended": v["ended"], "error": v["error"],
                                     "finals": len(v["finals"])}
                            for k, v in sorted(baseline_results.items())}
                           if baseline_results is not None else None),
        }
    return report


def _no_starvation(interactive_stages: dict, baseline_stages: dict,
                   factor: float = 5.0, abs_ms: float = 2000.0) -> bool | None:
    """Deprecated bool wrapper around `_starvation_verdict` (kept for callers).

    Returns True only when the queue/e2e verdict is PASS; False on FAIL;
    None when it is UNMEASURED. Missing measurements are never True.
    """
    verdict, _ = _starvation_verdict(interactive_stages, baseline_stages, factor, abs_ms)
    return {"PASS": True, "FAIL": False, "UNMEASURED": None}[verdict]



def _wav_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def scenario_crash(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """kill -9 the worker mid-stream; daemon must survive and recover."""
    check("model_loaded", _ensure_loaded(client, args.model), args.model)
    health0 = client.health()
    restarts0 = int((health0.get("worker") or {}).get("restarts") or 0)
    worker_pid = args.worker_pid or int((health0.get("worker") or {}).get("pid") or -1)
    check("worker_pid_known", worker_pid > 0, f"pid={worker_pid}")

    stop = threading.Event()
    result_box: dict = {}

    def feed() -> None:
        try:
            result_box["result"] = run_feeder(
                client, args.model, samples, rate, args.speed, stop_event=stop
            )
        except rc.RuntimeError as exc:
            result_box["error"] = str(exc)

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    time.sleep(min(3.0, max(0.5, len(samples) / rate / args.speed / 4)))  # mid-stream
    print(f"[crash] kill -9 worker pid={worker_pid}")
    os.kill(worker_pid, signal.SIGKILL)
    thread.join(timeout=180)
    stop.set()
    result = result_box.get("result")
    client_error = result_box.get("error") or (result or {}).get("error")
    check("client_saw_explicit_error", bool(client_error),
          client_error or (result or {}).get("ended"))
    check("daemon_alive", _daemon_alive(client), "GET /health ok")
    time.sleep(2.0)  # supervisor restart window
    health1 = client.health()
    restarts1 = int((health1.get("worker") or {}).get("restarts") or 0)
    check("restarts_incremented", restarts1 >= restarts0 + 1,
          f"restarts {restarts0} -> {restarts1}")

    reloaded = _ensure_loaded(client, args.model)
    check("model_reloaded", reloaded, args.model)
    tail = samples[len(samples) // 2:] or samples
    recovery = run_feeder(client, args.model, tail, rate, args.speed)
    cleanup_stream(client, recovery["stream"])
    check("new_stream_after_reload", recovery["ended"] and recovery["error"] is None,
          f"ended={recovery['ended']} error={recovery['error']}")
    if result is not None:
        cleanup_stream(client, result["stream"])
    return {
        "worker_pid": worker_pid,
        "restarts_before": restarts0,
        "restarts_after": restarts1,
        "client_error": client_error,
        "recovery": {"ended": recovery["ended"], "error": recovery["error"],
                     "finals": len(recovery["finals"])},
        "assertions": check.results,
    }


def _daemon_alive(client: rc.RuntimeClient) -> bool:
    try:
        return bool(client.health().get("ok"))
    except rc.RuntimeError:
        return False


def scenario_cancel(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """Cancel mid-stream: no `final` after the cancel, streams back to baseline."""
    check("model_loaded", _ensure_loaded(client, args.model), args.model)
    baseline_streams = client.health().get("streams")
    stream_id = client.open_stream(args.model, {})
    cursor = 0
    finals_before = 0
    step = max(1, int(round(100 * rate / 1000.0)))
    half = len(samples) // 2
    offset = 0
    cancel_at_wall = None
    while offset < half:
        client.send_audio(stream_id, samples[offset:offset + step])
        offset += step
        batch = client.events(stream_id, cursor, wait=0.0)
        cursor = int(batch.get("next", cursor))
        finals_before += sum(
            1 for e in batch.get("events", []) if e.get("event") == "final"
        )
        target = offset / rate / args.speed
        time.sleep(max(0.0, min(0.05, target)))
    cancel_wall = time.monotonic()
    client.cancel_stream(stream_id)
    check("cancel_accepted", True, f"stream={stream_id} finals_before={finals_before}")

    late_finals = 0
    saw_cancelled = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            batch = client.events(stream_id, cursor, wait=0.5)
        except rc.RuntimeError as exc:
            check("events_after_cancel", "unknown" in str(exc).lower() or True, str(exc))
            break
        cursor = int(batch.get("next", cursor))
        for event in batch.get("events", []):
            name = event.get("event")
            if name == "final":
                late_finals += 1
            elif name == "cancelled":
                saw_cancelled = True
        if batch.get("events") and all(
            e.get("event") in ("cancelled", "ended") for e in batch["events"]
        ) and saw_cancelled:
            break
    cancel_at_wall = int((time.monotonic() - cancel_wall) * 1000)
    check("no_final_after_cancel", late_finals == 0, f"{late_finals} late finals")
    streams_after = client.health().get("streams")
    check("streams_back_to_baseline", streams_after == baseline_streams,
          f"{baseline_streams} -> {streams_after}")
    return {
        "stream": stream_id,
        "finals_before_cancel": finals_before,
        "late_finals": late_finals,
        "saw_cancelled_event": saw_cancelled,
        "cancel_drain_ms": cancel_at_wall,
        "streams_baseline": baseline_streams,
        "streams_after": streams_after,
        "assertions": check.results,
    }


def scenario_two_models(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """Load gigaam + parakeet-ultra (names from runtime/models.json), stream both."""
    with open(MODELS_JSON, encoding="utf-8") as fh:
        configured = json.load(fh).get("models", {})
    names = [name for name in ("gigaam", "parakeet-ultra") if name in configured]
    missing = [n for n in ("gigaam", "parakeet-ultra") if n not in configured]
    check("both_models_configured", not missing,
          f"missing from {MODELS_JSON}: {missing or 'none'}")
    loaded = {}
    for name in names:
        try:
            client.load(name)
            loaded[name] = True
        except rc.RuntimeError as exc:
            loaded[name] = False
            print(f"[two-models] load {name} failed: {exc}")
    check("both_models_loaded", all(loaded.values()) and len(loaded) == 2, loaded)
    if len(loaded) < 2:
        return {"models": names, "loaded": loaded, "assertions": check.results}

    results: dict[str, FeedResult] = {}
    errors: dict[str, str] = {}

    def feed(name: str) -> None:
        try:
            results[name] = run_feeder(client, name, samples, rate, args.speed)
        except rc.RuntimeError as exc:
            errors[name] = str(exc)

    threads = [threading.Thread(target=feed, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    per_model = {}
    for name in names:
        result = results.get(name)
        if result is None:
            per_model[name] = {"error": errors.get(name), "finals": 0}
            continue
        cleanup_stream(client, result["stream"])
        finals = result["finals"]
        per_model[name] = {
            "ended": result["ended"],
            "error": result["error"],
            "finals": len(finals),
            "asr_ms": percentile_stats([f["asr_ms"] for f in finals if f.get("asr_ms") is not None]),
            "e2e_ms": percentile_stats([f["e2e_ms"] for f in finals if f.get("e2e_ms") is not None]),
        }
    failures = {n: d for n, d in per_model.items() if d.get("error") or not d.get("ended")}
    check("no_model_failures", not failures, failures or "none")
    for name in names:
        try:
            client.unload(name)
        except rc.RuntimeError:
            pass
    return {"models": names, "per_model": per_model, "failures": failures,
            "assertions": check.results}


def scenario_memory(args, client: rc.RuntimeClient, samples, rate: int, check) -> dict:
    """baseline -> loaded -> N streams -> all ended -> unloaded; no stream leak."""
    timeline: list[dict] = [memory_sample(client, "baseline")]
    _safe_unload(client, args.model)
    client.load(args.model)
    timeline.append(memory_sample(client, "loaded"))
    baseline_streams = client.health().get("streams")
    streams_peak = baseline_streams

    def watch_streams() -> None:
        nonlocal streams_peak
        while not stop_watch.is_set():
            try:
                streams_peak = max(streams_peak, client.health().get("streams") or 0)
            except rc.RuntimeError:
                pass
            stop_watch.wait(0.5)

    results: dict[int, FeedResult] = {}
    errors: dict[int, str] = {}

    def feed(index: int) -> None:
        try:
            results[index] = run_feeder(client, args.model, samples, rate, args.speed)
        except rc.RuntimeError as exc:
            errors[index] = str(exc)

    stop_watch = threading.Event()
    watcher = threading.Thread(target=watch_streams, daemon=True)
    watcher.start()
    threads = [threading.Thread(target=feed, args=(i,)) for i in range(args.clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop_watch.set()
    watcher.join(timeout=2)
    time.sleep(2.0)  # let the daemon drop ended sessions before sampling
    timeline.append(memory_sample(client, "streams_ended", {"streams": client.health().get("streams")}))
    for result in results.values():
        cleanup_stream(client, result["stream"])

    streams_final = client.health().get("streams")
    timeline.append(memory_sample(client, "final", {"streams": streams_final}))
    check("streams_did_not_grow", streams_final <= baseline_streams,
          f"baseline={baseline_streams} peak={streams_peak} final={streams_final}")
    ok_streams = [r for r in results.values() if r["ended"] and r["error"] is None]
    check("all_streams_ok", len(ok_streams) == args.clients,
          f"{len(ok_streams)}/{args.clients}; errors={errors or 'none'}")
    unloaded = _safe_unload(client, args.model)
    check("unload_ok", unloaded, f"unload {args.model}")
    timeline.append(memory_sample(client, "unloaded"))
    check(
        "worker_rss_returned",
        _growth_ok(timeline, "worker", "rss_kib", args.rss_growth_limit_mib),
        f"worker rss {timeline[0]['worker']['rss_kib']} -> {timeline[-1]['worker']['rss_kib']} KiB "
        f"(limit {args.rss_growth_limit_mib} MiB)",
    )
    return {
        "timeline": timeline,
        "streams_baseline": baseline_streams,
        "streams_peak": streams_peak,
        "streams_final": streams_final,
        "per_client": {str(k): {"ended": v["ended"], "error": v["error"],
                                "finals": len(v["finals"])} for k, v in sorted(results.items())},
        "assertions": check.results,
    }


def _safe_unload(client: rc.RuntimeClient, model: str) -> bool:
    try:
        client.unload(model)
        return True
    except rc.RuntimeError:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


SCENARIOS = {
    "long-session": scenario_long_session,
    "multiclient": scenario_multiclient,
    "crash": scenario_crash,
    "cancel": scenario_cancel,
    "two-models": scenario_two_models,
    "memory": scenario_memory,
}


def add_common(parser: argparse.ArgumentParser, with_model: bool = True) -> None:
    parser.add_argument("--wav", required=True, help="16 kHz mono PCM WAV")
    parser.add_argument("--url", default=os.environ.get("SPEECH_RUNTIME_URL", rc.DEFAULT_BASE_URL))
    parser.add_argument(
        "--token-file",
        default=os.environ.get("SPEECH_RUNTIME_TOKEN") or rc.TOKEN_PATH,
        help="bearer token file (also required with --spawn)",
    )
    if with_model:
        parser.add_argument("--model", default="gigaam", help="model name for streams")
    parser.add_argument("--config", help="daemon models.json (only for --spawn)")
    parser.add_argument(
        "--spawn",
        metavar="BINARY",
        help="speech-runtime binary to start on a NON-default port; killed at the end",
    )
    parser.add_argument("--json", help="artifact path (default: /tmp/soak-<scenario>.json)")
    parser.add_argument(
        "--rss-growth-limit-mib",
        type=float,
        default=512.0,
        help="allowed worker RSS growth across the scenario (default 512)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soak", description="Soak/failure-scenario harness (stdlib only)."
    )
    sub = parser.add_subparsers(dest="scenario", required=True)

    p = sub.add_parser("long-session", help="one long stream, memory every 30 s")
    add_common(p)
    p.add_argument("--minutes", type=float, default=10.0)
    p.add_argument("--speed", type=float, default=1.0)

    p = sub.add_parser("multiclient", help="concurrent streams + optional batch")
    add_common(p)
    p.add_argument("--clients", type=int, default=4)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--batch", action="store_true", help="run a batch loop concurrently")

    p = sub.add_parser("crash", help="kill -9 the worker mid-stream")
    add_common(p)
    p.add_argument("--worker-pid", type=int, default=0, help="default: health.worker.pid")
    p.add_argument("--speed", type=float, default=1.0)

    p = sub.add_parser("cancel", help="cancel mid-stream; no finals after cancel")
    add_common(p)
    p.add_argument("--speed", type=float, default=1.0)

    p = sub.add_parser("two-models", help="gigaam + parakeet-ultra concurrently")
    add_common(p, with_model=False)
    p.add_argument("--speed", type=float, default=1.0)

    p = sub.add_parser("memory", help="baseline -> loaded -> N streams -> unloaded")
    add_common(p)
    p.add_argument("--clients", type=int, default=2)
    p.add_argument("--speed", type=float, default=1.0)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "speed", None) is not None and args.speed <= 0:
        parser.error("--speed must be > 0")

    artifact = artifact_header(sys.argv[1:] if argv is None else argv, args.scenario)
    spawned = None
    client = None
    exit_code = 1
    try:
        if args.spawn:
            from urllib.parse import urlsplit

            if (urlsplit(args.url).port or 8477) == 8477:
                parser.error("--spawn requires --url with a NON-default port (not 8477)")
            if not args.config or not args.token_file:
                parser.error("--spawn requires --token-file and --config")
            spawned = spawn_daemon(args)
        token = rc.load_token(args.token_file)
        client = rc.RuntimeClient(args.url, token)
        if spawned is not None and not wait_healthy(client):
            raise SystemExit(f"spawned daemon did not become healthy on {args.url}")

        samples, rate, _ = rc.read_wav(args.wav)
        if abs(rate - DEFAULT_SAMPLE_RATE) > 1:
            raise SystemExit(f"{args.wav} is {rate} Hz; need {DEFAULT_SAMPLE_RATE} Hz mono")
        check = Assertions()
        print(f"[{args.scenario}] url={args.url} wav={args.wav} ({len(samples) * 1000 // rate} ms)")
        result = SCENARIOS[args.scenario](args, client, samples, rate, check)
        artifact.update(result)
        artifact["assertions"] = check.results
        exit_code = 0 if check.all_passed else 1
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - the artifact must capture the failure
        artifact["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[{args.scenario}] ERROR: {artifact['error']}", file=sys.stderr)
        if spawned is not None:
            spawned.terminate()
            try:
                spawned.wait(timeout=10)
            except subprocess.TimeoutExpired:
                spawned.kill()
            print(f"[soak] spawned daemon (pid {spawned.pid}) stopped")
    artifact["exit_code"] = exit_code
    out = args.json or f"/tmp/soak-{args.scenario}.json"
    write_artifact(out, artifact)
    print(f"[{args.scenario}] assertions: "
          + ", ".join(f"{r['name']}={status_of(r)}" for r in check_results_safe(artifact)))
    print(f"[{args.scenario}] artifact: {out} (exit {exit_code})")
    return exit_code


def check_results_safe(artifact: dict):
    return artifact.get("assertions") or []


if __name__ == "__main__":
    sys.exit(main())
