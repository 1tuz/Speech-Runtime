#!/usr/bin/env python3
"""Memory localization matrix M0/M1/M3 from RESEARCH.md §3.2.

Each mode runs identical series on ONE daemon process, with a quiet period between
series, and samples once per second (external probe + `/health` counters):

  M0  no model, idle                    -> background growth
  M1  50 rps JSON 404 (no worker IPC)   -> HTTP, connections, parser
  M3  50 rps `/health` (worker IPC)     -> HTTP + IPC

Acceptance (RESEARCH §6.3): the stabilized footprint after series 2 and series 3
must differ by no more than 32 MiB, and counters must return to baseline. A
smaller footprint right after a restart proves nothing — only equal series do.

    python3 -m runtime_eval.memory_matrix --mode all --out results
    python3 -m runtime_eval.memory_matrix --mode M1 --series 2 --seconds 20 --cooldown 5
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .memprobe import FIELDS, probe, selected

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "runtime/daemon/.build/release/speech-runtime"
WORKER = REPO / "runtime/worker/target/release/sr-worker"
CONFIG = REPO / "runtime/models.json"
RESULTS = Path(__file__).resolve().parent / "results"

STABILIZE_MIB = 32.0




class Daemon:
    """Isolated daemon on its own port; the token file never lands in results/.

    `workdir` holds only the daemon log; the bearer token is written in a
    private TemporaryDirectory that is removed in `stop()`.
    """

    def __init__(self, port: int, workdir: Path, quick_unload: bool = False) -> None:
        self.port = port
        self.workdir = workdir
        self.token = "matrix-token"
        self.out = ""
        workdir.mkdir(parents=True, exist_ok=True)
        self.token_dir = tempfile.TemporaryDirectory(prefix=f"matrix-{port}-")
        token_file = Path(self.token_dir.name) / "token"
        token_file.write_text(self.token)
        log = (workdir / f"daemon-{port}.log").open("w")
        self.proc = subprocess.Popen(
            [
                str(DAEMON),
                "--port", str(port),
                "--config", str(CONFIG),
                "--token-file", str(token_file),
                "--worker", str(WORKER),
                "--idle-unload", "1" if quick_unload else "99999",
            ],
            stdout=log, stderr=subprocess.STDOUT, cwd=str(REPO),
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"daemon exited: see {workdir}/daemon-{port}.log")
            try:
                self.health()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("daemon did not answer /health")

    def request(self, path: str, method: str = "GET", body: bytes | None = None, timeout: float = 10):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())

    def health(self) -> dict:
        return self.request("/health")

    def worker_pid(self) -> int | None:
        try:
            children = subprocess.run(["pgrep", "-P", str(self.proc.pid)], capture_output=True, text=True).stdout.split()
        except OSError:
            return None
        return int(children[0]) if children else None

    def stop(self) -> None:
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.token_dir.cleanup()



@dataclass
class Sampler:
    daemon: Daemon
    health: bool = True
    rows: list[dict] = field(default_factory=list)
    stop_flag: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    index: int = 0

    def start(self, label: str) -> None:
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._loop, args=(label,), daemon=True)
        self.thread.start()

    def _loop(self, label: str) -> None:
        while not self.stop_flag.is_set():
            self.index += 1
            self.rows.append(measure(self.daemon, label, self.index, self.health))
            self.stop_flag.wait(1.0)

    def mark(self, label: str) -> None:
        """Relabel subsequent samples: stops the loop, drains, restarts with the new label."""
        self.stop(label)
        self.start(label)

    def stop(self, label: str = "final") -> None:
        if self.thread is not None:
            self.stop_flag.set()
            self.thread.join(timeout=15)
        self.index += 1
        self.rows.append(measure(self.daemon, label, self.index, self.health))


def measure(daemon: Daemon, label: str, sample_index: int = 0, health: bool = True) -> dict:
    row: dict = {"t": time.time(), "label": label}
    daemon_pid = daemon.proc.pid
    row["daemon"] = selected(probe(daemon_pid))
    worker_pid = daemon.worker_pid()
    row["worker_pid"] = worker_pid
    if worker_pid:
        row["worker"] = selected(probe(worker_pid))
    if health:
        try:
            report = daemon.health()
        except (OSError, ValueError):
            row["health_error"] = True
            return row
        row["counters"] = {
            "worker": {key: report.get("worker", {}).get(key) for key in
                       ("pending_replies", "event_waiters", "session_buffers", "retired_sessions",
                        "restarts", "sessions")},
            "streams": report.get("streams"),
            "memory": report.get("memory"),
        }
    row["fds"] = count_fds(daemon_pid) if sample_index % 30 == 0 else None
    row["threads"] = count_threads(daemon_pid)
    return row


def count_fds(pid: int) -> int | None:
    """Open descriptors. `lsof` costs ~100 ms, so callers sample it at a reduced cadence."""
    try:
        out = subprocess.run(["lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return None
    return sum(1 for line in out.stdout.splitlines() if line.startswith("f"))


def count_threads(pid: int) -> int | None:
    try:
        out = subprocess.run(["ps", "-o", "thcount=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip()) if out.stdout.strip() else None
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def burst(daemon: Daemon, path: str, rps: float, seconds: float, threads: int = 8) -> dict:
    """Fixed-rate load. Returns achieved rate and a status histogram."""
    total = int(rps * seconds)
    done = 0
    errors: dict[str, int] = {}
    lock = threading.Lock()
    start = time.time()

    def one() -> None:
        nonlocal done
        try:
            daemon.request(path, timeout=10)
            with lock:
                done += 1
        except urllib.error.HTTPError as error:
            with lock:
                done += 1
                errors[f"http_{error.code}"] = errors.get(f"http_{error.code}", 0) + 1
        except (OSError, ValueError) as error:
            with lock:
                errors[type(error).__name__] = errors.get(type(error).__name__, 0) + 1

    def worker(index: int) -> None:
        next_at = start + index / rps
        while next_at < start + seconds:
            delay = next_at - time.time()
            if delay > 0:
                time.sleep(delay)
            one()
            next_at += threads / rps

    pool = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    elapsed = time.time() - start
    return {"requests": done, "errors": errors, "seconds": round(elapsed, 2),
            "achieved_rps": round(done / elapsed, 1), "target": f"{rps} rps x {seconds}s (ask {total})"}


def stabilized(rows: list[dict], label: str) -> dict:
    """Median of the last 30 samples of a label: the quiet, settled value."""
    return {"daemon": process_settled(rows, label, "daemon"),
            "worker": process_settled(rows, label, "worker")}


def process_settled(rows: list[dict], label: str, who: str, samples: int = 30) -> dict:
    """Settled footprint of one process in a labelled window.

    Both processes are reported separately: a single combined number cannot say whether a
    grown daemon was holding a worker's data, and `ri_lifetime_max_*` is a peak that never
    decreases, so only the current footprint may be compared between series.
    """
    window = [row for row in rows if row["label"] == label][-samples:]
    values = sorted(row.get(who, {}).get("ri_physical_footprint_bytes") for row in window)
    values = [value for value in values if isinstance(value, int)]
    rss = sorted(row.get(who, {}).get("ri_resident_size") for row in window
                 if isinstance(row.get(who, {}).get("ri_resident_size"), int))
    return {"footprint_mib": None if not values else round(values[len(values) // 2] / 2**20, 1),
            "rss_mib": None if not rss else round(rss[len(rss) // 2] / 2**20, 1),
            "samples": len(window)}


def series(daemon: Daemon, mode: str, path: str, series_count: int, rps: float, seconds: float,
           cooldown: float, health: bool = True) -> dict:
    sampler = Sampler(daemon, health=health)
    sampler.start("baseline")
    time.sleep(5)
    runs = []
    for index in range(series_count):
        sampler.mark(f"series{index + 1}-load")
        stats = burst(daemon, path, rps, seconds)
        # The quiet period *is* the cooldown: one label covers settling, and its tail is the
        # "stabilized" value the acceptance criterion compares between series 2 and 3.
        sampler.mark(f"series{index + 1}-settle")
        time.sleep(cooldown)
        runs.append({"series": index + 1, "load": stats, "settled": stabilized(sampler.rows, f"series{index + 1}-settle"),
                     "settled_baseline": stabilized(sampler.rows, "baseline")})
    sampler.stop("end")
    runs[-1]["counters_final"] = sampler.rows[-1].get("counters")
    runs[-1]["counters_samples"] = sum(1 for row in sampler.rows if row.get("counters"))
    return {"mode": mode, "path": path, "series": runs, "rows": sampler.rows}


def verdict(runs: list[dict]) -> dict:
    """Both processes must stop growing: a flat daemon in front of a growing worker is a FAIL."""
    if len(runs) < 3:
        return {"status": "UNMEASURED", "detail": "need 3 series to compare settled series 2 and 3"}
    second = runs[1]["settled"]
    third = runs[2]["settled"]
    deltas = {}
    for who in ("daemon", "worker"):
        a, b = second[who]["footprint_mib"], third[who]["footprint_mib"]
        if a is None or b is None:
            return {"status": "UNMEASURED", "detail": f"{who} footprint missing"}
        deltas[who] = round(b - a, 1)
    grown = [who for who, delta in deltas.items() if abs(delta) > STABILIZE_MIB]
    status = "FAIL" if grown else "PASS"
    detail = (f"settled footprint delta series3-series2: "
              + ", ".join(f"{who}={deltas[who]} MiB" for who in ("daemon", "worker")))
    if grown:
        detail += f" (limit {STABILIZE_MIB} MiB, growing: {', '.join(grown)})"
    return {"status": status, "delta_mib": deltas, "limit_mib": STABILIZE_MIB, "detail": detail}


def run_mode(mode: str, port: int, options) -> dict:
    workdir = Path(options.out) / f"matrix-{mode}"
    daemon = Daemon(port, workdir)
    try:
        time.sleep(3)
        if mode == "M0":
            sampler = Sampler(daemon)
            sampler.start("M0-idle")
            time.sleep(options.seconds)
            sampler.stop("M0-end")
            # The idle control answers one question: does a daemon that serves nothing drift?
            # Compare the ends of the idle window instead of demanding two load series.
            idle = [row for row in sampler.rows if row["label"] == "M0-idle"]
            drift = {}
            for who in ("daemon", "worker"):
                head = process_settled(idle[:30], "M0-idle", who)
                tail = process_settled(idle, "M0-idle", who)
                first, last = head["footprint_mib"], tail["footprint_mib"]
                if first is None or last is None:
                    return {"mode": "M0", "rows": sampler.rows, "verdict": {
                        "status": "UNMEASURED", "detail": f"{who} footprint missing"}}
                drift[who] = round(last - first, 1)
            status = "PASS" if all(abs(d) <= STABILIZE_MIB for d in drift.values()) else "FAIL"
            return {"mode": "M0", "rows": sampler.rows, "verdict": {
                "status": status, "drift_mib": drift, "limit_mib": STABILIZE_MIB,
                "detail": f"idle drift first30->last30 of {len(idle)} samples: "
                          + ", ".join(f"{who}={drift[who]} MiB" for who in ("daemon", "worker"))}}
        path = "/no-such-route" if mode == "M1" else "/health"
        # M1 isolates HTTP/parser/connection cost, so its sampler must not add worker IPC.
        result = series(daemon, mode, path, options.series, options.rps, options.seconds,
                        options.cooldown, health=(mode != "M1"))
        result["verdict"] = verdict(result["series"])
        return result
    finally:
        daemon.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="all", choices=["M0", "M1", "M3", "all"])
    parser.add_argument("--series", type=int, default=3)
    parser.add_argument("--rps", type=float, default=50)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--cooldown", type=float, default=360)
    parser.add_argument("--port", type=int, default=8591)
    parser.add_argument("--out", default=str(RESULTS))
    options = parser.parse_args(argv)
    if not DAEMON.exists() or not WORKER.exists():
        print("build the daemon (swift build -c release) and worker (cargo build --release) first", file=sys.stderr)
        return 2
    modes = ["M0", "M1", "M3"] if options.mode == "all" else [options.mode]
    report = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "options": vars(options),
              "epoch": {"note": "each mode uses its own daemon process; series share one process"}}
    for index, mode in enumerate(modes):
        print(f"== {mode}", flush=True)
        report[mode] = run_mode(mode, options.port + index, options)
        print(f"   verdict: {report[mode]['verdict']}", flush=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(options.out) / f"memory-matrix-{stamp}.json"
    path.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {path}")
    for mode in modes:
        entry = report[mode]
        for run in entry.get("series", []):
            print(f"  {mode} series{run['series']}: {run['settled']} baseline={run['settled_baseline']} "
                  f"load={run['load']['achieved_rps']} rps errors={run['load']['errors']}")
        print(f"  {mode}: {entry['verdict']['status']} {entry['verdict']['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
