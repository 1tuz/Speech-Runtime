# A4 — Discovery: существующие универсальные речевые серверы/демоны (состояние на 2026-10-25)

Задача: найти проекты, которые реализуют **общий** речевой сервер/демон/библиотеку для нескольких приложений (не CLI и не одно приложение), и проверить это по коду. Все ссылки на код — permalink с фиксированным SHA, который я реально читал локально. Метка `[INFERENCE]` — мой вывод, не факт из кода.

Проверено клонов в `/tmp/srr/extra`: `audio.cpp`, `LocalAI`, `mlx-audio`, `wyoming`, `wyoming-faster-whisper`, `whisper.cpp`, `vosk-server`, `moshi` (kyutai), `dsm`, `RealtimeSTT`, `whisper-asr-webservice`, `Handy`, `transcribe.cpp`, `dsnote`, `VoiceInk`, `sherpa` (k2-fsa), `parakeet-mlx`, `faster-whisper-server`, `ollama`.

---

## Итог

1. **Прямого аналога «Ollama для речи» на 2026-10-25 нет**, но ближе всех подошёл `0xShug0/audio.cpp`: один C++/ggml HTTP-сервер, реестр моделей в конфиге, `lazy_load` / `max_loaded_models` / `idle_unload_ms` / `min_free_memory_mb`, per-model busy-lock с таймаутом и `503 server_busy`, а также ASR + TTS + VAD + диаризация + выравнивание в одном процессе. Лицензия Apache-2.0. Слабое место: CUDA — основной целевой бэкенд, Metal есть, но покрытие/перф на Apple Silicon не подтверждено кодом или бенчмарками.
2. **Ollama сама уже трогает речь, но не как ASR-движок**: `/v1/audio/transcriptions` реализован как преобразование в chat-запрос к мультимодальной LLM (то есть распознавание идёт «LLM-ом», без таймстемпов и без dedicated ASR-моделей), а `/v1/audio/speech` — это TTS («speech»-capability, Phonon). Это подтверждено кодом, а не блогом.
3. **Самая зрелая инфраструктура жизненного цикла моделей — в LocalAI**: watchdog с idle/busy/LRU/memory-reclaimer, «pinned» модели, группы взаимоисключения, `max-active-backends` (по умолчанию 0 = без лимита), backends как отдельные процессы за gRPC, и готовые metal-варианты аудио-бэкендов (`whisper`, `parakeet-cpp`, `audio-cpp`, `sherpa-onnx`, `mlx-audio`). Цена — размер и сложность (Go + Python + C++ + галерея YAML).
4. **Wyoming — единственный «протокол», а не «сервер»**: JSONL-события + бинарные payload, `describe/info` для интроспекции моделей, mDNS `_wyoming._tcp`, по одному asyncio-handler на соединение. `wyoming-faster-whisper` поверх него уже держит 6 бэкендов (faster-whisper, transformers, sherpa, onnx-asr, funasr, qwen3-asr) и кеш моделей по (backend, model, streaming) с per-key lock. Это лучший образец «контракта между клиентом и речевым сервисом».
5. **`whisper.cpp --server` для роли общего рантайма не годится**: один глобальный `whisper_context`, все запросы под одним `std::mutex` (сериализация), `/load` меняет модель по пути к файлу и при ошибке делает `exit(1)`, OpenAI-эндпоинтов нет вовсе (только `/inference`).
6. **`vosk-server` — устарел**: последний коммит 2025-07-25. Архитектурно интересен (per-connection recognizer, thread-pool, смена модели через `config`-сообщение, `SpkModel` для x-vector эмбеддингов), но глобальная мутация `model` в общем скоупе и отсутствие развития исключают его как основу.
7. **Мультиклиентность «по-настоящему» реализована только в двух местах, которые я нашёл в коде**: sherpa-onnx (`OnlineRecognizer` один, `OnlineStream` на соединение, батч по `max_batch_size`; offline-сервер батчит `DecodeStreams` для нескольких соединений) и moshi-server (Rust/axum, `BatchedAsr` с `batch_size=64`, бинарный WS-протокол). Оба — только про стриминговый ASR, без управления моделями.
8. **Управление моделями (скачивание/версии/лицензии) лучше всего формализовано не в серверах, а в каталоге `transcribe.cpp`** (handy-computer): `schema: transcribe-catalog-v1`, variant/family/license.spdx/upstream_commit/size_bytes по квантизациям/capabilities с полем `verified`/WER-бенчмарки с CI. Это готовый шаблон схемы каталога для Runtime.
9. **Паттерн «приложение как сервер» стал массовым, но это не Runtime**: TypeWhisper (GPL-3.0, локальный REST `/v1/transcribe`, `/v1/models/load\|unload`), WhisperKit/argmax-cli `serve` (Vapor, один модель-аргумент на процесс), Handy (Tauri + worker-процесс с кадровым протоколом `[u32 json_len][json][u32 pcm_len][pcm]`). Полезны как источники API-решений, не как основа.
10. **Ни один из проверенных проектов не закрывает целевой набор целиком** (общий каталог + версии + несколько движков-адаптеров + одновременные независимые сессии + VAD/диаризация/эмбеддинги + ленивая загрузка + минимальный простой). Максимальное пересечение: `audio.cpp` (сервер/жизненный цикл) + `transcribe.cpp`/LocalAI gallery (каталог) + Wyoming (протокол) + sherpa-onnx/moshi (мультиклиентный стриминг).

---

## Факты

### Сводная таблица (что проверено)

Активность — дата последнего коммита в клоне или `pushed_at` GitHub API на 2026-10-25; звёзды — GitHub API на ту же дату.

| Проект | Что это | API | Мульти-клиент | Модели / менеджмент | Движки | VAD / диаризация | Активность | Лицензия | Вердикт |
|---|---|---|---|---|---|---|---|---|---|
| **audio.cpp** (`0xShug0/audio.cpp`) | C++/ggml HTTP-сервер + CLI, «Ollama для аудио» | OpenAI-совместимый: `/v1/models`, `/v1/models/load\|unload`, `/v1/audio/speech`, `/v1/audio/transcriptions`, `/transcriptions/details`, `/transcriptions/live` (SSE), `/v1/audio/alignments`, `/v1/batches/transcriptions`, `/v1/tasks/{run,batch,stream}` | Да (поток на соединение), но по умолчанию **один запрос на модель**; экспериментальные `slots` 1..16 | Реестр в `server.json` (`id/family/task/mode/quant`), lazy load, LRU-эвикция idle-моделей, idle-unload, memory-guard, встроенный model manager (скачивание/удаление пакетов) | ggml: 110+ семейств / 190+ вариантов (`README.md#L28`), ASR (в т.ч. **gigaam_asr ru/kk/ky/uz/en**, qwen3_asr с ru, parakeet_tdt, nemotron_asr streaming, vibevoice_asr_streaming), TTS, VC, sep | VAD: silero, firered_vad, marblenet, pulsevad, smart_turn; диаризация: sortformer, nemotron_3_diar, moss_transcribe_diarize | 2026-10-25, 3750★ | Apache-2.0 | **Брать как главный архитектурный референс** |
| **LocalAI** (`mudler/LocalAI`) | Универсальный локальный AI-сервер (Go), audio как подсистема | OpenAI: `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/audio/diarization`; realtime: `/v1/realtime`, `/v1/realtime/transcription_session`; legacy `/tts` | Да (HTTP/gRPC, много одновременных) | Галерея YAML (`gallery/*.yaml`), backend-процессы за gRPC, watchdog idle/busy/LRU/memory, pinned, группы взаимоисключения, `--max-active-backends` | ~20 аудио-бэкендов: `whisper` (whisper.cpp), `parakeet-cpp`, `sherpa-onnx`, `faster-whisper`, `whisperx`, `moonshine`, `mlx-audio`, TTS (`kokoro`, `piper`, `vibevoice`, `audio-cpp`…) | Да: методы `VAD`, `Diarize` в gRPC-контракте; `whisperx` + pyannote | 2026-10-25, 49950★ | MIT | **Брать жизненный цикл и gRPC-контракт**; тяжеловат |
| **mlx-audio** (`Blaizzy/mlx-audio`) | Python/FastAPI сервер поверх MLX | `/v1/models` GET/POST/DELETE, `/v1/audio/speech`, `/v1/audio/transcriptions` (в т.ч. `ndjson`, `stream`), `/v1/audio/voices`, `/v1/audio/separations`, WS `/v1/audio/transcriptions/realtime`, `/v1/realtime` | Да на уровне HTTP, но **вся инференс-работа сериализована одним broker-потоком**; TTS — continuous batch (env, дефолт 8) | Загрузка по имени HF-модели при первом запросе; `DELETE /v1/models` выгружает; idle-unload/версий/лицензий нет | MLX: 30 ASR-семейств (parakeet, nemotron_asr, qwen3_asr, voxtral, moonshine, granite…), VAD (silero, sortformer, nemotron_diarization), TTS | VAD: silero/webrtcvad в realtime-пути; диаризация: sortformer/nemotron-модели как отдельные таски | 2026-10-25, 8097★ | MIT | Брать API-форму и MLX-адаптер; сериализация — нет |
| **Wyoming** (`rhasspy/wyoming` → `OHF-Voice`) | Протокол + эталонные серверы (Python) | События JSONL: `describe/info`, `transcribe`, `audio-start/chunk/stop`, `transcript{,-start,-chunk,-stop}`, `synthesize`, `detect-language`, `ping` | Да: `asyncio`-задача на соединение | У проекта-хоста (см. ниже) | — | `vad.py` (sensitivity), endpointing в хостах | 2026-09-29, 410★ | MIT | **Брать протокол/интроспекцию/mDNS** |
| **wyoming-faster-whisper** (`rhasspy` → `OHF-Voice`) | Wyoming-сервер, 6 бэкендов | Wyoming | Да (per-connection handler, общий `ModelLoader`) | Кеш транскрайберов по `(library, model, streaming)` + `asyncio.Lock`; `--model auto` → эвристика по языку/ARM/GPU | faster-whisper, transformers, sherpa (batch+streaming), onnx-asr, funasr, qwen3-asr | Silero-endpointing, VAD-clipping | 2026-10-14, 394★ | MIT | **Брать как пример поли-бэкендного адаптера** |
| **whisper.cpp server** (`ggml-org/whisper.cpp`) | C++ HTTP-сервер | `/inference` (form-data), `/load`, `/health`; **OpenAI-эндпоинтов нет** | Нет: один `whisper_context` + один `std::mutex` | Модель задаётся `-m`; `/load` подменяет контекст по пути (и `exit(1)` при ошибке) | whisper.cpp (+ `parakeet-cli` в дереве) | `--vad` (Silero), `--diarize` (stereo), `--tinydiarize` | 2026-10-19, 54662★ | MIT | Не брать как сервер (только движок) |
| **vosk-server** (`alphacep/vosk-server`) | 4 сервера: websocket/grpc/mqtt/webrtc | WS-сообщения + `config` | Да (per-connection `KaldiRecognizer`, thread-pool) | Модель из argv; **смена модели через `config.model` мутирует глобальную переменную** | Vosk/Kaldi | `SpkModel` (x-vector) для spk-эмбеддингов | **2025-07-25**, 1265★ | Apache-2.0 | Не брать (устарел, гонки) |
| **moshi-server** (`kyutai-labs/moshi`) | Rust/axum сервер Kyutai STT/TTS | WebSocket, бинарный протокол (`MT`: handshake/audio/text/control) + REST (`/api/build_info`, `/metrics`) | Да, батчингом: `BatchedAsr` c `batch_size` (в конфиге 64) | Модели в TOML/JSON-конфиге (`modules` — HashMap), фиксированный набор модулей на процесс | Candle (LM/ASR/TTS/Mimi) — **только en/fr** | Нет VAD/диаризации как API | 2026-09-09, 11300★ | Apache-2.0 | Брать батч-планировщик; язык не наш |
| **sherpa-onnx** (`k2-fsa/sherpa-onnx`) | C++/Python, websocket-серверы | WS (online/offline), HTTP (`python-api-examples`) | Да: один recognizer + stream на соединение; offline-сервер батчит `DecodeStreams` | Модель из флагов; менеджмента/версий нет | onnxruntime: zipformer/paraformer/whisper/moonshine… | Silero VAD-модели в комплекте; speaker diarization (по описанию репозитория и каталогу моделей) | 2026-10-25, 15507★ | Apache-2.0 | Брать модель конкурентности |
| **Ollama** (`ollama/ollama`) | Go-демон, менеджер моделей | `/v1/audio/transcriptions` (→ chat-запрос), `/v1/audio/speech` (TTS, PCM/WAV stream), `/api/*`, `/v1/models` | Да: scheduler, `OLLAMA_NUM_PARALLEL` (дефолт 1), `OLLAMA_MAX_QUEUE` 512 | Манифесты/blobs, `pull/rm/create`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_KEEP_ALIVE` (дефолт 5m), capability-based маршрутизация | llama.cpp/MLX; для речи — мультимодальные LLM (`audio`) и TTS-модели (`speech`) | Нет | 2026-10-25, 184741★ | MIT | Брать менеджмент/keep-alive; ASR-путь не брать |
| **WhisperKit / argmax-cli serve** (`argmaxinc/WhisperKit`) | Swift/Vapor локальный сервер | `/v1/audio/transcriptions`, `/v1/audio/translations` (OpenAPI из спеки OpenAI), стриминг вывода | HTTP параллельно, но модель одна (из CLI-флага) | Нет: `serve --model …`, без загрузки/выгрузки | CoreML (WhisperKit), SpeakerKit (диаризация), TTSKit | Диаризация — отдельный CLI/SpeakerKit, не в API | 2026-10-11, 6417★ | MIT | Брать «Swift+OpenAPI» как шаблон клиента; сервер — нет |
| **whisper-asr-webservice** (`ahmetoner`) | Python/FastAPI | `/asr` (txt/vtt/srt/tsv/json), `/detect-language` | Да (async), но инференс блокирующий | Движок и модель фиксируются env (`ASR_ENGINE`, `MODEL_NAME`) на старте | openai-whisper, faster-whisper, whisperx | `vad_filter` (faster-whisper), диаризация только через whisperx | 2026-08-09 (HEAD), 3365★ | MIT | Не брать (нет выбора модели в рантайме) |
| **RealtimeSTT** (`KoljaB`) | Python-библиотека + `production_server.py` (FastAPI/WS) | `/v1/ws`, `/v1/audio/transcriptions/stream`, `/transcribe-pcm16`, health/capabilities | Да (WS-сессии, семафоры) | Одна модель+realtime-модель на процесс | faster-whisper (+ openwakeword, silero/webrtc VAD) | VAD: silero/webrtc; wake-word; диаризации нет | 2026-10-23, 10204★ | MIT | Не брать как сервер (нет реестра моделей) |
| **Handy** (`cjpais/Handy`) | Tauri-приложение + worker-процессы | внутрипроцессный кадровый протокол (`[u32 json_len][json][u32 pcm_len][pcm]`), наружу API нет | n/a (одно приложение) | Каталог движка + модель-файлы на диск | `transcribe-cpp` (ggml, 16 семейств) | VAD/диаризация — в движке | 2026-10-25, 34627★ | MIT | Брать паттерн supervisor+worker |
| **transcribe.cpp** (`handy-computer`) | C++/ggml ASR-движок + CLI + каталог | нет сервера | n/a | **Каталог `transcribe-catalog-v1`**: license.spdx, upstream_commit, downloads по квантизациям с `size_bytes`, capabilities с `verified`, WER CI | ggml, 16 семейств (в т.ч. **gigaam-v3 ctc/rnnt/e2e**) | diarize, vad, streaming флаги по семействам | 2026-10-25, 2085★ | MIT | **Брать схему каталога** |
| **TypeWhisper** (`TypeWhisper`) | macOS menu-bar приложение + локальный REST | `/v1/transcribe`, `/v1/transcribe/local-file`, `/v1/models` GET/`load`/`unload`/DELETE, `/v1/status`, `/v1/history`, `/v1/dictation/*` | Да, но процесс один и с UI | Загрузка/выгрузка по HTTP, импорт локальных моделей | локальные плагины + облачные API | VAD/диаризация — внутри плагинов | 2026-10-25, 1877★ | GPL-3.0 | Не брать (GPL, приложение) |
| **Speech Note / dsnote** (`mkiol/dsnote`) | Linux-приложение | **DBus** (single-instance контроль), не речевой API | n/a | Менеджер моделей внутри приложения | whisper.cpp, faster-whisper, sherpa-onnx, coqui/piper | VAD в движках | 2026-10-24, 1701★ | MPL-2.0 | Не брать (нет внешнего API) |
| **VoiceInk** (`Beingpax`) | macOS-приложение | XPC только для LLM-refine | n/a | Внутри приложения | whisper.cpp/parakeet и др. | — | 2026-10-25, 6918★ | GPL-3.0 (README) | Не брать |
| **parakeet-mlx** (`senstella`) | Python-библиотека + CLI | нет | n/a | HF-кеш | MLX Parakeet | — | 2026-10-01, 988★ | Apache-2.0 | Только движок |
| **speaches** (ex `fedirz/faster-whisper-server`) | Python/FastAPI, OpenAI-совместимый | `/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/models/{id}` POST/DELETE, `/v1/registry` | Да | `POST /v1/models/{id}` = установка, `DELETE` = удаление; реестр | faster-whisper, Kokoro TTS | VAD (faster-whisper), диаризация — роутер `diarization.py` | 2026-10-22, 3736★ | MIT | Смежный (см. примечание ниже) |
| **LM Studio** | Проприетарный локальный сервер | OpenAI-compat: `/v1/models`, `/v1/chat/completions`, `/v1/responses`, `/v1/embeddings`, `/v1/completions`, `/v1/decisions` — **аудио-эндпоинтов в списке нет** | Да | UI-менеджер | llama.cpp/MLX | — | docs | проприетарная | Не подходит (закрытый, без ASR API) |
| **llama.cpp `llama-server`** | Универсальный LLM-сервер | `/v1/chat/completions` с `multimodal_data` (audio), `/tts` (TTS + клонирование голоса), `/v1/models` с capability `audio` | Да (слоты) | Нет аудио-менеджмента | ggml/mtmd | Нет | 2026-10 (upstream) | MIT | Не ASR-сервер |
| **NVIDIA Riva / Speech NIM** | Контейнерные микросервисы | gRPC/REST | Да | NGC-контейнеры + профили | NeMo/TensorRT/Triton | VAD/диаризация в пайплайнах | docs | NVIDIA AI Enterprise (EULA) | Не для Apple Silicon |

Примечание про `faster-whisper-server`: `github.com/fedirz/faster-whisper-server` теперь редиректит на `speaches-ai/speaches` (GitHub API отдаёт `full_name: speaches-ai/speaches`, тот же SHA `993994f7…`, что и клон `speaches` в `/tmp/srr/repos`). То есть «форки faster-whisper-server» — это фактически один живой проект `speaches`; отдельной поддерживаемой ветки `fedirz` больше нет.

### audio.cpp — самый близкий к цели (проверено по коду)

Permalink-база: `https://github.com/0xShug0/audio.cpp/blob/1cada9b7045cc6935bcafb3f5a26806d133535e7/`.

- **Реестр моделей и политика памяти в конфиге сервера**: `lazy_load` (дефолт `false`), `max_loaded_models` (дефолт `0` = без лимита), `idle_unload_ms` (дефолт `0`), `busy_timeout_ms` (дефолт `300000`), `min_free_memory_mb` (дефолт `0`) — `app/server/config.h#L85-L114`.
- **Ленивая загрузка + эвикция**: `evict_for_model_limit()` вытесняет LRU **только idle** модель (`try_acquire` busy-лока) — `app/server/runtime.cpp#L614-L655`.
- **Admission-лок на модель с таймаутом**: `acquire_model_run()` → `model.busy.acquire(timeout_ms, model_id)`, `run_model()` берёт лок → готовит сессию → выполняет — `app/server/runtime.cpp#L779-L820`. Таймаут резолвится как `min(request, ceiling)` — `app/server/busy_guard.h#L52-L72`, ошибка маппится в HTTP 503 — `app/server/busy_guard.h#L15-L22`.
- **Idle-unload в фоновом потоке**, не выгружает модель во время инференса — `app/server/runtime.cpp#L2435-L2483`.
- **Маршруты**: `/health`, `/v1/models`, `/v1/models/load`, `/v1/models/unload`, `/v1/audio/speech`, `/v1/audio/speech/live`, `/v1/audio/transcriptions`, `/v1/audio/transcriptions/details`, `/v1/batches/transcriptions`, `/v1/audio/alignments`, `/v1/audio/transcriptions/live`, `/v1/tasks/{run,batch,stream,unload_models}` — `app/server/request_dispatch.h#L47-L146`.
- **Транспорт**: `accept()` + detached `std::thread(handle_client, …)` на соединение, backlog 16 — `app/server/http.cpp#L767-L905`. То есть конкурентность на уровне HTTP есть, а на уровне модели её ограничивает busy-lock.
- **Параллельные сессии — экспериментальные**: `--parallel-jobs` включает `ParallelServerState`, `slots` 1..16, `GET /v1/models` начинает отдавать `active_slots/queued_requests`; континуального батчинга нет — `app/server/README.md#L808-L866` (раздел «Experimental parallel model slots»); контракт поведения — `docs/maintainers/parallel_server_behavior_contract.md`.
- **RU-модель в каталоге семейств**: `gigaam_asr` (ru, kk, ky, uz, en; GigaAM v3 CTC/RNN-T/E2E, Multilingual) — `README.md#L134`; Qwen3-ASR с `ru` — `README.md#L153`; VAD/диаризация — `README.md#L140-L156`, `#L242`.
- **Политика дефолтной конкурентности явно описана**: «The default runtime runs one request per model at a time… Requests that cannot acquire the model within the policy return HTTP 503 (`server_busy`)» — `app/server/README.md#L451-L468`.

### LocalAI — зрелый жизненный цикл и gRPC-контракт

Permalink-база: `https://github.com/mudler/LocalAI/blob/f7011f18702f107a5c24e53ab0914b37523215b0/`.

- **Аудио-эндпоинты**: `/v1/audio/transcriptions` (+ legacy `/audio/transcriptions`), `/v1/audio/speech`, `/v1/audio/diarization`, realtime `/v1/realtime`, `/v1/realtime/sessions`, `/v1/realtime/transcription_session` — `core/http/routes/openai.go#L38-L41`, `#L194-L195`, `#L211-L212`, `#L239-L240`.
- **Watchdog как модель жизненного цикла**: поля `idleTime`, `lastUsed` (LRU), `pendingEvictions`, `timeout/idletimeout`, `busyCheck/idleCheck`, `lruLimit`, `memoryReclaimer*`, `sizeAwareEviction`, `pinnedModels`, `modelGroups` — `pkg/model/watchdog.go#L29-L77`. Цикл: `Run()` → `checkBusy/checkIdle/checkMemory` — `pkg/model/watchdog.go#L813-L846`; `checkIdle()` — `#L848-L878`.
- **Лимит одновременно загруженных бэкендов**: `--max-active-backends` (env `LOCALAI_MAX_ACTIVE_BACKENDS`, дефолт `0` = без лимита), `--single-active-backend` — deprecated — `core/cli/run.go#L121-L122`.
- **Капабилити-таблица по бэкендам** (что важно для Runtime): для `audio-cpp` — `MethodTTS, MethodTTSStream, MethodAudioTranscription, MethodVAD, MethodDiarize, MethodSoundGeneration, MethodAudioTransform`, usecases `TTS/Transcript/VAD/Diarization/SoundGeneration/AudioTransform`, с комментарием, что «какое RPC отвечает — решает семейство модели в GGUF» — `core/config/backend_capabilities.go#L589-L625`. Для `sherpa-onnx`: `AudioTranscription, TTS, TTSStream, VAD` — `core/config/backend_capabilities.go#L583-L589`.
- **gRPC-методы как минимальный ABI движка**: `AudioTranscription`, `VAD`, `Diarize` + `LoadModel` — `core/config/backend_capabilities.go#L57-L68`, `pkg/grpc/client.go#L152`, `#L379-L423`.
- **Metal-варианты аудио-бэкендов**: `metal-whisper` (`backend/index.yaml#L161-L186`), `metal-parakeet-cpp` (`#L287-L312`), `metal-audio-cpp` / `metal-darwin-arm64` (`#L133-L160`), `metal-mlx-audio` (`#L2106-L2127`), `metal-sherpa-onnx` (`#L3084`).
- **Модель описывается YAML-ом**, минимальный пример: `gallery/whisper-base.yaml` — `name` + `config_file: backend: whisper`.
- **Диаризация через whisperx** (pyannote; нужен HF-токен, диаризация считается лениво и кешируется) — `backend/python/whisperx/backend.py#L114-L123`.

### mlx-audio — Apple Silicon-специфичный сервер (но без параллелизма)

Permalink-база: `https://github.com/Blaizzy/mlx-audio/blob/0bda35ec9c54937f187db560eccb0173918291cd/`.

- **Реестр моделей — обычный dict в памяти**, `load_model(name)` при первом обращении, `remove_model` — удаление из dict, никаких таймеров/версий — `mlx_audio/server.py#L90-L116`.
- **Управление моделями через API**: `GET /v1/models`, `POST /v1/models?model_name=…`, `DELETE /v1/models` — `mlx_audio/server.py#L933-L985`.
- **Все вычисления сериализованы одним broker-потоком**: `InferenceBroker` создаёт один `threading.Thread(target=self._run)` и в цикле берёт запросы из `queue.Queue` — `mlx_audio/server_inference.py#L129-L150`; в `_run` прямой комментарий: «Keep all GPU work serialized» — `#L195-L250`. Следствие: два клиента на двух разных моделях **не** выполняются параллельно.
- **Стриминг/реалтайм есть**: WS `/v1/audio/transcriptions/realtime` (с webrtcvad) — `mlx_audio/server.py#L1283`, WS `/v1/realtime` (server VAD, Silero по умолчанию) — `#L1610`, CLI `--realtime`, `--vad-model`, `--tts-max-batch-size` — `#L2078-L2160`.
- **Транскрипция** поддерживает `response_format` (`ndjson` — родной стриминговый, `text`, `json`, `verbose_json`) — `mlx_audio/server.py#L1051-L1090`.
- **Загрузка моделей**: `load_model()` определяет категорию (`tts/stt/sts/lid/vad`) по конфигу/имени — `mlx_audio/utils.py#L862-L902`.

### Wyoming + wyoming-faster-whisper — контракт и поли-бэкендность

Permalink-базы: `https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/` и `https://github.com/rhasspy/wyoming-faster-whisper/blob/b03d4e40cf47e67969fe798b8149b62267db0711/`.

- **Типы событий ASR**: `transcript`, `transcribe`, `transcript-start/chunk/stop`; `Transcribe` несёт `name` (имя модели!), `language`, `context`, `vad_sensitivity`, `transcript_names/terms` (биасинг словаря) — `wyoming/asr.py#L9-L120`.
- **Интроспекция моделей**: `describe`/`info` + `AsrModel`, `AsrProgram`, `TtsVoice`, `HandleModel`, `WakeModel` — `wyoming/info.py#L11-L20`, `#L98-L112`.
- **Сервер**: `AsyncServer` → на каждое соединение `asyncio.create_task(handler.run())` — `wyoming/server.py#L64-L110`; TCP-вариант — `#L171`.
- **Обнаружение сервиса**: mDNS `_wyoming._tcp.local.` — `wyoming/zeroconf.py#L28-L46`.
- **Поли-бэкендность**: `SttLibrary` = `faster-whisper | transformers | sherpa | onnx-asr | funasr | qwen3-asr` — `wyoming_faster_whisper/const.py#L10-L19`; выбор бэкенда кешируется по языку, доступность проверяется через `find_spec` **без импорта** (иначе torch роняет процесс на некоторых ARM) — `wyoming_faster_whisper/models.py#L101-L133`.
- **Кеш транскрайберов + per-key `asyncio.Lock`** по `(stt_library, model, streaming)`; `auto`-модель выбирается эвристикой по языку/ARM/GPU — `wyoming_faster_whisper/models.py#L193-L236`.
- **Один `ModelLoader` на процесс, общий для всех соединений** — `wyoming_faster_whisper/__main__.py#L282-L341`.
- **Endpointing/VAD на входе** (Silero) + `audio-start/chunk/stop` конвертация в 16 кГц mono — `wyoming_faster_whisper/dispatch_handler.py#L30-L120`.

### whisper.cpp server — почему не сервер

Permalink-база: `https://github.com/ggml-org/whisper.cpp/blob/231638a0717b7822784297e5980965ffcec05810/`.

- **Единственный контекст + глобальный мьютекс**: `std::mutex whisper_mutex;` — `examples/server/server.cpp#L647`; `std::lock_guard<std::mutex> lock(whisper_mutex);` в начале обработчика `/inference` — `#L826-L828`.
- **`/load`**: принимает **путь к файлу** модели, освобождает контекст, инициализирует новый, при неудаче — `exit(1)` (TODO в коде: «perhaps load prior model here instead of exit») — `#L1175-L1210`.
- **`/health`** возвращает 503 пока модель грузится — `#L1218-L1228`.
- **OpenAI-совместимости нет**: `grep -c transcription examples/server/server.cpp` = 0; единственный путь инференса — `-ip/--inference-path`, дефолт `/inference` — `#L62`.
- **VAD/диаризация — флаги процесса**: `--vad/--vad-model`, `--diarize` (только стерео), `--tinydiarize` (нужна `*-tdrz` модель) — `examples/server/server.cpp#L155-L156`, `#L225`.
- Отдельно: в дереве появился `examples/parakeet-cli/` — whisper.cpp получил Parakeet-путь (ggml), но это CLI, не сервер.

### vosk-server — архитектурно интересно, но заброшено

Permalink-база: `https://github.com/alphacep/vosk-server/blob/47998a26f232252f7baaed53dd8ce89d38bbfbce/`.

- **Per-connection recognizer + thread-pool**: `pool = concurrent.futures.ThreadPoolExecutor(os.cpu_count())`, каждый чанк — `await loop.run_in_executor(pool, process_chunk, rec, message)` — `websocket/asr_server.py#L110-L115`, `#L71`.
- **Смена модели по сообщению клиента мутирует глобальную переменную**: `model = Model(jobj['model'])` внутри `recognize()` — `websocket/asr_server.py#L50-L52`. Это гонка между соединениями.
- **Speaker-эмбеддинги**: `SpkModel(args.spk_model_path)` + `rec.SetSpkModel(spk_model)` — `#L113`, `#L68-L69`.
- **GPU-батч-вариант** использует `BatchModel/BatchRecognizer/GpuInit` — `websocket-gpu-batch/asr_server_gpu.py#L11-L38`.
- **Устаревание**: последний коммит `2025-07-25` (≈15 месяцев на дату отчёта), при живом `vosk-api` (pushed 2026-10-15).

### moshi-server (Kyutai) — батч-планировщик для стриминга

Permalink: `https://github.com/kyutai-labs/moshi/blob/e6a55d2722a65870ef52a6c9f6ecfc0e90f38362/`.

- **Конфиг = набор модулей**: `pub modules: HashMap<String, ModuleConfig>` — `rust/moshi-server/src/main.rs#L201-L206`; `ModuleConfig::BatchedAsr { path, config, batch_size }` → `batched_asr::BatchedAsr::new(*batch_size, …)` — `#L352-L353`.
- **Батчированный цикл ASR**: тензоры формы `(batch_size, 1, frame_size)`, `StreamMask`, декинг по `channel_id` — `rust/moshi-server/src/batched_asr.rs#L164-L240`, `#L290-L300`.
- **Бинарный WS-протокол**: `MT=0 handshake / 1 audio (ogg-opus) / 2 text / 3 control / 4 metadata / 5 error / 6 ping` — `rust/protocol.md#L1-L40`.
- **Официальная эксплуатационная формулировка**: «server that can process multiple streaming queries in parallel… For a L40S GPU, a batch size of 64 works well and requests can be processed at 3x real-time speed» — `delayed-streams-modeling/README.md#L111-L150` (репозиторий `kyutai-labs/delayed-streams-modeling`, SHA `4c4f65e1…`); конфиг с `batch_size = 64` — `configs/config-stt-en_fr-hf.toml`.
- **Языки**: только `en_fr` / `en` (модели `stt-1b-en_fr`, `stt-2.6b-en`) — не подходит для русской речи.

### sherpa-onnx — эталон мультиклиентности на shared weights

Permalink-база: `https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/`.

- **Online WS-сервер**: один `OnlineRecognizer` на декодер, `recognizer_->CreateStream()` на каждое соединение, очередь `ready_connections_`, `max_batch_size` — `sherpa-onnx/csrc/online-websocket-server-impl.cc#L60-L90`, `#L127-L205`.
- **Offline WS-сервер**: собирает до `max_batch_size` соединений, по стриму на соединение, затем `recognizer_.DecodeStreams(p_ss.data(), size)` (комментарий: «DecodeStreams is thread-safe») — `sherpa-onnx/csrc/offline-websocket-server-impl.cc#L59-L123`.
- Именно этот сервер используют сторонние клиенты как «локальный HTTP/WS ASR» (например, Obsidian-плагин Local Speech Recognition указывает на сборки sherpa-onnx).

### Ollama — менеджмент моделей есть, ASR-движка нет

Permalink-база: `https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/`.

- **Роуты**: `r.POST("/v1/audio/transcriptions", middleware.TranscriptionMiddleware(), s.ChatHandler)` и `r.POST("/v1/audio/speech", s.SpeechHandler)` — `server/routes.go#L2425-L2426`.
- **Транскрипция = chat-запрос к мультимодальной модели**: middleware парсит multipart (лимит 25 МБ), собирает `openai.TranscriptionRequest` и подменяет тело на `chatReq` — `middleware/openai.go#L1352-L1420`; сама конверсия добавляет system-prompt «Transcribe the audio exactly as spoken…» и кладёт аудио в `Images` сообщения user с `temperature: 0` — `openai/openai.go#L956-L979`.
- **Capabilities**: `audio` (понимание аудио) и `speech` (генерация речи) — разные capability — `types/model/capability.go#L6-L16`; маршрутизация по capability в `server/speech.go#L26-L70`.
- **TTS**: `/v1/audio/speech` стримит PCM/WAV, лимит 4096 символов, поддерживает только `speed=1` — `server/speech.go#L26-L78`; в runner — `POST /v1/audio/speech`, стрим 24 кГц 16-bit PCM — `mlxrunner/server.go#L239`, `mlxrunner/speech.go#L19-L30`.
- **Жизненный цикл**: `OLLAMA_NUM_PARALLEL` (дефолт **1**), `OLLAMA_MAX_QUEUE` (512), `OLLAMA_MAX_LOADED_MODELS` (0), `OLLAMA_KEEP_ALIVE` (5m) — `envconfig/config.go#L126-L129`, `#L276-L281`, `#L325-L328`; scheduler с `expireTimer` и вытеснением — `server/sched.go#L287-L389`.
- **Итог**: dedicated ASR-моделей (whisper/parakeet/gigaam) в библиотеке нет — `https://ollama.com/library/whisper` и `/library/parakeet` отдают 404; блог 2026-10-24 анонсирует «speech models… starting with Phonon from Gradium» (это TTS): https://ollama.com/blog.

### WhisperKit / argmax-cli serve — Swift-сервер на один процесс

Permalink-база: `https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/`.

- **Vapor + OpenAPI, сгенерированный из спеки OpenAI**: `let transport = VaporTransport(routesBuilder: app)` … `handler.registerHandlers(on: transport, serverURL: URL(string: "/v1")!)` — `Sources/ArgmaxCLI/Server/ServeCLI.swift#L27-L45`; хост/порт — `#L73-L76`.
- **Модель выбирается аргументом CLI и грузится на старте** (нет per-request выбора, нет load/unload) — тот же файл `#L31-L39`.
- **Эндпоинты**: только `/audio/transcriptions` и `/audio/translations` — `scripts/specs/localserver_openapi.yaml#L16-L47`.
- **Разделение OSS/Pro**: README прямо пишет, что «Argmax Local Server for non-native apps» и WS-реалтайм с Deepgram-совместимым API — только в Pro — `README.md#L30-L35`, `#L268-L274`.

### Handy + transcribe.cpp — паттерн «супервизор + worker» и схема каталога

Permalink-базы: `https://github.com/cjpais/Handy/blob/73c095857bc4eadff097f64767c40ad129cc372d/` и `https://github.com/handy-computer/transcribe.cpp/blob/8facde6fcd8fb9caa585a5788ab450df6e8ec7db/`.

- **Кадровый IPC-протокол**: «Each message is one frame: `[u32 json_len][json][u32 pcm_len][pcm bytes]`… Strict request/response: every `Request` gets exactly one `Response`»; enum `Request::{Hello, Load, Run, StreamBegin, Feed, Finalize, StreamReset}`, `DeviceSelector::{Auto, Key, Index}` — `src-tauri/src/engine_supervisor/protocol.rs#L1-L93`.
- **Worker как отдельный процесс со stdin/stdout**, закрытие stdin = команда на выход, crash worker'а → respawn — `src-tauri/src/engine_supervisor/supervisor.rs#L1080-L1150`, `#L265`.
- **Каталог моделей** `transcribe-catalog-v1`: `variant`, `family`, `params`, `license{spdx,display}`, `upstream_repo`, `upstream_commit`, `published_repo`, `languages[]`, `long_form_strategy`, `capabilities{transcribe,translate,lang_detect,timestamps,streaming,diarize,batching}` c `supported/verified`, `downloads[{quant,filename,size_bytes}]`, `accuracy_benchmarks[]` с CI и `n_utts` — `catalog/nemotron-3.5-asr-streaming-0.6b.json#L1-L50`; в каталоге есть `gigaam-v3-ctc.json`, `gigaam-v3-rnnt.json`, `gigaam-v3-e2e-*.json`.

### Прочее, что проверил (коротко, с доказательством)

- **RealtimeSTT**: `production_server.py` — FastAPI с `/v1/ws`, `/v1/audio/transcriptions/stream`, `/transcribe-pcm16`, health/ready/capabilities — `https://github.com/KoljaB/RealtimeSTT/blob/a3c3484434e527dd6e50ae39dc7a557b4a8d1d29/RealtimeSTT_server/production_server.py#L4886-L5100`; модель задаётся аргументами процесса (`-m`, `-r`) — `RealtimeSTT_server/stt_server.py#L404-L420`. Реестра моделей/выгрузки нет.
- **whisper-asr-webservice**: движок фиксируется `ASR_ENGINE` (env) — `https://github.com/ahmetoner/whisper-asr-webservice/blob/ec32cd50b508412e0b8ebe81c26cd7a7dc900b7a/app/config.py#L12-L17`; фабрика создаёт **один** движок — `app/factory/asr_model_factory.py#L10-L20`; обработчик `/asr` без параметра модели — `app/webservice.py#L54-L110`.
- **TypeWhisper** (GPL-3.0): локальный REST с `/v1/transcribe`, `/v1/models/load`, `/v1/models/unload`, `DELETE /v1/models`, `/v1/dictation/*` — `https://github.com/TypeWhisper/typewhisper-mac/blob/main/TypeWhisper/Services/HTTPServer/APIHandlers.swift#L55-L77`; токен-аутентификация и привязка к loopback — `TypeWhisper/Services/HTTPServer/APIRouter.swift#L93-L146`.
- **dsnote (Speech Note)**: «сервер» — это DBus single-instance контроллер (`QDBusConnection::sessionBus()`, `registerService`), а не речевой API — `https://github.com/mkiol/dsnote/blob/6a3b1fc7163733f0f2d7333238a0dd9108132e3e/src/app_server.cpp#L8-L45`.
- **VoiceInk**: XPC-сервис только для LLM-«refine» (`VoiceInkRefineXPCService.swift`), наружу API нет — `https://github.com/Beingpax/VoiceInk/blob/b6f5baff7d95b8a7696c8f488226e9da5e468e7a/VoiceInkRefineXPC/VoiceInkRefineXPCService.swift`; лицензия GPL-3.0 (README).
- **parakeet-mlx**: только библиотека + `cli.py`, сервера нет — `https://github.com/senstella/parakeet-mlx/blob/2d9748c04aca31405274a13c20ef9b62f1a73351/parakeet_mlx/cli.py`.
- **LM Studio**: список OpenAI-совместимых эндпоинтов (`/v1/models`, `/v1/responses`, `/v1/decisions`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/completions`) **без** аудио — https://lmstudio.ai/docs/developer/openai-compat.
- **llama.cpp**: аудио только как вход мультимодальной модели (`multimodal_data`, base64, маркеры MTMD) — `tools/server/README.md#L518`; TTS-эндпоинт `POST /tts` с клонированием по `speaker_ref` — `#L849-L871`; capability `audio` — `#L1358`. `/v1/audio/transcriptions` отсутствует.
- **NVIDIA Riva / Speech NIM**: самодостаточные контейнеры (NeMo + TensorRT + Triton) с gRPC/REST, разворачиваются на NVIDIA GPU (RTX/DC) — https://docs.nvidia.com/nim/speech/latest/about/index.html , https://docs.nvidia.com/nim/speech/latest/asr/ . Локальность есть, Apple Silicon — нет.
- **speaches** (см. примечание про редирект): установка/удаление моделей через API — `https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/models.py#L83-L110`, реестр — `#L112`.
- **k2-fsa/sherpa** (PyTorch-фреймворк, 1013★, Apache-2.0, push 2026-10-25): серверные скрипты `sherpa/bin/streaming_server.py` («It supports multiple clients sending at the same time»), `offline_ctc_server.py`, `offline_transducer_server.py`, gRPC-сервер `sherpa/cpp_api/grpc/online-grpc-server-impl.cc`; это исследовательский предшественник sherpa-onnx, для продакшена авторы отсылают к sherpa-onnx.

---

## Ограничения и риски

1. **Ни один проект не является «Runtime» в нашем смысле.** Все они либо (а) сервер вокруг одного движка, либо (б) менеджер LLM-моделей с побочной поддержкой речи, либо (в) приложение с побочным API. Комбинировать придётся самим.
2. **`audio.cpp` — CUDA-first.** README прямо говорит: «CUDA is the primary optimization target; performance and model coverage differ on other backends» (`app/server/README.md#L325-L326`), при этом Metal-сборка существует (`README.md#L482`, таблица платформ `#L257`). Проверить реальную скорость/покрытие ASR на M-серии по коду невозможно — нужны замеры.
3. **`audio.cpp` по умолчанию не параллелит одну модель**: один запрос на модель, `503 server_busy` при занятости; параллельные сессии — экспериментальные (`--parallel-jobs`, `slots` 1..16) с явным предупреждением, что GPU-streaming и полный жизненный цикл не верифицированы (`app/server/README.md#L808-L866`).
4. **LocalAI тяжёл**: Go-ядро + Python-бэкенды + C++-бэкенды + галерея YAML + gRPC-супервизор портов (`core/services/worker/supervisor.go`, 45 КБ). Для «минимальных накладных расходов в простое» это плохой фундамент: каждый бэкенд — отдельный процесс (плюс память интерпретатора для Python-бэкендов). Реально ли на macOS стабильно работают metal-варианты `whisper`/`parakeet-cpp`/`audio-cpp` — не проверял запуском.
5. **`mlx-audio` сериализует GPU**: один broker-поток на весь сервер (`server_inference.py#L195-L250`). Формально многоклиентность есть, фактически два клиента встают в очередь — это ровно тот антипаттерн, от которого мы уходим.
6. **Wyoming — «умный» сервер**: `wyoming-faster-whisper` сам выбирает модель и бэкенд по языку (`guess_stt_library`/`guess_model`), что противоречит нашему принципу «клиент явно указывает модель и операцию». Протокол годится, политика выбора — нет.
7. **`vosk-server` устарел** (2025-07-25) и содержит гонку: смена модели клиентом мутирует глобальную переменную (`websocket/asr_server.py#L50-L52`).
8. **`whisper.cpp --server` небезопасен как общий сервис**: `/load` принимает произвольный путь к файлу и при неудаче убивает процесс (`server.cpp#L1175-L1210`); модель одна; всё сериализовано.
9. **Ollama-путь ASR — это LLM-ответ, а не распознавание**: нет таймстемпов, нет сегментов, нет language-модели ASR, качество зависит от того, «послушается» ли LLM system-prompt; лимит 25 МБ на файл (`middleware/openai.go#L1352-L1360`). Для русской речи с IT-терминами это риск галлюцинаций и «перевода» вместо транскрипции.
10. **Лицензии**: `TypeWhisper` и `VoiceInk` — GPL-3.0 (нельзя брать код в закрытый/иной по лицензии продукт); `dsnote` — MPL-2.0 (файловый copyleft); `audio.cpp` — Apache-2.0 (код), но **веса моделей имеют свои лицензии** (например, `nemotron-3.5-asr-streaming-0.6b` в каталоге transcribe.cpp помечен `OpenMDW-1.1`, `catalog/nemotron-3.5-asr-streaming-0.6b.json#L7-L12`) — при установке моделей Runtime обязан показывать лицензию весов отдельно от лицензии кода.
11. **Русский язык**: из всех серверов только `audio.cpp` (gigaam_asr, qwen3_asr) и `transcribe.cpp` (gigaam-v3) имеют RU-модели «из коробки»; LocalAI — через `audio-cpp`/`faster-whisper`/`whisper` (то есть те же веса), но без явной RU-таблицы; mlx-audio — только qwen3_asr/voxtral (RU есть в qwen3), wyoming-faster-whisper — через faster-whisper/whisper модели.

---

## Выводы для Runtime

### Что взять

1. **Модель жизненного цикла — как в `audio.cpp`, плюс `pinned`/`groups` из LocalAI.** Минимальный набор состояний и настроек, который стоит скопировать дословно:
   `lazy_load`, `max_loaded_models`, `idle_unload_ms`, `min_free_memory_mb`, per-model `busy_timeout_ms` c правилом `effective = min(request, ceiling)`, `503 server_busy`, `503 insufficient_memory`, вытеснение **только idle** LRU-модели, `POST /v1/models/load|unload`, `POST /v1/tasks/unload_models` (`runtime.cpp#L614-L820`, `#L2435-L2483`, `busy_guard.h#L15-L72`). Добавить из LocalAI: `pinned` (не выгружать), `model_groups` (взаимоисключение больших моделей), `memory_reclaimer` по порогу (`watchdog.go#L29-L77`).
2. **Каталог моделей — по схеме `transcribe-catalog-v1`.** Поля, которые доказали свою нужность: `family`, `task`, `mode (offline|streaming)`, `variant`, `params`, `languages[]`, `license{spdx,display,link}`, `upstream_repo`, `upstream_commit`, `downloads[{quant, filename, size_bytes}]`, `capabilities{..., verified}`, `accuracy_benchmarks[]`. Это даёт «установку/удаление/обновление/версии» почти бесплатно: версия = `upstream_commit` + список файлов с размерами. **Важно**: в схеме нет хешей файлов (grep по `sha256` в `catalog/*.json` и `catalog/_schema.json` — 0 совпадений), только `size_bytes`, — целостность скачивания нам придётся добавить самим. Аналог по смыслу — `model_specs/*.json` в audio.cpp и YAML-галерея LocalAI, но `transcribe.cpp` формализован лучше всех.
3. **Контракт клиент↔сервис — Wyoming-подобный, но без авто-выбора модели.** Взять: явные события (`audio-start/chunk/stop`, `transcribe`, `transcript-start/chunk/stop`), интроспекцию (`describe`/`info` со списком моделей и их возможностей — `wyoming/info.py#L11-L112`), поле `name` (модель) в запросе (`wyoming/asr.py#L67-L120`), mDNS-обнаружение (`_wyoming._tcp` → у нас `_speechruntime._tcp`), плюс unix-сокет для «своих» приложений. Не брать: авто-выбор модели по языку.
4. **Поверх этого — OpenAI-совместимый фасад** (`/v1/audio/transcriptions`, `/v1/audio/speech`, `/v1/models`), потому что это единственная форма, которую уже понимают клиенты: так сделано в LocalAI, mlx-audio, Ollama, WhisperKit, speaches, whisper-asr-webservice. Но: OpenAI-API не умеет «сессии» и «модель-независимый стриминг» — нужен свой нативный API рядом (ср. `/v1/audio/transcriptions/live` в audio.cpp, `/v1/realtime` в mlx-audio/LocalAI, Deepgram-совместимый WS в Argmax Pro).
5. **Конкурентность — три разных механизма, не путать:**
   - streaming-сессии на общих весах: один экземпляр модели, по стриму на клиента (sherpa-onnx `online-websocket-server-impl.cc#L60-L90`); это ровно то, что нам нужно для «переиспользования весов между клиентами»;
   - батчинг офлайн-запросов: собрать N соединений → `DecodeStreams` (sherpa-onnx offline `#L59-L123`) или `BatchedAsr` с фиксированным `batch_size` (moshi);
   - сериализация на модель с очередью и таймаутом (audio.cpp busy-guard) — как честный fallback, а не как основной режим.
   **Не** копировать `mlx-audio`: один broker-поток на всё.
6. **Адаптер движка — по образцу gRPC-контракта LocalAI**: `LoadModel`, `AudioTranscription` (+ stream), `TTS`/`TTSStream`, `VAD`, `Diarize`, `AudioTransform`, `SoundGeneration` (`backend_capabilities.go#L57-L68`, `#L606-L625`). Это ровно тот минимальный набор операций, который различает CoreML/MLX/ONNX-адаптеры, и он используется десятками бэкендов в галерее LocalAI. Плюс правило LocalAI: «какое RPC доступно — решает семейство модели», то есть capability объявляется моделью, а не адаптером.
7. **Изоляция движка — по паттерну Handy**: worker-процесс на движок + кадровый протокол `[u32 json_len][json][u32 pcm_len][pcm]` + `Hello/Load/Run/StreamBegin/Feed/Finalize` (`protocol.rs#L1-L93`), respawn при падении (`supervisor.rs#L1080-L1150`). Это дешёвый способ не тащить в один процесс несовместимые рантаймы (MLX/PyTorch/CoreML/ONNX) и одновременно лечить падения движка.
8. **`OLLAMA_KEEP_ALIVE`-семантика** (по умолчанию 5 мин) и `OLLAMA_MAX_LOADED_MODELS` — проверенный UX-дефолт для «загрузка по требованию + переиспользование весов между клиентами» (`envconfig/config.go#L126-L129`, `#L278-L281`). Взять как дефолты, заменив на «idle_unload_ms».

### Что не брать

- **`whisper.cpp --server` как сервер** — один контекст, глобальный мьютекс, `/load` по произвольному пути с `exit(1)`. Движок — да, сервер — нет.
- **`vosk-server`** — заброшен, гонка при смене модели, нет управления моделями.
- **`mlx-audio` как архитектуру конкурентности** — единственный broker-поток; брать только API-формы и MLX-адаптер.
- **Ollama ASR-путь (`/v1/audio/transcriptions` → chat)** — нет таймстемпов/сегментов, лимит 25 МБ, зависимость от податливости LLM. Как отдельную «опцию для мультимодальных моделей» — можно, как основной ASR — нет.
- **`RealtimeSTT`/`whisper-asr-webservice`** — движок и модель фиксируются на старте процесса; это не рантайм, а «демо-сервер».
- **`TypeWhisper`/`VoiceInk`** — GPL-3.0 + приложение-центричность.
- **`dsnote`** — «сервер» это DBus-контроль единственного экземпляра приложения.
- **`moshi-server`** для RU-задач — только en/fr модели; планировщик `BatchedAsr` изучать, но не переиспользовать целиком (Rust+candle+Kyutai-специфика).

### Как интегрировать (практическая раскладка)

- **Обязательный минимум MVP**: свой демон (Go/Rust/Swift — вне рамок этой задачи) с: каталогом моделей по схеме `transcribe-catalog-v1`; ленивой загрузкой; idle-unload с keep-alive; per-model single-flight + очередь с таймаутом и `503`; OpenAI-совместимыми `/v1/audio/transcriptions` и `/v1/audio/speech`; нативным WS/сокет-протоколом для стриминга и VAD/диаризации; mDNS-обнаружением.
- **Адаптеры**: (1) ONNX Runtime (sherpa-onnx-модели, VAD/диаризация) — самый переносимый; (2) MLX (mlx-audio-модели, Apple Silicon); (3) CoreML (WhisperKit/FluidAudio-модели); (4) ggml/`audio.cpp`- или `transcribe.cpp`-модели как «быстрый C++ путь». Каждый адаптер объявляет capability-таблицу, как в LocalAI.
- **Где можно переиспользовать чужой код напрямую** (лицензии позволяют): `audio.cpp` (Apache-2.0) — целиком как движковый слой/сервер; `transcribe.cpp` (MIT) — как ggml-движок и каталог; `LocalAI` (MIT) — как источник идей/кода watchdog и gRPC-контракта; `wyoming`/`wyoming-faster-whisper` (MIT) — как протокол; `sherpa-onnx` (Apache-2.0) — как движок и образец WS-сервера.
- **Чего избегать в дизайне**: автоподбора модели (wyoming-faster-whisper), глобального мутабельного состояния модели (vosk-server, whisper.cpp `/load`), одного GPU-потока на весь сервер (mlx-audio), «модель = аргумент процесса» (WhisperKit serve, whisper-asr-webservice, RealtimeSTT).

---

## Неизвестные / что измерить

1. **`audio.cpp` на Apple Silicon**: реальные RTF/latency для ASR на Metal (gigaam_asr ru, qwen3_asr, parakeet), покрытие семейств на Metal vs CUDA, потребление памяти при `max_loaded_models=2`. В README нет ни одного замера для macOS — только заявления о поддержке (`README.md#L257`, `#L482`).
2. **`audio.cpp` параллельные сессии**: работает ли `--parallel-jobs` с реальными каталожными моделями (в README прямо сказано, что контролируемые тесты не допускают реальные модели к multi-slot, а GPU streaming/жизненный цикл не верифицированы, `app/server/README.md#L818-L826`).
3. **LocalAI на macOS**: стабильность `metal-whisper`/`metal-parakeet-cpp`/`metal-audio-cpp`, холодный старт процесса бэкенда, RSS Python-бэкендов (`whisperx`, `faster-whisper`) в простое. Всё это не измерял.
4. **Простой (idle) каждого демона**: у `whisper.cpp`, `wyoming-*`, `sherpa-onnx` модель остаётся резидентной всегда; у LocalAI/audio.cpp есть idle-unload, но фактический остаточный RSS после выгрузки (аллокаторы MLX/Metal не всегда возвращают память ОС) не измерен.
5. **Совместимость mlx-audio с «двумя приложениями одновременно»**: broker сериализует; насколько это критично для двух интерактивных сессий (очередь > 1 запроса) — не измерено.
6. **Качество RU + IT-терминов**: gigaam_asr (audio.cpp/transcribe.cpp), qwen3_asr, nemotron_asr на нашей терминологии (Kubernetes/PostgreSQL/Istio/CI/CD) — ни один проект не публикует таких бенчмарков; нужен собственный прогон. WER в каталоге transcribe.cpp есть только для FLEURS и по языкам, без доменной лексики.
7. **Latency оверхеда протоколов**: Wyoming (JSONL + WAV-буферизация в файл!) — в `dispatch_handler.py` аудио пишется в tmp-WAV; насколько это добавляет задержки на длинных сессиях — не измерено.
8. **Лицензии весов** для RU-моделей (GigaAM, Qwen3-ASR, Nemotron) — нужно отдельно проверить условия распространения/коммерческого использования; в отчёте зафиксирована только пометка каталога `OpenMDW-1.1` для Nemotron и `spdx: other` в transcribe.cpp.
9. **Существует ли ещё один «Ollama для речи»**, который я не нашёл: поиск по GitHub API (`topic:asr`, `speech-to-text server`, `language:rust suche`) дал только перечисленные проекты; web-поиск в момент работы частично отказывал (провайдеры блокировали запросы), поэтому нельзя исключать проект без звёзд/без топиков. Кандидаты, которые стоит догуглить вручную: `owhisper`/`anarlog` (бывший Hyprnote — `owhisper serve` с Deepgram-совместимым API, см. J-community), `FluidInference/fluid-server` (14★, последний push 2025-12-15, Windows-only — по метаданным мёртв).
10. **Не проверял запуском ни один сервер** (задача — исследование): все утверждения о поведении — из кода и документации в репозиториях.

---

## Источники

**Код (permalink с проверенным SHA)**

- audio.cpp @ `1cada9b7045cc6935bcafb3f5a26806d133535e7` — https://github.com/0xShug0/audio.cpp/blob/1cada9b7045cc6935bcafb3f5a26806d133535e7/app/server/config.h#L85-L114 ; `app/server/runtime.cpp#L614-L820`, `#L2435-L2483` ; `app/server/request_dispatch.h#L47-L146` ; `app/server/busy_guard.h#L15-L72` ; `app/server/http.cpp#L767-L905` ; `app/server/README.md#L451-L468`, `#L808-L866`, `#L325-L326` ; `README.md#L28`, `#L125-L163`, `#L236-L260`, `#L482` ; `LICENSE`.
- LocalAI @ `f7011f18702f107a5c24e53ab0914b37523215b0` — `core/http/routes/openai.go#L38-L41`, `#L194-L195`, `#L211-L212`, `#L239-L240` ; `pkg/model/watchdog.go#L29-L77`, `#L813-L878` ; `core/cli/run.go#L121-L122` ; `core/config/backend_capabilities.go#L57-L68`, `#L589-L625` ; `backend/index.yaml#L133-L186`, `#L287-L312`, `#L2106-L2127`, `#L3084` ; `gallery/whisper-base.yaml` ; `backend/python/whisperx/backend.py#L114-L123` ; `pkg/grpc/client.go#L152`, `#L379-L423`.
- mlx-audio @ `0bda35ec9c54937f187db560eccb0173918291cd` — `mlx_audio/server.py#L90-L116`, `#L933-L985`, `#L989-L1015`, `#L1051-L1090`, `#L1283`, `#L1610`, `#L2078-L2160` ; `mlx_audio/server_inference.py#L129-L150`, `#L195-L250` ; `mlx_audio/utils.py#L862-L902`.
- wyoming @ `08a0c258df83fea6e1643bcfe67e86243f01cf31` — `wyoming/asr.py#L9-L120` ; `wyoming/info.py#L11-L20`, `#L98-L112` ; `wyoming/server.py#L64-L110`, `#L171` ; `wyoming/zeroconf.py#L28-L46`.
- wyoming-faster-whisper @ `b03d4e40cf47e67969fe798b8149b62267db0711` — `wyoming_faster_whisper/const.py#L10-L19` ; `models.py#L101-L133`, `#L193-L236` ; `__main__.py#L282-L341` ; `dispatch_handler.py#L30-L120`.
- whisper.cpp @ `231638a0717b7822784297e5980965ffcec05810` — `examples/server/server.cpp#L62`, `#L155-L156`, `#L225`, `#L647`, `#L826-L828`, `#L1175-L1228` ; дерево `examples/parakeet-cli/`.
- vosk-server @ `47998a26f232252f7baaed53dd8ce89d38bbfbce` — `websocket/asr_server.py#L50-L71`, `#L110-L115` ; `websocket-gpu-batch/asr_server_gpu.py#L11-L38`.
- moshi @ `e6a55d2722a65870ef52a6c9f6ecfc0e90f38362` — `rust/moshi-server/src/main.rs#L201-L206`, `#L352-L353` ; `rust/moshi-server/src/batched_asr.rs#L164-L240`, `#L290-L300` ; `rust/protocol.md#L1-L40`.
- delayed-streams-modeling @ `4c4f65e147df056adf3346290d64c7b9649b18c9` — `README.md#L111-L150`, `configs/config-stt-en_fr-hf.toml`.
- sherpa-onnx @ `532f1b5a8a207bf53b116b747207c1506696c843` — `sherpa-onnx/csrc/online-websocket-server-impl.cc#L60-L205` ; `offline-websocket-server-impl.cc#L59-L123` ; `python-api-examples/http_server.py`.
- Ollama @ `d44c2581904a98430e348d89cc2af1529b1808a0` — `server/routes.go#L2425-L2426` ; `middleware/openai.go#L1352-L1420` ; `openai/openai.go#L956-L979` ; `types/model/capability.go#L6-L16` ; `server/speech.go#L26-L78` ; `mlxrunner/server.go#L239` ; `mlxrunner/speech.go#L19-L30` ; `envconfig/config.go#L126-L129`, `#L276-L281`, `#L325-L328` ; `server/sched.go#L287-L389`.
- WhisperKit @ `9f99b5cb6a907730543823ad271b82532782a899` — `Sources/ArgmaxCLI/Server/ServeCLI.swift#L27-L76` ; `scripts/specs/localserver_openapi.yaml#L16-L47` ; `README.md#L30-L35`, `#L268-L300`.
- Handy @ `73c095857bc4eadff097f64767c40ad129cc372d` — `src-tauri/src/engine_supervisor/protocol.rs#L1-L93` ; `supervisor.rs#L265`, `#L1080-L1150`.
- transcribe.cpp @ `8facde6fcd8fb9caa585a5788ab450df6e8ec7db` — `catalog/nemotron-3.5-asr-streaming-0.6b.json#L1-L50`, `catalog/gigaam-v3-*.json` ; `README.md#L1-L55`.
- whisper-asr-webservice @ `ec32cd50b508412e0b8ebe81c26cd7a7dc900b7a` — `app/config.py#L12-L17` ; `app/factory/asr_model_factory.py#L10-L20` ; `app/webservice.py#L54-L110`.
- RealtimeSTT @ `a3c3484434e527dd6e50ae39dc7a557b4a8d1d29` — `RealtimeSTT_server/production_server.py#L4886-L5100` ; `stt_server.py#L404-L420`.
- speaches @ `993994f7984bf3fe9655b267448328cf66fccb42` — `src/speaches/routers/models.py#L83-L112`.
- dsnote @ `6a3b1fc7163733f0f2d7333238a0dd9108132e3e` — `src/app_server.cpp#L8-L45`.
- VoiceInk @ `b6f5baff7d95b8a7696c8f488226e9da5e468e7a` — `VoiceInkRefineXPC/VoiceInkRefineXPCService.swift`.
- k2-fsa/sherpa @ `497a51f74bd9c07caee66a38190ab7687bf314cc` — `sherpa/bin/streaming_server.py`, `sherpa/cpp_api/grpc/online-grpc-server-impl.cc`.

**Документация / веб**

- Ollama blog, «Speech models in Ollama» (2026-10-24): https://ollama.com/blog ; отсутствие ASR-моделей: https://ollama.com/library/whisper (404), https://ollama.com/library/parakeet (404).
- LM Studio OpenAI-compat (без аудио-эндпоинтов): https://lmstudio.ai/docs/developer/openai-compat
- llama.cpp server: audio в `multimodal_data` https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#L518 ; `/tts` https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#L849-L871
- NVIDIA Speech NIM: https://docs.nvidia.com/nim/speech/latest/about/index.html , https://docs.nvidia.com/nim/speech/latest/asr/
- audio.cpp server docs: https://github.com/0xShug0/audio.cpp/blob/main/app/server/README.md , https://github.com/0xShug0/audio.cpp/blob/main/app/server/api.md
- TypeWhisper REST API: https://github.com/TypeWhisper/typewhisper-mac/blob/main/TypeWhisper/Services/HTTPServer/APIHandlers.swift
- Метаданные репозиториев (звёзды, `pushed_at`, лицензии) — GitHub API `/repos/...` и `/search/repositories`, получено 2026-10-25. В частности редирект `fedirz/faster-whisper-server` → `speaches-ai/speaches`.
- Смежные отчёты этой волны (не дублирую факты): `D2-streaming-asr.md` (модели и стриминг), `J-community.md` (OWhisper/Hyprnote `owhisper serve`, ожидания сообщества), `K-licenses-maintenance.md` (лицензии зависимостей), `C-speechrail.md`.
