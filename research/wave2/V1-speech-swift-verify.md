# V1. Независимая проверка недостатков speech-swift по исходникам

Дата: 2026-10-26. Ревизия: `cbad3be29480825707890490f446b19d7288be2d` (2026-10-25), клон `/tmp/srr/repos/speech-swift` (только чтение).
Метод: чтение исходников и тестов в клоне, `git log`/`git tag`/`git diff`, сверка с документацией репозитория, GitHub API (issues/PR/discussions/releases). Сервер **не собирался и не запускался** (нужен macOS 15 + Xcode, MLX/CoreML и гигабайты весов) — поэтому утверждения о поведении получены из кода/тестов/доков, а не из живых измерений; где вывод сделан мной, а не прочитан в коде, стоит `[INFERENCE]`.

Проверялись отчёты первой волны `wave1/B1-speech-swift-server.md` и `wave1/B2-speech-swift-engines.md`. Оба в целом достоверны: **ни одно из проверенных ключевых обвинений не опровергнуто**. Найдены: одна фактическая ошибка в B2 (19 против 21 метода протокола), одна выдача самоотчёта за «замер», одна неверная интерпретация (Parakeet и русский), и несколько преувеличений в формулировках.

## Итог

1. **Ядро обвинений B1 подтверждается кодом полностью.** `InferenceGate` — один булев слот на процесс + FIFO на 32 ожидающих без таймаута; `ModelCache` не имеет ни одного метода выгрузки; ASR не потоковый даже для streaming-моделей; роутов `diarize/vad/speaker/separate/turn` нет; auth/CORS/Origin-проверки/TLS нет. Это проверяемые факты, а не впечатление от README.
2. **Обвинения B2 подтверждаются, кроме трёх мест.** (а) `RealtimeModelLoading` — **21** метод, а не 19; (б) «замер 97/191/284/378 мс» существует, но это **самоотчёт автора в доке репозитория**, а не независимое измерение, как подано в B2; (в) «Parakeet поддерживает `russian`» — ошибка: найденный `case .russian` в `ParakeetASR+Protocols.swift` относится к маппингу **выходного** текста в имена Qwen3-TTS, а не к входным языкам ASR.
3. **Цифра «97/191/284/378 мс» не выдумана** — она дословно в `docs/benchmarks/runtime-ownership-2026-10-17.md:91-93`, на железе автора (M5 Pro 48 GiB, macOS 26.6.2, Swift 6.4), воспроизведение не выполнялось. Правильная квалификация: «измерение, опубликованное автором проекта», а не «externally verified».
4. **Сильнее документа — тест.** `Tests/AudioServerTests/ASRRuntimeRegressionTests.swift:84-107` (8 параллельных HTTP-запросов → `maximum == 1`) и `:130-165` (HTTP+WS делят одну полосу) — исполняемое доказательство сериализации. Но оба теста используют **заглушку** вместо реального инференса, т.е. доказывают работу гейта, а не поведение MLX/CoreML.
5. **Реестр: 48 записей, 29 с клиентской операцией** — арифметика B1/B2 верна (asr 8 + tts 15 + s2s 4 + enhance 2 = 29; 19 без роутов). Уточнение: `silero-vad` не «мёртвая строка» — он используется внутри WS как turn detector; у него просто нет своего роута.
6. **«Нет абстракции бэкенда» — подтверждено, и это хуже, чем звучит.** Публичных `InferenceBackend`/`ComputeEngine` нет вовсе; 277 файлов `import MLX` и 77 `import CoreML` не делят ни строки вычислений. **ONNX не поддерживается вообще**: 5 упоминаний в `Sources/` и все — в комментариях, ни одного импорта onnxruntime.
7. **Ключевой архитектурный факт, которого нет в wave1: `AudioServer` — не библиотечный продукт.** В `Package.swift` он объявлен только как `.target` за executable-продуктами `speech-server`/`audio-server`; продуктов `.library(name: "AudioServer", …)` нет. То есть «взять сервер как библиотеку» через SPM **невозможно в принципе** — только форк или vendoring таргета.
8. **Проект движется, но не в нашу сторону.** 655 коммитов за 180 дней (1067 всего) — но в `Sources/AudioServer|AudioServerCLI|SpeechRuntime` с 2026-09-01 всего **12** коммитов, и все они про добавление моделей и native inference, не про многоклиентность/очереди/выгрузку. Roadmap (discussion #81, 2026-03-06) описывает три слоя — Primitive / Pipeline / App — где HTTP-сервер числится «приложением»; слоя «рантайм/демон» в архитектуре автора нет. В `docs/`, `README.md`, `AGENTS.md` — **ноль** упоминаний `ollama`, `launchd`, `daemon`, «multi-client».
9. **Вердикт по форку: библиотека моделей, а не форк сервера.** Против форка — 6 из 13 требований Goal требуют переписывания, а не патча (сессии, потоковый ASR, API-контракт, ONNX-адаптеры, жизненный цикл моделей, накладные расходы), причём три из них противоречат явной позиции автора («models are not thread-safe **by design**», «cached variants remain resident for the server's lifetime»). За библиотеку — из 13 требований 5 закрываются бесплатно (ленивая загрузка, переиспользование весов, VAD-модели, диаризация-модели, эмбеддинги), а публичный API за 3 месяца и 4 тега **не потерял ни одного публичного объявления** (проверено `git diff v0.0.23 HEAD`).
10. **Форк сервера был бы дёшев по поддержке, но бессмыслен по ценности**: серверный код — 4077 строк и 12 коммитов за 2 месяца (ребейз тривиален), но именно эти строки и нужно выбросить. Дорогая часть проекта — не сервер, а 61 модуль моделей; и её как раз можно брать как зависимость.

## Проверенные утверждения

Базовый permalink-префикс: `https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/`

| # | Утверждение (из wave1) | Источник | Вердикт | Доказательство |
|---|---|---|---|---|
| 1 | Глобальный `InferenceGate`: 1 слот + FIFO 32, без таймаута | B1 §3, §8 | ПОДТВЕРЖДЕНО | [`Sources/SpeechRuntime/InferenceGate.swift:5-99`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L5-L99) — `private var occupied = false` (булев слот, не счётчик), `waiters: [Waiter]`, `init(maximumPending: Int = 32)`, `acquire()` без deadline. Один экземпляр на процесс: [`AudioServer.swift:538-545`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L538-L545). |
| 2 | Тест доказывает `maxActive == 1` | B1 §3 | ПОДТВЕРЖДЕНО (с оговоркой) | [`Tests/AudioServerTests/ASRRuntimeRegressionTests.swift:84-107`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Tests/AudioServerTests/ASRRuntimeRegressionTests.swift#L84-L107) — 8 параллельных запросов, `XCTAssertEqual(maximum, 1)`; `:130-165` — HTTP+WS одна полоса, `maximum == 1`; `:167-247` — `blockedMaximum == 0`, пока permit удержан извне. Оговорка: инференс подменён заглушкой → тест проверяет гейт, не модели. |
| 3 | `ModelCache` без выгрузки (нет TTL/LRU/refcount/unload) | B1 §4, B2 §3 | ПОДТВЕРЖДЕНО | [`ModelCache.swift:6-33`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelCache.swift#L6-L33) — члены: `loads: [Key: Task<Box,Error>]`, единственный публичный метод `load(key:loader:)`. Выгрузки нет by design: `docs/inference/model-runtime.md:36-37` — «Cached server variants remain resident for the server's lifetime; admission limits are not a total model-memory budget». |
| 4 | Сервер не вызывает `unload` ни в одном пути | B1 §6 | ПОДТВЕРЖДЕНО | `grep -rn "unload\|evict\|LRU\|refcount\|resident" Sources/AudioServer/*.swift` → 0 совпадений (выполнено в клоне). |
| 5 | `ModelRuntime.unload()` есть, но используется только Qwen3-ASR | B1 §5 | ПОДТВЕРЖДЕНО | [`ModelRuntime.swift:6-43`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelRuntime.swift#L6-L43). В `AudioServer.swift` слова `ModelRuntime(` нет вообще; единственный потребитель — `Qwen3ASRRuntime`. |
| 6 | 48 записей реестра vs 29 диспетчеризуемых | B1 §4, B2 §6 | ПОДТВЕРЖДЕНО (с уточнением) | Подсчёт по `ModelRegistry.swift`: 48 `.init(name:`. Разбивка `kind:` — asr 8, tts 15, s2s 4, enhance 2, music 6, vad 4, separate 3, sr 2, speaker 2, turn 1, diarize 1. Уточнение: `silero-vad` используется внутри WS ([`AudioServer.swift:568-575`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L568-L575)), т.е. «недостижим» = «нет клиентской операции», а не «код мёртв». |
| 7 | Нет роутов diarize/vad/speaker/separate/turn/compose | B1 §2, B2 §6 | ПОДТВЕРЖДЕНО | Полный список: `/health`, `/v1/models`, `/v1/realtime/models`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/transcribe`, `/speak`, `/respond`, `/enhance`, `/v1/inference{,/capabilities,/provenance,/cancel}`, WS `/v1/realtime`. `diarize`, `vad`, `speaker`, `separate`, `turn`, `compose` — ноль. При этом код обещает обратное: [`AudioServer.swift:1028-1034`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1028-L1034) — «routed via dedicated HTTP endpoints (/enhance, /compose, /diarize, …)». |
| 8 | ASR не потоковый даже для streaming-моделей | B1 §A5, B2 §7 | ПОДТВЕРЖДЕНО | [`AudioServer.swift:1750-1785`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1750-L1785) — `dispatchTranscribe` для `parakeet`, `parakeet-streaming`, `nemotron`, `omnilingual`, `qwen3-asr` зовёт один батчевый `transcribeAudio(audio16k, …)` на весь буфер. `grep -c "partial" Sources/AudioServer/AudioServer.swift` → **0**; `makeStreamingSession`/`StreamingRecognitionSession` в `Sources/AudioServer/` — **0** совпадений. Протокол `StreamingRecognitionModel` сервером не используется вообще. |
| 9 | Нет auth / CORS / Origin-проверки / TLS | B1 §11 | ПОДТВЕРЖДЕНО | [`AudioServer.swift:52-77`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L52-L77) — `Application(router:server:configuration: .init(address:))` без `middleware`; WS-апгрейд `guard path == "/v1/realtime"` → `.upgrade([:])`: `Origin` не читается, заголовки ответа пустые. |
| 10 | `personaplex-8bit` тихо исполняет 4-bit | B1 §B11 | ПОДТВЕРЖДЕНО (и хуже) | `AudioServer.swift:736-757`: `cache.load(key: "loadPersonaPlex")` + `PersonaPlexModel.fromPretrained(progressHandler:)` — **без `modelId`**. Дефолт — `modelId: String = defaultModelId` ([`PersonaPlex.swift:1577`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/PersonaPlex/PersonaPlex.swift#L1577)), `defaultModelId = "aufklarer/PersonaPlex-7B-MLX-4bit"` ([`:16`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/PersonaPlex/PersonaPlex.swift#L16)), `modelId8bit` ([`:19`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/PersonaPlex/PersonaPlex.swift#L19)) в сервере не используется **нигде**. Обе строки есть в реестре (`ModelRegistry.swift:230-239`), `/respond` резолвит вариант и его `modelId` игнорирует → строка `personaplex-7b-mlx-8bit` мёртвая. |
| 11 | Замер «4 запроса → 97/191/284/378 мс» | B2 §8 | ЧАСТИЧНО | Цитата дословная: [`docs/benchmarks/runtime-ownership-2026-10-17.md:91-93`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/runtime-ownership-2026-10-17.md#L91-L93) — «Four simultaneous HTTP requests completed successfully at approximately 97, 191, 284 and 378 ms, with 378 ms total wall time. This is the intended serial admission behavior.» Это **документ-самоотчёт**, не тест; железо — M5 Pro 48 GiB, macOS 26.6.2, Swift 6.4 (`:1-5`). Формулировка B2 «подтверждено замером» завышает статус доказательства. Воспроизведение не выполнялось. |
| 12 | 14 протоколов в `AudioCommon/Protocols.swift` | B2 §2 | ПОДТВЕРЖДЕНО | `grep -n "^public protocol "` → 14: `ModelMemoryManageable:60`, `SpeechGenerationModel:127`, `SpeechRecognitionModel:222`, `StreamingRecognitionSession:297`, `StreamingRecognitionModel:324`, `ForcedAlignmentModel:331`, `SpeechToSpeechModel:339`, `VoiceActivityDetectionModel:367`, `StreamingVADProvider:377`, `TurnCompletionProvider:394`, `SpeakerEmbeddingModel:422`, `SpeechEnhancementModel:434`, `SpeakerDiarizationModel:442`, `SpeakerExtractionCapable:452` (последний наследует `SpeakerDiarizationModel`). |
| 13 | «Нет абстракции бэкенда» | B2 §3 | ПОДТВЕРЖДЕНО | Публичных `protocol *Backend/*Engine/*Adapter` нет. Совпадения: `Nemotron3InferenceBackend` ([`Nemotron3Diarizer.swift:91`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/Nemotron3Diarizer.swift#L91), без `public`), `Qwen35ChatBackend` (`Qwen35PipelineLLM.swift:5`, про LLM), `BenchEngine`/`VADBenchEngine`/`DiarizationBenchEngine` — бенчмарки. Разделение реализации: `import MLX` в 277 файлах `Sources/`, `import CoreML` в 77 (в т.ч. 3 `@preconcurrency import CoreML` в PocketTTS). |
| 14 | Модели not-thread-safe by design | B1 §8, B2 §8 | ПОДТВЕРЖДЕНО | [`docs/shared-protocols.md:591-608`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/shared-protocols.md#L591-L608) — «All model classes are **not thread-safe** by design… Adding synchronization primitives would introduce overhead for a scenario no caller exercises». Список из 16 классов; thread-safe — только `WeSpeakerModel`. |
| 15 | `--preload` прогревает не дефолты + `preload` в init игнорируется | B1 §5, §E31 | ПОДТВЕРЖДЕНО | [`AudioServer.swift:79-86`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L79-L86) — грузит Qwen3-ASR + TTS + PersonaPlex + Enhancer, тогда как дефолты сессии — Parakeet + Kokoro. `public init(…, preload: Bool = false, …)` (`:39`) параметр не использует. |
| 16 | `fatalError` в резолвере дефолтов | B1 §4 | ПОДТВЕРЖДЕНО | [`ModelRegistry.swift:399-404`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/ModelRegistry.swift#L399-L404) — `defaultVariant(forEngine:kind:) -> ModelVariant` с `fatalError`. |
| 17 | 21 метод в «god protocol» `RealtimeModelLoading` (B1) / 19 (B2) | B1 §D25, B2 §7 | ПОДТВЕРЖДЕНО у B1; **ОПРОВЕРГНУТО** у B2 | Подсчёт `func \w+` в теле протокола → **21**: `transcribeQwen3ASR`, `loadVAD`, `loadQwen3ASR`, `loadParakeet`, `loadParakeetStreaming`, `loadNemotron`, `loadOmnilingual`, `loadQwen3TTS`, `loadCosyVoice`, `loadKokoro`, `loadVoxCPM2`, `loadVoxtralTTS`, `loadPocketTTS`, `loadIndicMio`, `loadMagpie`, `loadMagpieCoreML`, `loadQwen3TTSCoreML`, `loadVibeVoice`, `loadVibeVoice15B`, `loadHibiki`, `loadPersonaPlex`. Цифра B2 «19 сильно типизированных `loadXxx`» занижена. |
| 18 | Второй, независимый механизм сериализации `NativeInferenceGate` | B1 §D23 | ПОДТВЕРЖДЕНО | [`NativeInference.swift:86-99`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L86-L99) — `actor` с `busy: Bool` и `[CheckedContinuation<Void, Never>]`: очередь **неограниченная**, ожидание **неотменяемое**, `queueFull` не существует. Две разные семантики очереди в одном процессе. |
| 19 | До `88fce9a` (2026-10-17) в `AudioServer.swift` не было сериализации | B1 §Итог 9, §12 | ПОДТВЕРЖДЕНО | `git show 88fce9a^:Sources/AudioServer/AudioServer.swift \| grep -c "Lock\|gate\|serial\|queue"` → **0**. Дата коммита: 2026-10-17, «Harden inference ownership and add an optional Qwen3-ASR runtime». |
| 20 | RU у Nemotron 3.5 заявлен | B2 §9 | ЧАСТИЧНО | В репозитории нет списка языков как данных — он грузится из `languages.json` внутри HF-бандла ([`NemotronStreamingASR.swift:250,308`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/NemotronStreamingASR/NemotronStreamingASR.swift#L250-L308)). `ru` фигурирует только в примере language-guide: `docs/models/nemotron-asr-streaming.md:60-66` (`expectedLanguages: ["en-US", "en-GB", "ru-RU"]`). Там же `:125` — «128 slots total: 84 used languages + auto», что расходится с «75 языков» из HF-карточки. Кодом ни 75, ни 84 не подтверждается — нужен `languages.json` из бандла. |
| 21 | RU-поддержка Qwen3-ASR противоречива (7 языков в карточке vs 52 в доке) | B2 §9 | ПОДТВЕРЖДЕНО как внутреннее противоречие репозитория | [`docs/benchmarks/asr-wer.md:113-130`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/asr-wer.md#L113-L130): строка `Russian \| WER \| 16.35 \| 10.52 \| 11.49`, текст «Qwen3 supports 52 languages». В том же абзаце — «Parakeet is English-only (25 European languages)», что противоречит собственной строке с Parakeet 11.49 по русскому. Док противоречит сам себе, независимо от карточек HF. |
| 22 | «Parakeet поддерживает `russian`» | B2 (вывод из §9) | **ОПРОВЕРГНУТО** | [`ParakeetASR+Protocols.swift:27-48`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/ParakeetASR/ParakeetASR%2BProtocols.swift#L27-L48): `case .russian: return "russian"` — это `ttsLanguageName(for: NLLanguage)`, маппинг **выходного** распознанного текста в имена языков Qwen3-TTS через `NLLanguageRecognizer`. К входным языкам ASR отношения не имеет; списка входных языков в коде Parakeet нет вовсе. Утверждение B2 опирается на карточку `nvidia/parakeet-tdt-0.6b-v3`, а не на код. |
| 23 | `AudioServer` можно подключить как библиотеку | — (новая проверка) | **ОПРОВЕРГНУТО** | В `Package.swift` нет `.library(name: "AudioServer", …)`. Он объявлен `.target(name: "AudioServer", …)` (`:947`), достижимый только через `.executable(name: "speech-server", targets: ["AudioServerCLI"])` (`:203`) и deprecated `audio-server` (`:212`). Импортировать его из другого пакета через SPM нельзя. |
| 24 | ONNX-бэкенд отсутствует | — (новая проверка) | ПОДТВЕРЖДЕНО | `grep -rn "onnxruntime\|ONNX\|Onnx" Sources/` → 5 совпадений, **все в комментариях** (`Audio2Face3D/Configuration.swift:6`, `CanaryASR/Configuration.swift:43`, `MossTranscribe/MossCoreMLRuntime.swift:210`, `ChatterboxTTS/MTLTokenizer.swift:66`). Ни одного импорта, ни одной зависимости в `Package.swift`. |
| 25 | Репозиторий часто ломает публичный API | — (новая проверка) | **ОПРОВЕРГНУТО** (на уровне объявлений) | `git diff v0.0.23 HEAD -- Sources/ \| grep -c "^-.*public "` → **0**. За 3+ месяца (v0.0.23 = 2026-07-18 → HEAD = 2026-10-25, 4 тега) **ни одно** публичное объявление не удалено и не изменено. Изменения аддитивные: например, `WordConfidence` получил `startTime`/`endTime` с дефолтами в `init` (диff v0.0.27→v0.0.29, `Protocols.swift`). Риск не в ломке, а в том, что версия `0.0.x`. |
| 26 | 655 коммитов / 180 дней | контекст задачи | ПОДТВЕРЖДЕНО | `git rev-list --count HEAD` = 1067; `--since=2026-04-28` = **655**. |
| 27 | 117 HF-репозиториев `aufklarer/*` существуют | B2 §9 | НЕ ПРОВЕРЯЕМО здесь | Требует перебора HF API; в этой сессии не выполнялось (приоритет — код и git). |

## Детали по ключевым пунктам

### InferenceGate: «очередь на весь сервер», а не «на модель»

```swift
private let lock = NSLock()
private var occupied = false            // ровно один активный permit
private var waiters: [Waiter] = []      // FIFO
private let maximumPending: Int         // = 32 по умолчанию
```
- `acquire()` — `withTaskCancellationHandler` + `withCheckedThrowingContinuation`; отмена снимает ожидающего из очереди; при переполнении — `AdmissionError.queueFull`;
- `InferencePermit.release()` идемпотентен (обнуляет `gate` под локом), `deinit { release() }` — утечка permit невозможна;
- таймаута ожидания **нет**: `acquire()` не имеет ни deadline, ни `sleep`-страховки.

Экземпляр создаётся один раз в `ModelState.init` (`AudioServer.swift:538-545`) и передаётся всем путям. Permit берут `dispatchTranscribe:1757`, `dispatchSynthesize:1803`, `/respond:274`, `/enhance:353`, `preloadModels:80`, WS `response.create:1200`. Гейт не знает, какая модель запрошена → ожидание за TTS 7B блокирует ASR. Это следует из кода буквально.

### ModelCache: single-flight есть, жизненного цикла нет

- ключ = `(ObjectIdentifier(Model.self), String)` — тип участвует, поэтому одинаковые строки у разных типов не конфликтуют;
- ошибка удаляет запись (`catch { loads[key] = nil; throw }`) — «отравления» нет;
- **нет** `unload`, `evict`, `count`, `memoryCost`, TTL.

Док прямо фиксирует это как решение, а не как долг: `docs/inference/model-runtime.md:36-37`. Ключи в сервере не унифицированы: `modelId`, `"<backend>:<modelID>"` (Qwen3-ASR, `:553-565`), константы `"loadMagpie"`, `"loadMagpieCoreML"`, `"loadPersonaPlex"` (`:684-757`). Константный ключ означает: смена `modelId` в реестре не меняет ключ → разные варианты одного движка дают один инстанс. Для Magpie это совпадает случайно, для PersonaPlex — прямая ошибка (#10).

### Реестр: 48 записей и что с ними происходит

| kind | записей | клиентская операция |
|---|---:|---|
| asr | 8 | `/transcribe`, `/v1/audio/transcriptions`, WS |
| tts | 15 | `/speak`, `/v1/audio/speech`, WS |
| s2s | 4 | `/respond`, WS |
| enhance | 2 | `/enhance` |
| music | 6 | нет |
| vad | 4 | нет своего роута (Silero — внутри WS turn detection) |
| separate | 3 | нет |
| sr | 2 | нет |
| speaker | 2 | нет |
| turn | 1 | нет (Smart Turn не подключён к WS) |
| diarize | 1 | нет |
| **итого** | **48** | **29** |

`/v1/models` отдаёт все 48 без признака достижимости; `/v1/realtime/models` фильтрует `[.asr, .tts, .s2s]` = 27. Клиент, увидевший `sortformer-diarization-coreml`, не может его вызвать; через `session.update` имя молча принимается и эхо-возвращается, слот не меняется (`AudioServer.swift:1028-1034`).

### `docs/shared-protocols.md`: это позиция, а не баг

Автор не «забыл» про потокобезопасность — он её отверг аргументом «a scenario no caller exercises». GitHub issue #34 (2026-02-26) предлагал три варианта (`@MainActor`, внутренние локи, «явно задокументировать однопоточный контракт») — выбран третий, и он и есть `docs/shared-protocols.md:591-608`. Для нашего проекта это значит: **любая** модель из speech-swift при многоклиентности требует либо внешней сериализации (получаем текущий speech-swift), либо N инстансов (память × N), либо патча самих классов. Третьего варианта код не предлагает. Исключения: `WeSpeakerModel` (все `let`), `MossTTSNanoModel` (`public actor`), `MossTranscribeModel` (`NSLock`).

## Свежие намерения автора (куда движется проект)

### Git: серверный код почти не меняется, библиотека — очень сильно

| Метрика | Значение | Команда |
|---|---|---|
| Всего коммитов | 1067 | `git rev-list --count HEAD` |
| С 2026-04-28 (180 дней) | 655 | `git rev-list --count --since=2026-04-28 HEAD` |
| В `Sources/AudioServer|AudioServerCLI|SpeechRuntime` с 2026-09-01 | **12** | `git log --since=2026-09-01 --oneline -- <директории>` |
| Диff между двумя последними тегами (v0.0.27→v0.0.29) | 536 файлов, +64391/−1438 | `git diff --shortstat v0.0.27 v0.0.29` |
| Теги | 29, `v0.0.10`…`v0.0.29`; 2026-10-25 / 2026-09-23 / 2026-09-02 | `git tag` |
| Размер серверного кода | 4077 строк (`AudioServer` 3801 + `AudioServerCLI` 59 + `SpeechRuntime` 217) | `wc -l` |

12 коммитов с 2026-09-01 в серверных каталогах: `cf6e612` (Smart Turn v3.2 — registry + 2 строки в `AudioServer.swift`), `5ff7e30` (server VAD), `bb2d668` (cancellation API), `88fce9a` (inference ownership + Qwen3-ASR runtime), `5a04e9c` (native Voxtral TTS), `8d01771` (Pocket TTS CPU streaming), `cecd93b` (немецкие голоса Kokoro), `ff73724` (offline native inference service), `b948e81` (Gemma 4 12B), `ce28f6f` (логирование native failures), `510615d` (native chat failures), `6f3eafe` (Gemma4 prefill). **Ни одного** про многоклиентность, очереди на модель, выгрузку или версионирование API.

То есть вся инерция проекта (64391 строка за 7 недель) идёт в **модели**, а сервер получает только то, что нужно для подключения очередной модели.

### GitHub: открытых issues 6, PR 4, discussions 2

- Открытые issues: `#503` (ANE compile Nemotron на iOS), `#479` (SIGSEGV в MLX bfloat16 на A19 Pro), `#470` (просит demo video), `#454` (лицензии IndexTTS2), `#233` (оптимизация batch decode Qwen3-ASR), **`#219` (hotword biasing для Qwen3-ASR, с 2026-04-23 — прямо про наш ru-IT кейс, не реализовано)**.
- Открытые PR: `#504` (документация Nemotron iOS), **`#445` (embeddable Qwen3-TTS runtime controls, с 2026-08-01 — про «встраиваемость», но библиотечную, а не серверную)**.
- Discussions: 2. **`#81` «Roadmap: v0.1 - Foundation» (2026-03-06)** — единственный roadmap. Архитектура там: `Layer 1: Primitives` → `Layer 2: Pipelines` → `Layer 3: Apps`, где `HTTP server ✅` и `WebSocket streaming ✅` лежат **в слое Apps**. Слоя «рантайм/демон с жизненным циклом моделей» в архитектуре автора нет. Пункт «Model loading orchestrator — parallel loading of N models with unified progress» (#123) закрыт как PR #130 → реализован как `Sources/AudioCommon/ModelLoader.swift` (загрузка N моделей с прогрессом), но сервер им не пользуется: он ходит в `ModelCache` напрямую.
- Поиск по issues/PR: `daemon` → **0 результатов**; `server` → 39, все про добавление моделей/транспорта (`#517` native inference, `#508` inference ownership, `#477` server VAD, `#415` OpenAI-compatible TTS, `#321` auto-ping, `#311` model в `session.update`); `unload` → 12, ключевые `#79`/`#86` (март 2026, `unload()` + memory footprint, реализовано **на уровне моделей**, не сервера) и `#498/#525/#536/#537` (октябрь 2026 — ограничение MLX buffer cache, т.е. работа с памятью идёт, но через глобальные лимиты, а не через вытеснение моделей).
- В `docs/`, `README.md`, `AGENTS.md` — **ноль** упоминаний `ollama`, `launchd`, `daemon`, `launch agent`, `multi-client`, `multiple clients`, `concurrent sessions`.
- Метаданные репозитория: 1248 звёзд, 181 форк, создан 2026-02-04, Apache-2.0, последний push 2026-10-25.

**Вывод по риску расхождения форка.** Проект не идёт в нашу сторону и не собирается: единственный roadmap (март) относит сервер к «приложениям», а все силы уходят в модели. Значит форк сервера не «догонит» нас — он будет расходиться по мере того, как upstream добавляет модели и меняет `ModelState`/`RealtimeModelLoading`. При этом сам серверный код мал (4077 строк) и медленный (12 коммитов/2 месяца), т.е. конфликты при ребейзе будут редкими — но и обновляться там нечему, кроме списка моделей.

## Стоимость форка

Требования взяты из формулировки Goal (в тексте задачи сказано «12 требований»; в Goal я насчитал **13** атомарных пунктов — привожу все, чтобы ничего не потерять).

Легенда объёма: **S** — патч в один-два файла; **M** — новая подсистема на 200–500 строк; **L** — переписывание архитектурного слоя.

| # | Требование | Что есть в speech-swift | Что пришлось бы переписать | Объём |
|---|---|---|---|---|
| 1 | Один локальный сервис | `speech-server` (Hummingbird 2), CLI из 4 флагов, `/health` | Конфиг-файл/env, graceful shutdown с дренажом permit, request-id и структурные логи, `serverConfiguration` (таймауты, лимиты соединений), launchd-юнит. Сам процесс — есть | M |
| 2 | Общий каталог моделей | `~/Library/Caches/qwen3-speech/models/<owner>/<repo>/`, HF-совместимо, `QWEN3_CACHE_DIR` (историческое имя), tree-API + sha256 LFS + range-докачка + `HF_ENDPOINT` | Манифест установленного, `delete`/GC, revision-пиннинг (сейчас всегда `revision: "main"`, `HuggingFaceRepoManifest.swift:86`), размер/лицензия per-artifact, проверка целостности при повторных загрузках (сейчас только при скачивании) | M |
| 3 | Единый стабильный API (Swift, Rust, …) | 9 HTTP + 1 WS + 4 native-роута; 4 несовместимых формата ошибок; нет версии протокола | Практически весь слой: версионирование (заголовок/handshake/`schema_version`), единый словарь ошибок (RFC 9457), идемпотентные `id`, отмена по `id`, backpressure-политика. Файл `AudioServer.swift` = 2074 строки, где смешаны роуты, WS-протокол, диспетчеризация, PCM-конверсия, WAV-кодеки, JSON-хелперы | **L** |
| 4 | Ленивая загрузка | Есть: `ModelCache.load` на первом обращении, single-flight, ошибка не кэшируется | Загрузка идёт **внутри** permit (`dispatchTranscribe:1757` → `loadParakeet:1764`), поэтому холодный старт 3-гигабайтного бандла блокирует все запросы. Нужны отдельная фаза `loading(progress)`, ответ 202/`Retry-After` остальным, прогрев по факту, а не по `--preload` с неправильным списком | S–M |
| 5 | Переиспользование весов между клиентами | Есть и работает: один `ModelCache` на процесс, один инстанс на `modelId`, все сессии ссылаются на него (`RealtimeSession` хранит только выбор варианта) | Ничего, кроме унификации ключей (`"loadPersonaPlex"`-константы ломают инвариант «ключ = идентичность весов») | S |
| 6 | Одновременные независимые сессии | Состояние на соединение есть (`RealtimeSession` на каждый WS, VAD-инстанс на сессию). Исполнение — глобально сериализовано; модели **not thread-safe by design** | Гейт на модель/инстанс + пул инстансов + бюджет памяти + приоритеты (realtime > interactive > batch) + политика вытеснения. Это не патч: требует отказа от главного архитектурного допущения библиотеки («no caller exercises it») и, следом, патча самих классов моделей либо N копий весов | **L** |
| 7 | Потоковый и пакетный ASR | Пакетный — есть. Потоковый: протоколы `StreamingRecognitionSession`/`StreamingRecognitionModel` и реализации (Parakeet EOU, Nemotron CoreML+MLX, Kyutai) **есть**, но сервер их не вызывает ни разу | WS-события `partial`/`revision`/`final`, склейка stable-prefix, сессия на соединение, отмена. Работа в основном в сервере, модели уже готовы — это самый «дешёвый» из крупных пунктов | M–L |
| 8 | VAD | Silero v6.2.1 (MLX и CoreML), `StreamingVADProcessor` с pre-roll, Smart Turn v3.2. Используется **только** внутри WS, инстанс на соединение, без таймлайна | Отдельный роут с выдачей `[SpeechSegment]`, батч-режим, EOT как отдельная операция. Модели готовы, нужен только API | S |
| 9 | Диаризация | Sortformer (+streaming-сессии), Pyannote Community-1 + VBx, Nemotron-3-Diarization, `PyannoteDiarizationPipeline`; все реализуют `SpeakerDiarizationModel`; `SpeechVAD` уже в зависимостях `AudioServer` | Роут `diarize` (offline + streaming), формат сегментов, DER-совместимый вывод. Кода моделей писать не надо | M |
| 10 | Голосовые эмбеддинги | `WeSpeakerModel` (256-d, thread-safe, единственный конформ `SpeakerEmbeddingModel`), `ReDimNet2`, `CamPlusPlus` (последние два вне протокола) | Роут `embeddings/speaker` + `speakers/match` (косинус), хранение зарегистрированных голосов. Модель готова | S–M |
| 11 | Несколько движков через адаптеры (CoreML, MLX, ONNX) | CoreML и MLX — два независимых набора реализаций (277 vs 77 файлов), 5 идиом выбора движка, никакой общей абстракции. **ONNX отсутствует полностью** | Свой слой `Engine`-адаптеров поверх чужих реализаций + ONNX-воркер (sherpa-onnx или onnxruntime) извне. Внутри speech-swift это делать бессмысленно: пришлось бы трогать 354 файла вычислений | **L** (и делается **вне** форка) |
| 12 | install / remove / update / версии моделей | Загрузчик (2474 строки в 4 файлах `AudioCommon`), прогресс через `print`, native-режим требует абсолютных локальных каталогов | API `pull`/`delete`/`load`/`unload`/`ps` с прогрессом (SSE), каталог версий/ревизий, атомарная активация, откат, квоты. `ModelCache` для этого непригоден — у него нет даже `count` | M–L |
| 13 | Минимальные накладные расходы | Один процесс тянет `SourceSeparation`, `FlashSR`, `MAGNeTMusicGen`, `StableAudio3MusicGen`, `Qwen3Chat` и ещё ~30 таргетов — мёртвый вес для нашего профиля | Урезание зависимостей; но при переходе на «библиотеку моделей» вопрос снимается: линкуется только то, что нужно (см. ниже) | S (или 0) |

**Сводка по объёму:** L — 4 требования (3, 6, 11, 12-частично), M — 5, S — 4. Из 13 требований **ни одно** не закрывается правкой в одном месте, а три (3, 6, 11) требуют отказа от явно задекларированных решений автора.

### Альтернатива: speech-swift только как библиотека моделей

Что подключаемо через SPM **без** `AudioServer` (проверено по `Package.swift`):

| Продукт | Зависимости | Ценность для нас |
|---|---|---|
| `AudioCommon` | только `Hub` (swift-transformers) | `Protocols.swift` (14 протоколов), **HF-загрузчик 2474 строки** (`HuggingFaceDownloader` 835 + `HuggingFaceTransfer` 590 + `HuggingFaceBackgroundTransfer` 686 + `HuggingFaceRepoManifest` 363), `ModelLoader`, WAV/audio I/O, `CoreMLComputeUnits` |
| `SpeechRuntime` | нет | `InferenceGate`, `ModelCache`, `ModelDescriptor` — 217 строк готовых примитивов |
| `ParakeetASR`, `KokoroTTS`, `WhisperASR` | только `AudioCommon` | самые «дешёвые» движки: не тянут MLX вообще |
| `ParakeetStreamingASR`, `NemotronStreamingASR`, `SpeechVAD`, `OmnilingualASR` | `AudioCommon` (+ `MLXCommon` для MLX-вариантов) | streaming ASR, VAD, EOT, диаризация, эмбеддинги |
| `Qwen3ASR`, `Qwen3TTS`, `CosyVoiceTTS`, `PersonaPlex`, `VibeVoiceTTS`, `VoxtralTTS` | + `MLXCommon` → `mlx-swift` | MLX-модели |
| `Qwen3ASRRuntime` | `SpeechRuntime`, `Qwen3ASR`, `AudioCommon` | единственный готовый «adapter поверх MLX/CoreML» |

`AudioServer` в этом списке отсутствует **не по выбору, а по конструкции** — он не библиотечный продукт (#23).

**Стабильность API — измеренная, а не предполагаемая:**
- за 3+ месяца и 4 тега (`v0.0.23` 2026-07-18 → HEAD) **ни одно публичное объявление не удалено и не изменено** (`git diff … | grep -c "^-.*public "` → 0); изменения аддитивные, с дефолтными аргументами, т.е. source-compatible;
- но нумерация `0.0.x` и `from: "0.0.29"` в SwiftPM = `.upToNextMajor` → диапазон `0.0.29..<0.1.0`, т.е. разрешает любой `0.0.x`/`0.0.9x` (а при `from: "0.1.0"`, если автор решит так прыгнуть — `0.1.0..<1.0.0`). Публичных обещаний стабильности нет; на практике нужно пинить `.exact("0.0.29")` или следить за каждым тегом;
- объём изменений при этом огромный: 536 файлов, +64391/−1438 между двумя соседними тегами. Source-compat не означает «поведение не изменилось»: те же сигнатуры могут давать другой результат;
- теги выходят каждые 2–4 недели (2026-09-02 → 09-23 → 10-25), т.е. апгрейд — регулярная операция, а не разовая.

### Вердикт

**Использовать speech-swift как библиотеку моделей. Форк сервера — нет.**

Три причины против форка сервера:
1. **Ценность серверного кода близка к нулю для нас**: из 13 требований он закрывает 2 полностью (ленивая загрузка, переиспользование весов), 2 частично и 9 не закрывает; три невыполнимых требования (одновременные сессии, потоковый ASR-контракт, адаптеры движков) упираются в позицию автора, а не в недоработку.
2. **Форк не даст «догнать upstream»**: сервер получает ~6 коммитов в месяц, и все — про подключение новых моделей, т.е. ровно те правки, которые при форке превращаются в конфликты в `ModelState`/`RealtimeModelLoading`/`ModelRegistry`. Выигрыша «бесплатно получаем улучшения» не будет — улучшения идут в модели.
3. **Стабильность библиотеки достаточна для зависимости, но не для совместного владения кодом**: 0 удалённых публичных объявлений за 3 месяца — хороший знак; 64391 строка изменений за 7 недель — плохой знак для форка.

Что берём как зависимость: `AudioCommon` (протоколы + HF-загрузчик + `CoreMLComputeUnits`), `SpeechRuntime` (гейт/кэш/дескриптор как примитивы), точечно `ParakeetASR`/`ParakeetStreamingASR`/`NemotronStreamingASR`/`WhisperASR`/`SpeechVAD`/`KokoroTTS`/`Qwen3ASR`/`CosyVoiceTTS`. Пиннинг — `.exact` или `.upToNextMinor`.

Что **не** берём и пишем сами: HTTP/WS API-слой, реестр моделей (данные, не Swift-массив), планировщик сессий и памяти, версионирование протокола, ONNX-воркер. Полезные идеи для заимствования (переписать, не копировать): `InferenceGate` как примитив **на модель**, `ModelDescriptor` как формат строки каталога, `ModelCache` single-flight, дисциплина проверки sha256 из LFS.

## Неизвестные / что измерить

1. **Воспроизвести 97/191/284/378 мс.** Нужен запуск сервера на Apple Silicon с прогретыми весами и 4 параллельными `curl` на `/transcribe` с фиксированным WAV. Сейчас это самоотчёт автора на M5 Pro 48 GiB / macOS 26.6.2 / Swift 6.4.
2. **Реальный RSS после загрузки N моделей.** Из кода следует «никогда не выгружаем», но цена в гигабайтах не измерена. В доке только точечно: 705 MiB MLX / 1022 MiB CoreML current footprint после двух по 80 запросов, peak 7834 MiB MLX.
3. **Освобождается ли permit при обрыве WS-клиента** во время `response.create` — зависит от поведения Hummingbird при закрытии сокета; нужен эксперимент.
4. **Конкурентная запись в `WebSocketOutboundWriter`** (keepalive-задача + основной цикл, `AudioServer.swift:984-998`) — безопасность не проверена ни кодом, ни тестом.
5. **Точная резолвнутая версия Hummingbird** — `Package.resolved` в репозитории отсутствует, диапазон `2.5.0..<2.17.0`.
6. **Фактический список языков Nemotron** — лежит в `languages.json` внутри HF-бандла; ни «75» (карточка), ни «84» (док) кодом не подтверждены.
7. **Качество RU-IT у Nemotron 3.5 / Parakeet v3 / Whisper large-v3-turbo / Qwen3-ASR 8-bit** — ни один бенчмарк репозитория не меряет домен (kubectl, Postgres, Helm) и code-switching. `asr-wer.md` даёт только FLEURS.
8. **Поведение при OOM** (MLX Metal OOM vs CoreML eviction) — `MetalBudget.canFit` существует, политики вытеснения нет.

## Источники

**Код** (ревизия `cbad3be29480825707890490f446b19d7288be2d`):
- `Sources/SpeechRuntime/{InferenceGate,ModelCache,ModelRuntime,ModelDescriptor}.swift`
- `Sources/AudioServer/AudioServer.swift` (2074), `ModelRegistry.swift` (404), `NativeInference.swift` (367), `NativeInferenceFailures.swift` (50), `OpenAISpeech.swift` (267), `OpenAITranscriptions.swift` (332), `RealtimeVAD.swift` (307)
- `Sources/AudioServerCLI/AudioServerCommand.swift` (59)
- `Sources/AudioCommon/{Protocols.swift (455), HuggingFaceDownloader.swift (835), HuggingFaceTransfer.swift (590), HuggingFaceBackgroundTransfer.swift (686), HuggingFaceRepoManifest.swift (363), ModelLoader.swift}`
- `Sources/PersonaPlex/PersonaPlex.swift:16-19,1577`; `Sources/ParakeetASR/ParakeetASR+Protocols.swift:27-48`
- `Package.swift` (products/targets/deps), `Tests/AudioServerTests/ASRRuntimeRegressionTests.swift:84-247`

**Документация репозитория:**
- `docs/benchmarks/runtime-ownership-2026-10-17.md:1-5,91-93` (железо, цифры 97/191/284/378)
- `docs/shared-protocols.md:591-608` (thread safety)
- `docs/inference/model-runtime.md:20-40` (ownership, «remain resident for the server's lifetime»)
- `docs/benchmarks/asr-wer.md:113-130` (RU WER, самопротиворечие «English-only»)
- `docs/models/nemotron-asr-streaming.md:60-66,125` (language guide, 84 языка)

**Git:**
- `git rev-list --count HEAD` → 1067; `--since=2026-04-28` → 655
- `git log --since=2026-09-01 -- Sources/AudioServer Sources/AudioServerCLI Sources/SpeechRuntime` → 12 коммитов
- `git diff --shortstat v0.0.27 v0.0.29` → 536 files, +64391/−1438
- `git diff v0.0.23 HEAD -- Sources/ | grep -c "^-.*public "` → 0
- `git show 88fce9a^:Sources/AudioServer/AudioServer.swift | grep -c "Lock\|gate\|serial\|queue"` → 0
- `git tag` → 29 тегов `v0.0.10`…`v0.0.29`

**GitHub API (2026-10-26):**
- `GET /repos/soniqo/speech-swift` — 1248 звёзд, 181 форк, Apache-2.0, создан 2026-02-04
- `GET /search/issues?q=repo:soniqo/speech-swift+is:issue+is:open` — 6 открытых
- `GET /repos/soniqo/speech-swift/issues/34,79,219,445`
- `GET /repos/soniqo/speech-swift/discussions/81` — «Roadmap: v0.1 - Foundation» (2026-03-06)
- `GET /repos/soniqo/speech-swift/releases?per_page=4` — v0.0.29 / v0.0.28 / v0.0.27 / v0.0.26
