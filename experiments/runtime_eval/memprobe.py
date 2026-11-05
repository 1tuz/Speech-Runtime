#!/usr/bin/env python3
"""External per-process memory sampling shared by the runtime experiments.

Wraps `tools/memory_probe/memory_probe_macos`, which reports `proc_pid_rusage(RUSAGE_INFO_V6)`
for another process. `task_vm_info` needs a task port (root), so it stays `unsupported` for
external pids and the rusage fields are the only attribution we have. `ri_neural_footprint` is
current ANE accounting for that process; the lifetime maximum never decreases, so a peak must
never be reported as the current value.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROBE = REPO / "tools/memory_probe/memory_probe_macos"

# Fields kept in every sample: current values and peaks are always separated.
FIELDS = (
    "ri_resident_size",
    "ri_physical_footprint_bytes",
    "ri_neural_footprint",
    "ri_lifetime_max_neural_footprint",
    "ri_interval_max_neural_footprint",
)


def probe(pid: int) -> dict:
    """One external sample; rusage V6 fields only, plus the visibility boundary."""
    try:
        out = subprocess.run([str(PROBE), "--pid", str(pid), "--json"],
                             capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as error:
        return {"error": f"probe failed: {error}"}
    if out.returncode != 0:
        return {"error": f"probe rc={out.returncode}", "stderr": out.stderr.strip()[:200]}
    try:
        doc = json.loads(out.stdout)
        sample = doc["samples_data"][0]
    except (json.JSONDecodeError, KeyError, IndexError):
        return {"error": "probe output was not the expected JSON"}
    values = {"target": doc.get("target"), "visibility_boundary": doc.get("visibility_boundary")}
    rusage = sample.get("proc_pid_rusage", {})
    values["rusage_api"] = rusage.get("api_level")
    values["rusage_status"] = rusage.get("status")
    for entry in rusage.get("fields", []):
        values[entry["field"]] = entry.get("value")
        if entry.get("status"):
            values[f'{entry["field"]}_status'] = entry["status"]
    values["task_vm_info"] = sample.get("task_vm_info", {}).get("status", "absent")
    return values


def selected(sample: dict) -> dict:
    """The fields a series needs, `None` (never 0) when the kernel did not report them."""
    return {key: sample.get(key) for key in FIELDS}


def system_background() -> dict:
    """Compressor/swap as context, never as an attribution to our processes."""
    out: dict = {}
    try:
        raw = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=10).stdout
        out["swapusage"] = " ".join(raw.split())
    except (subprocess.SubprocessError, OSError):
        out["swapusage"] = None
    try:
        raw = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
        pages: dict[str, int] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            digits = value.strip().rstrip(".").replace(".", "")
            if digits.isdigit():
                pages[key.strip()] = int(digits)
        out["compressor_pages"] = pages.get("Pages occupied by compressor")
        out["free_pages"] = pages.get("Pages free")
    except (subprocess.SubprocessError, OSError):
        pass
    return out
