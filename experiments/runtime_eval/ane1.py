#!/usr/bin/env python3
"""ANE-1: per-process memory accounting across the model lifecycle.

Runs the fixed sequence from RESEARCH.md §4.3 against a real daemon + worker:

    1. fresh worker, no model
    2. load (cold start)
    3. first inference
    4. N repeats of one input
    5. inputs of different durations, same weights
    6. unload
    7. idle 15 / 60 / 300 s
    8. worker killed -> new worker process, load + inference again
    9. two models loaded, then unload one, then both

At every point both processes are sampled *externally* (tools/memory_probe,
proc_pid_rusage V6): resident, phys_footprint, current neural footprint,
neural lifetime/interval maxima, plus system compressor/swap as background.
Nothing is summed: RSS, footprint and neural overlap, so each is reported on
its own.

Visibility boundary: the probe can read rusage for another same-user process
but task_vm_info only for itself, so `task_vm_info` values are absent for the
daemon/worker: "process neural counters" != "every byte of ANE use on the Mac".

usage:
  python3 -m runtime_eval.ane1 [--quick] [--repeats N] [--port 8588] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from .memprobe import PROBE, probe, system_background

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "runtime/daemon/.build/release/speech-runtime"
WORKER = REPO / "runtime/worker/target/release/sr-worker"
CONFIG = REPO / "runtime/models.json"
SHORT = REPO / ("experiments/corpus/local/tools/FluidAudio/Tests/FluidAudioTests/"
                "ASR/Parakeet/SlidingWindow/Fixtures/03-diff-explanation-16.9s.wav")
TOKEN = "ane1-token"


# ---------------------------------------------------------------- sampling

# ---------------------------------------------------------------- HTTP

def call(path: str, body=None, method="GET", timeout=1800):
    url = f"http://127.0.0.1:{PORT}{path}"
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    if data:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def transcribe(pcm: bytes, model: str = "gigaam", priority: str = "batch"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/audio/transcriptions?model={model}&priority={priority}",
        data=pcm, method="POST")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("Content-Type", "application/octet-stream")
    request.add_header("x-sample-format", "s16le")
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read())


# ---------------------------------------------------------------- audio

def read_pcm(path: Path, seconds: float | None = None) -> bytes:
    with wave.open(str(path)) as handle:
        assert handle.getframerate() == 16000, f"{path} is not 16 kHz"
        assert handle.getnchannels() == 1, f"{path} is not mono"
        frames = handle.readframes(handle.getnframes())
    if seconds is None:
        return frames
    return frames[: int(seconds * 16000) * 2]


# ---------------------------------------------------------------- daemon

class Daemon:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.process: subprocess.Popen | None = None

    def start(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(self.log_path, "w")
        self.process = subprocess.Popen(
            [str(DAEMON), "--port", str(PORT), "--config", str(CONFIG),
             "--token-file", str(self.log_path.parent / "token"),
             "--worker", str(WORKER), "--idle-unload", "99999"],
            stdout=self.log, stderr=subprocess.STDOUT)
        (self.log_path.parent / "token").write_text(TOKEN)
        for _ in range(120):
            try:
                call("/health", timeout=2)
                return self.process.pid
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"daemon did not come up; see {self.log_path}")

    def worker_pid(self) -> int | None:
        out = subprocess.run(["pgrep", "-P", str(self.process.pid)],
                             capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if getattr(self, "log", None):
            self.log.close()


# ---------------------------------------------------------------- sequence

POINTS: list[dict] = []


def record(label: str, daemon: Daemon, note: str = "") -> dict:
    dpid = daemon.process.pid
    wpid = daemon.worker_pid()
    point = {
        "step": label,
        "t_s": round(time.time() - START, 1),
        "note": note,
        "daemon": probe(dpid),
        "worker": probe(wpid) if wpid else {"error": "no worker child"},
        "system": system_background(),
    }
    POINTS.append(point)
    w = point["worker"]
    print(f"  {label:<28} worker rss={w.get('ri_resident_size')} "
          f"footprint={w.get('ri_physical_footprint_bytes')} "
          f"neural={w.get('ri_neural_footprint')} "
          f"neural_peak={w.get('ri_lifetime_max_neural_footprint')}")
    return point


def run(daemon: Daemon, repeats: int, idle_seconds: list[int]) -> dict:
    summary: dict = {"notes": [], "transcribe": []}
    pcm_short = read_pcm(SHORT, 4.0)
    pcm_medium = read_pcm(SHORT, 8.0)
    pcm_long = read_pcm(SHORT)

    record("1-fresh-no-model", daemon)

    started = time.time()
    load = call("/v1/models/load", {"model": "gigaam", "backend": "ane"}, method="POST")
    summary["load"] = load
    summary["notes"].append(f"cold load {round((time.time() - started) * 1000)} ms")
    record("2-loaded", daemon, "cold start")

    first = transcribe(pcm_short)
    summary["first"] = {k: v for k, v in first.items() if k != "text"}
    record("3-first-inference", daemon)

    for index in range(repeats):
        result = transcribe(pcm_short)
        if index == 0:
            summary["transcribe"].append({"repeat": 0, **{k: v for k, v in result.items() if k != "text"}})
    record(f"4-{repeats}-repeats", daemon, f"{repeats}x {len(pcm_short)/32000:.1f}s input")

    for label, pcm in (("short-4s", pcm_short), ("medium-8s", pcm_medium), ("long-16.9s", pcm_long)):
        result = transcribe(pcm)
        summary["transcribe"].append({"input": label, **{k: v for k, v in result.items() if k != "text"}})
    record("5-varied-lengths", daemon)

    call("/v1/models/unload", {"model": "gigaam"}, method="POST")
    record("6-unloaded", daemon)

    for seconds in idle_seconds:
        time.sleep(seconds)
        record(f"7-idle-{seconds}s", daemon)

    old_worker = daemon.worker_pid()
    os.kill(old_worker, signal.SIGKILL)
    for _ in range(80):
        time.sleep(0.25)
        new_worker = daemon.worker_pid()
        if new_worker and new_worker != old_worker:
            break
    else:
        raise RuntimeError("worker did not restart after SIGKILL")
    summary["worker_restart"] = {"old": old_worker, "new": new_worker}
    record("8-new-worker-no-model", daemon, f"old pid {old_worker} replaced by {new_worker}")
    call("/v1/models/load", {"model": "gigaam", "backend": "ane"}, method="POST")
    transcribe(pcm_short)
    record("8b-new-worker-loaded", daemon)

    call("/v1/models/load", {"model": "parakeet-ultra", "backend": "ane"}, method="POST")
    transcribe(pcm_short, model="parakeet-ultra")
    record("9-two-models", daemon)
    call("/v1/models/unload", {"model": "gigaam"}, method="POST")
    record("9b-one-unloaded", daemon)
    call("/v1/models/unload", {"model": "parakeet-ultra"}, method="POST")
    record("9c-both-unloaded", daemon)

    final = call("/health")
    record("10-final-health", daemon, "health itself performs IPC (worker.stats)")
    summary["health"] = {k: final.get(k) for k in ("ok", "uptime_s", "memory", "worker")}
    return summary


START = time.time()
PORT = 8588


def main(argv=None) -> int:
    global PORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="fewer repeats, short idle")
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--port", type=int, default=8588)
    parser.add_argument("--out", type=Path, default=REPO / "experiments/runtime_eval/results")
    options = parser.parse_args(argv)
    PORT = options.port
    repeats = 5 if options.quick else options.repeats
    idle = [2, 3] if options.quick else [15, 60, 300]

    for required in (PROBE, DAEMON, WORKER, CONFIG, SHORT):
        if not required.exists():
            print(f"missing prerequisite: {required}", file=sys.stderr)
            return 2

    options.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = options.out / f"ane1-{stamp}.log"

    daemon = Daemon(log_path)
    daemon.start()
    print(f"daemon pid={daemon.process.pid} worker pid={daemon.worker_pid()} port={PORT}")
    try:
        summary = run(daemon, repeats, idle)
    finally:
        daemon.stop()

    report = {
        "schema": "ane1/1",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip(),
        "audio": str(SHORT.relative_to(REPO)),
        "repeats": repeats,
        "idle_seconds": idle,
        "visibility_boundary": (
            "task_vm_info is only readable for the probe's own process; daemon/worker "
            "numbers come from proc_pid_rusage RUSAGE_INFO_V6 (same-user pids, no root). "
            "Process neural counters are not whole-system ANE usage."
        ),
        "points": POINTS,
        "summary": summary,
    }
    path = options.out / f"ane1-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
