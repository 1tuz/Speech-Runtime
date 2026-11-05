#!/usr/bin/env python3
"""Validate memory_probe_macos JSON output.

`json.load` alone is not a check: duplicate keys inside one object are silently
collapsed to the last value, so a probe that repeats `"field"` nine times still
"parses". This validator rejects duplicate keys outright and then asserts the
expected field set per sample.

usage: validate.py <probe-output.json>            # or:  ... | validate.py -
"""

import json
import sys

TVI_FIELDS = {
    "resident_size",
    "phys_footprint",
    "compressed",
    "ledger_tag_neural_footprint",
    "ledger_tag_neural_footprint_compressed",
    "ledger_tag_neural_nofootprint",
    "ledger_tag_neural_nofootprint_compressed",
    "ledger_tag_neural_nofootprint_total",
}
RUSAGE_FIELDS = {
    "ri_physical_footprint_bytes",
    "ri_resident_size",
    "ri_neural_footprint",
    "ri_lifetime_max_neural_footprint",
    "ri_interval_max_neural_footprint",
}


class DuplicateKey(ValueError):
    pass


def _strict_object(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise DuplicateKey(f"duplicate key {key!r} in one object")
        seen[key] = value
    return seen


def _fields(container, label, sink):
    """Yield (name, value) from a `fields` array and collect the names in sink."""
    if "fields" not in container:
        raise ValueError(f"{label}: missing 'fields' array")
    for entry in container["fields"]:
        name = entry.get("field")
        if not isinstance(name, str):
            raise ValueError(f"{label}: field entry without a name")
        sink.add(name)
        value = entry.get("value", "<missing>")
        if value is not None and not isinstance(value, int):
            raise ValueError(f"{label}.{name}: value must be an integer or null, got {value!r}")
        if value is None and not entry.get("status"):
            raise ValueError(f"{label}.{name}: null value must carry a status")
        yield name, value


def validate(doc):
    if doc.get("schema") != "memory_probe_macos/1":
        raise ValueError(f"unexpected schema {doc.get('schema')!r}")
    samples = doc.get("samples_data")
    if not isinstance(samples, list) or not samples:
        raise ValueError("samples_data must be a non-empty list")

    external = doc.get("target", "").startswith("external pid")
    for index, sample in enumerate(samples):
        tvi_names, rusage_names = set(), set()

        tvi = sample.get("task_vm_info")
        if tvi is None:
            if not external:
                raise ValueError(f"[{index}] missing task_vm_info")
        elif "fields" in tvi:
            names = {n for n, _ in _fields(tvi, f"[{index}] task_vm_info", tvi_names)}
            if names != TVI_FIELDS:
                raise ValueError(
                    f"[{index}] task_vm_info fields {sorted(names)} != {sorted(TVI_FIELDS)}"
                )
        elif not (external and tvi.get("reason")):
            # No fields is only acceptable as the declared visibility boundary.
            raise ValueError(f"[{index}] task_vm_info without fields and without a reason")

        list(_fields(sample["proc_pid_rusage"], f"[{index}] proc_pid_rusage", rusage_names))
        missing = RUSAGE_FIELDS - rusage_names
        if missing:
            raise ValueError(f"[{index}] proc_pid_rusage missing {sorted(missing)}")


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    text = sys.stdin.read() if argv[1] == "-" else open(argv[1], encoding="utf-8").read()
    try:
        doc = json.loads(text, object_pairs_hook=_strict_object)
        validate(doc)
    except (ValueError, DuplicateKey) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        return 1
    print(f"OK: {doc['samples_data'].__len__()} sample(s), no duplicate keys, all fields present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
