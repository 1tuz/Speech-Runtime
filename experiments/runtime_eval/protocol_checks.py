#!/usr/bin/env python3
"""HTTP protocol checks for the runtime daemon (RESEARCH.md §6, Этап 1, criterion 1).

Each case speaks HTTP/1.1 over a raw socket, so the framing itself is under test and not
urllib's client-side validation:

* malformed framing is answered with the documented status (400/413/431/501) and the
  socket is closed, instead of a crash, a hang, or a dispatch;
* every accepted request gets exactly one response, and no more;
* the daemon survives every case: `/health` still answers, same pid, zero worker restarts;
* no request payload (canary) and no raw worker/JSON line reaches the daemon log.

    python3 -m runtime_eval.protocol_checks
    python3 -m runtime_eval.protocol_checks --include-slow   # adds the 30 s stall case
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .memory_matrix import REPO, RESULTS, Daemon

TOKEN = "matrix-token"  # Daemon() writes exactly this token into its workdir
CANARY = "PROTOCOL-CANARY-7134f0a1-do-not-log"
BAD_STATUSES = {400, 413, 431, 501}


def raw(request: bytes, port: int, wait: float = 5.0) -> dict:
    """One request on a fresh connection. Always reads until the peer closes or times out."""
    started = time.time()
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as sock:
        sock.settimeout(wait)
        sock.sendall(request)
        chunks, timed_out = [], False
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                timed_out = True
                break
            except ConnectionResetError:
                break
            if not data:
                break
            chunks.append(data)
    body = b"".join(chunks)
    head, _, rest = body.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n") if head else []
    status = None
    if lines and lines[0].startswith(b"HTTP/1.1 "):
        try:
            status = int(lines[0].split(b" ")[1])
        except (IndexError, ValueError):
            status = None
    return {
        "status": status,
        "responses": head.count(b"HTTP/1.1 "),
        "headers": [line.decode("latin-1") for line in lines[1:]],
        "body": rest,
        "bytes": body,
        "timed_out": timed_out,
        "seconds": round(time.time() - started, 3),
    }


def request(
    method: str = "POST",
    path: str = "/v1/models/load",
    headers: list[str] | tuple[str, ...] = (),
    body: bytes = b"",
    auth: bool = True,
    close: bool = True,
) -> bytes:
    """Build a request from explicit header lines (duplicates preserved)."""
    lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
    if auth:
        lines.append(f"Authorization: Bearer {TOKEN}")
    lines.extend(headers)
    lines.append(f"Content-Length: {len(body)}")
    if close:
        lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def framed(head: str, body: bytes = b"") -> bytes:
    """Raw request from a hand-written head block (Content-Length decided by the caller)."""
    return (head + "\r\n\r\n").encode() + body


# ---------------------------------------------------------------- cases
# Each case returns (name, request bytes, expectation callable over the raw() result, note).

def cases(port: int) -> list[tuple[str, bytes, callable, str]]:
    big_header = "X-Pad: " + "a" * (40 * 1024)
    many_headers = [f"X-N-{i}: {i}" for i in range(120)]
    return [
        ("health_without_token_is_open", request("GET", "/health", auth=False),
         lambda r: r["status"] == 200, "health is the only unauthenticated route"),
        ("missing_token_is_401", request("GET", "/v1/models", auth=False),
         lambda r: r["status"] == 401, "no Authorization header"),
        ("bad_token_is_401", framed("POST /v1/models/load HTTP/1.1\r\nAuthorization: Bearer wrong"),
         lambda r: r["status"] == 401, "wrong bearer token"),
        ("unknown_route_is_404", request("GET", "/v1/nope"),
         lambda r: r["status"] == 404, "authenticated unknown path"),
        ("unknown_method_is_404", request("PUT", "/v1/models/load", body=b"{}"),
         lambda r: r["status"] == 404, "path exists, method does not"),
        ("content_length_negative_is_400", framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: -1"),
         lambda r: r["status"] == 400, "sign is not a length"),
        ("content_length_plus_is_400", framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: +5"),
         lambda r: r["status"] == 400, "plus sign must not be parsed as a length"),
        ("content_length_text_is_400", framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: abc"),
         lambda r: r["status"] == 400, "non-digits"),
        ("content_length_hex_is_400", framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: 0x10"),
         lambda r: r["status"] == 400, "hex is not decimal"),
        ("duplicate_content_length_conflict_is_400",
         framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: 4\r\nContent-Length: 5", b'{"a'),
         lambda r: r["status"] == 400, "conflicting declarations must not be resolved by guessing"),
        ("duplicate_content_length_equal_is_accepted",
         framed("POST /v1/models/load HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\nAuthorization: Bearer " + TOKEN, b"{}"),
         lambda r: r["status"] is not None and r["responses"] == 1, "identical duplicates are consistent framing"),
        ("content_length_over_limit_is_413",
         framed("POST /v1/audio/transcriptions HTTP/1.1\r\nContent-Length: 314572800"),
         lambda r: r["status"] == 413, "300 MiB > 256 MiB limit, rejected before the body"),
        ("transfer_encoding_is_501",
         framed("POST /v1/models/load HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 4", b"4\r\n{}"),
         lambda r: r["status"] == 501, "chunked framing is refused, not misread"),
        ("obsolete_line_folding_is_400",
         framed("POST /v1/models/load HTTP/1.1\r\nX-A: 1\r\n folded-continuation"),
         lambda r: r["status"] == 400, "continuation lines are ambiguous with the terminator"),
        ("header_without_colon_is_400", framed("POST /v1/models/load HTTP/1.1\r\nBadHeaderLine"),
         lambda r: r["status"] == 400, "no colon"),
        ("header_name_with_space_is_400", framed("POST /v1/models/load HTTP/1.1\r\nBad Header: 1"),
         lambda r: r["status"] == 400, "space is not a token character"),
        ("too_many_headers_is_431",
         framed("POST /v1/models/load HTTP/1.1\r\n" + "\r\n".join(many_headers)),
         lambda r: r["status"] == 431, "120 > 100 header limit"),
        ("oversized_headers_is_431",
         framed("POST /v1/models/load HTTP/1.1\r\n" + big_header),
         lambda r: r["status"] == 431, "40 KiB > 32 KiB header-byte limit"),
        ("garbage_request_line_is_400", framed("\x01\x02\x03"),
         lambda r: r["status"] == 400, "no method/target split"),
        ("empty_head_is_400", framed(""),
         lambda r: r["status"] == 400, "empty request line"),
        ("bad_http_version_is_400", framed("GET /health HTTP/2.0"),
         lambda r: r["status"] == 400, "only HTTP/1.x is framed here"),
        ("http_0_9_style_line_is_served", framed("GET /health"),
         lambda r: r["status"] == 200, "two-part request line, version omitted"),
        ("exactly_one_reply_per_request",
         request("POST", "/v1/streams", body=b'{"model":"no-such-model"}'),
         lambda r: r["responses"] == 1 and r["status"] is not None,
         "an error is still exactly one response; the socket closes"),
        ("error_body_is_json", request("POST", "/v1/streams", body=b'{"model":"no-such-model"}'),
         lambda r: b'"error"' in r["body"], "errors carry a machine-readable message"),
        ("slow_body_still_framed",
         None,  # handled specially: headers, pause, body
         lambda r: r["status"] is not None and r["responses"] == 1,
         "the parser waits for the declared body instead of guessing length 0"),
        ("pipelined_second_request_is_not_answered",
         None,  # handled specially
         lambda r: r["responses"] == 1,
         "one request per connection: the surplus bytes are dropped, not dispatched"),
        ("canary_body_is_not_logged",
         request("POST", "/v1/models/load", body=json.dumps({"model": CANARY, "note": CANARY}).encode()),
         lambda r: r["status"] in (400, 404, 500) or b"error" in r["body"],
         "payload must not reach the log file"),
    ]


def run_slow_case(port: int) -> dict:
    """Headers, then the body after a pause: incomplete framing must not be dispatched early."""
    body = b'{"model":"no-such-model"}'
    head = (
        "POST /v1/streams HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        f"Authorization: Bearer {TOKEN}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.settimeout(10)
        sock.sendall(head)
        time.sleep(0.4)
        sock.sendall(body)
        data = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
    head_bytes, _, rest = data.partition(b"\r\n\r\n")
    return {
        "status": int(head_bytes.split(b" ")[1]) if head_bytes.startswith(b"HTTP/1.1 ") else None,
        "responses": head_bytes.count(b"HTTP/1.1 "),
        "body": rest,
        "bytes": data,
        "timed_out": False,
        "seconds": None,
    }


def run_pipeline_case(port: int) -> dict:
    """Two complete requests on one connection: the server closes after the first."""
    first = request("GET", "/health", auth=False, close=False)
    second = request("GET", "/health", auth=False, close=False)
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(first + second)
        data = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, ConnectionResetError):
                break
            if not chunk:
                break
            data += chunk
    head_bytes = data.split(b"\r\n\r\n")[0]
    return {"status": 200 if head_bytes.startswith(b"HTTP/1.1 200") else None,
            "responses": head_bytes.count(b"HTTP/1.1 "), "body": b"", "bytes": data,
            "timed_out": False, "seconds": None}


def run_stall_case(port: int, seconds: float = 40.0) -> dict:
    """Open a connection, send nothing: `Limits.requestTimeout` must close it (30 s)."""
    started = time.time()
    with socket.create_connection(("127.0.0.1", port), timeout=seconds + 10) as sock:
        sock.settimeout(seconds + 10)
        closed_at = None
        while time.time() - started < seconds:
            try:
                if sock.recv(1) == b"":
                    closed_at = round(time.time() - started, 2)
                    break
            except ConnectionResetError:
                closed_at = round(time.time() - started, 2)
                break
            except socket.timeout:
                break
    return {"status": None, "responses": 0, "body": b"", "bytes": b"", "timed_out": closed_at is None,
            "closed_at_s": closed_at, "seconds": round(time.time() - started, 2)}


def health_ok(daemon: Daemon) -> tuple[bool, dict]:
    try:
        doc = daemon.health()
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as error:
        return False, {"error": f"{error}"}
    return bool(doc.get("ok")), doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8592)
    parser.add_argument("--include-slow", action="store_true", help="add the 30 s stalled-connection case")
    parser.add_argument("--out", type=Path, default=RESULTS)
    options = parser.parse_args(argv)

    workdir = REPO / "experiments/runtime_eval/.work/protocol-checks"
    daemon = Daemon(options.port, workdir)
    rows, failures = [], []
    try:
        before_ok, before = health_ok(daemon)
        pid_before = (before.get("worker") or {}).get("pid")
        for name, payload, expect, note in cases(options.port):
            if name == "slow_body_still_framed":
                result = run_slow_case(options.port)
            elif name == "pipelined_second_request_is_not_answered":
                result = run_pipeline_case(options.port)
            else:
                result = raw(payload, options.port)
            alive, health = health_ok(daemon)
            passed = False
            try:
                passed = bool(expect(result))
            except Exception:  # a malformed result must not abort the suite
                passed = False
            ok = passed and alive
            rows.append({"case": name, "status": result["status"], "responses": result["responses"],
                         "passed": ok, "expectation_met": passed, "daemon_alive": alive,
                         "seconds": result["seconds"], "note": note,
                         "reply": result["body"][:200].decode("latin-1", "replace")})
            if not ok:
                failures.append(name)
            print(f"{'PASS' if ok else 'FAIL'}  {name:44s} status={result['status']} "
                  f"responses={result['responses']} alive={alive}")
        if options.include_slow:
            result = run_stall_case(options.port)
            alive, _ = health_ok(daemon)
            met = result["closed_at_s"] is not None and result["closed_at_s"] <= 40
            rows.append({"case": "stalled_connection_is_closed_by_the_server", "status": None,
                         "responses": 0, "passed": met and alive, "expectation_met": met,
                         "daemon_alive": alive, "seconds": result["seconds"],
                         "closed_at_s": result["closed_at_s"],
                         "note": "32 s request timeout releases a socket that never speaks"})
            if not (met and alive):
                failures.append("stalled_connection_is_closed_by_the_server")
            print(f"{'PASS' if met and alive else 'FAIL'}  stalled_connection_is_closed_by_the_server  "
                  f"closed_at={result['closed_at_s']}s")

        # Payload privacy: the canary and raw worker/JSON lines must not be in the log file.
        log_path = workdir / f"daemon-{options.port}.log"
        log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
        canary_hits = log_text.count(CANARY)
        suspicious = [marker for marker in ('"model"', '"error"', '{"', '"text"') if marker in log_text]
        after_ok, after = health_ok(daemon)
        pid_after = (after.get("worker") or {}).get("pid")
        restarts = (after.get("worker") or {}).get("restarts")
        privacy_ok = canary_hits == 0 and not suspicious
        if not privacy_ok:
            failures.append("log_privacy")
        print(f"{'PASS' if privacy_ok else 'FAIL'}  log_privacy  canary_hits={canary_hits} "
              f"suspicious={suspicious}")
        print(f"{'PASS' if after_ok and pid_after == pid_before and restarts == 0 else 'FAIL'}  "
              f"daemon_survived_all_cases  pid={pid_before}->{pid_after} worker_restarts={restarts}")

        report = {
            "schema": "protocol_checks/1",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "port": options.port,
            "cases": rows,
            "failures": failures,
            "log": {"path": str(log_path), "canary_hits": canary_hits, "suspicious_markers": suspicious},
            "daemon": {"pid_before": pid_before, "pid_after": pid_after, "worker_restarts": restarts,
                       "alive": after_ok},
            "verdict": {"status": "PASS" if not failures else "FAIL", "failures": failures},
        }
        options.out.mkdir(parents=True, exist_ok=True)
        path = options.out / f"protocol-checks-{time.strftime('%Y%m%d-%H%M%S')}.json"
        path.write_text(json.dumps(report, indent=1))
        print(f"\nwrote {path}")
        print(f"protocol checks: {report['verdict']['status']}  failures={failures or 'none'}")
        return 0 if not failures else 1
    finally:
        daemon.stop()


if __name__ == "__main__":
    sys.exit(main())
