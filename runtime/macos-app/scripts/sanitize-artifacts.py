#!/usr/bin/env python3
"""Redact published credential values without printing them. Explicit --write required."""
import argparse
import json
from pathlib import Path

SENSITIVE_KEYS = {"token", "authorization", "speech_runtime_token", "hf_token"}
SENSITIVE_FLAGS = {"--token", "--api-key", "--password"}

def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        result = []
        hide_next = False
        for item in value:
            if hide_next:
                result.append("[REDACTED]"); hide_next = False; continue
            if isinstance(item, str) and item in SENSITIVE_FLAGS:
                result.append(item); hide_next = True; continue
            if isinstance(item, str) and any(item.startswith(flag + "=") for flag in SENSITIVE_FLAGS):
                result.append(item.split("=", 1)[0] + "=[REDACTED]"); continue
            result.append(redact(item))
        return result
    return value

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3] / "experiments/runtime_eval/results")
    args = parser.parse_args()
    count = 0
    for path in sorted(args.root.rglob("*.json")):
        try:
            original = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        clean = redact(original)
        if clean != original:
            count += 1
            print("redact:", path.relative_to(args.root))
            if args.write:
                path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
    print(f"{count} artifact(s) {'updated' if args.write else 'need redaction; use --write'}; values not printed.")
