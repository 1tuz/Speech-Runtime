#!/usr/bin/env python3
"""Capture the environment artifact required by RESEARCH.md §3.1 / §7.

Writes `results/environment.json`: OS build, hardware model, RAM, compiler and
toolchain versions, build mode, git commit (with a dirty marker), hashes of the
built binaries, and — for every model in `runtime/models.json` — size, SHA-256
and resolved path. Serial numbers are deliberately not collected.

    python3 -m runtime_eval.environment
    python3 -m runtime_eval.environment --skip-hashes   # fast, no multi-GB reads
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
CONFIG = REPO / "runtime/models.json"
BINARIES = {
    "daemon_release": REPO / "runtime/daemon/.build/release/speech-runtime",
    "daemon_debug": REPO / "runtime/daemon/.build/debug/speech-runtime",
    "worker_release": REPO / "runtime/worker/target/release/sr-worker",
    "worker_debug": REPO / "runtime/worker/target/debug/sr-worker",
}
REMOVED_ENV_KEYS = ("SPEECH_RUNTIME_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")


def run(*command: str) -> str:
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return f"unavailable: {error}"
    if out.returncode != 0:
        return f"failed[{out.returncode}]: {(out.stderr or out.stdout).strip()[:200]}"
    return out.stdout.strip()


def sha256(path: Path) -> dict:
    record = {"path": repo_relative(path), "exists": path.exists()}
    if not path.exists():
        return record
    digest = hashlib.sha256()
    started = time.time()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    stat = path.stat()
    record.update({"bytes": stat.st_size, "sha256": digest.hexdigest(),
                   "hash_seconds": round(time.time() - started, 2)})
    return record


def repo_relative(path: Path) -> str:
    """Path relative to the repo root when inside it; otherwise POSIX-absolute.

    Anonymisation: `/Users/<name>/...` never reaches the artifact, while
    `machine_model`/`host` stay as before (they identify hardware, not files).
    """
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return path.as_posix()


def git_state() -> dict:
    return {
        "commit": run("git", "-C", str(REPO), "rev-parse", "HEAD"),
        "short": run("git", "-C", str(REPO), "rev-parse", "--short", "HEAD"),
        "subject": run("git", "-C", str(REPO), "log", "-1", "--pretty=%s"),
        "committed_at": run("git", "-C", str(REPO), "log", "-1", "--pretty=%cI"),
        "dirty": bool(run("git", "-C", str(REPO), "status", "--porcelain")),
        "dirty_files": len([line for line in run("git", "-C", str(REPO), "status", "--porcelain").splitlines() if line]),
    }


def models(with_hashes: bool) -> dict:
    try:
        config = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError) as error:
        return {"error": f"{error}"}
    out = {}
    for name, entry in (config.get("models") or {}).items():
        path = Path(os.path.expanduser(entry.get("path", "")))
        record = {"backend": entry.get("backend"), "languages": entry.get("languages"),
                  "configured_path": entry.get("path"),
                  # basename only: model weights live under private home paths
                  "resolved_path": path.name, "exists": path.exists()}
        if path.exists():
            record["bytes"] = path.stat().st_size
            if with_hashes:
                record["sha256"] = sha256(path)["sha256"]
        out[name] = record
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RESULTS / "environment.json")
    parser.add_argument("--skip-hashes", action="store_true", help="do not read multi-GB model files")
    options = parser.parse_args(argv)

    cargo_lock = REPO / "runtime/worker/Cargo.lock"
    report = {
        "schema": "environment/1",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reproduce": "python3 -m runtime_eval.environment",
        "host": {
            "os": platform.platform(),
            "os_version": run("sw_vers", "-productVersion"),
            "os_build": run("sw_vers", "-buildVersion"),
            "arch": platform.machine(),
            "machine_model": run("sysctl", "-n", "hw.model"),
            "cpu": run("sysctl", "-n", "machdep.cpu.brand_string"),
            "cpu_count": os.cpu_count(),
            "memory_bytes": int(run("sysctl", "-n", "hw.memsize") or 0) or None,
            "python": sys.version.split()[0],
        },
        "toolchains": {
            "swift": run("swift", "--version").replace("\n", " | "),
            "swiftpm": run("swift", "package", "--version").replace("\n", " | "),
            "rustc": run("rustc", "-Vv").replace("\n", " | "),
            "cargo": run("cargo", "-V"),
            "clang": run("clang", "--version").splitlines()[0] if run("clang", "--version") else None,
            "ffmpeg": run("ffmpeg", "-version").splitlines()[0] if run("ffmpeg", "-version") else None,
        },
        "build_mode": "release (swift build -c release, cargo build --release); debug binaries hashed separately",
        "git": git_state(),
        "dependencies": {
            "cargo_lock_sha256": sha256(cargo_lock)["sha256"] if cargo_lock.exists() else None,
            "python": "stdlib only (no third-party packages used by the experiment harnesses)",
        },
        "binaries": {name: sha256(path) for name, path in BINARIES.items() if path.exists()},
        "models": models(with_hashes=not options.skip_hashes),
        "privacy": {
            "excluded": ["hardware serial number", "host UUID", "user name", "tokens", "audio payloads",
                         "transcripts"],
            "env_keys_not_read": list(REMOVED_ENV_KEYS),
        },
    }
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(report, indent=1))
    print(f"wrote {options.out}")
    print(f" commit {report['git']['short']} dirty={report['git']['dirty']} "
          f"model_hashes={'skipped' if options.skip_hashes else 'computed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
