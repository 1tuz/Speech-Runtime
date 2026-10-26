# V3 — Готовые реализации для недостающих частей Speech Runtime

Дата: 2026-10-26. Агент: V3MissingAPIs. Волна 2 (исследование, не реализация).

Метод: чтение кода в клонах `/tmp/srr/repos/*` (sha из задания) и `/tmp/srr/extra/*`; метаданные
активности/лицензий — GitHub API и crates.io API на 2026-10-26. Все permalink'и привязаны к commit.
Свои выводы помечены `[INFERENCE]`.

Вердикты: **ЗАВИСИМОСТЬ** — брать как зависимость; **КОПИРОВАТЬ** — скопировать код с атрибуцией;
**ИДЕЯ** — переписать по мотивам; **САМИ** — писать с нуля.

## Итог

1. **Планировщик: писать самим на Rust, но по точной модели LocalAI `WatchDog`.** Это самый близкий
   к заданию код из найденного: busy-kill зависшего бэкенда, idle-TTL, LRU-лимит, порог памяти
   (VRAM→RAM), pinned-модели, size-aware eviction, group-exclusivity. Ollama `sched.go` даёт эталон
   refcount+`expireTimer`, но без kill и без бюджета памяти. Обе — MIT/Go → **ИДЕЯ**.
2. **audio.cpp — НЕ основа runtime.** Apache-2.0, ggml/C++, 110+ семейств моделей (включая GigaAM v3 ru),
   но: нет WebSocket, нет refcount на сессию, один процесс на все модели, нет sha256-верификации, нет
   CAS, нет speaker-embedding API, Metal — догоняющий бэкенд (CUDA-first). Из него стоит **КОПИРОВАТЬ**
   `model_slots.h` (admission с management-barrier) и `busy_guard.h` (fail-fast 503), лицензия позволяет.
3. **WS-протокол сессии ASR: писать самим.** Готового протокола «partial/final/VAD/EOU» под наши нужды
   нет ни у кого. Словарь событий — Deepgram-семантика (де-факто стандарт, имена событий не охраняются);
   лучший образец инвариантов — WLK `deepgram_compat.py` (Apache-2.0, 1136 строк) → **ИДЕЯ**, не код.
   Ближайший готовый *полный* набор событий — `TranscriptLiveResponse` в LocalAI `backend.proto`.
4. **Фрейминг IPC: КОПИРОВАТЬ формат Handy** — `[u32 json_len][json][u32 pcm_len][pcm]`, little-endian,
   JSON вместо bincode, лимит `1<<30` на секцию. 314 строк, MIT, работает в проде.
5. **Скачивание: `hf-hub` 1.1.0 (ЗАВИСИМОСТЬ)** для Rust — Apache-2.0, активен, даёт revision-pin,
   gated, Xet, и **CAS-подобный кэш на диске** (`blobs/<etag>` + `snapshots/<commit>/…` + `refs/<rev>`).
   Но hf-hub **не проверяет sha256 скачанных байт** (sha2 используется только при upload) → проверку
   дописываем сами. Для Swift эталон проверки — `speech-swift/HuggingFaceTransfer.swift` (КОПИРОВАТЬ).
6. **CAS-хранилище: писать самим** по схеме ollama (`blobs/sha256-<hex>` + OCI-подобный manifest
   `schemaVersion/mediaType/config/layers`). Готовых зрелых Rust-крейтов OCI-реестра нет.
7. **Speaker registry: ЗАВИСИМОСТЬ на FluidAudio `SpeakerManager`** (Apache-2.0, 638 строк, Codable,
   threshold-based assign/merge/permanence) — если воркер на Swift. Для Rust — портировать
   `sherpa-onnx/SpeakerEmbeddingManager` (Apache-2.0, 305 строк, cosine + Verify/GetBestMatches).
   Критично: хранить `encoder_family` + `encoder_weights` вместе с эмбеддингом (образец — LocalAI `KnownVoice`).
8. **Декодирование/ресемплинг без ffmpeg — обе платформы закрыты готовым кодом:** Rust —
   `symphonia` 0.6 + `rubato` 5 (Handy `decode.rs`, MIT, КОПИРОВАТЬ); Swift — `AVAudioConverter`
   (FluidAudio `AudioConverter.swift` + `AudioSourceFactory.swift`, Apache-2.0, ЗАВИСИМОСТЬ/КОПИРОВАТЬ).
9. **Супервизор: КОПИРОВАТЬ Handy `engine_supervisor`** (MIT, 2018+479+314 строк): worker = тот же
   бинарь с `--worker`, очередь запросов, cancel убивает воркер, hang→retry на CPU, GPU помечается
   недоступным. Для автозапуска: `service-manager` 0.11 (launchd) + `objc2-service-management`
   (SMAppService для app-bundle, как в Handy).
10. **SDK: REST генерировать, WS — руками.** Swift: `swift-openapi-generator` (Apache-2.0, tools 6.2,
   1993★). Rust: `progenitor` (MPL-2.0, клиенты) или `utoipa` (code-first, сервер). Для AsyncAPI
   зрелых генераторов Swift/Rust **нет** (`asyncapi/modelina` — TypeScript; rust-ws-шаблон — 2★).

## Проверенные утверждения

| Утверждение | Источник-отчёт | Вердикт | Доказательство |
|---|---|---|---|
| audio.cpp лицензирован Apache-2.0 (ShugoAI LLC) | A4-discovery | ПОДТВЕРЖДЕНО | `https://github.com/0xShug0/audio.cpp/blob/1cada9b7045cc6935bcafb3f5a26806d133535e7/LICENSE#L1-L3` |
| audio.cpp поддерживает GigaAM v3 (ru/kk/ky/uz/en) | D1-russian-asr | ПОДТВЕРЖДЕНО | `…/blob/1cada9b7/README.md#L134` — `gigaam_asr \| ASR \| ru, kk, ky, uz, en` |
| audio.cpp имеет VAD: Silero, FireRed, MarbleNet, PulseVAD | E1-vad-turn | ПОДТВЕРЖДЕНО | `README.md#L140`, `#L144-L145`, `#L156` |
| audio.cpp имеет диаризацию: Sortformer, Nemotron 3 Diar | E2-diarization | ПОДТВЕРЖДЕНО | `README.md#L148`, `#L159`, `#L242` |
| audio.cpp содержит LRU-эвикцию, idle-unload, busy-timeout→503 | G-model-management | ПОДТВЕРЖДЕНО | `app/server/runtime.cpp#L614-L651`, `#L2435-L2482`, `app/server/busy_guard.h#L39-L46` |
| audio.cpp **не** поддерживает WebSocket | — (не утверждалось) | ПОДТВЕРЖДЕНО | `grep -rn -i websocket app/server/*.cpp app/server/*.h` → 0 совпадений; `app/server/api.md#L386`: «A WebSocket transport … could be added alongside this» |
| audio.cpp **не** верифицирует sha256 скачанных файлов | G-model-management | ПОДТВЕРЖДЕНО | `src/framework/package_manager/manager.cpp#L573-L581` (только size/etag/x-repo-commit), `#L884-L896` (etag → лишь `version_state`) |
| audio.cpp: Metal по умолчанию OFF, включается только на APPLE; проект CUDA-first | — | ПОДТВЕРЖДЕНО | `CMakeLists.txt#L93` + `#L97-L99`; README: «**Optimized for CUDA**» |
| audio.cpp: модели живут в одном процессе (нет изоляции воркеров) | — | ПОДТВЕРЖДЕНО | `app/server/runtime.h` — `std::unordered_map<…> models_`, `model_index_` в `ServerState` |
| WhisperLiveKit — Apache-2.0 | A2 | ПОДТВЕРЖДЕНО | `LICENSE` L1-2 |
| WLK реализует Deepgram `Results(is_final,speech_final,from_finalize)`/`SpeechStarted`/`UtteranceEnd` | A2 | ПОДТВЕРЖДЕНО | `whisperlivekit/deepgram_compat.py#L500-L563` |
| gotranscribesrv — **GPL-3.0** | A3 (лицензия не указана) | ПОДТВЕРЖДЕНО | `README.md#L397-L399`; в корне только `LICENSE.md`, файла `LICENSE` нет |
| Wyoming — MIT; фрейминг = JSON-строка + `data_length` + `payload_length` | — | ПОДТВЕРЖДЕНО | `LICENSE.md#L1-L3`; `wyoming/event.py#L12-L14`, `#L89-L101` |
| Ollama — MIT; `sched.go` = refCount + expireTimer/expiresAt + keep-alive | G-model-management | ПОДТВЕРЖДЕНО | `server/sched.go#L1380-L1396`, `#L427-L452`, `#L1714-L1740` |
| LocalAI — MIT; watchdog = busy-kill/idle/LRU/memory-reclaimer/pinned/size-aware | G-model-management | ПОДТВЕРЖДЕНО | `pkg/model/watchdog.go#L16-L26`, `#L31-L88`, `#L813-L920` |
| LocalAI `backend.proto` содержит RPC для потокового и live ASR, voice embed/verify | — | ПОДТВЕРЖДЕНО | `backend/backend.proto#L23-L24`, `#L36`, `#L56-L58` |
| sherpa-onnx: hotwords только у трансдьюсеров и только с `modified_beam_search` | I-ru-it-adaptation (A6/A12) | ПОДТВЕРЖДЕНО | `sherpa-onnx/csrc/offline-recognizer.cc#L85-L90`, `online-recognizer.cc#L150-L156` |
| sherpa-onnx: per-stream hotwords (`hotwords_buf`, `CreateStream(recognizer, hotwords)`) | I (A7) | ПОДТВЕРЖДЕНО | `sherpa-onnx/c-api/c-api.h#L395-L398`, `#L522-L537`; `online-recognizer-transducer-impl.h#L210-L242` |
| sherpa-onnx `SpeakerEmbeddingManager` (Add/Search/Verify/GetBestMatches) | E2 | ПОДТВЕРЖДЕНО | `sherpa-onnx/csrc/speaker-embedding-manager.h#L19-L122` (файл 124 строки) |
| FluidAudio `SpeakerManager`: 256-d, threshold assign/merge/permanence, Codable | E2 | ПОДТВЕРЖДЕНО | `Sources/FluidAudio/Diarizer/Clustering/SpeakerManager.swift#L9-L13`, `#L136-L185`, `#L224-L283`, `#L528-L560` |
| FluidAudio **не имеет** bias/hotword/word-boosting в ASR | I (косвенно) | ПОДТВЕРЖДЕНО (grep) | `grep -rn "boost" Sources/FluidAudio --include=*.swift` → только Sortformer-скореры; `vocabulary` → только CLI-бенчмарки |
| speech-swift downloader верифицирует sha256 и умеет ranged-resume | G-model-management | ПОДТВЕРЖДЕНО | `Sources/AudioCommon/HuggingFaceTransfer.swift#L103-L120`, `#L13-L60` |
| speech-swift WS — это [OI] Realtime S2S (`session.created`, `input_audio_buffer.speech_*`, `server_vad`), а не ASR-partial | B1 (косвенно) | ПОДТВЕРЖДЕНО | `Sources/AudioServer/AudioServer.swift#L967-L978`, `#L1127-L1139`, `#L1690` |
| hf-hub 1.1.0 кэширует по `blobs/<etag>` + `snapshots/<commit>` + `refs/<rev>` (CAS-подобно) | — | ПОДТВЕРЖДЕНО | `hf-hub/src/cache/storage.rs#L134-L142` |
| hf-hub **не** проверяет sha256 при скачивании | — | ПОДТВЕРЖДЕНО | `sha2` встречается только в `repository/upload.rs` (`#L24`, `#L621-L627`); в `repository/download.rs` — 0 совпадений |
| Handy frame protocol: `[u32 json_len][json][u32 pcm_len][pcm]`, JSON, лимит `1<<30` | — | ПОДТВЕРЖДЕНО | `src-tauri/src/engine_supervisor/protocol.rs#L1-L18` |
| Handy регистрирует автозапуск через `SMAppService` на macOS 13+ | — | ПОДТВЕРЖДЕНО | `src-tauri/src/autostart.rs#L1-L10`, `#L49-L70` |
| Handy декодирует аудио через symphonia+rubato | — | ПОДТВЕРЖДЕНО | `src-tauri/src/audio_toolkit/audio/decode.rs#L1-L25`; `Cargo.toml#L61,L63` |
| `swift-openapi-generator` — Apache-2.0, tools 6.2, 1993★, активен | — | ПОДТВЕРЖДЕНО | `LICENSE.txt#L1-L2`; `Package.swift#L1`; GitHub API 2026-10-23 |
| `progenitor` — MPL-2.0, генерирует только клиентов | — | ПОДТВЕРЖДЕНО | `LICENSE#L1-L2`; `README.md#L3-L7` |
| Зрелого AsyncAPI→Swift/Rust генератора нет | — | ПОДТВЕРЖДЕНО (поиск) | `asyncapi/modelina` 446★ (TypeScript); `kanekoshoyu/asyncapi-rust-ws-template` 2★ |

---

## 1. Потоковый WS-протокол сессии ASR (partial / final / VAD)

### Кандидаты

| Кандидат | Что даёт | Лицензия | Вердикт |
|---|---|---|---|
| WLK `deepgram_compat.py` | семантика `Results{is_final, speech_final, from_finalize}`, `SpeechStarted`, `UtteranceEnd`, `Metadata`; endpointing по часам аудио | Apache-2.0 | **КОПИРОВАТЬ идею/имена событий** |
| WLK `diff_protocol.py` | `snapshot` + `diff` с `seq`/`n_lines`/`lines_pruned` — дельта-протокол вместо полного стейта | Apache-2.0 | **ИДЕЯ** (для экономии трафика на длинных сессиях) |
| gotranscribesrv `deepgram_realtime.go` | 524 строки Go, WS + Deepgram-события, гейтинг по query-параметрам | **GPL-3.0** | **ТОЛЬКО ИДЕЯ** (код брать нельзя) |
| Wyoming (`rhasspy/wyoming`) | фрейминг: JSON-строка + `data_length`/`payload_length`; события `transcribe`/`transcript-start`/`transcript-chunk`/`transcript-stop` | MIT | **КОПИРОВАТЬ формат фрейминга** |
| speech-swift `AudioServer` | Swift/Hummingbird WS-сервер, но протокол — [OI] Realtime S2S, не ASR-partial | Apache-2.0 | **ИДЕЯ для транспорта** (Hummingbird + WS), протокол не наш |
| audio.cpp | SSE + chunked PCM; WS отсутствует | Apache-2.0 | не подходит |
| LocalAI `backend.proto` | `TranscriptLiveResponse` — полный словарь событий | MIT | **КОПИРОВАТЬ словарь** |

### Что именно взять

**Инварианты WLK** (`deepgram_compat.py#L1-L10`) — это готовая формулировка требований к нашей сессии:
```
interim hypotheses may be replaced;
final word ranges are immutable and emitted exactly once;
endpointing is driven by VAD pauses on the submitted audio clock;
UtteranceEnd is a separate gap detector anchored to a finalized word.
```
Это ровно то, чего не хватает в черновике архитектуры (`partial(revision, stable_prefix_chars)` без
явного правила «final неизменяем»). `[INFERENCE]` Наш протокол должен явно гарантировать:
`final`-сегменты иммутабельны и приходят один раз; `partial` заменяет предыдущий; `revision`
монотонно растёт; `vad.start/vad.end` привязаны к сэмпловому времени входного потока, а не к времени
получения.

**Словарь событий** — гибрид Deepgram + LocalAI `TranscriptLiveResponse`
(`backend/backend.proto#L650-L659`): `ready` (ack открытия), `delta`, `eou`, `words[]`, `final_result`,
`eob` (backchannel, НЕ граница хода), `speakers[]`, `sounds[]`. Это покрывает VAD/EOU/диаризацию в одном
сообщении и разделяет «конец хода» и «поддакивание» — деталь, которой нет у Deepgram и которая важна
для русского диалога.

**Фрейминг для бинарного канала** — Wyoming (`wyoming/event.py#L89-L101`): newline-terminated JSON
header, затем `readexactly(data_length)`, затем `readexactly(payload_length)`. Проверено: обе ветки
(асинхронная и синхронная) реализуют дочитывание циклами (`#L159-L180`). Это лучше, чем чистый
`len-prefixed`, потому что JSON-заголовок читаем глазами при отладке, а PCM идёт отдельной секцией
без base64.

**Транспорт** — если control plane на Rust: `axum` 0.8 `WebSocketUpgrade` (проверено: 0.8.9, 543M
загрузок, активен). Если на Swift: `Hummingbird` + `hummingbird-websocket` (уже в зависимостях
speech-swift, `Package.swift#L254-L258`).

### Вердикт
**САМИ**, с копированием: (а) инвариантов и имён событий WLK, (б) словаря `TranscriptLiveResponse`,
(в) формата фрейминга Wyoming. Готовый сервер брать нельзя ни у кого: WLK — Python/одна модель на
процесс, gotranscribesrv — GPL, speech-swift — не тот протокол, audio.cpp — нет WS.

---

## 2. Планировщик моделей: refcount + TTL + бюджет

### Кандидаты

| Кандидат | refcount | TTL | бюджет памяти | kill зависшего | pinned | Вердикт |
|---|---|---|---|---|---|---|
| Ollama `server/sched.go` (MIT, 1849 стр) | ✅ `refCount uint` | ✅ `expireTimer`/`expiresAt` | частично (`updateFreeSpace`, `waitForVRAMRecovery`) | ❌ | ❌ | **ИДЕЯ** |
| LocalAI `pkg/model/watchdog.go` (MIT) | `inFlight`/`requestStarts` (по-запросно) | ✅ `idletimeout` | ✅ `memoryReclaimerThreshold` | ✅ `ShutdownModelForce` | ✅ `pinnedModels` | **ИДЕЯ (основная модель)** |
| audio.cpp `model_slots.h`+`busy_guard.h`+`runtime.cpp` (Apache-2.0) | ❌ (слот запроса) | ✅ `idle_unload_ms` | ✅ `min_free_memory_mb` + оценка | ✅ fail-fast 503 | ❌ | **КОПИРОВАТЬ код** |
| 0xShug0/audio.cpp как основа целиком | ❌ | ✅ | ✅ | ✅ | ❌ | **НЕТ** (см. §2.4) |

### 2.1 Ollama `sched.go` — что именно устроено хорошо

`runnerRef` (`#L1380-L1396`): `refCount uint // prevent unloading if > 0`, `sessionDuration`,
`expireTimer *time.Timer`, `expiresAt time.Time`. Завершение запроса (`#L410-L460`):
```go
runner.refCount--
if runner.refCount <= 0 {
    if runner.sessionDuration <= 0 { /* expire now */ }
    else { runner.expireTimer = time.AfterFunc(runner.sessionDuration, ...) }
}
```
Новый запрос: `runner.refCount++` и `expireTimer.Reset` (`#L450-L452`). Дефолт — `envconfig.KeepAlive()`,
переопределяется на запрос (`#L555-L557`).

Выбор жертвы (`#L1714-L1740`): `sort.Sort(ByDurationAndName(runnerList))`, сначала ищутся `refCount == 0`,
иначе берётся самый «короткий». Комментарий в коде прямо признаёт неоптимальность: «In the future we can
enhance the algorithm to be smarter … will one make room for the request?».

**Чего не хватает для нас:** нет kill зависшего раннера (зависший llama-server держит слот вечно),
нет бюджета памяти как явного admission-критерия, нет pinned.

### 2.2 LocalAI `WatchDog` — ближе всего к заданию

Поля (`pkg/model/watchdog.go#L31-L88`): `busyTime`, `inFlight`, `requestStarts`, `idleTime`, `lastUsed`,
`pendingEvictions`, `timeout`/`idletimeout`, `lruLimit`, `memoryReclaimerEnabled` +
`memoryReclaimerThreshold`, `forceEvictionWhenBusy`, `sizeAwareEviction` + `modelSizes`, `pinnedModels`,
`modelGroups`.

Цикл (`#L813-L920`): `Run()` → `checkIdle()` (`#L848`, убивает по `idletimeout`, `#L867`), `checkBusy()`
(`#L880`, убивает по `timeout`, `#L896`), `checkMemory()` (`#L910`, порог VRAM/RAM → LRU-эвикция).
`ShutdownModelForce` (`#L77-L81`, `#L729`) — принудительная остановка без ожидания gRPC-вызова.

**Три вещи, которых нет даже здесь, и которые нужны нам:**
1. `inFlight` считает *запросы*, а не *держателей*. Клиент, открывший сессию и замолчавший, не держит
   модель. `[INFERENCE]` Нам нужен отдельный `Lease { model_id, holder_id, generation }` с явным
   `release`, а не только счётчик запросов.
2. Нет `generation` — при перезагрузке модели старые lease'ы должны инвалидироваться, иначе клиент
   получит сессию на выгруженных весах.
3. `modelGroups` — статическая эксклюзивность (две модели не сосуществуют). Нам нужна динамическая
   (бюджет памяти), хотя идея группы пригодится для «MLX-воркер только один».

### 2.3 audio.cpp — что копировать

`app/server/model_slots.h` (**237 строк**, header-only) — единая FIFO-очередь «запросы + management»:
- `class ModelSlots` (`#L16`), `Lock acquire_run(int timeout_ms, label)` (`#L125`),
  `Pending queue_management(...)` (`#L147`).
- Management-барьер: `queue_management` ждёт полного опустошения (`exclusive_`) и блокирует новые
  запросы — это ровно то, что нужно для `POST /v1/models/{id}:unload` без гонки с инференсом.
- `dispatch()` корректно обрабатывает истёкшие дедлайны (`waiter->expired`) и не теряет пробуждения —
  каждый `Waiter` владеет своим `condition_variable`.

`app/server/busy_guard.h` (**147 строк**): `model_run_has_overrun()` (`#L39-L46`) — «держатель уже
превысил timeout → не ждать вовсе, вернуть 503» (`#L102-L120`). Это защита от накопления очереди на
зависшем GPU, которой нет у Ollama.

`app/server/model_memory.cpp` (**119 строк**): `estimate_model_memory_bytes()` — веса × 1.5 + 128 MiB +
`resources.resident_bytes`; корректно разбирает GGUF-выбор и safetensors-деревья с лимитами
(глубина 3, 10000 файлов); при неоднозначности возвращает `nullopt` и **пропускает** проверку вместо
ложного 503.

`app/server/runtime.cpp`: `evict_for_model_limit()` (`#L614-L651`) — сортировка по `last_used_ms` и
**неблокирующий** `busy.try_acquire()` у жертвы (иначе два evict'а дедлочат друг друга);
`idle_unload_loop()` (`#L2435`) + `unload_idle_models()` (`#L2457`), шаг `max(1000, idle_unload_ms/10)`,
`last_activity_ms_` обновляется и на старте, и на финише запроса (`#L806-L807`).

**Вердикт: КОПИРОВАТЬ (Apache-2.0, с атрибуцией в NOTICE) → портировать на Rust.** Это самый
отточенный кусок из всех проверенных: обработка дедлайнов, неблокирующий evict, fail-fast, оценка
памяти с честным «не знаю».

### 2.4 Отдельный вердикт: может ли audio.cpp быть основой runtime

**НЕТ.** Семь причин, каждая проверена по коду:

1. **C++/ggml.** Встраивание в Swift/Rust-приложение → CMake-сборка ggml + Metal-шейдеры; наружу только
   C ABI (`include/audiocpp.h`, 536 строк). Swift/Rust-обёрток нет.
2. **Нет WebSocket** (`grep` = 0). Стриминг — SSE/chunked (`app/server/api.md#L178-L198`, `#L294-L330`).
3. **Apple Silicon — догоняющая платформа.** `CMakeLists.txt#L93` `ENGINE_DEFAULT_ENABLE_METAL OFF`,
   `#L97-L99` включение только `if (APPLE)`; README — «**Optimized for CUDA**»; Metal-оптимизации
   точечные (VoxCPM2 TTS, `docs/reports/metal_convtranspose_performance.md`).
4. **Нет refcount-lease.** Удержание = `BusyGuard::Lock` на один запрос; молчащая сессия не держит модель.
5. **Один процесс на все модели** (`ServerState::models_` в `app/server/runtime.h`) — падение любой
   модели роняет сервер. Требуемой supervisor-изоляции нет.
6. **Нет sha256-верификации и нет CAS** — только дерево каталогов на пакет (`manager.cpp#L573-L581`,
   `#L884-L896`).
7. **Нет speaker-embedding/enrollment API** — `grep "speaker_embed"` даёт только TTS-кондиционеры
   (`chatterbox_turbo`, `glm_tts`, `kitten_tts2`) и speaker-tagged ASR.

**Плюсы, которые нельзя игнорировать:** ru-покрытие ASR шире, чем у speech-swift+FluidAudio+sherpa
вместе (`gigaam_asr`, `parakeet_tdt`, `qwen3_asr`, `nemotron_asr`, `vibevoice_asr_streaming`,
`sense_asr`, `owsm` — `README.md#L133-L161`, таблица ASR), и есть готовые VAD/diarization-семейства. `[INFERENCE]`
Разумная роль audio.cpp в проекте — **опциональный ONNX/ggml-воркер для ru-моделей**, а не control plane.

---

## 3. Скачивание из HF (ревизии, sha256) и CAS-хранилище

### Кандидаты

| Кандидат | Ревизии | sha256-проверка | Resume | CAS-раскладка | Лицензия | Вердикт |
|---|---|---|---|---|---|---|
| `hf-hub` 1.1.0 (Rust) | ✅ `revision`, `refs/<rev>` | ❌ | ✅ (внутри download) | ✅ `blobs/<etag>` | Apache-2.0 | **ЗАВИСИМОСТЬ** + дописать verify |
| `swift-transformers` `Hub` | ✅ | ❌ (проверять нечего) | — | ✅ как HF-кэш | Apache-2.0 | ЗАВИСИМОСТЬ (Swift) |
| `swift-huggingface` | ✅ | ❌ | — | — | Apache-2.0 | альтернатива, 178★ |
| speech-swift `HuggingFaceDownloader/Transfer` | ✅ (по `api/models/<id>/tree/<rev>`) | ✅ **да** | ✅ ranged | ❌ (плоский каталог) | Apache-2.0 | **КОПИРОВАТЬ** (Swift) |
| audio.cpp `package_manager/manager.cpp` | ✅ (`resolve/<rev>/<path>`) | ❌ (etag только для «есть обновление») | ❌ | ❌ | Apache-2.0 | **ИДЕЯ** (ModelScope listing) |
| ollama `manifest/` + `blobs/` | — | ✅ digest | — | ✅ `blobs/sha256-<hex>` + OCI-manifest | MIT | **КОПИРОВАТЬ схему** |

### 3.1 hf-hub — что даёт и чего не даёт

`hf-hub` 1.1.0 (crates.io, активен 2026-10-22, 339★). Публичный вход: `repository::{download_file,
download_file_stream, download_file_to_bytes, snapshot_download}` (sync-варианты там же,
`hf-hub/src/repository/download.rs#L1640-L1896`).

Кэш (`hf-hub/src/cache/storage.rs#L134-L142`):
```
<cache>/<repo_folder>/blobs/<etag>
<cache>/<repo_folder>/snapshots/<commit_hash>/<filename>
<cache>/<repo_folder>/refs/<revision>
```
То есть **blobs адресуются по etag (= sha256 для LFS), а snapshots — по commit**. Это уже почти CAS:
одинаковые байты между ревизиями переиспользуются, `refs/` даёт pin. Не хватает только (а) явного
digest-адреса для *наших* артефактов (не из HF) и (б) проверки.

**Проверки нет:** `sha2` в крейте используется только в `repository/upload.rs` (`#L24`, `#L621-L627`).
В `repository/download.rs` совпадений `sha256|verify|integrity` — ноль. `[INFERENCE]` Значит: скачивание
принимает байты по `content-length`, и повреждённый (но правильной длины) файл пройдёт. Для весов это
проявится как «нечитаемый тензор» глубоко в загрузке — ровно та проблема, которую speech-swift решает
явно.

### 3.2 speech-swift — эталон проверки (Swift)

`Sources/AudioCommon/HuggingFaceRepoManifest.swift` тянет `api/models/<id>/tree/<rev>` и извлекает
`lfs.oid` → `sha256` (`#L155-L163`). `HuggingFaceTransfer.swift#L103-L120`:
```swift
guard written == file.size else { remove; throw failedToDownload }
guard let expected = file.sha256 else { return }
guard actual == expected else { remove; throw checksumMismatch(...) }
```
Плюс осознанное решение не перехешировать уже скачанное (`#L39-L43`: «Re-hashing cached weights on every
launch would cost seconds per gigabyte for no benefit — verification happens when bytes are written»).
`[INFERENCE]` Это правильный компромисс: проверять при записи, а не при каждом старте; для
параноидального режима — отдельная команда `verify` в CLI.

### 3.3 CAS — писать самим

Зрелых Rust-крейтов OCI-реестра нет: `oci-distribution`/`oci-client` на crates.io не отвечают (404),
на GitHub — `containers/oci-distribution` → API `None`. Альтернативы (`Trow-Registry/trow` 1032★ —
Docker-реестр на Go; `no-std-rs/oci-zero` 1★) не подходят как библиотека.

**Схема — из ollama** (`manifest/manifest.go#L43-L58`, `manifest/paths.go#L400-L417`):
```
<root>/blobs/sha256-<hex>            # digest "sha256:<hex>" → имя файла с дефисом
<root>/manifests/<registry>/<ns>/<name>/<tag>   # OCI-подобный манифест
```
Манифест OCI-совместим: `schemaVersion`, `mediaType`, `config`, `layers[]`, `manifests[]` (для
manifest-list по движкам — ровно то, что нужно для «одна модель, разные движки»).
`[INFERENCE]` Нам нужно добавить к этому то, чего у ollama нет: `license` per layer, `revision`
(upstream commit) и `quantization` в аннотациях слоя.

**Вердикт:** `hf-hub` — ЗАВИСИМОСТЬ для сети/ревизий/gated/Xet; CAS-слой — **САМИ** (~300 строк:
blob-write с проверкой sha256 на потоке, manifest read/write, GC по refcount манифестов), схема —
КОПИРОВАТЬ у ollama.

---

## 4. Speaker registry / enrollment

### Кандидаты

| Кандидат | Язык | API | Персистентность | Лицензия | Вердикт |
|---|---|---|---|---|---|
| FluidAudio `SpeakerManager` | Swift | `assignSpeaker`, `findSpeaker`, `mergeSpeaker`, `makeSpeakerPermanent`, `upsertSpeaker` | ✅ `Speaker: Codable` | Apache-2.0 | **ЗАВИСИМОСТЬ** (Swift-воркер) |
| sherpa-onnx `SpeakerEmbeddingManager` | C++ | `Add`, `Search`, `Verify`, `Score`, `GetBestMatches(p, thr, n)` | ❌ (in-memory) | Apache-2.0 | **КОПИРОВАТЬ** (порт на Rust) |
| LocalAI `KnownVoice` | proto | `id`, `name`, `embedding`, `model`, `encoder_family`, `encoder_weights` | — | MIT | **КОПИРОВАТЬ схему** |
| pyannote.audio | Python | pipeline + `PretrainedSpeakerEmbedding` | — | MIT | **ИДЕЯ** (только алгоритмы) |
| audio.cpp | — | — | — | — | отсутствует |

### Детали

**FluidAudio** (`SpeakerManager.swift`, 638 строк): `embeddingSize = 256` (`#L13`);
`assignSpeaker(...)` (`#L136`) с четырьмя порогами — `speakerThreshold`, `embeddingThreshold`,
`minSpeechDuration`, `minEmbeddingUpdateDuration` (`#L26-L44`); `findSpeaker(with:speakerThreshold:)`
(`#L185`) возвращает `(id, distance)`; `mergeSpeaker` (`#L251`) + `findMergeablePairs` (`#L283`);
`makeSpeakerPermanent`/`revokePermanence` (`#L224`/`#L235`) — «не удалять при очистке»;
`upsertSpeaker` (`#L528`, `#L553`) — загрузка/сохранение, `Speaker` объявлен `Codable`
(`SpeakerTypes.swift#L7`). Это готовая модель реестра, но **in-process и в памяти** — для сервиса нужен
персистентный слой сверху.

**sherpa-onnx** (`speaker-embedding-manager.h`, 305 строк .cc): `Add(name, p)` и
`Add(name, embedding_list)` (усреднение списка — готовый примитив для enrollment по нескольким
записям, `#L44-L51`); `Search(p, threshold)` для идентификации; `Verify(name, p, threshold)` для
верификации; `GetBestMatches(p, threshold, n)` для top-N; `GetEmbedding(name)`. Всё на косинусе.
**КОПИРОВАТЬ** — 305 строк тривиально портируются на Rust (`ndarray` + `HashMap`).

**LocalAI `KnownVoice`** (`backend/backend.proto#L875-L885`) — критичная деталь, которой нет ни у
FluidAudio, ни у sherpa:
```proto
string encoder_family = 5;   // "voicedetect:<arch>:<name>:<dim>"
string encoder_weights = 6;  // "sha256:<hex>"
```
`[INFERENCE]` Без этого реестр ломается при смене энкодера: 256-d WeSpeaker и 192-d CAM++ несравнимы,
а после обновления весов того же энкодера старые эмбеддинги могут стать мусором. Наш формат записи
обязан содержать `(encoder_family, encoder_weights, dim)` и при несовпадении — не сравнивать, а
помечать запись `stale`.

**pyannote** (MIT, 10706★, активен 2026-10-25) — только алгоритмы: VBx-кластеризация, PLDA-скоринг,
пороги. Кода брать нечего (Python + torch).

### Вердикт
Схема записи — **КОПИРОВАТЬ** у LocalAI (`encoder_family`/`encoder_weights` обязательны).
Алгоритм матчинга — **КОПИРОВАТЬ** `SpeakerEmbeddingManager` (Apache-2.0) или **ЗАВИСИМОСТЬ** на
FluidAudio, если воркер на Swift. Персистентность и HTTP-роуты (`/v1/speakers`, `/v1/speakers/match`) —
**САМИ**.

---

## 5. Супервизор воркер-процессов + launchd / SMAppService

### Кандидаты

| Кандидат | Что даёт | Лицензия | Вердикт |
|---|---|---|---|
| Handy `engine_supervisor` (`mod/protocol/supervisor/worker`) | worker = тот же бинарь с флагом; очередь; cancel убивает воркер; hang→retry на CPU; GPU помечается недоступным | MIT | **КОПИРОВАТЬ** |
| `service-manager` 0.11 (Rust) | `install/uninstall/start/stop` для launchd/systemd/sc.exe | MIT OR Apache-2.0 | **ЗАВИСИМОСТЬ** |
| `objc2-service-management` 0.3.2 | `SMAppService::mainAppService()` | Zlib OR Apache-2.0 OR MIT | **ЗАВИСИМОСТЬ** (app-bundle login item) |
| `launchd` crate 0.3.0 | парсинг/создание plist | — | НЕ БРАТЬ (последний релиз 2023-08-08) |
| `plist` 1.10.3 | сериализация plist | MIT | **ЗАВИСИМОСТЬ** (если пишем plist сами) |
| LocalAI `go-processmanager` + `ShutdownModelForce` | менеджер процессов + принудительный kill | MIT | **ИДЕЯ** |

### Детали

**Handy** (`src-tauri/src/engine_supervisor/mod.rs#L1-L22`) — законченная модель:
> All transcribe.cpp native code … runs in a child process: the Handy executable re-launched with
> `WORKER_FLAG`. A GPU driver fault, `GGML_ASSERT` abort, or hang then kills only the worker.
> `EngineSupervisor` is the single owner of that worker. It loads and unloads models, runs and streams
> transcriptions, cancels them, and recovers from crashes and hangs (retrying in a CPU-only worker when
> the GPU was at fault) … At most one worker holds a model at a time, and it lives exactly as long as
> that model, so unloading returns all of its CPU and GPU memory to the OS.

Ключевые решения, которые стоит перенести:
- **Воркер = тот же бинарь + флаг** (`WORKER_FLAG = "--transcribe-worker"`, `is_worker_invocation()`
  проверяется в `main` до CLI/Tauri). Не нужен второй исполняемый файл и его подпись/нотаризация.
- **CPU-only воркер как fallback** (`CPU_ONLY_FLAG`) — «a driver that crashes or hangs while
  initializing can't take the CPU fallback down with it».
- **Cancel убивает воркер** (`supervisor.rs#L472-L483`): «Bypasses the queue. The worker doing the work
  is killed, so the call's owner sees it die» — единственный надёжный способ прервать ggml-инференс.
- **Hang-дедлайн и разный бюджет для GPU/CPU** (`#L43-L58`): на GPU зависание восстанавливается
  (retry на CPU), на CPU — нет, поэтому ловить надо раньше; плюс отдельный запас на «first-use shader
  compiles are not mistaken for hangs».
- **`Unloading`**: «It reads as unloaded at once and `Unloading::wait` returns once the worker has
  exited» (`#L374-L377`) — модель помечается выгруженной немедленно, но память возвращается позже;
  это то, что нужно для быстрого `:unload` без блокировки API.

**launchd.** Для демона вне app-bundle: `service-manager` (Launchd-адаптер, `install/uninstall/start/stop`).
Для login item внутри app-bundle: `SMAppService` — Handy показывает рабочий рецепт
(`autostart.rs#L49-L70`): `AnyClass::get(c"SMAppService").is_some()` как проверка версии ОС (API с macOS 13),
`service.status()` перед `registerAndReturnError()` (иначе повторный unregister возвращает ошибку на
каждом старте), и явное удаление старого LaunchAgent-plist (`remove_plugin_launch_agent`) чтобы не
было двойного запуска после миграции.

### Вердикт
Супервизор — **КОПИРОВАТЬ Handy** (MIT, ~2800 строк, проверено в проде). launchd — **ЗАВИСИМОСТЬ**
`service-manager`; SMAppService — **ЗАВИСИМОСТЬ** `objc2-service-management`. Крейт `launchd` не брать
(заброшен).

---

## 6. Декодирование / ресемплинг аудио без ffmpeg

### Кандидаты

| Кандидат | Язык | Форматы | Ресемплинг | Лицензия | Вердикт |
|---|---|---|---|---|---|
| `symphonia` 0.6.1 | Rust | AAC/ALAC/MP3/FLAC/Vorbis/WAV/MP4/AIFF | — | MPL-2.0 | **ЗАВИСИМОСТЬ** |
| `rubato` 5.0.1 | Rust | — | FFT/синхронный/асинхронный | MIT OR Apache-2.0 | **ЗАВИСИМОСТЬ** |
| Handy `audio_toolkit/audio/decode.rs` | Rust | через symphonia | через rubato | MIT | **КОПИРОВАТЬ** (готовый рецепт) |
| FluidAudio `AudioConverter.swift` (560 стр) | Swift | — | `AVAudioConverter` | Apache-2.0 | **ЗАВИСИМОСТЬ** |
| FluidAudio `AudioSourceFactory.swift` (232 стр) | Swift | через `AVAudioFile` | `AVAudioConverter` | Apache-2.0 | **КОПИРОВАТЬ** (mmap-путь для WAV) |
| WhisperKit `AudioProcessor.swift` | Swift | — | `AVAudioConverter` | MIT | альтернатива |

### Rust

Handy `decode.rs` — рабочая реализация: `symphonia` probe → выбрать первый трек с
`codec != CODEC_TYPE_NULL` → декодировать пакетами, **пропуская повреждённые пакеты**
(`SymphoniaError::DecodeError → warn + continue`, `#L65-L71`) → downmix в моно делением на число каналов
→ `rubato::Fft` с `RESAMPLER_CHUNK_FRAMES = 1024`. Важные детали, которые обычно забывают и которые
здесь есть: (а) отбрасывание задержки ресемплера (`delay_left`), (б) ошибка, если частота меняется
посередине файла, (в) потоковая обработка — файл не держится целиком в исходной частоте.

Версии: Handy пинует `symphonia = "0.5.5"`, на crates.io уже **0.6.1** (2026-08-13) → при переносе
нужна адаптация API. `rubato = "5"` совпадает с текущим 5.0.1.

Лицензии: `symphonia` — **MPL-2.0** (file-level copyleft: изменения *его* файлов надо публиковать,
наш код — нет; для линковки безопасно). `rubato` — `license = "MIT OR Apache-2.0"` (из `Cargo.toml`,
GitHub API отдаёт NOASSERTION — проверено по файлу).

### Swift

FluidAudio `AudioConverter` — «Uses `AVAudioConverter` for all sample-rate, sample-format, and
channel-count conversions. Avoids any manual resampling» (`#L1-L10`), target по умолчанию
16 kHz/mono/float32 (`#L29-L36`). `AudioSourceFactory.makeDiskBackedSource` (`#L12-L30`) добавляет
важную оптимизацию: 16-битный моно-WAV в целевой частоте читается **через `mmap`, без конвертации**
(`PcmWavLayout.parse` + `DiskBackedAudioSampleSource(pcm16MappedWav:…)`) — «a day of audio opens in the
time it takes to map the file». Для остальных форматов — `AVAudioFile` + `AVAudioConverter` во временный файл.

### Вердикт
Обе платформы закрыты: Rust — **ЗАВИСИМОСТИ** `symphonia`+`rubato` с **КОПИРОВАНИЕМ** рецепта Handy;
Swift — **ЗАВИСИМОСТЬ** на FluidAudio (или КОПИРОВАНИЕ двух файлов, Apache-2.0). ffmpeg не нужен.

---

## 7. Бинарный фрейминг IPC

### Кандидаты

| Кандидат | Формат | Лицензия | Вердикт |
|---|---|---|---|
| Handy `engine_supervisor/protocol.rs` | `[u32 json_len][json][u32 pcm_len][pcm]`, LE, JSON | MIT | **КОПИРОВАТЬ** |
| Wyoming `event.py` | newline-JSON + `data_length` + `payload_length` | MIT | **КОПИРОВАТЬ** (альтернатива) |
| LocalAI `backend.proto` | gRPC/protobuf | MIT | **КОПИРОВАТЬ** определения сообщений |

### Детали

**Handy** (`protocol.rs#L1-L18`) — ровно тот формат, что в черновике архитектуры:
```
Each message is one frame: `[u32 json_len][json][u32 pcm_len][pcm bytes]`
(lengths little-endian, PCM as little-endian f32). JSON (not bincode) is
deliberate: transcribe-cpp's serde impls round-trip NaN confidences through
`null`, which only works with a self-describing format.
```
Плюс защита от мусорной длины: `MAX_SECTION_BYTES = 1 << 30` («a garbage length must not turn into a
multi-gigabyte allocation»), и обоснование размера: «One hour of 16 kHz f32 PCM is ~230 MB».
Сообщения: `Hello{list_devices}`, `Load{path,backend,device}`, `Run{options}`, `StreamBegin{run,stream}`,
`Feed`, `Finalize{want_language}`, `StreamReset`; ответы `Hello/Loaded/Transcript/Fed/Finalized/Ok/Error`.

Замечание: PCM как **f32**, а не i16 — вдвое больше трафика, но без конвертации на границе. Для
loopback UDS это оправдано; `[INFERENCE]` для сетевого транспорта стоит перейти на i16 или f16.

**Wyoming** (`event.py#L89-L101`) — альтернатива: JSON-строка до `\n`, затем бинарные секции
фиксированной длины. Выигрыш — читаемый заголовок и отсутствие требования «сначала узнай длину JSON».
Проигрыш — нужен `readline`, что на порядок медленнее фиксированного префикса. `[INFERENCE]` Для
loopback-IPC Handy-формат лучше (два `read_exact` по 4 байта + два буфера).

**LocalAI `backend.proto`** — конкретные RPC и сообщения для аудио (1691 строка, из них аудио —
`#L598-L673`, `#L1255-L1272`):

| RPC | Направление | Назначение |
|---|---|---|
| `AudioTranscription(TranscriptRequest) → TranscriptResult` | unary | batch ASR |
| `AudioTranscriptionStream(TranscriptRequest) → stream TranscriptStreamResponse` | server-stream | batch с дельтами (`delta`, `final_result`) |
| `AudioTranscriptionLive(stream TranscriptLiveRequest) → stream TranscriptLiveResponse` | **bidi** | live ASR |
| `TTS`, `TTSStream`, `TTSLive` | — | (вне нашей задачи) |
| `VoiceEmbed(VoiceEmbedRequest) → VoiceEmbedResponse` | unary | эмбеддинг говорящего |
| `VoiceVerify`, `VoiceAnalyze` | unary | верификация/анализ |
| `LoadModel(ModelOptions) → Result`, `Free(HealthMessage) → Result`, `Status → StatusResponse` | unary | жизненный цикл |

`TranscriptLiveRequest` (`#L632-L648`) — `oneof { TranscriptLiveConfig config; TranscriptLiveAudio audio }`,
где `TranscriptLiveAudio { repeated float pcm }` и `TranscriptLiveConfig { language, sample_rate, params,
known_voices }`. Это правильная форма: конфиг и аудио — разные ветки одного `oneof`, поэтому порядок
не фиксирован и клиент может менять конфиг на лету.

`StatusResponse` (`#L993-L1003`): `enum State { UNINITIALIZED, BUSY, READY, ERROR }` + `MemoryUsageData` +
`speaker_encoder` («trusted metadata from the loaded server encoder, never request data») — готовая
форма для `/v1/ps`.

### Вердикт
Фрейминг для UDS — **КОПИРОВАТЬ Handy**. Если понадобится gRPC (кроссплатформенные клиенты) —
**КОПИРОВАТЬ** сообщения и RPC-набор LocalAI (protobuf-схемы не охраняются авторским правом как API,
но атрибуция в NOTICE всё равно уместна).

---

## 8. Парсинг / применение bias и hotwords на разных движках

### Кандидаты и что реально поддерживается

| Движок | Механизм | Ограничение | Лицензия | Вердикт |
|---|---|---|---|---|
| sherpa-onnx transducer | `ContextGraph` (Aho–Corasick + бонус по токенам) | **только** transducer + **только** `modified_beam_search` | Apache-2.0 | **ЗАВИСИМОСТЬ** (через C API) |
| sherpa-onnx CTC/Whisper/Paraformer | — | не поддерживается | — | — |
| audio.cpp `kroko_asr` | `HotwordGraph` в декодере | только Kroko-модели | Apache-2.0 | **ИДЕЯ** |
| FluidAudio (Parakeet/Nemotron/Sortformer) | — | **нет** bias вообще (проверено grep) | — | — |
| WhisperKit | `promptTokens` (conditioning prompt) | лимит контекста промпта | MIT | **ЗАВИСИМОСТЬ** |
| Apple `SpeechAnalyzer` | `contextualStrings` | только ru-DictationTranscriber | — | ЗАВИСИМОСТЬ (системный) |
| Qwen3-ASR / FunASR (через sherpa) | prompt (system-сегмент chat-template) | лимит `max_total_len` | — | через движок |

### Детали

**sherpa-onnx** — проверено по коду:
- `ContextGraph` (`sherpa-onnx/csrc/context-graph.h#L22-L67`): узлы с `token_score`, `node_score`,
  `output_score`, `ac_threshold`, `fail`/`output` (AC-автомат). Конструктор принимает
  `(token_ids, context_score, ac_threshold, scores, phrases, ac_thresholds)` — то есть **пер-фразный
  score и пер-фразный порог** уже есть.
- Гейт декодера (`offline-recognizer.cc#L85-L90`, `online-recognizer.cc#L150-L156`):
  ```cpp
  if (!hotwords_file.empty() && decoding_method != "modified_beam_search") {
      SHERPA_ONNX_LOGE("Please use --decoding-method=modified_beam_search if you"
                       " provide --hotwords-file. Given --decoding-method='%s'", ...);
      return false;   // отказ старта
  }
  ```
  Подтверждает A6/A12 из wave1.
- Per-stream hotwords: `c-api.h#L395-L398` (`hotwords_buf`, `hotwords_buf_size` — in-memory вместо файла),
  `#L522-L537` (`SherpaOnnxCreateOnlineStreamWithHotwords(recognizer, hotwords)`).
- Слияние дефолтных и пер-стримовых (`online-recognizer-transducer-impl.h#L210-L242`): считается
  `num_default_hws`, списки конкатенируются, скоры тоже — то есть словарь приложения и словарь сессии
  **складываются**, а не конфликтуют. Готовый примитив для нашего `bias` per-request.
- `hotwords_score` — бонус **на каждый токен** с отменой при провале пути (подтверждено wave1 A4;
  `ContextGraph::ForwardOneStep` возвращает `(score, state, output_state)`).

**audio.cpp `kroko_asr`** — единственное семейство с bias: `decoder.cpp#L36` `class HotwordGraph`,
`session.cpp#L322-L330` — опции `hotwords` (через `,`) и `hotwords_score` (дефолт 1.5),
`tokenizer.cpp#L104-L168` `encode_hotword` с проверкой «cannot be represented by this package vocabulary».
`[INFERENCE]` Ценность — как образец диагностики: явная ошибка «фраза не кодируется словарём» вместо
тихого игнора.

**FluidAudio — bias отсутствует.** `grep -rn "boost"` даёт только Sortformer-скореры
(`SortformerStateUpdater.swift#L255-L259`), `vocabulary` — только CLI-бенчмарки. То есть заявление о
«CTC-WS/trie bias» в черновике архитектуры для FluidAudio **не подтверждается кодом** на этом commit.

**WhisperKit** — `Configurations.swift#L153-L180`: `promptTokens: [Int]?` — «Array of token IDs to use as
the conditioning prompt for the decoder. These are prepended to the prefill tokens». Это prompt-биасинг,
не акустический; токенизацию терминов надо делать самим.

### Архитектурный вывод

`[INFERENCE]` Единый `bias` в API должен компилироваться в **три разных механизма** с отчётом
`applied/rejected`:
1. `ContextGraph` (sherpa transducer) — принимает фразы + вес; **требует** `modified_beam_search`
   (иначе 400, а не тихий игнор); многословные фразы требуют правильного modeling unit (charwise GigaAM
   склеивает символы — см. wave1 A3).
2. Prompt (Whisper/Qwen3/FunASR/Apple `contextualStrings`) — принимает **токенизированный** список;
   нужен бюджет токенов и явный отказ при переполнении.
3. Ничего (FluidAudio, sherpa CTC) — вернуть `rejected: ["engine has no bias support"]`.

### Вердикт
Механизм — **САМИ** (единый API + компилятор в движко-специфичные формы + отчёт). Реализация
`ContextGraph` — **ЗАВИСИМОСТЬ** на sherpa-onnx (не переписывать: AC-автомат с fail/output и
пер-фразными порогами — 169 строк плотной логики). Формат файла hotwords (`фраза:score#threshold@orig`) —
**КОПИРОВАТЬ** у sherpa.

---

## 9. Клиентские SDK (генерация из OpenAPI / AsyncAPI)

### Кандидаты

| Кандидат | Язык | Что генерирует | Лицензия | Активность | Вердикт |
|---|---|---|---|---|---|
| `apple/swift-openapi-generator` | Swift | клиент+сервер+типы, SPM-плагин | Apache-2.0 | 1993★, 2026-10-23 | **ЗАВИСИМОСТЬ** |
| `oxidecomputer/progenitor` | Rust | **только клиент** | MPL-2.0 | активен | **ЗАВИСИМОСТЬ** |
| `utoipa` 6.0.0 | Rust | OpenAPI из кода (code-first, сервер) | MIT/Apache | 57M загрузок | **ЗАВИСИМОСТЬ** (для нашего сервера) |
| `OpenAPITools/openapi-generator` | Java (CLI) | клиенты/серверы для 50+ языков, включая rust и swift | Apache-2.0 | 26827★, 2026-10-26 | **ИДЕЯ**/fallback |
| `asyncapi/modelina` | TypeScript | модели из AsyncAPI | Apache-2.0 | 446★ | не подходит (JS) |
| `asyncapi-rust-ws-template` | Rust | шаблон WS | MIT | 2★ | не брать |

### Детали

**swift-openapi-generator** (`Package.swift#L1` `swift-tools-version:6.2`): продукт
`swift-openapi-generator` + плагин `OpenAPIGenerator` (`capability: .buildTool()`), то есть генерация
встроена в сборку SPM — не нужен внешний шаг. Требует `swift-openapi-runtime` и транспорт
(`swift-openapi-urlsession` для клиента). `[INFERENCE]` Ограничение: генератор требует, чтобы спека
была валидным OpenAPI 3.1; наши WS-эндпоинты в неё не попадут.

**progenitor** (`README.md#L3-L7`): «a Rust crate for generating opinionated clients from API
descriptions… It generates a type called `Client` with methods that correspond to the…». Только клиенты —
для сервера не годится. MPL-2.0 — file-level copyleft; генератор как build-time инструмент не заражает
сгенерированный код (он не производная от файлов progenitor), но `progenitor-client` линкуется — это
MPL-файлы, изменения в них надо публиковать. `[INFERENCE]` Практически безопасно, но требует внимания
при форке.

**AsyncAPI.** Зрелого генератора для Swift/Rust нет: `asyncapi/modelina` — TypeScript-библиотека
(446★), `kanekoshoyu/asyncapi-rust-ws-template` — 2★. `[INFERENCE]` Практический путь: описать WS-события
как JSON Schema и сгенерировать типы через `quicktype`-подобный шаг, либо просто написать `Codable`/
`serde` структуры руками (событий ~12, это дешевле интеграции генератора).

**`OpenAPITools/openapi-generator`** (Java, Apache-2.0, 26827★) — универсальный fallback: умеет и
`rust`, и `swift5`. `[INFERENCE]` Для нас он хуже специализированных: генерирует «словарные» клиенты без
интеграции с SPM-сборкой (в отличие от `swift-openapi-generator`) и без async-tokio-эргономики
progenitor; плюс тянет JVM в CI. Держать как резерв, если понадобится генератор для третьего языка.

### Вердикт
REST — **ЗАВИСИМОСТИ** (`swift-openapi-generator` для Swift, `progenitor` для Rust-клиента,
`utoipa` для Rust-сервера). WS — **САМИ** (ручные типы из JSON Schema). AsyncAPI-генератор не брать.

---

## Сводная таблица: пробел → вердикт

| # | Пробел | Главный кандидат | Вердикт |
|---|---|---|---|
| 1 | WS-протокол ASR-сессии | WLK `deepgram_compat.py` + LocalAI `TranscriptLiveResponse` + Wyoming-фрейминг | **САМИ** + КОПИРОВАТЬ идеи/словарь |
| 2 | Планировщик refcount+TTL+бюджет | LocalAI `watchdog.go` (модель) + audio.cpp `model_slots.h`/`busy_guard.h` (код) | **САМИ** на Rust + **КОПИРОВАТЬ** audio.cpp-код |
| 2b | audio.cpp как основа | — | **НЕТ** (см. §2.4) |
| 3 | Скачивание HF | `hf-hub` 1.1.0 | **ЗАВИСИМОСТЬ** + дописать sha256-verify |
| 3b | CAS-хранилище | схема ollama `blobs/sha256-<hex>` + OCI-manifest | **САМИ** по схеме ollama |
| 4 | Speaker registry | FluidAudio `SpeakerManager` / sherpa `SpeakerEmbeddingManager` + LocalAI `KnownVoice` | **ЗАВИСИМОСТЬ**/КОПИРОВАТЬ + своя персистентность |
| 5 | Супервизор воркеров | Handy `engine_supervisor` | **КОПИРОВАТЬ** |
| 5b | launchd / SMAppService | `service-manager` 0.11 / `objc2-service-management` | **ЗАВИСИМОСТЬ** |
| 6 | Декод/ресемплинг | Rust: `symphonia`+`rubato` (Handy `decode.rs`); Swift: `AVAudioConverter` | **ЗАВИСИМОСТЬ** + КОПИРОВАТЬ рецепт |
| 7 | Бинарный фрейминг IPC | Handy `protocol.rs`; LocalAI `backend.proto` (RPC-набор) | **КОПИРОВАТЬ** |
| 8 | bias / hotwords | sherpa `ContextGraph`; формат файла sherpa | **ЗАВИСИМОСТЬ** + **САМИ** (компилятор bias) |
| 9 | Клиентские SDK | `swift-openapi-generator`, `progenitor`, `utoipa` | **ЗАВИСИМОСТЬ**; WS — САМИ |

---

## Неизвестные / что измерить

1. **Скорость и качество GigaAM v3 в audio.cpp на Apple Silicon** (Metal vs CPU vs sherpa-onnx).
   Не измерялось: сборка audio.cpp на macOS в этой сессии не выполнялась. В отчётах проекта
   (`docs/reports/`) Metal-метрики есть только для TTS. Нужен замер WER + RTF на ru-IT-корпусе.
2. **Metal-бэкенд audio.cpp для encoder-decoder ASR.** README заявляет Metal-оптимизации по VoxCPM2
   (TTS); для GigaAM/Whisper-энкодеров данных нет.
3. **Реальный RSS одного MLX-инстанса vs CoreML-инстанса** на M-чипе для моделей 0.6B–4B — определяет
   коэффициент бюджета памяти (сейчас в audio.cpp эвристика ×1.5 + 128 MiB, не проверена на Apple Silicon).
4. **Стоимость `mmap`-пути** FluidAudio для WAV длиной >1 ч (заявлено «opens in the time it takes to
   map the file» — не измерялось).
5. **Поведение `hf-hub` при повреждённом файле** правильной длины: нужен эксперимент (скачать, испортить
   байт, вызвать `download_file` повторно) — ожидание `[INFERENCE]`: не перекачает, т.к. проверки нет.
6. **Формат `contextualStrings` для Apple `SpeechAnalyzer`** на русском: требует проверки на устройстве
   (в D3 отчёте не подтверждено для ru).
7. **Лицензия gotranscribesrv**: `LICENSE.md` в корне есть, `README.md#L397-L399` говорит GPL-3.0 —
   нужно юридическое подтверждение перед любым переиспользованием (сейчас: не использовать).
8. **MPL-2.0 `symphonia` и `progenitor`** — проверить у юриста, устраивает ли file-level copyleft при
   статической линковке в закрытый продукт (стандартно — да, но политика проекта неизвестна).

---

## Источники

Клоны (только чтение), sha зафиксированы:

| Репозиторий | Commit | Лицензия |
|---|---|---|
| `0xShug0/audio.cpp` | `1cada9b7045cc6935bcafb3f5a26806d133535e7` | Apache-2.0 (ShugoAI LLC) |
| `ollama/ollama` | `d44c2581904a98430e348d89cc2af1529b1808a0` | MIT |
| `mudler/LocalAI` | `f7011f18702f107a5c24e53ab0914b37523215b0` | MIT |
| `rhasspy/wyoming` | `08a0c258df83fea6e1643bcfe67e86243f01cf31` | MIT |
| `cjpais/Handy` | `73c095857bc4eadff097f64767c40ad129cc372d` | MIT |
| `huggingface/hf-hub` | `473de9430b0fbc544fb1d91102180eb713f8dc87` | Apache-2.0 (v1.1.0) |
| `apple/swift-openapi-generator` | `a1161dd90c548fb82c674465e29164b70478b328` | Apache-2.0 |
| `oxidecomputer/progenitor` | `af512986d0ca96984e550691ab9813c501e93f9c` | MPL-2.0 |
| `QuentinFuxa/WhisperLiveKit` | `eb92c115ee7abc1e161b0b2159da31785b0f5566` | Apache-2.0 |
| `FluidInference/FluidAudio` | `1b0ed50d686873128a4129b3510ce3914c849bab` | Apache-2.0 |
| `k2-fsa/sherpa-onnx` | `532f1b5a8a207bf53b116b747207c1506696c843` | Apache-2.0 |
| `soniqo/speech-swift` | `cbad3be29480825707890490f446b19d7288be2d` | Apache-2.0 |
| `argmaxinc/WhisperKit` | `9f99b5cb6a907730543823ad271b82532782a899` | MIT |
| `sagostin/gotranscribesrv` | `a5f4289078553ba66f83510c62146043bed91a7a` | **GPL-3.0** |

Проверенные команды (примеры, воспроизводимы):

```bash
# audio.cpp: Metal-приоритет и отсутствие WS
grep -n "ENGINE_DEFAULT_ENABLE_METAL" CMakeLists.txt           # 93: OFF, 97-99: if(APPLE) ON
grep -rn -i "websocket" app/server/*.cpp app/server/*.h        # пусто

# audio.cpp: отсутствие sha256-верификации
grep -n "etag" src/framework/package_manager/manager.cpp       # 573-581 сбор, 884-896 только version_state

# hf-hub: sha2 только в upload
grep -rn "sha2\|Sha256::" --include=*.rs hf-hub/src             # только repository/upload.rs

# sherpa: гейт hotwords
sed -n '85,90p' sherpa-onnx/csrc/offline-recognizer.cc

# FluidAudio: отсутствие bias
grep -rn "boost" Sources/FluidAudio --include=*.swift            # только Sortformer

# метаданные активности/лицензий
curl -s https://api.github.com/repos/<owner>/<repo> | jq '.license.spdx_id, .pushed_at'
curl -s https://crates.io/api/v1/crates/<crate> | jq '.crate.max_version, .crate.updated_at'
```

Отчёты первой волны, чьи утверждения перепроверялись: `A2-whisperlivekit-speaches.md`,
`A3-gotranscribesrv-whisperkit.md`, `A4-discovery.md`, `B1-speech-swift-server.md`,
`D1-russian-asr.md`, `E1-vad-turn.md`, `E2-diarization-embeddings.md`, `G-model-management.md`,
`I-ru-it-adaptation.md`.
