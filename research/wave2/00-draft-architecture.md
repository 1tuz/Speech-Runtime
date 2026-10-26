# Черновик архитектуры Speech Runtime (гипотеза основного агента для критики во 2-й волне)

Статус: ГИПОТЕЗА. Основана на отчётах research/wave1/*.md. Подлежит независимой критике.

## Основа
Собственный сервис (не форк целиком). Готовые проекты используются как библиотеки движков внутри
воркеров: speech-swift (MLX/CoreML реализации моделей, AudioCommon-протоколы), FluidAudio (CoreML:
Parakeet/Nemotron/Sortformer/pyannote+WeSpeaker/Silero), sherpa-onnx (ONNX: GigaAM v3 RNNT/CTC, T-one,
Whisper, Parakeet, Nemotron, speaker embeddings, Silero/FireRed VAD), Apple SpeechAnalyzer (опционально).
Причины: у speech-swift сервер — глобальный InferenceGate (1 инференс на процесс), ModelCache без
выгрузки, ASR не потоковый, нет auth, нет diarize/vad/embedding-роутов (B1/B2). SpeechRail — один
активный артефакт, batch XOR streaming, только Qwen3 (C). speaches заморожен, WLK — одна модель на процесс.

## Процессы
```
clients (Swift/Rust/CLI/OpenAI SDK)
   │  HTTP+WS (loopback TCP, bearer token) | тот же протокол по UDS
   ▼
speechd (control plane, один процесс, launchd LaunchAgent)
   - API gateway: native /v1 + OpenAI-compat подмножество
   - Model Registry (CAS blobs + manifests + manifest list по движкам, sha256, revision pin, license per artifact)
   - Scheduler: refcount + TTL (keep_alive) + бюджет памяти + очереди по классам (realtime > interactive > batch)
   - Session manager: session_id, лимиты, отмена, backpressure
   - Worker supervisor: spawn/health/restart, lease-файлы
   │  UDS, бинарный фрейминг (len-prefixed header JSON/CBOR + raw PCM)
   ▼
engine workers (отдельные процессы, по семейству движков; модели внутри — с refcount)
   - worker-mlx (Swift; speech-swift MLX модели) — инференс сериализован внутри процесса (MLX evalLock)
   - worker-coreml (Swift; FluidAudio/speech-swift CoreML) — пул инстансов MLModel, 1 поток на инстанс
   - worker-onnx (sherpa-onnx; один Recognizer, N Stream, DecodeStreams батчинг)
   - worker-apple (SpeechAnalyzer; ru только DictationTranscriber)
```
Открытый вопрос: язык control plane — Rust (рекомендация F) или Swift (один тулчейн).
Гипотеза: control plane на Rust (tokio, process supervision, кроссплатформенность, малый RSS),
воркеры на Swift для Apple-движков и на Rust/C для sherpa-onnx (C API) — либо sherpa внутри Swift-воркера.

## Модель конкурентности
- Веса: один загруженный экземпляр модели на воркер; сессии держат lease (refcount).
- Состояние сессии (кэш энкодера, RNN-state VAD, декодер-гипотезы, буфер диаризации) — per-session, вне весов.
- Параллелизм: ONNX — батчинг потоков; CoreML — N инстансов (пул, память×N); MLX — сериализация, при
  необходимости второй воркер-процесс (память×2). Admission control по бюджету.
- Падение воркера: supervisor помечает сессии failed (error event), перезапускает воркер, модели грузятся лениво.

## API (черновик v1)
- REST: POST /v1/transcribe (batch, multipart или ссылка на файл), /v1/vad, /v1/diarize, /v1/embeddings/speaker,
  /v1/speakers/match (stateless), GET /v1/models, POST /v1/models/{id}:load|:unload, POST /v1/models:pull,
  DELETE /v1/models/{id}, GET /v1/ps, GET /v1/health, GET /v1/capabilities.
- WS /v1/stream: config(model, ops: asr/vad/eot/diarize, language, bias) → бинарные PCM кадры →
  события partial(revision, stable_prefix_chars)/final/vad.start/vad.end/eot/speaker/error → close.
- OpenAI-compat: /v1/audio/transcriptions (+ realtime transcription session — только completed).
- Ошибки RFC 9457, единый словарь code для HTTP/WS; версия протокола в handshake.
- Модель указывается явно всегда; runtime не выбирает модель по смыслу. Допустимы алиасы, заданные пользователем.

## Адаптация ru-IT
Runtime: per-request `bias` (термины+вес) → компиляция в модель-специфичный механизм (sherpa hotwords для
трансдьюсеров, FluidAudio CTC-WS/trie bias, prompt для Whisper/Qwen3/Voxtral, contextualStrings для Apple),
отчёт applied/rejected; хранение именованных словарей. Приложение: источник терминов, пост-коррекция LLM,
накопление исправлений.

## Матрица моделей (стартовая)
- ru batch точный: GigaAM v3 (e2e_rnnt/ctc, MIT, ONNX); Whisper large-v3-turbo (смешанный ru/en); Qwen3-ASR 1.7B.
- ru streaming: Nemotron 3.5 Streaming 0.6B (ru, OpenMDW); Voxtral Mini 4B Realtime (ru, Apache, MLX); T-one (без латиницы).
- VAD: Silero v6 (MIT); FireRedVAD. EOT: Smart Turn v3.x (BSD-2).
- Diarization: FluidAudio offline (pyannote community-1 + WeSpeaker, VBx); Nemotron-3-Diarization (streaming); Sortformer v2 (CC-BY).
- Embeddings: WeSpeaker ResNet34 / CAM++.

## План (зависимости)
0 протокол+реестр → 1 speechd + worker-onnx (GigaAM, Silero) batch → 2 стриминг WS + VAD/EOT → 3 worker-coreml/mlx
→ 4 диаризация/эмбеддинги/speaker match → 5 bias → 6 OpenAI-compat, gRPC (опц.), клиентские SDK Swift/Rust.
