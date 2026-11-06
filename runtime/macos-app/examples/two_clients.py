#!/usr/bin/env python3
"""Bounded transport smoke: two sessions, 0.5s silence each. Not a WER benchmark."""
import concurrent.futures
import json
from pathlib import Path
import struct
import urllib.request

BASE = "http://127.0.0.1:8477"
TOKEN = (Path.home() / "Library/Application Support/SpeechRuntime/token").read_text().strip()

def request(method, path, body=None, raw=False):
    if not raw and body is not None:
        body = json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + TOKEN,
               "Content-Type": "application/octet-stream" if raw else "application/json"}
    if raw:
        headers.update({"X-Sample-Format": "f32le", "X-Sample-Rate": "16000"})
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

def session(index):
    opened = request("POST", "/v1/streams", {"model": "parakeet-ultra"})
    identifier = opened["stream"]
    try:
        request("POST", "/v1/streams/audio?stream=" + identifier, struct.pack("<8000f", *([0.0] * 8000)), raw=True)
        ended = request("POST", "/v1/streams/end?stream=" + identifier)
        assert ended.get("ended") is True, "session did not end"
        return identifier
    finally:
        request("DELETE", "/v1/streams?stream=" + identifier)

if __name__ == "__main__":
    # The menu app has already warmed the model. Loading is idempotent and does not
    # give this client its own model process. Repeat load after a worker generation change.
    request("POST", "/v1/models/load", {"model": "parakeet-ultra", "backend": "ane"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(session, range(2)))
    assert len(set(ids)) == 2
    print("PASS: two independent sessions ended; no accuracy or performance claim.")
