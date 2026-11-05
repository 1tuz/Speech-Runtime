# memory_probe_macos

A standalone C probe for per-process macOS memory and neural-footprint
counters. It exists to make the visibility boundary explicit: what a
process can measure about *itself* (and same-user processes) without
sudo, and what it cannot.

## Build

```
cc -O2 -Wall -Wextra -o tools/memory_probe/memory_probe_macos \
    tools/memory_probe/memory_probe_macos.c
```

Builds clean (no warnings) on macOS 27.2 / arm64 with the Xcode SDK.

## Run

```
./tools/memory_probe/memory_probe_macos                     # self, 1 sample, JSON
./tools/memory_probe/memory_probe_macos --json              # same, explicit
./tools/memory_probe/memory_probe_macos --samples 3 --interval-ms 500
./tools/memory_probe/memory_probe_macos --pid <same-user-pid> --json
./tools/memory_probe/memory_probe_macos --pid 1 --json      # permission_denied path
```

Output is always JSON on stdout (the `--json` flag is accepted for CLI
compatibility). Validate with the included checker — plain `json.load` is not
a check, because duplicate keys in one object are silently collapsed to the
last value (a flat field list would "parse" while losing 8 of 9 readings):

```
./tools/memory_probe/memory_probe_macos | python3 tools/memory_probe/validate.py -
./tools/memory_probe/memory_probe_macos --pid <same-user-pid> | python3 tools/memory_probe/validate.py -
```

`validate.py` rejects duplicate keys, requires every expected field, and
requires each value to be an integer or `null` (never a string, never a
fabricated `0`).

## Structure

Per sample there are three objects, each holding a `fields` **array** so no
key repeats:

- `task_vm_info.fields[]` — 9 entries: `resident_size`, `phys_footprint`,
  `compressed`, `ledger_tag_neural_footprint(_compressed)`,
  `ledger_tag_neural_nofootprint(_compressed|_total)`.
- `task_vm_info_peaks.fields[]` — `ledger_tag_neural_nofootprint_peak`.
- `proc_pid_rusage.fields[]` — `ri_physical_footprint_bytes`,
  `ri_resident_size`, `ri_neural_footprint`,
  `ri_lifetime_max_neural_footprint`, `ri_interval_max_neural_footprint`.

For an external pid, `task_vm_info` is replaced by
`{"status": ..., "reason": ...}` (no task port without root); the rusage
fields remain.

## What it reports

Per sample, **separately, never summed** (RSS + phys_footprint +
neural can overlap; a total would double-count):

- `task_info(mach_task_self(), TASK_VM_INFO, ...)` —
  `resident_size`, `phys_footprint`, `compressed`,
  `ledger_tag_neural_footprint(_compressed)`,
  `ledger_tag_neural_nofootprint(_compressed|_total|_peak)`.
  Truncation safety: the returned `task_info_count` is compared to the
  SDK layout; fields beyond the count are reported `"unsupported"`
  with `value: null`, never read.
- `proc_pid_rusage(pid, RUSAGE_INFO_V6, ...)` —
  `ri_physical_footprint_bytes`, `ri_resident_size`,
  `ri_neural_footprint`, `ri_lifetime_max_neural_footprint`,
  `ri_interval_max_neural_footprint`.
  If V6 is unavailable (compile-time guard on `RUSAGE_INFO_V6`, or
  runtime call failure), it falls back to V4 and emits `null` for all
  neural fields with a `status` explaining why.

Every value carries `"unit": "bytes"`, a `"source"` naming the API and
SDK struct, and a `"kind"` that distinguishes **current** balances from
**lifetime/interval maxima** (peaks are historical and never decrease —
a non-falling peak does not prove memory is still held).

Field status values: `ok`, `unsupported`, `permission_denied`,
`unavailable`. Unavailable data is `null`, never `0`.

## Visibility boundary

- These are **counters the kernel attributes to one process**. They are
  not a whole-system ANE measurement: other processes, system services
  (e.g. coreaudiod, ANE-related daemons), and ANE firmware/state are
  invisible here. "Measured the process's neural ledger" ≠ "measured
  every byte of ANE use on the Mac".
- For `--pid`, only **same-user** PIDs are readable without root
  (`proc_pid_rusage` returns EPERM otherwise). External pids get no
  `task_vm_info` (no task port without `task_for_pid`, which needs
  root/entitlement); `pid 1` yields `permission_denied`, not a crash.
- `ledger_tag_neural_nofootprint*` does not mean "free memory", and
  zero on this SDK is a real read zero (the fields exist and the count
  covers them), not a placeholder. Zero neural counters for a process
  that has not used the ANE is expected.
- This tool deliberately does not use the runtime daemon `/health`
  endpoint; sampling must be independent of the thing measured.

## SDK notes (macOS 27.2, arm64, Xcode clang 21)

- `struct task_vm_info` in this SDK declares all nine neural ledger
  fields including `ledger_tag_neural_nofootprint_peak`;
  `TASK_VM_INFO_COUNT` = 93 and the kernel returns 93 — fully covered.
- `RUSAGE_INFO_V6` and `struct rusage_info_v6` (with the three neural
  fields) are available both compile-time and runtime
  (`proc_pid_rusage(..., RUSAGE_INFO_V6, ...)` returns 0); V4 fallback
  exists for older SDKs/kernels only.

## Residual risk

- Peak/lifetime-max semantics are historical: a high peak after model
  unload does not prove current usage; compare current fields.
- Kernel-to-SDK mismatch: a newer kernel returning a shorter struct is
  handled by the count check, but a *longer* struct is silently
  truncated by `task_info` itself.
- `ri_interval_max_neural_footprint` window semantics are
  kernel-defined and may reset; do not treat it as lifetime.
- No attribution for memory shared with system services (XPC caches,
  wired driver buffers) that the kernel bills elsewhere.
