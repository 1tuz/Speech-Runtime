# Handy client: Parakeet-only contract

- In this integration, the only configured ASR model is `parakeet-ultra`.
  The experimental measurements about GigaAM remain in historical research
  files, but GigaAM is not required for this setup.
- Handy discovers its bearer token locally at
  `~/Library/Application Support/SpeechRuntime/token`; no manual API key.
- Runtime binds `127.0.0.1:8477` by default. Never expose it to the LAN.
- Preflight: `GET /v1/models`, then `POST /v1/models/load` if the
  Parakeet model is unloaded. A first-time ANE preparation may be slow.
- Dictation on key release: raw 16 kHz mono WAV to
  `POST /v1/audio/transcriptions?model=parakeet-ultra&priority=interactive`.
- File import/history retry: the same endpoint with `priority=batch`.
  Chunking into reasonable segments preserves UI progress and cancellation
  between chunks. The daemon's batch splitter must not block interactive
  requests indefinitely.
- Do not pass a forced `language=ru` hint: it can suppress Latin spellings
  of mixed Russian/English technical speech in this Parakeet configuration.
- Do not substitute Whisper or other ASR models if Runtime is unreachable.
  Show an error and preserve the recording for retry.
- HTTP dictation cancellation drops Handy's in-flight client future. This
  does not guarantee that already-running batch work is cancelled by the
  daemon; proper server cancellation uses the streaming session API.
- Runtime already has session endpoints (`/v1/streams`, `/audio`, `/events`,
  `/end`, DELETE). It currently publishes utterance-final events, not
  word-by-word interim hypotheses. A future Handy streaming client should
  consume only the capabilities actually declared by `/v1/models`.

Run `bash runtime/check-handy-runtime.sh` after installing and starting
Speech Runtime. This checks the catalog and connectivity, not audio quality.
