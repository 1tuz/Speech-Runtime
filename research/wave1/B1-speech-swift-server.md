# B1. speech-swift: аудит серверной части (`AudioServer`) как кандидата на форк

Дата: 2026-10-25. Ревизия: `cbad3be29480825707890490f446b19d7288be2d` (2026-10-25), локальный клон `/tmp/srr/repos/speech-swift`.
Метод: чтение исходников (не README), `git log/blame`, тесты в `Tests/AudioServerTests`, `Tests/SpeechRuntimeTests`. Сервер **не собирался и не запускался** — все утверждения о поведении выводятся из кода и тестов; всё непроверенное помечено `[INFERENCE]`.
Особенность репозитория: файлы и идентификаторы в `Sources/AudioServer` названы с префиксом `OpenAI` (например `OpenAISpeech.swift`, `OpenAISpeechRequest`) — это осознанное переименование для обхода товарного знака; пути в пермалинках приведены как есть.

---

## Итог

1. **Транспорт**: Hummingbird 2 (`2.5.0..<2.17.0`) + `hummingbird-websocket` (`2.6.0..<2.7.0`), HTTP/1.1, один WS-апгрейд на жёстко зашитый путь `/v1/realtime`. Никакой версии протокола, кроме префикса `/v1` и формы событий, нет; заголовок/сабпротокол версии не читается и не возвращается.
2. **Поверхность мала и плоская**: 9 HTTP-маршрутов + 1 WebSocket; ещё 4 маршрута появляются только при `--inference-manifest` (отдельный «native inference» режим). Нет `/v1/models/{id}`, нет load/unload/pull/delete, нет `/metrics`, нет ни одного маршрута для диаризации, speaker-эмбеддингов, VAD-таймлайна, source separation, super-resolution, music — хотя 19 из 48 записей реестра относятся именно к этим видам.
3. **Вся инференция сериализована глобально**: один `InferenceGate` на процесс — mutex на 1 слот + FIFO до 32 ожидающих, без таймаута. Тест явно доказывает, что при занятом WS HTTP-запрос ждёт, и максимум одновременных инференсов = 1 (`ASRRuntimeRegressionTests`). Это не «очередь на модель», а «очередь на весь сервер»: второй клиент ждёт за генерацией 7B-модели первого.
4. **Жизненного цикла моделей нет**: `ModelCache` (actor) хранит `Task<Model>` по ключу `(тип, строка)` и **никогда не выгружает** — ни TTL, ни LRU, ни refcount, ни бюджета памяти, ни API выгрузки. `ModelRuntime.unload()` существует, но сервер его не вызывает. Загруженная модель живёт до смерти процесса.
5. **`--preload` прогревает не те модели**: грузит Qwen3-ASR small, Qwen3-TTS, PersonaPlex 7B и DeepFilterNet3, тогда как дефолты сессии — Parakeet и Kokoro. То есть «прогрев» не убирает холодный старт дефолтного пути.
6. **Стриминг есть только на выходе TTS.** ASR полностью буферный: `dispatchTranscribe` → `model.transcribeAudio(...)` для всех движков, включая `parakeet-streaming` и `nemotron` (потоковые модели используются как батч). Частичных гипотез, `transcript.delta` по ASR и таймстемпов нет нигде.
7. **Реестр — компилируемый Swift-массив из 48 записей**; диспетчеризуются 29 (ASR 8, TTS 15, S2S 4, enhance 2). Остальные 19 отдаются в `/v1/models`, но при выборе либо молча игнорируются (WS), либо падают с «engine not enabled in this build». Комментарий в коде обещает маршруты `/compose`, `/diarize`, которых не существует.
8. **Безопасности нет вообще**: bind по умолчанию `127.0.0.1`, но `--host 0.0.0.0` даёт полный неаутентифицированный доступ; нет middleware (ни auth, ни CORS, ни логирования запросов), WS-апгрейд не проверяет `Origin`, TLS отсутствует, rate limit отсутствует.
9. **Конкурентная модель сервера очень молодая**: `SpeechRuntime` (`InferenceGate`, `ModelCache`, `ModelRuntime`, `ModelDescriptor`) появился одним коммитом 2026-10-17 — за 8 дней до текущей даты. До этого коммита в `AudioServer.swift` не было **никакой** сериализации: параллельные запросы били в одни и те же не thread-safe инстансы моделей.
10. **Техдолг структурный, а не косметический**: два независимых механизма сериализации (`InferenceGate` и `NativeInferenceGate` с разной семантикой), три-четыре формата ошибок, «god-protocol» на 21 метод, мёртвый код (`loadQwen3ASR`/`loadASR`), захардкоженные ключи кэша, из-за которых `personaplex-7b-mlx-8bit` тихо исполняет 4-битный бандл, `fatalError` в резолвере дефолтов и temp-файл на каждый WAV в обе стороны.

---

## Факты

### 1. Транспорт, точка входа, конфигурация процесса

| Что | Доказательство |
|---|---|
| Пакет `AudioServer` зависит от `Hummingbird`, `HummingbirdWebSocket` и явно пинит `WSCore` (swift-websocket `1.5.0..<1.6.0`, потому что 1.6.0 ломает сборку `import NIOSSL`) | `Package.swift:947-991` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L947-L991 ; пины — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L242-L260 |
| Продукты: `speech-server` (+ устаревший алиас `audio-server`) | https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L208-L223 |
| CLI: только 4 опции — `--host` (127.0.0.1), `--port` (8080), `--preload`, `--inference-manifest`. Конфиг-файла, env-конфига, hot-reload нет | `Sources/AudioServerCLI/AudioServerCommand.swift:12-22` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServerCLI/AudioServerCommand.swift#L8-L24 |
| Сервер: `Application(router:server:configuration: .init(address: .hostname(host, port: port)))` — без `serverConfiguration` (таймауты, лимиты соединений — дефолтные Hummingbird), без middleware | `Sources/AudioServer/AudioServer.swift:61-77` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L61-L77 |
| WS-апгрейд: только `path == "/v1/realtime"`, ответ `.upgrade([:])` — пустые заголовки, `Sec-WebSocket-Protocol` не согласуется, query-строка игнорируется | там же, L65-L70 |
| Конфиг WS: `maxFrameSize: 1<<24` (16 МиБ), `autoPing: .disabled`; вместо ping — собственный `realtime.keepalive` каждые 15 c | `AudioServer.swift:377-387`, `:985-998`, `:1572-1575` |
| Входящие сообщения WS: `inbound.messages(maxSize: 50 * 1024 * 1024)` — 50 МиБ на сообщение при лимите кадра 16 МиБ | `AudioServer.swift:1000` |

Комментарий в коде объясняет отключение autoPing: `URLSessionWebSocketTask` не отдаёт pong, пока висит receive, поэтому watchdog Hummingbird ложно срабатывал. Это реальная проблема, которую они обошли (история: коммиты `0705a6a`, `5c4f03c`, `69969b1` от 2026-06-19).

### 2. Таблица маршрутов

Базовый URL пермалинков: `https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/`.

| # | Метод | Путь | Операция | Параметр модели | Стриминг | Тело запроса | Ответ | Источник |
|---|---|---|---|---|---|---|---|---|
| 1 | GET | `/health` | liveness (статический `{"status":"ok"}`, без состояния моделей) | — | — | — | `application/json` | [AudioServer.swift#L96-L101](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L96-L101) |
| 2 | GET | `/v1/models` | каталог: все 48 записей реестра (`id`, `object`, `engine`, `kind`, `model_id`, `aliases`, для qwen3-asr — `backend`/`input_sample_rate`/`streaming`) | — | — | — | JSON `{object:list,data:[...]}` | [L104-L129](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L104-L129) |
| 3 | GET | `/v1/realtime/models` | подмножество kinds `asr/tts/s2s` | — | — | — | тот же формат | [L131-L141](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L131-L141) |
| 4 | POST | `/v1/audio/transcriptions` | ASR, OpenAI-совместимый | `model` (multipart-поле или JSON); **неизвестное имя молча → Parakeet** | нет | `multipart/form-data` (`file`,`language`,`response_format`,`model`) либо JSON с `audio_base64`; лимит 50 МиБ | `json` `{"text"}`, `text`, `srt`, `vtt`, `verbose_json` (1 фиктивный сегмент) | [AudioServer.swift#L144-L146](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L144-L146) → [OpenAITranscriptions.swift#L22-L155](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/OpenAITranscriptions.swift#L22-L155) |
| 5 | POST | `/v1/audio/speech` | TTS, OpenAI-совместимый | `model` **обязателен**; неизвестное имя → 400 | да, но только `response_format=pcm` и только `pocket-tts`/`voxtral-tts` | JSON `{model,input,voice,response_format,speed,language,stream}`; `input` ≤ 4096 симв., лимит 1 МиБ | `audio/wav` 24 кГц или `audio/pcm` (chunked) | [L148-L150](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L148-L150) → [OpenAISpeech.swift#L133-L246](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/OpenAISpeech.swift#L133-L246) |
| 6 | POST | `/transcribe` | ASR, «родной» | `model` в JSON-теле; неизвестное имя → 400; **для сырого WAV-body модель выбрать нельзя** | нет | JSON `{audio_base64,model,language,sample_rate}` или сырой аудиофайл; 50 МиБ | JSON `{text,model,duration}` | [L152-L198](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L152-L198) |
| 7 | POST | `/speak` | TTS | приоритет `model` > legacy `engine` > дефолт Kokoro | нет (полный WAV) | JSON `{text,model?,engine?,language?,voice?}`; 1 МиБ | `audio/wav` 24 кГц | [L200-L239](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L200-L239) |
| 8 | POST | `/respond` | speech-to-speech (PersonaPlex / Hibiki) | `model`; неизвестное → 400; дефолт PersonaPlex 4-bit | нет | JSON `{audio_base64,model?,voice?,max_steps?,language?,format?}` либо сырой аудиофайл; 50 МиБ | `audio/wav` 24 кГц или JSON с `audio_base64`+`transcript` | [L241-L326](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L241-L326) |
| 9 | POST | `/enhance` | шумоподавление (DeepFilterNet3) | `model` (kind `.enhance`) | нет | JSON/сырой аудиофайл; 50 МиБ | `audio/wav` 48 кГц | [L328-L372](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L328-L372) |
| 10 | WS | `/v1/realtime` | OpenAI-подобный Realtime: 6 входящих типов событий, 16 исходящих | `session.update.session.model` (+ `input_audio_transcription.model`, legacy `engine`) | выход TTS — `response.audio.delta`; ASR — только по `commit` | JSON-текст, PCM16 24 кГц base64 | JSON-события | [L968-L1530](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L968-L1530) |
| 11 | GET | `/v1/inference/capabilities` | только с `--inference-manifest`: 4-5 моделей, `schema_version: 1` | — | — | — | JSON | [NativeInference.swift#L279](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L279) |
| 12 | GET | `/v1/inference/provenance` | там же: конфиг, `pid`, MLX-счётчики памяти | — | — | — | JSON (раскрывает абсолютные пути) | [L280-L297](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L280-L297) |
| 13 | POST | `/v1/inference/cancel` | там же: отмена по `requestID` | — | — | `{"requestID": "..."}` ≤ 1024 Б | `{"cancelled": bool}` | [L298-L303](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L298-L303) |
| 14 | POST | `/v1/inference` | там же: `operation ∈ {chat,speech,transcribe,vad}` | `model` обязан совпасть с ожидаемым для операции | NDJSON для `speech`+`stream` | JSON ≤ 16 МиБ | JSON или `application/x-ndjson` | [L304-L365](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L304-L365) |

Отдельно: CLI печатает при старте список из 8 эндпоинтов и не упоминает `/v1/models`, `/v1/realtime/models` и native-маршруты (`AudioServerCommand.swift:46-55`). Справка `--host`/`--port` совпадает с кодом, а вот комментарий к `/respond` в help-тексте («voice/max_steps via query») неверен: query-строк сервер не парсит вообще — `grep "query|uri"` по `Sources/AudioServer/*.swift` не даёт ни одного совпадения.

### 3. Протокол WebSocket `/v1/realtime`

Входящие события (единственные распознаваемые; всё прочее → `error: "Unknown event type: ..."`), `AudioServer.swift:1012-1521`:

| Событие | Что делает | Строки |
|---|---|---|
| `session.update` | обновляет слоты `asrVariant`/`ttsVariant`/`s2sVariant`, `language`, `voice`, `input_audio_format`, `turn_detection`, `voice_cloning`; неизвестное имя модели **принимается и эхом возвращается без изменения движков** | [L1012-L1100](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1012-L1100) |
| `input_audio_buffer.append` | base64 PCM16; в режиме VAD — сразу прогон через Silero и, при закрытии хода, **синхронный ASR прямо в обработчике**; без VAD — накопление до 5 мин | [L1103-L1165](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1103-L1165) |
| `input_audio_buffer.clear` | сброс буфера и VAD | [L1167-L1174](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1167-L1174) |
| `input_audio_buffer.commit` | ASR по буферу (или отдаёт аудио в слот S2S) | [L1176-L1198](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1176-L1198) |
| `response.create` | TTS/S2S; **захватывает permit и держит его до конца стриминга, включая сетевые записи** | [L1199-L1502](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1199-L1502) |
| `conversation.item.create` | только эхо `conversation.item.created` для текстовых частей; иначе молча ничего | [L1504-L1517](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1504-L1517) |

Исходящие: `session.created`, `session.updated`, `input_audio_buffer.speech_started`, `input_audio_buffer.speech_stopped`, `input_audio_buffer.committed`, `input_audio_buffer.cleared`, `conversation.item.created`, `conversation.item.input_audio_transcription.completed`, `response.created`, `response.audio.delta`, `response.audio.done`, `response.audio_transcript.delta`, `response.audio_transcript.done`, `response.done`, `error`, `realtime.keepalive`.

Чего в протоколе нет: `response.cancel`, `conversation.item.truncate`, `conversation.item.delete`, клиентского `input_audio_buffer.speech_started`, `session.update` во время ответа (barge-in), `output_audio_format` кроме pcm16, `input_audio_transcription` как поток (`*.delta` по распознаванию не существует). `usage` в `response.done` всегда `{total_tokens: 0, output_tokens: 0}` (`AudioServer.swift:1280-1300`, `:1490-1500`), `response.audio_transcript.delta` отдаёт **исходный текст запроса**, а не транскрипт синтеза (`:1653-1656`).

### 4. ModelRegistry

- `ModelVariant` = `{name, engine, modelId, aliases[], kind, descriptor?}`; `Kind` — 11 значений: `asr, tts, s2s, enhance, music, vad, diarize, speaker, separate, sr, turn` (`ModelRegistry.swift:42-86`).
- `MODEL_REGISTRY` — глобальный `let` массив, 48 записей (`ModelRegistry.swift:96-374`). Раскладка по видам: asr 8, tts 15, s2s 4, enhance 2, music 6, vad 4, turn 1, diarize 1, speaker 2, separate 3, sr 2.
- Резолвинг — два линейных прохода: точное имя, затем первый алиас (`:381-397`). Алиасы включают OpenAI-имена (`tts-1`, `tts-1-hd`, `gpt-4o-mini-tts`, `gpt-4o-mini-tts-2025-12-15` → Kokoro) — `:140-152`.
- `defaultVariant(forEngine:kind:)` возвращает non-optional и **падает `fatalError`, если записи нет** (`:399-404`) — то есть опечатка в коде даёт крэш процесса на первом запросе, а не ошибку старта.
- `ModelDescriptor` (`id, modelID, backend ∈ {mlx,coreml,hybrid}, inputSampleRate, streaming ∈ {buffered,incremental}`) заполнен **только у трёх** записей qwen3-asr; для остальных 45 `descriptor == nil`, и `/v1/models` не сообщает ни backend, ни признак потоковости (`SpeechRuntime/ModelDescriptor.swift:3-23`, `AudioServer.swift:104-117`).
- Реестр не описывает артефакты: `modelId` — это HF-repo, квантование зашито в имя репозитория (`cosyvoice-3-0.5b-mlx-8bit-full`, `voxcpm2-mlx-int8`, …). Нет ни списка файлов, ни ревизии, ни размера, ни лицензии, ни требований к памяти.

### 5. ModelCache, InferenceGate, ModelRuntime

**`ModelCache`** (`Sources/SpeechRuntime/ModelCache.swift:6-33`):

```swift
public actor ModelCache {
    private struct Key: Hashable { let type: ObjectIdentifier; let identity: String }
    private var loads: [Key: Task<Box, Error>] = [:]
    public func load<Model>(key: String, loader: @escaping @Sendable () async throws -> Model) async throws -> Model
}
```
- Один `Task` на ключ → single-flight бесплатно; при ошибке запись удаляется и следующий вызов повторяет загрузку (`:26-31`).
- **Нет** `unload`, `evict`, `count`, `memoryCost`, TTL, LRU, refcount. Успешно загруженная модель остаётся в словаре навсегда, а значит и в памяти.
- Ключ — произвольная строка; сервер использует три разных соглашения: `modelId`, `"<backend>:<modelID>"` (Qwen3-ASR runtime) и константы-заглушки `"loadMagpie"`, `"loadMagpieCoreML"`, `"loadPersonaPlex"` (`AudioServer.swift:553-565`, `:684-700`, `:736-757`).

**`InferenceGate`** (`Sources/SpeechRuntime/InferenceGate.swift:5-99`): один слот + FIFO `waiters`, `maximumPending: Int = 32`, `AdmissionError.queueFull` при переполнении, ожидание отменяемое (`withTaskCancellationHandler`), `InferencePermit.release()` идемпотентен и освобождает в `deinit`. Таймаута ожидания нет: `acquire()` ждёт сколько угодно.

**`ModelRuntime<Model>`** (`Sources/SpeechRuntime/ModelRuntime.swift:6-42`): «правильная» обёртка — `withModel { }` держит permit на загрузку+выполнение, `unload()` выгружает. Используется **только** внутри `Qwen3ASRRuntime` (`Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift:41-105`); остальные 20 загрузчиков сервера ходят в `ModelCache` напрямую. Тесты на неё есть (`Tests/SpeechRuntimeTests/RuntimeOwnershipTests.swift:15-147`), в проде — нет.

### 6. Жизненный цикл моделей: загрузка, выгрузка, память

- Загрузка — ленивая, по первому обращению, **внутри permit** `InferenceGate`: `dispatchTranscribe` берёт permit (`AudioServer.swift:1757-1758`) и уже внутри вызывает `state.loadParakeet(...)` (`:1764-1765`). То есть первая загрузка 7B-модели (скачивание с HF + распаковка весов) блокирует **все** запросы сервера на всё это время.
- Выгрузки нет ни в одном пути. `grep -n "unload|evict|LRU|TTL" Sources/AudioServer/*.swift` не даёт ни одного совпадения по логике; единственный `unload()` в проекте — `Qwen3ASRRuntime.unload()`, который сервер не вызывает.
- `ModelState` — `final class ... @unchecked Sendable` с `NSLock` только вокруг `spmDecoder` (`AudioServer.swift:538-549`); всё остальное состояние (`cache`, гейт) либо actor, либо «снаружи под гейтом».
- MLX-бюджет в основном режиме не настраивается: `Memory.cacheLimit`/`memoryLimit` выставляются только в `NativeInferenceService.init` (128 МиБ / 20 ГиБ, `NativeInference.swift:164-166`). В обычном сервере лимит кэша MLX остаётся дефолтным, а `Memory.clearCache()` между запросами не вызывается (в native-режиме — вызывается, `:268`, `:272`).
- VAD грузится **на каждое WS-соединение** отдельным инстансом: `loadVAD()` → `SileroVADModel.fromPretrained(engine: .coreml)` без кэша (`AudioServer.swift:568-575`), с комментарием, что так сделано ради изоляции recurrent-состояния. Файлы на диске общие, но модель в памяти — своя на сессию.

### 7. Как выбирается модель в запросе

| Путь | Правило | Поведение при неизвестном имени |
|---|---|---|
| `/transcribe`, `/speak`, `/respond`, `/enhance` | `model` → alias/точное имя → variant нужного kind | 400 с текстом «Unknown … model» (`:165-168`, `:210-215`, `:260-267`, `:340-347`) |
| `/v1/audio/transcriptions` | `model` → variant `.asr` | **молчаливый fallback на Parakeet** (`OpenAITranscriptions.swift:64-73`) — клиент, приславший `whisper-1`, получает Parakeet и не узнает об этом |
| `/v1/audio/speech` | `model` обязателен → variant `.tts` | 400 (`OpenAISpeech.swift:152-160`) |
| WS `session.update` | `model` → все совпавшие kinds обновляют свои слоты; `input_audio_transcription.model` → только ASR; legacy `engine` → только TTS | принимается и эхо-возвращается, слоты не меняются (`:1018-1045`) |

Дополнительно: `resolveAllVariants("qwen3")` возвращает и ASR, и TTS (общий алиас) — это документированное поведение «назвал семейство — настроил оба слота» (`:928-946`). `voice` для большинства движков игнорируется: `resolvedLocalVoice` возвращает `nil`, кроме `kokoro/qwen3-tts/voxtral-tts/pocket-tts` (`OpenAISpeech.swift:253-265`).

### 8. Конкурентность: сессии, потоки, одна модель на всех

- Состояние на соединение: `let session = RealtimeSession()` внутри `handleRealtimeWS` (`AudioServer.swift:973`), т.е. **на каждое WS — своё состояние** (буферы, слоты вариантов, VAD-контроллер, `lastCommittedAudio`).
- Веса — общие: все сессии идут в один `ModelState` → один `ModelCache` → один инстанс модели на `modelId`. `RealtimeSession` хранит лишь *выбор* варианта, а не модель.
- Исполнение — глобально сериализовано. Доказательства:
  - один `InferenceGate` создаётся в `ModelState.init` и живёт столько же, сколько сервер (`AudioServer.swift:538-545`);
  - permit берут `dispatchTranscribe` (`:1757`), `dispatchSynthesize` (`:1803`), `response.create` (`:1200`), `/respond` (`:274`), `/enhance` (`:353`), `preloadModels` (`:80`);
  - тест `testHTTPQueueOverflowIsServiceUnavailable` подтверждает 503 при переполнении, тест с двумя транспортами доказывает, что HTTP-запрос не начинает инференс, пока WS держит permit, и что `maxActive == 1` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Tests/AudioServerTests/ASRRuntimeRegressionTests.swift#L180-L247
- Два и более одновременных потока на одну модель: второй встаёт в FIFO, ждёт без таймаута, 33-й получает `queueFull` → HTTP 503 / WS `error`. Разделения «на модель» нет: ожидание в очереди за TTS 7B блокирует и ASR, и enhance.
- Permit удерживается через сетевые записи: в `response.create` `defer { permit.release() }` стоит в начале case-блока (`:1200-1201`), а все `outbound.write(...)` идут ниже. Медленный/зависший WS-клиент (backpressure) останавливает инференс всего процесса. `[INFERENCE]` — вывод из порядка операторов; измерением не подтверждено.
- `runOffloaded` (`:1600-1611`) уводит тяжёлый вызов в `Task.detached`, чтобы keepalive мог писать; отмена форвардится, но не прерывает уже начавшийся синхронный вызов модели (`model.respond`, `model.synthesize` — синхронные).
- В HTTP-путях офлоада нет: `dispatchSynthesize` вызывает синхронные `model.synthesize(...)` прямо в async-функции (`:1803-1860`). При гейте на 1 слот это блокирует одну кооперативную нить на всё время синтеза.
- Второй, независимый механизм — `NativeInferenceGate` (actor, `NativeInference.swift:86-99`): неограниченная очередь, **не** отменяемая, без `queueFull`; сериализует chat+TTS+ASR, но не VAD. Две разные семантики очереди в одном процессе.
- VAD-стримы в native-режиме ограничены 64 на процесс (`NativeInference.swift:124-144`).

### 9. Отмена и ошибки

- HTTP: при `CancellationError` обработчик делает `throw CancellationError()` (`:186-187`, `:223`), т.е. ответ не отправляется — соединение рвётся. Для клиента это неотличимо от падения.
- WS: `catch CancellationError { throw error }` (`:1523-1524`) → цикл завершается, соединение закрывается **без** события `error`. Все прочие ошибки → событие `error` с `type: server_error|invalid_request_error` и `event_type` (`:1531-1580`), соединение остаётся живым (покрыто `RealtimeAPIErrorHandlingTests`).
- Форматы ошибок не унифицированы:
  1. `{"error": "<строка>"}` — `/transcribe`, `/speak`, `/respond`, `/enhance` (`:2022-2030`);
  2. `{"error": {message,type,code,param}}` — `/v1/audio/*` (`OpenAITranscriptions.swift:163-190`);
  3. `{"type":"error","error":{...}}` — WS;
  4. `HTTPError(...)` Hummingbird — native-маршруты, тело ответа формирует фреймворк.
- Неперехваченные `try` (`decodeWAVData` в `/transcribe`, `:179`; `encodeWAV` в `/speak`, `:236`; `RequestParams.parse` при битом JSON, `:154`) дают 500 от Hummingbird с не-JSON телом.
- `inferenceErrorStatus` (`:2064-2073`) маппит в 400 только `PocketTTSError.invalidInput` и `VoxtralTTSError.invalidInput/unknownVoice`, в 503 — только `queueFull`; всё остальное (нет сети, 404 на HF, OOM, битые веса) → 500. `Retry-After` не выставляется нигде.

### 10. Хранилище моделей, скачивание, целостность

- Корень кэша: `~/Library/Caches/qwen3-speech` (`cacheDirName: String = "qwen3-speech"`, `HuggingFaceDownloader.swift:57`), переопределяется `QWEN3_CACHE_DIR` (и легаси `QWEN3_ASR_CACHE_DIR`) — `:820-834`.
- Две раскладки одновременно: старая плоская `…/qwen3-speech/<sanitized_modelId>/` (если уже наполнена) и новая Hub-style `…/qwen3-speech/models/<owner>/<repo>/`; выбор — мемоизированная проверка «есть ли веса» (`:57-84`).
- Скачивание: один запрос к tree API, отбор по glob (`config.json`, `*.safetensors`, `model.safetensors.index.json`, доп. паттерны вроде `encoder.mlmodelc/**`), сужение по shard-index, крупные файлы — конкурентными range-запросами (16 диапазонов по 8 МиБ), с докачкой и stall-guard (`:140-200`, `:600-720`).
- Целостность: размер + `sha256` из LFS-метаданных Hub, при несовпадении файл **удаляется** (`HuggingFaceTransfer.swift:104-119`). Проверка происходит только при скачивании; при последующих загрузках файлы не перепроверяются — их собственный комментарий признаёт, что битый файл затем «всплывает как unreadable-tensor глубоко в загрузке весов».
- Авторизация к Hub: `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` из окружения → `Authorization: Bearer` (`HuggingFaceTransfer.swift:411-419`). Keychain/файла токена нет.
- Офлайн: `offlineMode: true` у `fromPretrained` — только для native-режима и тестов; обычный сервер всегда `false`, т.е. офлайн-поведение зависит от сетевого слоя (при недоступности Hub и полном кэше загрузка продолжается, `HuggingFaceDownloader.swift:196-200`).
- Зеркало: `HF_ENDPOINT` (`:651-666`).

### 11. Безопасность

| Аспект | Состояние | Доказательство |
|---|---|---|
| Bind по умолчанию | `127.0.0.1` | `AudioServerCommand.swift:12-13`, `AudioServer.swift:35,39` |
| Аутентификация | отсутствует полностью (нет ни одного middleware, ни проверки `Authorization`) | `grep -n "middleware|Authorization|Bearer|apiKey|CORS"` по `Sources/AudioServer/*.swift` — 0 совпадений; `AudioServer.swift:72-76` |
| TLS | нет; только `http1WebSocketUpgrade` | `AudioServer.swift:65` |
| Проверка `Origin` для WS | нет → любая веб-страница может открыть `ws://127.0.0.1:8080/v1/realtime` и синтезировать речь / гонять модели (CSWSH) | `AudioServer.swift:65-70` |
| Лимиты | только размеры тела на маршрут (1 МиБ / 16 МиБ / 50 МиБ) и `queueFull`; rate limit, квот на соединения, idle-таймаута нет | `:153`, `:201`, `OpenAISpeech.swift:139`, `NativeInference.swift:305` |
| Утечка информации | `/v1/inference/provenance` отдаёт абсолютные пути конфига и `pid` | `NativeInference.swift:280-297` |
| Ввод | hand-rolled multipart-парсер, `decodeWAVData` пишет тело во временный файл в `FileManager.default.temporaryDirectory` c UUID-именем, читает через `AVAudioFile`, затем удаляет | `OpenAITranscriptions.swift:262-332`, `AudioServer.swift:1995-2010` |
| Секреты | `HF_TOKEN` читается из env процесса | `HuggingFaceTransfer.swift:411-419` |

### 12. История (git log по серверным файлам)

| Файл | Коммитов | Первый | Последний |
|---|---|---|---|
| `Sources/AudioServer/AudioServer.swift` | 23 | 2026-03-05 (`b75b89c` «Add HTTP API server…») | 2026-10-23 (`ff73724`) |
| `Sources/AudioServer/ModelRegistry.swift` | 12 | 2026-06-16 (`af78e98`) | 2026-10-17 (`8d01771`) |
| `Sources/AudioServer/OpenAISpeech.swift` | 4 | 2026-07-18 (`557137d`) | 2026-10-17 (`8d01771`) |
| `Sources/AudioServer/OpenAITranscriptions.swift` | 4 | 2026-05-26 (`0eb66eb`) | 2026-10-17 (`88fce9a`) |
| `Sources/AudioServer/RealtimeVAD.swift` | 1 | 2026-09-02 (`5ff7e30`) | 2026-09-02 |
| `Sources/AudioServer/NativeInference.swift` | 5 | 2026-10-23 (`ff73724`) | 2026-10-24 (`6f3eafe`) |
| `Sources/AudioServerCLI/AudioServerCommand.swift` | 6 | 2026-03-05 | 2026-10-23 |
| `Sources/SpeechRuntime/*` (Gate, Cache, Runtime, Descriptor) | по 1 | 2026-10-17 (`88fce9a`) | 2026-10-17 |

- Всего по `Sources/AudioServer` + `AudioServerCLI` + `SpeechRuntime`: +5052 / −968 строк за 31 коммит каталога `Sources/AudioServer`.
- Ключевые вехи: `e2f5d6a` (2026-03-07) — WS Realtime; `0eb66eb` (2026-05-26) — `/v1/audio/transcriptions`; `af78e98` (2026-06-16) — `model` в `session.update` начинает управлять движками; `0705a6a`/`5c4f03c`/`69969b1` (2026-06-19) — keepalive и отключение autoPing; `f950bc5` (2026-06-19) — вывод инференса из event-loop; `5ff7e30` (2026-09-02) — server VAD; `88fce9a` (2026-10-17) — **появление сериализации** (`InferenceGate`, `ModelCache`, `ModelRuntime`); `ff73724` (2026-10-23) — native inference.
- Проверка гипотезы «до 88fce9a сериализации не было»: `git show 88fce9a^:Sources/AudioServer/AudioServer.swift | grep -n "Lock\|gate\|serial\|queue"` → пусто. То есть весь механизм владения инференсом моложе 10 дней.
- Репозиторий целиком: 1067 коммитов, HEAD 2026-10-25; от `b75b89c` до HEAD — 958 коммитов.

---

## Ограничения и риски

### A. Архитектурные (мешают быть runtime)

1. **Один глобальный слот инференса.** `InferenceGate` создаётся на процесс (`AudioServer.swift:538-545`), permit берут все операции. Следствие: N клиентов = N последовательных запросов; «одновременные независимые сессии» невозможны по построению, даже если модели разные и железо простаивает. Доказательство — тест `maxActive == 1` (ASRRuntimeRegressionTests#L180-L247).
2. **Permit удерживается через сетевой ввод-вывод.** `response.create` держит permit, пока пишет `response.audio.delta` (`:1199-1201` + все `outbound.write` ниже). Медленный клиент = стоп-кран для всего сервера. `[INFERENCE]` по порядку операторов.
3. **Нет выгрузки и бюджета памяти.** `ModelCache` не имеет ни одного метода удаления (`ModelCache.swift:6-33`); сервер не вызывает `unload` нигде. Каталог из 48 моделей → потенциально все 48 резидентны; на 7B-моделях это OOM задолго до исчерпания каталога.
4. **Холодный старт под гейтом.** Загрузка идёт внутри permit (`:1757-1765`), значит скачивание 3-гигабайтного бандла блокирует `/transcribe`, `/speak`, `/respond`, `/enhance` и все WS-сессии одновременно. Нет ни «загружаю, подождите», ни прогресса по HTTP.
5. **Стриминговый ASR отсутствует.** `dispatchTranscribe` для всех движков зовёт батчевый `transcribeAudio` (`:1763-1783`), включая `parakeet-streaming` и `nemotron`. Ни partial-гипотез, ни `transcript.delta`, ни таймстемпов (даже `verbose_json` отдаёт один сегмент на весь файл, `OpenAITranscriptions.swift:109-133`).
6. **Нет диаризации/эмбеддингов/VAD-эндпоинта.** 19 записей реестра недостижимы: kinds `music, vad, diarize, speaker, separate, sr, turn`. Комментарий обещает `/compose`, `/diarize` (`AudioServer.swift:1028-1035`) — таких маршрутов нет.
7. **Нет версии протокола.** Ни заголовка, ни поля в `session.created`, ни сабпротокола; единственный признак версии — `schema_version: 1` в native-режиме (`NativeInference.swift:211`). Эволюционировать контракт без ломки клиентов нечем.
8. **Нет API управления моделями.** Ни `pull`, ни `delete`, ни `load/unload`, ни статуса («что загружено, сколько памяти, сколько раз использовалось»), ни ревизий/отката. Скачивание — побочный эффект первого инференса.
9. **Реестр в коде.** Добавление модели = правка Swift-массива + новый метод в 21-методном протоколе + новая ветка `switch` в `dispatchTranscribe`/`dispatchSynthesize` + (для WS) новая ветка в `response.create` — четыре места, компиляция, релиз.
10. **Блокирующие вызовы в HTTP-путях.** `dispatchSynthesize`/`model.respond`/`enhancer.enhanceChunked` — синхронные, без офлоада (в отличие от WS, где есть `runOffloaded`). При гейте на 1 это «всего одна нить», но keepalive/health в это время должны обслуживаться другими нитями. `[INFERENCE]` о деградации под нагрузкой без измерений.

### B. Корректность

11. **`personaplex-7b-mlx-8bit` тихо исполняет 4-битный бандл.** `loadPersonaPlex()` не принимает `modelId`, ключ кэша — константа `"loadPersonaPlex"`, вызов `PersonaPlexModel.fromPretrained(progressHandler:)` без `modelId` (`AudioServer.swift:736-757`), хотя дефолт сигнатуры — `defaultModelId` (4-bit), а `modelId8bit` в реестре указан (`ModelRegistry.swift:230-240`, `Sources/PersonaPlex/PersonaPlex.swift:16-19,1576-1580`). Алиас `personaplex-8bit` — ложь для клиента.
12. **`--preload` прогревает не дефолты.** `preloadModels()` грузит Qwen3-ASR small, Qwen3-TTS, PersonaPlex, DeepFilterNet3 (`:79-86`), а дефолты сессии — Parakeet + Kokoro (`:826-837`).
13. **`verbose_json` фабрикует метрики**: `avg_logprob: 0.0`, `no_speech_prob: 0.0`, `tokens: []`, один сегмент на весь файл (`OpenAITranscriptions.swift:109-133`). Клиент, фильтрующий по `no_speech_prob`, получает ложь.
14. **Молчаливый fallback в `/v1/audio/transcriptions`** (`:64-73`) при явном 400 в `/transcribe` — два противоположных контракта для одного и того же действия.
15. **Мёртвый код**: `loadQwen3ASR(modelId:)` и `loadASR()` объявлены в протоколе и реализованы (`:395`, `:461`, `:579`, `:587-589`), но не вызываются ни разу; Qwen3-ASR идёт через `transcribeQwen3ASR` → `Qwen3ASRRuntime`. При этом ключи кэша у двух путей разные (`"<backend>:<modelID>"` против `modelId`) — если бы оба пути использовались, веса грузились бы дважды.
16. **Тело-зависимый парсинг**: `RequestParams.parse` для не-JSON считает тело аудио, если `data.count > 44` (`:1965-1993`) — эвристика «длиннее WAV-заголовка». Для сырого тела параметров нет вообще, поэтому модель/язык/voice задать нельзя, что расходится с help-текстом CLI.
17. **Ошибки HF/OOM отдаются как 500** без различения (`:2064-2073`), `Retry-After` нет.

### C. Эксплуатация

18. **Нет логирования запросов и метрик.** Логи — `print("[server] Loading …")` в загрузчиках (`:573`, `:581`, `:593`, …) и `context.logger` только в native-маршрутах. Ни request id, ни длительности, ни размера очереди (`pendingCount` существует, но никуда не выводится, `InferenceGate.swift:28-31`).
19. **`/health` — константа** (`:96-101`): не говорит, загружены ли модели, идёт ли скачивание, есть ли очередь. Оркестратор не отличит «готов» от «качает 3 ГБ».
20. **Нет graceful shutdown/дренажа**: `try await app.run()` без обработки сигналов и без ожидания активных permit.
21. **Temp-файл на каждый WAV в обе стороны** (`:1995-2010`) — лишний диск в горячем пути ASR/TTS; при 50 МиБ тела это ещё и запись 50 МиБ на запрос.
22. **50 МиБ на WS-сообщение при 16 МиБ на кадр** (`:1000` vs `:385`) — рассинхрон лимитов: сообщение из нескольких кадров проходит проверку, но клиент упрётся в лимит кадра раньше.

### D. Техдолг

23. Две реализации очереди с разной семантикой (`InferenceGate` — bounded/отменяемая/503; `NativeInferenceGate` — unbounded/неотменяемая/без 503), `NativeInference.swift:86-99`.
24. Четыре формата ошибок в одном HTTP-сервере (см. §9).
25. «God protocol» `RealtimeModelLoading` — 21 метод (`:391-413`), единственная продакшн-реализация `ModelState`, второй «реализацией» является тестовый `FailingRealtimeModelLoading` (`:424-535`) — абстракция существует ради тестов и раздувает каждое добавление модели.
26. `fatalError` в `defaultVariant` (`ModelRegistry.swift:399-404`).
27. Файл `AudioServer.swift` — 2074 строки, в нём одновременно: маршруты, WS-протокол, реестр-резолверы, диспетчеризация, PCM-конвертация, WAV-кодеки, JSON-хелперы, маппинг языков, HTTP-ошибки.
28. Hand-rolled multipart-парсер (`OpenAITranscriptions.swift:262-332`) и hand-rolled `formatTimestamp`/SRT/VTT (`:207-223`) — риск краевых случаев (RFC 7578: `filename*`, вложенные `--boundary` в бинарнике частично обработаны, но без стриминга и без лимита на часть).
29. Захардкоженные ключи кэша (`"loadMagpie"`, `"loadMagpieCoreML"`, `"loadPersonaPlex"`) ломают инвариант «ключ = идентичность весов» (`:684-757`).
30. `Task.detached` для каждого инференса (`:1600-1611`) — нет структурированной конкурентности, нет ограничения на число detached-задач.

### E. Мелкие, но показательные дефекты контракта

31. **Публичный `init` принимает `preload: Bool` и игнорирует его** — параметр не используется в теле, предзагрузка возможна только отдельным вызовом `preloadModels()` из CLI (`AudioServer.swift:39-46`).
32. **`/speak` не умеет выбирать голос Kokoro/Qwen3-TTS**: `voice` пробрасывается только для `voxtral-tts` и `pocket-tts` (`:228-230`), тогда как `/v1/audio/speech` пробрасывает `voice` для `kokoro`, `qwen3-tts`, `voxtral-tts`, `pocket-tts` (`OpenAISpeech.swift:210-219`). Один и тот же сервер даёт разные возможности на двух TTS-маршрутах.
33. **S2S-слот на сессию — одиночный**: `lastCommittedAudio` перезаписывается каждым новым `commit`, `response.create` его обнуляет (`AudioServer.swift:1176-1198`, `:1230-1240`). Два быстрых хода подряд без промежуточного `response.create` → первый теряется молча.
34. **`maxSteps` для PersonaPlex в WS жёстко 200** и не читается из запроса (`:1246-1258`), в отличие от HTTP `/respond`, где есть `max_steps` (`:275`).

---

## Выводы для Runtime

### Что взять

1. **Hummingbird 2 + hummingbird-websocket как транспорт** — компактный, Swift-native, HTTP/1.1 + WS-апгрейд из коробки. Но обязательно добавить то, чего нет: middleware аутентификации, `Origin`-проверку для WS, request-id и структурные логи, `serverConfiguration` с таймаутами и лимитом соединений.
2. **Идею `ModelCache` (actor + `Task` на ключ)** — single-flight загрузки и «ошибка не отравляет кэш» (`ModelCache.swift:16-33`) реализованы чисто. Сверху нужен слой резидентности: `refCount` + TTL + бюджет памяти + `unload`, то есть `ModelRuntime`-подобная обёртка, но с вызовом из рантайма, а не только из Qwen3-ASR.
3. **`InferenceGate` как примитив** — bounded FIFO, отменяемые ожидающие, явный permit с идемпотентным `release()` и `deinit`-страховкой (`InferenceGate.swift:33-99`). Перенести как есть, но **инстанс на модель/движок**, а не на процесс, и с обязательным таймаутом ожидания + `Retry-After`.
4. **`ModelDescriptor`** — минимальный и правильный по смыслу (`backend`, `inputSampleRate`, `streaming ∈ {buffered, incremental}`). Расширить до артефакта: `{engine, quantization, files[{path,size,sha256}], revision, license, memoryEstimate, streaming}` — это ровно то, чего не хватает реестру speech-swift.
5. **Реестр как единственная точка правды для клиента** (`/v1/models` с `kind`/`aliases`/`model_id`) — идея верная: клиент интроспектирует каталог, а не угадывает имена. Но данные должны быть внешними (манифест), а не `let`-массивом в Swift.
6. **HF-загрузчик** (`Sources/AudioCommon/HuggingFaceDownloader.swift`, `HuggingFaceTransfer.swift`): tree-API одним запросом, `sha256` из LFS-oid с удалением битого файла, конкурентные range-запросы с докачкой, stall-guard, проверка полноты шардов по `model.safetensors.index.json`, офлайн-режим, `HF_ENDPOINT`. Это лучшая часть проекта и её стоит переиспользовать (Apache-2.0).
7. **`realtime.keepalive` вместо autoPing** — обход реального конфликта `URLSessionWebSocketTask` и watchdog'а; сохранить как приём, но с оговоркой о конкурентной записи в один `outbound` (см. «Неизвестные»).

### Что не брать

8. **Глобальный однопоточный гейт** — прямой отказ от «одновременных независимых сессий». Нужна очередь на инстанс модели + отдельный realtime-резерв, чтобы длинный стриминг одного клиента не блокировал батч-ASR другого.
9. **Permit через сетевые записи** — инференс должен завершаться (и освобождать слот) до/независимо от отправки дельт; отправка — отдельная очередь на соединение с ограничением буфера.
10. **Кэш без выгрузки** — неприемлемо: 48 моделей × 0.1–7B. Нужны `refCount`, TTL простоя, бюджет памяти и вытеснение по LRU с запретом вытеснять занятый инстанс.
11. **Загрузка модели внутри permit запроса** — вынести в отдельную фазу с состоянием `loading(progress)` и возможностью отвечать 202/`Retry-After` остальным.
12. **`switch`-диспетчеризация по строке `engine`** — заменить на протокол адаптера (`ASREngine`, `TTSEngine`, …) с регистрацией; иначе каждое добавление модели — 4 правки в одном файле.
13. **Молчаливые fallback'и и фабрикованные поля** (`verbose_json` с `avg_logprob: 0.0`, fallback на Parakeet) — контракт должен быть честным: либо поле есть и посчитано, либо его нет.
14. **`fatalError` в путях резолвинга**, hand-rolled multipart, temp-файлы на каждый WAV, «god protocol», захардкоженные cache-key-константы.

### Как интегрировать

15. **Сохранить OpenAI-совместимую поверхность** (`/v1/audio/transcriptions`, `/v1/audio/speech`, WS `/v1/realtime`) как *адаптер* над новым ядром: это даёт совместимость с существующими SDK-клиентами бесплатно, но ядро должно быть своим (модель → инстанс → очередь → движок), а не тем, что описано в §A.
16. **Разделить «протокол» и «движок»**: `ModelVariant` из speech-swift смешивает HF-repo, квантование и slot-kind. В нашем реестре это три сущности: `model` (логическая), `artifact` (файлы под конкретный engine/quantization), `binding` (какая операция каким движком обслуживается).
17. **Явный API жизненного цикла**: `GET /v1/models` (с `loaded`, `refcount`, `last_used`, `resident_bytes`), `POST /v1/models/{id}/load`, `DELETE /v1/models/{id}`, `POST /v1/models/{id}/pull` с SSE-прогрессом. В speech-swift этого нет вообще — и это главный дефицит для «Ollama для речи».
18. **Версионировать протокол с первого дня**: заголовок (`X-Speech-Runtime-Protocol: 1`) + поле в `session.created` + `schema_version` в `/v1/models`; иначе первый же breaking change похоронит клиентов.
19. **Диаризация/эмбеддинги/VAD** — сразу как первоклассные операции (`kind: diarize|speaker|vad` с реальными маршрутами), а не как «каталог без реализации». У speech-swift есть сами модели (Sortformer, WeSpeaker, Silero/Pyannote/FireRed), но нет ни одного маршрута.
20. **Изоляция падений**: в speech-swift всё в одном процессе — падение MLX/CoreML роняет и ASR, и TTS, и все сессии. Для runtime с «несколькими движками» разумно вынести движки в подпроцессы (референс — Ollama) или, как минимум, обеспечить per-engine watchdog.

---

## Неизвестные / что измерить

**Не подтверждено кодом/тестами:**

1. **Конкурентная запись в `WebSocketOutboundWriter`**: keepalive-задача (`AudioServer.swift:984-998`) и основной цикл пишут в один `outbound` без явной синхронизации. Безопасно ли это в `hummingbird-websocket` 2.6.0 — не проверял (нужно смотреть исходники пакета или гонять стресс-тест с двумя одновременными писателями).
2. **Отмена при разрыве соединения**: форвардится ли отмена из `for try await message in inbound.messages` в `runOffloaded`-задачу при закрытии сокета — зависит от поведения Hummingbird; в коде только `withTaskCancellationHandler` (`:1600-1611`). Нужен эксперимент: подключиться, начать `response.create` на 7B, оборвать соединение, замерить, освободился ли permit.
3. **Реальный RSS загруженной модели и кэша MLX** в обычном (не native) режиме: `Memory.cacheLimit` не выставляется, `clearCache()` не вызывается. Нужны замеры: RSS после загрузки 1/2/5 моделей, RSS после 100 запросов.
4. **Холодный старт**: ни одного бенчмарка сервера в репозитории нет. Нужно измерить: время до первого байта `/transcribe` для дефолтного parakeet-tdt-v3 и qwen3-1.7b (размеры бандлов не проверял), время скачивания, время до первого `response.audio.delta`.
5. **Поведение `queueFull` на WS**: тесты покрывают только HTTP 503 (`ASRRuntimeRegressionTests#L219-L230`). Что именно увидит WS-клиент (событие `error` + живое соединение) — по коду да, но тестом не закреплено.
6. **Декодирование не-WAV контейнеров**: тело пишется в файл с расширением `.wav` (`:1995-2000`), а `AVAudioFile` определяет формат по содержимому. Работает ли mp3/webm/m4a через этот путь — не проверял.
7. **Точная резолвнутая версия Hummingbird**: `Package.resolved` в репозитории отсутствует, диапазон `2.5.0..<2.17.0` — какая версия реально собирается, неизвестно.
8. **Существование и содержимое HF-репозиториев** из реестра (`aufklarer/Qwen3-ASR-0.6B-MLX-4bit`, `aufklarer/PersonaPlex-7B-MLX-8bit`, `aufklarer/CosyVoice3-0.5B-MLX-8bit-full`, …) — не проверял (это зона D1/G); в самом репозитории есть следы правок «репозиторий не существует» (коммит `e33ed32` «Drop the unpublished Stable Audio variants»).
9. **Пригодность дефолтного ASR (Parakeet TDT v3) для русской речи с англ. IT-терминами** — вне рамок этого аудита, но именно он выбирается, если клиент не указал `model`.
10. **Производительность при 2+ одновременных WS-сессиях** — ни теста, ни бенчмарка; из кода следует строгая последовательность, но фактическую задержку второго клиента нужно измерить (ожидание = длительность первого ответа).

**Что стоит измерить в первую очередь (для решения о форке):**

- TTFB и RTF для `/transcribe` (parakeet, qwen3-0.6b, qwen3-1.7b) на русском 30-секундном фрагменте;
- рост RSS при последовательной загрузке 3 разных TTS-моделей (проверка «никогда не выгружаем»);
- деградация второго клиента при параллельном WS-стриминге TTS;
- поведение при обрыве клиента во время `response.create` (освобождается ли permit);
- корректность `personaplex-8bit` (ожидаемо: реально 4-bit — проверить по `modelId` в `/v1/inference/provenance` или по логам загрузки).

---

## Источники

**Код (ревизия `cbad3be29480825707890490f446b19d7288be2d`)**

- `Sources/AudioServer/AudioServer.swift` (2074 строки) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift
- `Sources/AudioServer/ModelRegistry.swift` (404) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/ModelRegistry.swift
- `Sources/AudioServer/OpenAISpeech.swift` (267) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/OpenAISpeech.swift
- `Sources/AudioServer/OpenAITranscriptions.swift` (332) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/OpenAITranscriptions.swift
- `Sources/AudioServer/RealtimeVAD.swift` (307) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/RealtimeVAD.swift
- `Sources/AudioServer/NativeInference.swift` (367), `NativeInferenceFailures.swift` (50) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift
- `Sources/AudioServerCLI/AudioServerCommand.swift` (59) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServerCLI/AudioServerCommand.swift
- `Sources/SpeechRuntime/{InferenceGate,ModelCache,ModelRuntime,ModelDescriptor}.swift` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift
- `Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift
- `Sources/AudioCommon/HuggingFaceDownloader.swift` (835), `HuggingFaceTransfer.swift` (590) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceDownloader.swift
- `Sources/SpeechVAD/SileroVAD.swift:250-300` (per-session загрузка) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift#L250-L300
- `Sources/PersonaPlex/PersonaPlex.swift:16-19,1576-1580` (дефолт 4-bit) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/PersonaPlex/PersonaPlex.swift#L1576-L1580
- `Package.swift:208-260, 947-991` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L947-L991

**Тесты**

- `Tests/AudioServerTests/ASRRuntimeRegressionTests.swift:180-247` (сериализация между WS и HTTP, 503 при переполнении) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Tests/AudioServerTests/ASRRuntimeRegressionTests.swift#L180-L247
- `Tests/SpeechRuntimeTests/RuntimeOwnershipTests.swift:15-147` (single-flight, bounded queue, отмена, unload) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Tests/SpeechRuntimeTests/RuntimeOwnershipTests.swift#L15-L147
- `Tests/AudioServerTests/RealtimeAPIErrorHandlingTests.swift:43-260` (ошибки не рвут соединение, keepalive при блокирующей загрузке) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Tests/AudioServerTests/RealtimeAPIErrorHandlingTests.swift#L43-L260
- `Tests/AudioServerTests/RealtimeAPITests.swift` (52 теста протокола), `RealtimeVADTests.swift`, `E2ERealtimeAPIEnginesTests.swift`, `E2EOpenAITranscriptionsTests.swift`, `E2EVoxtralTransportTests.swift`, `E2EPocketTransportTests.swift`, `TestAudioServer.swift` (харнесс готовности).

**Документация репозитория**

- `README.md:555-564` (раздел HTTP API server) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/README.md#L556-L563
- `docs/shared-protocols.md:591-700` («Thread Safety»: все модели кроме WeSpeaker — not thread-safe; OpenAI-HTTP; Realtime errors; server VAD) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/shared-protocols.md#L517-L700
- `README.md:578-590` (кэш `~/Library/Caches/qwen3-speech/`, `QWEN3_CACHE_DIR`, `HF_ENDPOINT`) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/README.md#L600-L608
- `AGENTS.md:154-157, 254-258`
- `LICENSE` — Apache License 2.0.

**Git**

- `git log --format='%h %ad %s' --date=short -- Sources/AudioServer` (31 коммит, 2026-03-05 … 2026-10-24)
- `git show 88fce9a --stat` (появление `SpeechRuntime`)
- `git show 88fce9a^:Sources/AudioServer/AudioServer.swift | grep "Lock|gate|serial|queue"` → пусто (сериализации не было)
- `git log -n 100 --numstat -- Sources/AudioServer Sources/AudioServerCLI Sources/SpeechRuntime` → +5052/−968
