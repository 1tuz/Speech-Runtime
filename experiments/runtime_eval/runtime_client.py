"""Pure-stdlib HTTP client for the Speech Runtime loopback API.

Speaks exactly the frozen contract used by the daemon under `runtime/`:

- `GET  /health`
- `GET  /v1/models`
- `POST /v1/models/load`           body `{"model", "backend"?}`
- `POST /v1/models/unload`         body `{"model"}`
- `POST /v1/audio/transcriptions?model=M[&language=L][&priority=P]` body: WAV bytes
- `POST /v1/streams`               body `{"model", "config", "language"?}`
- `POST /v1/streams/audio?stream=ID` body: raw PCM, `X-Sample-Format: f32le`
- `POST /v1/streams/end?stream=ID` blocks until the worker confirmed the trailing
  utterance; response `{stream, ended: true, result:{session_end, pending, utterances, failed}}`
- `GET  /v1/streams/events?stream=ID&since=N&wait=S` response `{stream, next, ended, events}`
- `DELETE /v1/streams?stream=ID`

Standard library only: `urllib.request` for HTTP, `struct`/`wave` for audio,
`json` for bodies. No third-party dependencies, no daemon startup here.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

DEFAULT_BASE_URL = "http://127.0.0.1:8477"
TOKEN_PATH = os.path.expanduser(
    "~/Library/Application Support/SpeechRuntime/token"
)
SAMPLE_RATE = 16000
#: Endpointing events carry audio-stream milliseconds; the contract fixes this key set.
FINAL_FIELDS = (
    "utterance",
    "text",
    "asr_ms",
    "queue_ms",
    "audio_ms",
    "start_ms",
    "end_ms",
    "last_voice_ms",
    "detected_ms",
    "end_reason",
    "tokens",
)


class RuntimeError(Exception):
    """Raised for HTTP != 2xx or transport failure; message matches the daemon reply."""


def load_token(path: str = TOKEN_PATH) -> str:
    """Reads the daemon bearer token (same file `Paths.token` uses in Swift)."""
    with open(path, encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        raise RuntimeError(f"token file {path} is empty")
    return token


class RuntimeClient:
    """Thin synchronous client; one instance per process is enough."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else load_token()

    # -- transport ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
        timeout: float = 600.0,
    ) -> dict:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            request.add_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(detail).get("error", detail)
            except (ValueError, AttributeError):
                message = detail
            raise RuntimeError(f"HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach {self.base_url}: {exc.reason}") from exc
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except ValueError as exc:
            raise RuntimeError(f"non-JSON response from {path}: {payload[:200]!r}") from exc

    def _json_body(self, value) -> bytes:
        return json.dumps(value).encode("utf-8")

    # -- catalog -----------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/health")

    def models(self) -> dict:
        return self._request("GET", "/v1/models")

    def load(self, model: str, backend: str | None = None) -> dict:
        body: dict = {"model": model}
        if backend:
            body["backend"] = backend
        return self._request("POST", "/v1/models/load", body=self._json_body(body))

    def unload(self, model: str) -> dict:
        return self._request(
            "POST", "/v1/models/unload", body=self._json_body({"model": model})
        )

    # -- one-shot ----------------------------------------------------------

    def transcribe_wav(
        self,
        wav_bytes: bytes,
        model: str,
        language: str | None = None,
        priority: str = "batch",
        window_ms: int | None = None,
    ) -> dict:
        """POSTs a whole WAV; the daemon decodes 16 kHz mono PCM."""
        query = {"model": model, "priority": priority}
        if language:
            query["language"] = language
        if window_ms is not None:
            query["window_ms"] = str(window_ms)
        return self._request(
            "POST",
            "/v1/audio/transcriptions",
            query=query,
            body=wav_bytes,
            content_type="audio/wav",
        )

    # -- streaming ---------------------------------------------------------

    def open_stream(self, model: str, config: dict | None = None) -> str:
        body: dict = {"model": model, "config": dict(config or {})}
        if body["config"].get("language") is not None:
            body["language"] = body["config"]["language"]
        reply = self._request("POST", "/v1/streams", body=self._json_body(body))
        stream = reply.get("stream")
        if not stream:
            raise RuntimeError(f"daemon did not return a stream id: {reply!r}")
        return stream

    def send_audio(
        self, stream_id: str, samples, sample_format: str = "f32le"
    ) -> dict:
        """`samples`: iterable of floats in [-1, 1] (f32le) or ints (s16le)."""
        payload = encode_pcm(samples, sample_format)
        return self._request(
            "POST",
            "/v1/streams/audio",
            query={"stream": stream_id},
            body=payload,
            content_type="application/octet-stream",
            headers={"X-Sample-Format": sample_format, "X-Sample-Rate": str(SAMPLE_RATE)},
        )

    def end_stream(self, stream_id: str) -> dict:
        """Blocks until the worker confirmed the trailing utterance."""
        return self._request("POST", "/v1/streams/end", query={"stream": stream_id})

    def events(self, stream_id: str, since: int, wait: float = 0.0) -> dict:
        return self._request(
            "GET",
            "/v1/streams/events",
            query={"stream": stream_id, "since": str(since), "wait": f"{wait:.2f}"},
        )

    def cancel_stream(self, stream_id: str) -> dict:
        return self._request("DELETE", "/v1/streams", query={"stream": stream_id})


# -- audio helpers ---------------------------------------------------------


def encode_pcm(samples, sample_format: str = "f32le") -> bytes:
    if sample_format == "f32le":
        return struct.pack(f"<{len(samples)}f", *samples)
    if sample_format == "s16le":
        return struct.pack(f"<{len(samples)}h", *samples)
    raise ValueError(f"unsupported sample format: {sample_format}")


def read_wav(path_or_bytes) -> tuple[list[float], int, int]:
    """Returns (samples, sample_rate, channels) for a PCM WAV file or bytes."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        import io

        context = wave.open(io.BytesIO(path_or_bytes))
    else:
        context = wave.open(path_or_bytes, "rb")
    with context as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width == 2:
        ints = struct.unpack(f"<{len(frames) // 2}h", frames)
    elif width == 4:
        ints = struct.unpack(f"<{len(frames) // 4}i", frames)
    else:
        raise ValueError(f"unsupported WAV sample width: {width}")
    if channels == 1:
        mono = ints
    else:  # average channels, matching the corpus convention
        mono = [
            sum(ints[i : i + channels]) / channels
            for i in range(0, len(ints) - channels + 1, channels)
        ]
    if width == 2:
        samples = [v / 32768.0 for v in mono]
    else:
        samples = [v / 2147483648.0 for v in mono]
    return samples, rate, 1


def ms_to_samples(ms: float, sample_rate: int = SAMPLE_RATE) -> int:
    return int(round(ms * sample_rate / 1000.0))


def drain_finals(
    client: RuntimeClient,
    stream_id: str,
    since: int,
    wait: float = 0.1,
    max_polls: int = 600,
) -> tuple[list[dict], int, bool]:
    """Polls `events` until `ended`; returns (finals_data, next_cursor, ended).

    Error events raise `RuntimeError` with the daemon message: silent drops are
    forbidden by the harness contract.
    """
    finals: list[dict] = []
    cursor = since
    for _ in range(max_polls):
        batch = client.events(stream_id, cursor, wait=wait)
        cursor = int(batch.get("next", cursor))
        for event in batch.get("events", []):
            name = event.get("event")
            if name == "final":
                finals.append(event.get("data", {}))
            elif name == "error":
                message = event.get("data", {}).get("message", "stream error")
                raise RuntimeError(f"stream error: {message}")
        if batch.get("ended") and not batch.get("events"):
            return finals, cursor, True
    raise RuntimeError(f"stream {stream_id} did not end after {max_polls} polls")


def stream_file(
    client: RuntimeClient,
    wav_path: str,
    model: str,
    config: dict | None = None,
    realtime: float = 1.0,
    chunk_ms: int = 100,
    on_final=None,
) -> dict:
    """Feeds a 16 kHz mono WAV as a live stream at `realtime`× speed.

    Returns per-final records with the contract fields plus the client-computed
    `e2e_ms` (wall clock from the moment the audio position of `last_voice_ms`
    was fed until the final arrived). Pacing is identical to `sr stream`: sleep
    toward `fed_audio_s / realtime` between chunk posts.
    """
    samples, rate, _ = read_wav(wav_path)
    if abs(rate - SAMPLE_RATE) > 1:
        raise RuntimeError(f"file is {rate} Hz; convert to {SAMPLE_RATE} Hz mono first")
    config = dict(config or {})
    stream_id = client.open_stream(model, config)
    chunk = ms_to_samples(chunk_ms)
    started = time.monotonic()
    cursor = 0
    finals: list[dict] = []
    fed_audio_s = 0.0
    fed_clock = started

    def wall_at(audio_s: float) -> float:
        behind = max(fed_audio_s - audio_s, 0)
        return fed_clock - behind / realtime

    def drain(wait: float) -> None:
        nonlocal cursor
        batch = client.events(stream_id, cursor, wait=wait)
        cursor = int(batch.get("next", cursor))
        for event in batch.get("events", []):
            name = event.get("event")
            data = event.get("data", {})
            if name == "final":
                record = {key: data.get(key) for key in FINAL_FIELDS}
                record["text"] = data.get("text", "")
                last_voice_s = (data.get("last_voice_ms") or 0) / 1000.0
                record["e2e_ms"] = int((time.monotonic() - wall_at(last_voice_s)) * 1000)
                finals.append(record)
                if on_final is not None:
                    on_final(record)
            elif name == "error":
                raise RuntimeError(f"stream error: {data.get('message', '?')}")

    offset = 0
    while offset < len(samples):
        end = min(offset + chunk, len(samples))
        client.send_audio(stream_id, samples[offset:end])
        offset = end
        fed_audio_s = offset / SAMPLE_RATE
        fed_clock = time.monotonic()
        drain(wait=0.0)
        target = fed_audio_s / realtime
        elapsed = time.monotonic() - started
        if target > elapsed:
            time.sleep(target - elapsed)
    client.end_stream(stream_id)
    rest, cursor, _ = drain_finals(client, stream_id, cursor)
    for data in rest:
        record = {key: data.get(key) for key in FINAL_FIELDS}
        record["text"] = data.get("text", "")
        last_voice_s = (data.get("last_voice_ms") or 0) / 1000.0
        record["e2e_ms"] = int((time.monotonic() - wall_at(last_voice_s)) * 1000)
        finals.append(record)
        if on_final is not None:
            on_final(record)
    summary = client.events(stream_id, cursor, wait=0.0)
    ended = bool(summary.get("ended"))
    client.cancel_stream(stream_id)
    return {
        "stream": stream_id,
        "ended": ended,
        "audio_ms": int(len(samples) * 1000 / SAMPLE_RATE),
        "wall_ms": int((time.monotonic() - started) * 1000),
        "finals": finals,
    }


# -- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m runtime_eval.runtime_client",
        description="Stdlib-only client for the Speech Runtime loopback API",
    )
    parser.add_argument("--url", default=os.environ.get("SPEECH_RUNTIME_URL", DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("SPEECH_RUNTIME_TOKEN"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="worker and stream state")
    sub.add_parser("models", help="configured models and what is loaded")
    load_p = sub.add_parser("load", help="load a model")
    load_p.add_argument("model")
    load_p.add_argument("--backend", default=None)
    unload_p = sub.add_parser("unload", help="unload a model")
    unload_p.add_argument("model")

    trans_p = sub.add_parser("transcribe", help="one-shot WAV transcription")
    trans_p.add_argument("file")
    trans_p.add_argument("--model", required=True)
    trans_p.add_argument("--language", default=None)
    trans_p.add_argument("--priority", default="batch")

    stream_p = sub.add_parser("stream", help="feed a WAV as a live stream")
    stream_p.add_argument("file")
    stream_p.add_argument("--model", required=True)
    stream_p.add_argument("--realtime", type=float, default=1.0)
    stream_p.add_argument("--language", default=None)
    stream_p.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="k=v",
        help="stream config entry, e.g. --config min_silence_ms=700",
    )

    args = parser.parse_args(argv)
    try:
        client = RuntimeClient(args.url, args.token)
        if args.command == "health":
            print(json.dumps(client.health(), ensure_ascii=False, indent=2))
        elif args.command == "models":
            print(json.dumps(client.models(), ensure_ascii=False, indent=2))
        elif args.command == "load":
            print(json.dumps(client.load(args.model, args.backend), ensure_ascii=False, indent=2))
        elif args.command == "unload":
            print(json.dumps(client.unload(args.model), ensure_ascii=False, indent=2))
        elif args.command == "transcribe":
            with open(args.file, "rb") as fh:
                reply = client.transcribe_wav(fh.read(), args.model, args.language, args.priority)
            print(json.dumps(reply, ensure_ascii=False, indent=2))
        elif args.command == "stream":
            config = parse_config(args.config)
            if args.language:
                config["language"] = args.language
            result = stream_file(client, args.file, args.model, config, args.realtime)
            for record in result["finals"]:
                print(json.dumps(record, ensure_ascii=False))
            print(
                f"fed {result['audio_ms'] / 1000:.1f} s in {result['wall_ms'] / 1000:.1f} s, "
                f"{len(result['finals'])} utterance(s), ended={result['ended']}",
                file=sys.stderr,
            )
        return 0
    except RuntimeError as exc:
        print(f"runtime_client: {exc}", file=sys.stderr)
        return 1


def parse_config(entries: list[str]) -> dict:
    """`min_silence_ms=700 enter_threshold=0.5` → typed dict (int/float/str)."""
    config: dict = {}
    for entry in entries:
        key, sep, value = entry.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config expects k=v, got {entry!r}")
        config[key.strip()] = coerce_value(value.strip())
    return config


def coerce_value(value: str):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


if __name__ == "__main__":
    sys.exit(main())
