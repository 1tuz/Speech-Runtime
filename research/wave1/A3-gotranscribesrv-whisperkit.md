# A3. gotranscribesrv и серверные возможности WhisperKit

Дата: 2026-10-25. Аудит по исходникам двух клонов:
`gotranscribesrv` @ `a5f4289078553ba66f83510c62146043bed91a7a` (2026-08-26),
`WhisperKit` @ `9f99b5cb6a907730543823ad271b82532782a899` (2026-10-10).
Все permalink'и ниже указывают на эти SHA. Пометка `[INFERENCE]` — мой вывод, не факт из кода.

---

## Итог

1. **gotranscribesrv — это не локальный speech runtime, а биллинговый API-шлюз.** Go/Fiber-сервер (аутентификация, JWT, тарифы, rate limit, usage, Prometheus) проксирует запросы в отдельный Swift-сайдкар `sidecar-audio` (Vapor + FluidAudio) на порт 8101. Локального инференса в Go-части нет вообще.
2. **Ни whisper.cpp, ни MLX, ни ONNX там нет.** Единственный ASR-движок — FluidAudio `AsrModels.downloadAndLoad(version: .v3)`, т.е. NVIDIA Parakeet TDT 0.6B v3 в CoreML на ANE. Это 25 европейских языков + японский → **русского языка в стеке нет**, что делает проект непригодным как источник ASR-адаптера для нашей задачи (но пригодным как источник архитектурных решений).
3. **Выбора модели не существует ни на одном уровне.** `model` в `/v1/audio/transcriptions` не валидируется и не используется (это прямо написано в комментарии к коду), `/v1/models` — статический список с фейковыми ID (`whisper-1`, `gpt-4o-transcribe`, `tts-1`), а в ответе транскрипции всегда жёстко прошито `"model": "parakeet-tdt-v3-coreml"`. Для Runtime это антипаттерн, который нельзя копировать.
4. **Конкурентность устроена ровно наоборот от того, что нужно нам.** Batch-движки живут в одном `actor EngineManager` (то есть веса общие — хорошо), но движок «истинного стриминга» создаётся **на каждое WebSocket-соединение** (`variant.createManager()` + `engine.loadModels()`), т.е. N одновременных сессий = N копий весов в памяти (плохо). Комментарии в коде признают, что FluidAudio-менеджеры хранятся как `nonisolated(unsafe)`.
5. **Стриминговый протокол — самая ценная часть проекта.** Два слоя: (а) `/stream` — буферизация + пере-транскрипция буфера каждые ~2 с (partials) с VAD-эндпоинтингом и роллинг-финалами; (б) `/stream/realtime` — настоящий cache-aware streaming (Parakeet EOU 120M / Nemotron 0.6B / Parakeet Unified 0.6B, 10 вариантов размера чанка 160–2240 мс) + потоковый Silero VAD с событиями `speech_started`/`speech_stopped` и встроенным EOU для turn-taking. Событийная модель (`ready/partial/final/end_of_turn/done`) — готовый образец для нашего WS-протокола.
6. **Лицензия gotranscribesrv — GPL-3.0 на весь репозиторий**, включая Swift-сайдкар (он лежит в том же дереве). Код нельзя переиспользовать в permissive-продукте; можно читать и воспроизводить идеи (идеи лицензией не защищены).
7. **WhisperKit (переименован в `argmaxinc/argmax-oss-swift`, MIT) действительно содержит HTTP-сервер:** `argmax-cli serve` (он же `whisperkit-cli`), Vapor + swift-openapi, эндпоинты `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`, `GET /health`, дефолт `localhost:50060`, спецификация сгенерирована из OpenAI OpenAPI v2.3.0. **Но сервер по умолчанию не компилируется**: каталог `Sources/ArgmaxCLI/Server` исключён из target, подкоманда `serve` появляется только при `BUILD_ALL=1`.
8. **OSS-сервер WhisperKit — одномодельный и однофайловый.** Один `WhisperKit`-инстанс создаётся при старте; поле `model` из запроса только пишется в лог; `stream=true` даёт SSE-дельты `transcript.text.delta` по мере обработки окон **уже загруженного файла** — это не real-time с микрофона и не выбор модели. Настоящий real-time WebSocket-сервер (Deepgram-совместимый), real-time диаризация и custom vocabulary — платный Argmax Pro.
9. **Модельный слой WhisperKit — лучший готовый референс для нашего каталога моделей:** `ModelManager` с явной state machine `downloading → downloaded → loading → loaded` (+ `prewarming/prewarmed`, `unloading`), коалесингом параллельных загрузок (`ensureModelsLoaded()`), `unloadModels()`, `prewarmModels()`; настраиваемые `downloadBase` / `modelFolder` / `modelRepo` / `endpoint` / `revision` (`ModelDownloadConfig.revision` — branch/tag/sha). Дефолтный кэш — `~/Documents/huggingface`, но общий каталог задаётся параметром, и HF-совместимое зеркало тоже.
10. **Как ASR-адаптер WhisperKit годится, но с оговорками:** пакет даёт MIT-лицензию, per-component `MLComputeUnits`, `concurrentWorkerCount` (одна модель может параллельно обрабатывать несколько файлов — библиотека делает это сама через task group), SpeakerKit (pyannote v4, open source) и TTSKit (Qwen3-TTS, **русский среди 10 языков**). Мешают: `WhisperKit` — `open class` без actor-изоляции и с несинхронизированными `currentTimings`/`progress`; потоковый API (`AudioStreamTranscriber`) жёстко привязан к микрофону; VAD только энергетический (`EnergyVAD`); серверная обвязка для нескольких моделей/сессий отсутствует и её надо писать самим.

---

## Факты

### A3.1. gotranscribesrv — язык, архитектура, лицензия

| Параметр | Значение | Доказательство |
|---|---|---|
| Модуль | `github.com/shaunagostinho/gotranscribesrv`, Go 1.25.6 | `go.mod:1-3` — [permalink](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/go.mod#L1-L3) |
| HTTP-стек | Fiber v2.52.12 (+ contrib/websocket, fasthttp/websocket), GORM+Postgres, Prometheus, golang-jwt | [go.mod:5-18](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/go.mod#L5-L18) |
| Swift-сайдкар | `sidecar-audio` (Vapor ≥4.99 + FluidAudio `exact: 0.15.5`), `sidecar-llm` (Vapor + CoreML LLM) | [`sidecar-audio/Package.swift:85-87`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Package.swift#L85-L87) |
| Лицензия | **GNU GPL v3** (текст 673 строки), README повторяет | [`LICENSE.md:1-2`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/LICENSE.md#L1-L2), [`README.md:397-399`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/README.md#L397-L399) |
| Активность | 70 коммитов, 1 автор (Shaun Agostinho), **0 тегов/релизов**, HEAD 2026-08-26 | `git rev-list --count HEAD`=70, `git tag`=∅, [`git log`](https://github.com/sagostin/gotranscribesrv/commits/a5f4289078553ba66f83510c62146043bed91a7a) |
| Ни whisper.cpp/MLX/ONNX | `grep -rn "whisper.cpp\|ggml"` по `*.go/*.swift/*.md` — 0 совпадений; в `go.mod` нет ASR-зависимостей | проверено в клоне |

Топология из `docs/architecture.md`: Go (:3000) держит всё HTTP, Swift (:8101) — весь аудио-инференс, Swift (:8080) — LLM, Postgres — единственный общий компонент; узлы заявлены как stateless и масштабируются добавлением Mac mini. Раздельное развёртывание поощряется: Go на обычном сервере, Mac'и — чистые инференс-ноды за Caddy ([`docs/architecture.md:89-142`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/docs/architecture.md#L89-L142)).

### A3.2. gotranscribesrv — API-поверхность (Go-шлюз)

| Метод | Путь | Что делает | Доказательство |
|---|---|---|---|
| GET | `/health` | статус + модели сайдкара | [`cmd/server/main.go:215`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L215) |
| GET (WS) | `/ws/asr` | нативный стриминг (прокси) | [`:253`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L253) |
| GET (WS) | `/v2/listen` | Deepgram-compat **true real-time** | [`:294`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L294) |
| GET (WS) | `/v1/realtime` | OpenAI Realtime (transcription + опц. S2S) | [`:309`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L309-L313) |
| GET (WS)+POST | `/v1/listen` | Deepgram: WS (буферизованный) + pre-recorded REST | [`:371`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L371) |
| GET (WS)+POST | `/v1/recognize` | IBM Watson-compat | [`:338`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L338) |
| POST | `/api/v1/asr` | нативный batch ASR | [`:351`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L351) |
| GET | `/v1/models` | OpenAI-совместимый список (или ElevenLabs-формат при `xi-api-key`) | [`:357`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L357-L362) |
| POST | `/v1/audio/transcriptions` | OpenAI Whisper-compat | [`:363`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L363) |
| POST | `/v1/audio/speech`, `/api/v1/tts` | TTS (OpenAI / native) | [`:374-377`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L374-L377) |
| POST | `/v1/text-to-speech/:voice_id` (+`/stream`), `/v1/voices*`, `/v2/voices` | ElevenLabs-compat TTS + клонирование голоса | [`:380-387`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L380-L387) |
| POST | `/v1/chat/completions`, `/v1/messages`, `/v1/embeddings`, `/v1/images/generations`, `/v1/responses`, `/v1/conversations*` | LLM-шлюз (OpenAI + Anthropic диалекты) за флагом `ENABLE_LLM` | [`:390-406`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L390-L406) |
| — | `/api/v1/admin/*` | пользователи, ключи, глобальный usage, load/unload LLM-моделей | [`:428-485`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L428-L485) |

Все прикладные маршруты (кроме auth и WS-upgrade) идут через группу с `AuthMiddleware + RateLimiter + UsageTracker` ([`main.go:341-345`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/cmd/server/main.go#L341-L345)). Аутентификация — JWT access/refresh + API-ключи (`X-API-Key`).

### A3.3. gotranscribesrv — движки и модели (Swift-сайдкар)

Сайдкар поднимает 7 групп роутов: `healthRoutes`, `transcribeRoutes`, `streamRoutes`, `realtimeStreamRoutes`, `vadRoutes`, `diarizeRoutes`, `ttsRoutes` ([`sidecar-audio/Sources/Server/main.swift:48-54`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/main.swift#L48-L54)).

| Компонент | Модель / движок | Примечание | Доказательство |
|---|---|---|---|
| Batch ASR | `AsrModels.downloadAndLoad(version: .v3)` → `AsrManager(config: .default)` | Parakeet TDT 0.6B v3, CoreML/ANE | [`EngineManager.swift:83-96`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L83-L96) |
| Streaming ASR | `StreamingModelVariant` из FluidAudio: `parakeetEou160/320/1280ms`, `nemotron560/1120/2240ms`, `parakeetUnified320/640/1120/2080ms` | маппинг строк `?engine=` → вариант | [`RealtimeStreamRoute.swift:41-57`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/RealtimeStreamRoute.swift#L41-L57) |
| VAD | `VadManager` (Silero, CoreML/ANE), `VadStreamState` для 4096-сэмпловых чанков | и batch-сегментация, и потоковый режим | [`EngineManager.swift:97-106`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L97-L106), [`:161-177`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L161-L177) |
| Диаризация | `SortformerDiarizer` + `SortformerModels.loadFromHuggingFace` | «до 4 спикеров, для телефонных звонков» (комментарий автора) | [`EngineManager.swift:107-124`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L107-L124) |
| TTS | `PocketTtsManager` (стриминг + клонирование голоса) и `KokoroAneManager` (EN/zh/ja, только batch) | оба грузятся необязательно | [`EngineManager.swift:125-151`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L125-L151) |
| ITN | `TextNormalizer` (FluidAudio) + опциональная статически слинкованная `text-processing-rs` (Rust-порт NeMo ITN), символы `nemo_*` экспортируются через `-exported_symbol` для `dlsym` | без либы — passthrough | [`sidecar-audio/Package.swift:29-76`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Package.swift#L29-L76) |

Языковая поддержка (по документации FluidAudio, к которой привязан сайдкар): Parakeet TDT v3 — «25 European languages», Parakeet EOU — English only, Nemotron — en/es/fr/it/pt/de/zh/ja, Cohere Transcribe — 14 языков ([`Documentation/Models.md:14,20,33`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Models.md#L14-L33)). **Русского нет ни в одном ASR-варианте.** При этом параметр `language` принимается на всех уровнях (`internal/handlers/asr.go:104` — дефолт `"en"`), передаётся в сайдкар как поле формы и **в сайдкаре не используется**: в `TranscribeRoute` поле `audio.language` объявлено ([`:403`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L403)) и ни разу не читается.

Отдельно: в конфиге Go-сервера есть мёртвые поля `ASRModel` / `ASRRuntime` с дефолтами `mlx-community/parakeet-tdt-0.6b-v3` / `mlx` ([`internal/config/config.go:135-137`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/config/config.go#L135-L137)), которые **нигде не используются** (`grep -rn "cfg.ASRModel"` → только объявление). Это следы прежнего MLX-движка, а не рабочая функциональность.

### A3.4. gotranscribesrv — выбор модели: его нет

- Комментарий в коде: «The `model` field on `/v1/audio/transcriptions` is still not validated — unknown IDs are accepted and silently fall through to the real engine» ([`internal/handlers/models.go:49-50`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/models.go#L49-L50)).
- `/v1/models` — статический слайс с фейковыми OpenAI-ID (`whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`, `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`) плюс один настоящий (`parakeet-tdt-v3-coreml`) и `pocket-tts-1` ([`models.go:51-70`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/models.go#L51-L70)).
- Ответ транскрипции всегда содержит `model: "parakeet-tdt-v3-coreml"` (литерал) ([`TranscribeRoute.swift:183`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L183)).

### A3.5. gotranscribesrv — конкурентность и сессии

- **Один actor на все batch-движки.** `actor EngineManager` хранит `AsrManager`, `VadManager`, `SortformerDiarizer`, `TtsManager`, `KokoroManager` как `nonisolated(unsafe)` с комментарием «их методы потокобезопасны (внутренне синхронизированы через CoreML), и мы вызываем их только из контекста этого актора» ([`EngineManager.swift:1-22`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L1-L22)). FluidAudio `AsrManager` — тоже `actor` ([`AsrManager.swift:6`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/SlidingWindow/TDT/AsrManager.swift#L6)), так что batch-инференс сериализуется, а веса — общие.
- **Streaming-движок — на соединение.** В обработчике WS: `let engine = variant.createManager()` ([`RealtimeStreamRoute.swift:226`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/RealtimeStreamRoute.swift#L226)), затем `try await engine.loadModels()` внутри сессии ([`:315`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/RealtimeStreamRoute.swift#L315)), а в `onClose` — `engine.cleanup()`. `createManager` в FluidAudio документирован как «The returned manager is not yet loaded — call `loadModels()` before use» ([`ParakeetModelVariant.swift:133-166`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Streaming/ParakeetModelVariant.swift#L133-L166)). `[INFERENCE]` Каждая сессия получает собственный набор CoreML-моделей; N одновременных стримов масштабируют память линейно, а не разделяют веса.
- **Сессии stateless и «одна сессия = одно WS-соединение».** Go-слой держит `dgRealtimeSession` на соединение (два WS-проксирования: клиент↔Go↔сайдкар) ([`deepgram_realtime.go:52-75`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/deepgram_realtime.go#L52-L75)). Межзапросного состояния нет вообще — ни кэша моделей, ни менеджера сессий.
- **Ёмкость — расчёт автора, не бенчмарк.** «1 S2S-сессия ≈ 2 обычных ASR-стрима; M4 24 GB ≈ 3–4 S2S сессии, M4 Pro ≈ 5–6» ([`docs/realtime.md:235-241`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/docs/realtime.md#L235-L241)). Цифры не подтверждены измерениями в репозитории.

### A3.6. gotranscribesrv — стриминг, два уровня

**(а) `/stream` — буферизованный, Deepgram-подобный.** Внутри: буфер PCM 16 кГц, partial каждые 32 000 сэмплов (~2 с) при ≥16 000 сэмплов, финалы по VAD-эндпоинтингу (300 мс тишины по умолчанию, настраивается `?endpointing=`), роллинг-финал каждые 10 с, минимум 1600 сэмплов на финал ([`StreamRoute.swift:39-56`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/StreamRoute.swift#L39-L56)). Явно задокументированное ограничение: «known limitation: /stream has no real diarizer» — при `diarize=true` всем словам проставляется спикер 0 ([`:76-78`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/StreamRoute.swift#L70-L71)).

**(б) `/stream/realtime` — настоящий streaming.** Протокол: клиент шлёт бинарные PCM-кадры (`linear16`/`mulaw`/`alaw`, 8 кГц апсемплится ×2) + JSON-контрол (`stop` / `CloseStream` / `KeepAlive`); сервер шлёт `ready`, `partial`, `final`, `end_of_turn`, `speech_started`, `speech_stopped`, `done`, `error` ([`RealtimeStreamRoute.swift:7-33`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/RealtimeStreamRoute.swift#L7-L33)). Аудио, пришедшее до окончания загрузки модели, буферизуется и досылается после `markEngineReady()` ([`:313-330`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/RealtimeStreamRoute.swift#L313-L330)).

### A3.7. gotranscribesrv — качество batch-ответа (что стоит воспроизвести)

- Word-level таймстемпы строятся из `result.tokenTimings` с разбором `▁`/пробельных маркеров границ слова ([`TranscribeRoute.swift:44-100`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L44-L100)).
- Сегментация — группировка слов по паузам > 0.8 с; при диаризации — пересборка сегментов по смене спикера или паузе > 2 с + сводка по спикерам (число сегментов, слов, суммарная длительность) ([`:201-215`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L201-L215), [`:225-300`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L225-L300)).
- Назначение спикера слову — по максимальному перекрытию с diar-сегментом, фолбэк — по ближайшему сегменту ([`:369-400`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/Routes/TranscribeRoute.swift#L369-L400)).

### A3.8. WhisperKit — что это за пакет и где граница OSS/Pro

- Пакет переименован в `argmax-oss-swift`; продукты: `ArgmaxOSS` (umbrella), `WhisperKit`, `TTSKit`, `SpeakerKit`; исполняемые: `argmax-cli` и `whisperkit-cli` (оба → таргет `ArgmaxCLI`) ([`Package.swift:8-38`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L8-L38), [`Sources/ArgmaxOSS/ArgmaxOSS.swift:3-6`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxOSS/ArgmaxOSS.swift#L3-L6)).
- Лицензия: MIT (© 2024 argmax, inc.), `NOTICES` с атрибуцией swift-transformers (Apache-2.0) ([`LICENSE:1-3`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/LICENSE#L1-L3)).
- README прямо перечисляет, что закрыто в **Argmax Pro SDK**: real-time transcription with speakers, custom vocabulary, «Argmax Local Server for non-native apps», Android ([`README.md:27-37`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L27-L37)). Про Local Server отдельная note: «Argmax Pro Local Server provides a real-time streaming transcription with a WebSocket local server that is API-compatible with cloud-based providers such as Deepgram» ([`README.md:271-273`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L271-L273)). То есть **WebSocket/real-time сервер — платный**, OSS-сервер — только REST+SSE.
- Активность: 228 коммитов в клоне, HEAD 2026-10-10, последний тег `v1.1.1` (2026-10-07); до этого `v1.0.0` 2026-05-01 (`git for-each-ref refs/tags`).
- SpeakerKit и TTSKit добавлены как open source позже (`git log`: «Add SpeakerKit with Pyannote speaker diarization support (#440)», затем рефакторинг на `ModelManager` #452) — обе библиотеки в MIT-дереве.

### A3.9. WhisperKit — HTTP-сервер: сборка, эндпоинты, ограничения

| Факт | Доказательство |
|---|---|
| Сервер компилируется только при `BUILD_ALL=1`; иначе каталог `Sources/ArgmaxCLI/Server` исключён из таргета и `serve` отсутствует в списке подкоманд | [`Package.swift:146-152`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L146-L152), [`:139-140`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L139-L140), [`Sources/ArgmaxCLI/ArgmaxCLI.swift:8-13`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/ArgmaxCLI.swift#L8-L13), Makefile-цель `build-local-server` = `BUILD_ALL=1 swift build -c release --product argmax-cli` ([`Makefile:203-205`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Makefile#L203-L205)) |
| Дефолт `--host localhost --port 50060` | `Sources/ArgmaxCLI/Server/ServeCLIArguments.swift:11,14` |
| Ровно один `WhisperKit` на процесс, создаётся в `configure()` | `Sources/ArgmaxCLI/Server/ServeCLI.swift:43` |
| OpenAPI-сервер монтируется под `/v1` | `Sources/ArgmaxCLI/Server/ServeCLI.swift:45` |
| `GET /` → листинг (`status/service/endpoints`), `GET /health` → `{"status":"ok"}` (без состояния моделей) | `Sources/ArgmaxCLI/Server/ServeCLI.swift:48-62` |
| Эндпоинты: `POST /v1/audio/transcriptions`, `POST /v1/audio/translations` | [`scripts/specs/localserver_openapi.yaml:22-56`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/scripts/specs/localserver_openapi.yaml#L22-L56) |
| Спецификация сгенерирована из OpenAI OpenAPI v2.3.0 (`make generate-server`) | [`localserver_openapi.yaml:1-16`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/scripts/specs/localserver_openapi.yaml#L1-L16) |
| Мультипарт-поля: `file`, `model`, `language`, `prompt`, `response_format`, `temperature`, `include[]`, `timestamp_granularities[]`, `stream` | `Sources/ArgmaxCLI/Server/OpenAIHandler.swift:121-190` |
| `model` парсится, но только логируется (`logger.debug("Request model: …")`); движок не переключается | `Sources/ArgmaxCLI/Server/OpenAIHandler.swift:151-152`, `:229-231` |
| `response_format`: только `json` и `verbose_json`; чтение `srt`/`vtt`/`text` не реализовано (fallthrough в `verbose_json`) | `Sources/ArgmaxCLI/Server/OpenAIHandler.swift:265-330`; ограничение задокументировано в [`README.md:380-384`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L380-L384) |
| `stream=true` → `text/event-stream`: `AsyncStream<ArraySlice<UInt8>>`, в который колбэк прогресса пишет `transcript.text.delta`, а в конце — `transcript.text.done`; внутри вызывается `whisperKit.transcribe(audioPath:…)` по уже загруженному файлу | `Sources/ArgmaxCLI/Server/OpenAIHandler.swift:22-118`, `:233-247` |
| Аутентификации, лимитов, очередей, пула моделей нет; аудио пишется во временный файл и удаляется не всегда (tmp-файл создаётся, `defer` удаления нет) | `Sources/ArgmaxCLI/Server/OpenAIHandler.swift:141-149` (`[INFERENCE]` — утечка временных файлов при долгой работе) |

### A3.10. WhisperKit — модели, кэш, каталог

- **Дефолтный кэш:** `downloadBase ?? FileManager…documentDirectory + "huggingface"`, layout `downloadBase/models/<repo-id>/…`, эндпоинт `HF_ENDPOINT` или `https://huggingface.co` ([`Sources/ArgmaxCore/External/Hub/HubApi.swift:100-115`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/External/Hub/HubApi.swift#L100-L115), `:333-335`). Явно указано: «Argmax-modification: removed the HF_TOKEN_PATH, HF_HOME, ~/.cache/huggingface, and ~/.huggingface token file lookups» ([`:131`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/External/Hub/HubApi.swift#L131)) → **стандартный HF-кэш не подхватывается**, каталог только через параметр.
- **Общий каталог возможен:** `WhisperKitConfig` принимает `downloadBase`, `modelFolder` (локальная папка без скачивания), `modelRepo` (свой репозиторий), `modelEndpoint` (своё зеркало), `useBackgroundDownloadSession` ([`Sources/WhisperKit/Core/Configurations.swift:20-115`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Configurations.swift#L20-L115)); в CLI это `--download-model-path`, `--model-path`, `--endpoint` ([`TranscribeCLIUtils.swift:11-42`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/TranscribeCLIUtils.swift#L11-L42)).
- **Версионирование:** `ModelDownloadConfig` c `revision` (branch/tag/sha), `endpoint`, `modelRepo`, `downloadBase`, `modelFolder` ([`Sources/ArgmaxCore/ModelDownloader.swift:9-30`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ModelDownloader.swift#L9-L30)); `ModelInfo(version:variant:name:computeUnits:)` с `modelURL(baseURL:)` и `downloadPattern` (`name/version/variant/*`) — готовый ключ каталога «модель/версия/вариант/кванты» ([`ModelDownloader.swift:298-340`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ModelDownloader.swift#L298-L340)).
- **Lifecycle-менеджер:** `ModelManager` (open class, `@unchecked Sendable`) с `modelState` (`unloaded → downloading → downloaded → loading → loaded`, `prewarming → prewarmed`, `unloading`), `ensureModelsLoaded()` (коалесит параллельные вызовы через `LoadModelsCoordinator`, при ошибке задача сбрасывается), `downloadModels()`, `prewarmModels()`, `loadModels()`, `unloadModels()`, протокол `ModelLoader` для своего бэкенда ([`Sources/ArgmaxCore/ModelManager.swift:1-200`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ModelManager.swift#L1-L200), `:75-80`, `:121`, `:159`, `:194`).
- **Prewarm = компиляция в кэш ОС без удержания весов** — задокументировано как способ снизить пиковую память при первом запуске ([`ModelManager.swift:112-125`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ModelManager.swift#L112-L125), `WhisperKit.prewarmModels()` → `loadModels(prewarmMode: true)`).
- **Каталог моделей Whisper:** enum `ModelVariant` — только семейство Whisper (`tiny…large-v3` + `.en`-варианты) ([`Sources/WhisperKit/Core/Models.swift:39-60`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Models.swift#L39-L60)); но `model` — это glob по имени папки в репозитории (`openai*<model>`, [`TranscribeCLIUtils.swift:29`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/TranscribeCLIUtils.swift#L29)), поэтому подключаются и свои CoreML-конверсии через `whisperkittools` с любым `modelRepo`. Рекомендуемая модель выбирается по `config.json` из репозитория (`ModelSupportConfig`, device_support → default/supported/disabled, [`Models.swift:120-190`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Models.swift#L120-L190)).

### A3.11. WhisperKit — compute units

`ModelComputeOptions` (публичный, `Sendable`): `melCompute = .cpuAndGPU`, `audioEncoderCompute = .cpuAndNeuralEngine` (macOS 14+/iOS 17+, иначе `.cpuAndGPU`), `textDecoderCompute = .cpuAndNeuralEngine`; на симуляторе всё принудительно `.cpuOnly` ([`Sources/WhisperKit/Core/Models.swift:92-117`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Models.swift#L92-L117)). Применяется при загрузке: `loadModel(at:computeUnits:)` ставит `modelConfig.computeUnits` ([`:19-22`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Models.swift#L19-L22)); в CLI — флаги `--audio-encoder-compute-units` / `--text-decoder-compute-units` со значениями `all|cpuOnly|cpuAndGPU|cpuAndNeuralEngine|random` ([`CLIUtils.swift:8-19`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/CLIUtils.swift#L8-L19)).

### A3.12. WhisperKit — конкурентность: что можно и чего нельзя

- `WhisperKit` — `open class`, **не** actor ([`Sources/WhisperKit/Core/WhisperKit.swift:11`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L11)).
- **Параллельная обработка нескольких входов на одном инстансе — задумана авторами.** `transcribe(audioPaths:…)` режет входы на батчи по `concurrentWorkerCount` и запускает `taskGroup.addTask { … self.transcribe(audioArray:…) }` на **том же** экземпляре, «протаскивая» non-Sendable `self` через `WeakSendableWrapper` (`@unchecked Sendable`, комментарий: «caller is responsible for ensuring no actual data race occurs») ([`WhisperKit.swift:712-746`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L712-L746), [`WhisperKit.swift:875-895`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L875-L895), [`Sources/ArgmaxCore/ConcurrencyUtilities.swift:107-118`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ConcurrencyUtilities.swift#L105-L117)).
- **Дефолт параллелизма:** `concurrentWorkerCount ?? 16` на macOS, `?? 4` на остальных платформах ([`Sources/WhisperKit/Core/Configurations.swift:245-251`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Configurations.swift#L245-L251)); в CLI — `--concurrent-worker-count` дефолт 4 ([`Sources/ArgmaxCLI/TranscribeCLIArguments.swift:116`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/TranscribeCLIArguments.swift#L116)).
- **Что защищено:** сами `MLModel` в компонентах — `@Protected` (обёртка над `OSAllocatedUnfairLock`) ([`AudioEncoder.swift:23`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/AudioEncoder.swift#L23), `TextDecoder.swift:307`, `FeatureExtractor.swift:21`). Плюс `PropertyLock` для свойств `TranscriptionResult` с явным предупреждением, что read-modify-write не защищён ([`ConcurrencyUtilities.swift:55-90`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/ConcurrencyUtilities.swift#L55-L90)).
- **Что не защищено:** `currentTimings` и `progress` — обычные `public private(set) var` на инстансе, которые мутируются в каждом вызове (`WhisperKit.swift:676, 763, 951, 975, 1057, 1163`). Per-call состояние декодера живёт в `TranscribeTask` (свой `timings`, свои `MLMultiArray` на вызов) ([`TranscribeTask.swift:9-40`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/TranscribeTask.swift#L9-L40)). `[INFERENCE]` Практический вывод: параллельные вызовы на одном инстансе работают (и используются самим пакетом), но телеметрия/прогресс будут гонками, а поведение при одновременном `loadModels()`/`unloadModels()` и транскрипции не определено — внешняя сериализация lifecycle обязательна.
- **Отдельно про загрузку моделей:** хуки загрузки сделаны переопределяемыми (`open func loadModels`, `loadModelComponents`, `unloadModels`), см. коммит «Add overridable model loading hooks to WhisperKit and TTSKit (#553)» (`c88f830`) — удобно для собственного менеджера кэша.

### A3.13. WhisperKit — стриминг в библиотеке

- `AudioStreamTranscriber` — `public actor`, но **привязан к микрофону**: `startStreamTranscription()` требует разрешение на запись и вызывает `audioProcessor.startRecordingLive(inputDeviceID:)`, затем поллит `audioProcessor.audioSamples` в цикле с шагом 100 мс и транскрибирует накопленный буфер, если в нём > 1 c аудио ([`AudioStreamTranscriber.swift:26-110`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioStreamTranscriber.swift#L26-L110), `:146-175`). API подачи произвольных PCM-чанков снаружи нет.
- `transcribeFileStream(audioStream:)` — **private** ([`WhisperKit.swift:1214`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L1214)); публично доступны `transcribe(audioPath:)`, `transcribe(audioArray:)` и `transcribe(audioPaths:)` с `AudioLoadingMode.fullFile|.incremental(chunkDurationSeconds:maxBufferedChunks:)` ([`TranscribeCLIUtils.swift:44-62`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCLI/TranscribeCLIUtils.swift#L44-L62)). `[INFERENCE]` Для серверного стриминга придётся либо повторять схему gotranscribesrv (накапливать буфер и звать `transcribe(audioArray:)` по VAD-границам), либо форкать/переопределять цикл `AudioStreamTranscriber`.
- Прогресс отдаётся через `TranscriptionCallback` (текст + токены + `avgLogprob`), что и использует SSE в сервере.

### A3.14. WhisperKit — VAD

Только энергетический VAD: `EnergyVAD` (порог энергии 0.02, окно 0.1 с) на базе открытого `VoiceActivityDetector` (frame-based, `voiceActivity(in:)`, `calculateActiveChunks(in:)`) ([`Sources/WhisperKit/Core/Audio/EnergyVAD.swift:7-30`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/EnergyVAD.swift#L7-L30), [`VoiceActivityDetector.swift:6-42`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/VoiceActivityDetector.swift#L6-L42)). Нейросетевого (Silero и т.п.) VAD в пакете нет; подкласс можно подставить через `WhisperKitConfig.voiceActivityDetector`. Чанкинг по VAD — `VADAudioChunker` ([`AudioChunker.swift:43`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioChunker.swift#L43)).

### A3.15. WhisperKit — диаризация (SpeakerKit, open source)

- `SpeakerKit` — `open class … @unchecked Sendable`, бэкенд `SpeakerKitDiarizer.pyannote(config:)`, модель **Pyannote v4 (community-1)**, репозиторий `argmaxinc/speakerkit-coreml` ([`Sources/SpeakerKit/SpeakerKit.swift:11-45`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/SpeakerKit/SpeakerKit.swift#L11-L45), [`SpeakerKitConfig.swift:28-38`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/SpeakerKit/SpeakerKitConfig.swift#L28-L38)).
- API: `diarize(audioArray:options:progressCallback:)` → `DiarizationResult` (спикеры, сегменты, центроиды), `addSpeakerInfo(to:)` для склейки с транскрипцией (стратегии `.subsegment` / `.segment`), `generateRTTM(...)` ([`SpeakerKit.swift:60-110`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/SpeakerKit/SpeakerKit.swift#L64-L73), [`README.md:570-680`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L570-L680)).
- Опции: `numberOfSpeakers` (nil = авто), `clusterDistanceThreshold`, `useExclusiveReconciliation` ([`README.md:599-615`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L599-L615)).
- Внутренний параллелизм и compute units на компонентах: `concurrentSegmenterWorkers = 4`, `concurrentEmbedderWorkers = min(8, max(2, audioSeconds/30))`; сегментер по умолчанию `.cpuOnly`, эмбеддер `.cpuAndNeuralEngine`, кластеризатор `.cpuOnly` ([`PyannoteConfig.swift:9-56`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/SpeakerKit/Pyannote/PyannoteConfig.swift#L9-L56)).
- Локальные модели поддерживаются: `PyannoteConfig(modelFolder:)` → `PyannoteModelManager` берёт папку вместо скачивания ([`PyannoteModelManager.swift:49-70`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/SpeakerKit/Pyannote/PyannoteModelManager.swift#L49-L70)).
- `[INFERENCE]` Диаризация в OSS есть и работает, но real-time-диаризация (стрим, live speaker attribution) заявлена как Pro. Русский язык для pyannote-подхода не важен (акустические эмбеддинги), но качество на русской спонтанной речи не измерено.

### A3.16. WhisperKit — TTS (TTSKit, open source)

- Qwen3-TTS в CoreML, две модели: `.qwen3TTS_0_6b` (~1 ГБ, все платформы) и `.qwen3TTS_1_7b` (~2.2 ГБ, только macOS, поддерживает style instructions) ([`README.md:404-435`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L404-L435)).
- **10 языков, включая `russian`**: `.english, .chinese, .japanese, .korean, .german, .french, .russian, .portuguese, .spanish, .italian`; 9 встроенных голосов ([`README.md:447-458`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L453-L454)).
- Конфиг: `modelFolder` (локальная папка с ожидаемым layout `qwen3_tts/<component>/<version>/<variant>/*.mlmodelc`), `downloadBase`, `modelRepo`, `modelEndpoint`, per-component `ComputeOptions`, режимы `speechDecoderMode` (latency/throughput) и `multiCodeDecoderMode` ([`Sources/TTSKit/Qwen3TTS/Qwen3Config.swift:127-200`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/TTSKit/Qwen3TTS/Qwen3Config.swift#L127-L200), [`Sources/TTSKit/Configurations.swift:10-40`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/TTSKit/Configurations.swift#L10-L40)).
- Стриминг: `generate(...)` + `play(...)` с `playbackStrategy: .auto`, `SpeechStreamWriter` — потоковая генерация кадрами ([`README.md:459-478`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L459-L478)).
- `[INFERENCE]` Это единственный компонент из обоих проектов с подтверждённой поддержкой русского TTS, но качество именно на русском надо мерить.

### A3.17. Что в WhisperKit закрыто (сводка)

По README и структуре кода: OSS = Whisper (batch + file-incremental), SSE-стриминг по файлу, диаризация post-hoc (SpeakerKit), TTS (TTSKit), REST-сервер на 2 эндпоинта. Pro = real-time транскрипция со спикерами, custom vocabulary, WebSocket local server (Deepgram-совместимый), Android. Пункт про Pro взят из README ([`README.md:27-37`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L27-L37)) и не проверялся по коду — кода Pro в репозитории нет.

---

## Ограничения и риски

1. **GPL-3.0 у gotranscribesrv (весь репозиторий, включая Swift-сайдкар).** Любое копирование кода в наш runtime тянет GPL на весь продукт. Пригодно только как источник идей и как объект изучения. Ссылаться на код как «образец» — можно, копировать — нельзя.
2. **WhisperKit-сервер по умолчанию не собран.** Если запускать `whisperkit-cli` из Homebrew/релиза, подкоманды `serve` может не быть — она появляется только при сборке с `BUILD_ALL=1`. Это надо проверять в каждом конкретном артефакте (формула Homebrew собирается из тега, флаг в формуле не виден из репозитория — не подтверждено).
3. **Одна модель на процесс WhisperKit-сервера.** Поле `model` в запросе игнорируется; для второй модели нужен второй процесс (и, соответственно, второй набор весов в памяти). Для Runtime это означает, что OSS-сервер нельзя использовать как «каталог моделей».
4. **Нет реального real-time в OSS.** SSE по файлу ≠ потоковый ASR. WebSocket-сервер, Deepgram-совместимость и real-time-диаризация — платные. Строить наш real-time WS придётся самим (или брать протокол из gotranscribesrv).
5. **Несинхронизированное состояние WhisperKit.** `currentTimings`, `progress`, `modelState` — обычные var; одновременные `transcribe` + `loadModels`/`unloadModels` не защищены. Плюс `WeakSendableWrapper` — это осознанный обход проверок Sendable, а не гарантия корректности. Runtime обязан сериализовать lifecycle и (желательно) инференс на уровне своей очереди.
6. **Кэш не совместим с HF-стандартом.** `~/.cache/huggingface` и `HF_HOME` игнорируются (удалено в форке HubApi). Если у пользователя уже есть HF-кэш, WhisperKit его не увидит; общий каталог придётся задавать нашим `downloadBase`.
7. **Дефолтный `downloadBase` = `~/Documents/huggingface`** — неудачное место для системного сервиса (sandbox/TCC, бэкапы, права). Runtime должен переопределять.
8. **Временные файлы в WhisperKit-сервере.** Загруженный файл пишется в `FileManager.default.temporaryDirectory` без гарантированного удаления — при долгой работе это утечка диска и «следы» пользовательского аудио. `[INFERENCE]` по коду (defer отсутствует).
9. **gotranscribesrv: язык не поддержан вообще.** Ни один ASR-вариант FluidAudio, используемый проектом, не знает русского. Плюс сам `language` сквозь стек протаскивается, но в сайдкаре игнорируется — интерфейс лжёт.
10. **Ёмкость и латентность нигде не измерены.** Все числа (3–4 S2S сессии на M4 24 ГБ, «8 стримов на ноду») — оценки автора в README/docs, не бенчмарки. В репозитории WhisperKit цифр тоже нет — только ссылка на HF Space с бенчмарками.
11. **Prewarm/ANE-специализация.** Первый запуск CoreML-модели компилирует её под устройство; без prewarm первый запрос будет в разы медленнее (это прямо описано в `WhisperKitConfig` комментариях). Для «минимальных накладных расходов в простое» это означает: модели надо выгружать, но кэш компиляции ОС сохранять — иначе после каждого unload будет штраф.

---

## Выводы для Runtime

### Что взять из gotranscribesrv (идеи, не код)

| Решение | Почему | Как применять |
|---|---|---|
| **Двухуровневый стриминг** (буферизованный + cache-aware) как два разных класса адаптеров | Буферизованный работает с любым batch-движком (Whisper, GigaAM, T-one); cache-aware даёт настоящую низкую латентность, но требует модели со state | В API развести `stream_mode: "incremental" \| "buffered"`; в реестре моделей пометить, поддерживает ли модель состояние |
| **Событийная модель WS**: `ready`, `partial`, `final` (+`is_final/speech_final`), `speech_started`, `speech_stopped`, `end_of_turn`, `done`, `error` | Проверена на трёх клиентских диалектах (Deepgram/OpenAI/Watson) в одном проекте | Взять как основу нашего нативного протокола; диалекты — тонкие мапперы сверху |
| **Эндпоинтинг по тишине с настраиваемым окном** (300 мс дефолт, роллинг-финал на 10 с, минимум 1600 сэмплов) | Практические константы, отлаженные на реальных звонках | Дефолты конфигурации VAD/сегментации |
| **Пайплайн ITN** (spoken → written) с per-request opt-out и отдельной предобработкой телефонных номеров | ITN — обязательная часть для русского («сто двадцать пять рублей» → «125 ₽») | Отдельная стадия в пайплайне ответа, не в движке; для русского нужен свой NeMo-ITN/грамматика |
| **Word-level timestamps из токенов + сегментация по паузам + назначение спикера по перекрытию** | Универсально и не требует движковой поддержки | Реализовать в слое постобработки Runtime (общий для всех адаптеров) |
| **Разделение gateway (HTTP/биллинг) и inference-сайдкара** | Позволяет держать inference на Apple Silicon, а API — где угодно | У нас это уже заложено; подтверждает, что HTTP-граница между ними — нормальный дизайн |

### Что из gotranscribesrv не брать

- **Один actor на все движки** — сериализует batch-ASR и мешает параллельным сессиям. Нужен пул/очередь с явной политикой (N воркеров на движок или конкурентные акторы на сессию).
- **Создание streaming-движка на соединение** — прямое нарушение требования «переиспользование загруженных весов». Нужен реестр загруженных движков с ключом `(model_id, variant, compute_units)` и счётчиком ссылок.
- **Статический каталог моделей с фейковыми ID** — клиент должен явно указывать модель, и она должна валидироваться до инференса.
- **Игнорирование `language`** — параметр обязан доходить до движка или отвергаться.
- **Мёртвые поля конфига** (`ASR_MODEL`, `ASR_RUNTIME`) — не тащить «на будущее».

### Что взять из WhisperKit

| Компонент | Что именно | Как интегрировать |
|---|---|---|
| `ModelManager` + `ModelState` + `ModelLoader` | Готовая state machine с коалесингом и prewarm/unload | Взять как эталон семантики для нашего `ModelRegistry`: те же состояния в API (`/models/{id}` → `state`), тот же приём «параллельные загрузки схлопываются в одну» |
| `ModelInfo(version:variant:name:computeUnits:)` + `downloadPattern` | Ключ «модель/версия/вариант/кванты» и glob для скачивания | Каталог: `model_id/version/variant/`; поддерживать `revision` (git sha) и локальный `modelFolder` |
| `HubApi` с `downloadBase` / `endpoint` / `revision` | Позволяет общий каталог и своё зеркало | Наш `downloadBase` = общий каталог Runtime (например `~/Library/Application Support/SpeechRuntime/models`), HF-эндпоинт конфигурируем |
| `ModelComputeOptions` (по компонентам: mel/encoder/decoder) | Правильные дефолты для Apple Silicon (ANE для encoder+decoder, GPU для mel) | Интерфейс адаптера: `compute_units` per-component, переопределяемо клиентом конфига рантайма |
| `concurrentWorkerCount` + task group на одном инстансе | Доказывает, что одна загруженная модель может обслуживать несколько запросов | Политика «один загруженный экземпляр — N параллельных запросов», но с нашим внешним ограничителем и без гонок в телеметрии |
| `prewarmModels()` | Компиляция в кэш без удержания весов | Обязательный шаг после установки модели: `install → prewarm → (load по требованию) → unload` |
| SpeakerKit | Открытая диаризация (pyannote v4) с RTTM и склейкой с транскрипцией | Отдельная операция `diarize` в API; диаризация post-hoc, не в потоке (для real-time — своя разработка) |
| TTSKit | Qwen3-TTS, русский в списке языков, стриминг | Кандидат в адаптер TTS; проверить качество на русском |

### Что не брать / чем рискуем

- **`AudioStreamTranscriber`** как «streaming ASR» — он микрофонный и поллинговый; для сервера нужен собственный цикл поверх `transcribe(audioArray:)`.
- **`EnergyVAD`** — слишком примитивен для русского шума и телефонных кодеков; нужен Silero/TEN и т.п. (см. E1).
- **WhisperKit-класс как «движок под нагрузкой»** без внешней сериализации lifecycle.
- **OSS Local Server WhisperKit** как базу нашего HTTP-слоя: один эндпоинт-стиль, нет выбора модели, нет WS, нет состояния моделей. Можно взять только идею «OpenAI-compatible `/v1/audio/transcriptions`», которую мы всё равно поддержим как диалект.

### Минимальный вывод по приоритету «русский + IT-термины»

- gotranscribesrv даёт **0** идей по русскому ASR (движки не поддерживают), но **много** по стриминговому протоколу и постобработке.
- WhisperKit даёт рабочий ASR для русского (Whisper multilingual, MIT), открытую диаризацию и TTS с русским; но качество Whisper на русской IT-лексике и на смеси «русский + английские термины» надо измерять отдельно (это к D1).

---

## Неизвестные / что измерить

1. **Есть ли `serve` в публичных артефактах WhisperKit** (Homebrew-формула `whisperkit-cli`, релизные бинарники) — формула внешняя, `BUILD_ALL` в ней не виден; проверить `brew install whisperkit-cli && whisperkit-cli --help`.
2. **Пропускная способность OSS-сервера WhisperKit под параллельной нагрузкой** — сколько одновременных запросов выдерживает одна WhisperKit large-v3 на M-чипе без деградации, и что происходит с `progress`/`currentTimings` при гонке (нужен soak-тест: N параллельных POST + проверка корректности ответов и памяти).
3. **Реальная стоимость памяти при N streaming-сессиях в схеме «движок на соединение»** (gotranscribesrv) — подтвердить или опровергнуть линейный рост; замерить RSS сайдкара на 1/2/4/8 сессиях для eou-320 и unified-320.
4. **Качество Whisper large-v3 / distil на русской речи с английскими IT-терминами** (Kubernetes, Istio, kubectl, CI/CD) — WER отдельно на терминах и на обычной речи; сравнить с GigaAM v3 / T-one (см. D1).
5. **Качество pyannote v4 (SpeakerKit) на русской спонтанной речи и телефонном 8 кГц** — DER; в репозитории только тесты на jfk.wav/VADAudio.wav, метрик нет.
6. **Качество Qwen3-TTS 0.6B на русском** — разборчивость, просодия, обработка латиницы внутри русского текста (акронимы), латентность первого кадра.
7. **Стоимость prewarm/специализации CoreML** для больших моделей (Whisper large-v3: encoder 626 МБ) — сколько секунд занимает первый load, сколько держится кэш ОС после unload; влияет на политику «выгружать в простое».
8. **Лицензионный статус `argmaxinc/speakerkit-coreml` и `argmaxinc/ttskit-coreml` на HF** (gated/не-gated, условия) — в клоне только ссылка; проверить `cardData` через HF API (сеть в этой сессии недоступна).
9. **Не подтверждено по коду:** заявление README gotranscribesrv о «8 стримах на ноду» и «3–4 S2S сессиях» — это оценки автора; никаких логов замеров в репозитории нет.
10. **Совместимость `docs/api.md` gotranscribesrv с фактическими роутами** — проверял только `main.go`; отдельные расхождения в документации не исключены (не сверял построчно).

---

## Источники

**Клоны (зафиксированные SHA):**
- `gotranscribesrv` @ `a5f4289078553ba66f83510c62146043bed91a7a` — https://github.com/sagostin/gotranscribesrv (HEAD 2026-08-26, 70 коммитов, 1 автор, 0 тегов, GPL-3.0)
- `WhisperKit` @ `9f99b5cb6a907730543823ad271b82532782a899` — https://github.com/argmaxinc/WhisperKit (редирект на https://github.com/argmaxinc/argmax-oss-swift; HEAD 2026-10-10, тег `v1.1.1` 2026-10-07, MIT)
- `FluidAudio` @ `1b0ed50d686873128a4129b3510ce3914c849bab` — использован для проверки типов движков и списка моделей (не аудировался целиком)

**Ключевые файлы gotranscribesrv:**
- `cmd/server/main.go` (роуты), `internal/handlers/models.go` (статический каталог), `internal/config/config.go` (мёртвые ASR-поля), `internal/sidecar/client.go` (HTTP-клиент к сайдкару)
- `sidecar-audio/Package.swift`, `sidecar-audio/Sources/Server/main.swift`, `.../EngineManager.swift`, `.../Routes/TranscribeRoute.swift`, `.../Routes/StreamRoute.swift`, `.../Routes/RealtimeStreamRoute.swift`, `.../Routes/VadRoute.swift`, `.../Routes/DiarizeRoute.swift`, `.../ITNHelpers/ITNPreprocessor.swift`
- `docs/architecture.md`, `docs/realtime.md`, `README.md`, `TODO.md`, `LICENSE.md`

**Ключевые файлы WhisperKit/argmax-oss-swift:**
- `Package.swift`, `Package@swift-6.2.swift`, `Makefile`, `README.md`, `BENCHMARKS.md`, `LICENSE`, `NOTICES`
- `Sources/ArgmaxCLI/ArgmaxCLI.swift`, `Sources/ArgmaxCLI/TranscribeCLIArguments.swift`, `Sources/ArgmaxCLI/TranscribeCLIUtils.swift`, `Sources/ArgmaxCLI/CLIUtils.swift`
- `Sources/ArgmaxCLI/Server/ServeCLI.swift`, `ServeCLIArguments.swift`, `OpenAIHandler.swift`, `GeneratedSources/{Server,Types}.swift`, `scripts/specs/localserver_openapi.yaml`
- `Sources/WhisperKit/Core/WhisperKit.swift`, `Models.swift`, `Configurations.swift`, `TranscribeTask.swift`, `AudioEncoder.swift`, `TextDecoder.swift`
- `Sources/WhisperKit/Core/Audio/{AudioStreamTranscriber,EnergyVAD,VoiceActivityDetector,AudioChunker}.swift`
- `Sources/ArgmaxCore/{ModelManager,ModelDownloader,ModelState,ConcurrencyUtilities,HubWrapper,MLModelLoading}.swift`, `Sources/ArgmaxCore/External/Hub/{HubApi,Downloader}.swift`
- `Sources/SpeakerKit/{SpeakerKit,SpeakerKitConfig}.swift`, `Sources/SpeakerKit/Pyannote/{PyannoteConfig,PyannoteModelManager}.swift`
- `Sources/TTSKit/{TTSKit,Configurations}.swift`, `Sources/TTSKit/Qwen3TTS/Qwen3Config.swift`

**Веб:** сеть в этой сессии недоступна (все попытки `read`/`curl` к `argmaxinc.com`, `app.argmaxinc.com` завершились таймаутом); поэтому утверждения о границе «OSS vs Pro» взяты из `README.md` самого репозитория, а не из страницы Argmax Docs. Страница сравнения, которую стоит прочитать при наличии сети: https://app.argmaxinc.com/docs/wiki/open-source-vs-pro-sdk.
