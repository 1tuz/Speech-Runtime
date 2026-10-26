# F. Сравнение архитектур: полностью Swift / Rust-сервис с нативными адаптерами / гибрид Rust + Swift

Дата исследования: 2026-10-25. Платформа измерений (где указано «измерено здесь»): Apple M5 Pro, macOS 27.0 (сборка 26A428), Swift 6.4 (swiftlang-6.4.0.34.1), rustc/cargo 1.98.1.

Обозначение источников: `Фn` — факт №n в разделе «Факты». Ссылки на код даны permalink'ами с зафиксированным SHA; для репозиториев вне выданного списка SHA — это HEAD клона на 2026-10-25.

---

## Итог

1. **Все три варианта технически осуществимы; решает не «скорость языка», а доступ к движкам и модель изоляции.** Ни в одном варианте нет доказанного превосходства по скорости: единственные сопоставимые измерения, которые удалось найти в коде/доках (Ф5.4, Ф5.5), показывают, что разница определяется моделью и бэкендом, а не языком рантайма.
2. **Доступ к CoreML из Rust есть, но это сырой unsafe-ObjC.** `objc2-core-ml` 0.3.2 (288K загрузок, Zlib/Apache-2.0/MIT) даёт полный набор `MLModel`, `MLMultiArray`, `MLModelConfiguration`, `MLState`, компиляцию моделей — но каждое обращение `unsafe`, без Swift-удобств (`MLTensor`, `MLShapedArray`, async/await). `coreml-rs` 0.5.4 — обёртка на swift-bridge, 16 звёзд, последний релиз 2025-06-20 (Ф1).
3. **MLX из Rust (`mlx-rs` 0.32.0) технически зрелее, чем ожидается** (623 публичных функции в `ops`, свой `mlx-lm`, CI с leak/use-after-free гейтами, коммиты в день среза), но **модель потокобезопасности та же, что в Swift**: `Array` — `Send`, но не `Sync`, default stream — thread-local; мейнтейнер сознательно не делает `Sync` из-за segfault'ов (Ф2).
4. **MLX в Swift сериализуется глобальным `evalLock` (NSRecursiveLock).** `MLXArray` не `Sendable`; единственный официально `Sendable` путь — `MaterializedArray`/`materialized()` (появился в 2026). Мейнтейнер mlx-swift прямо пишет, что удержание блокировки вокруг блокирующего `wait()` — это «desired behavior», пока нижние слои не потокобезопасны (Ф3).
5. **CoreML документированно однопоточный на инстанс:** «Use an MLModel instance on one thread or one dispatch queue at a time… or create a separate model instance for each thread» (Ф4). То есть параллелизм CoreML = N инстансов модели, с неизвестным множителем памяти (надо мерить).
6. **Ключевой архитектурный вывод не зависит от языка:** и Swift, и Rust упираются в «одна GPU-очередь MLX на процесс» и «один MLModel на поток». Независимые одновременные сессии требуют либо нескольких процессов, либо нескольких инстансов моделей, либо явной очереди с батчингом. Реальный Swift-сервер (speech-swift) сегодня выбирает худший вариант — один глобальный шлюз на весь сервер: 4 одновременных запроса выполняются последовательно (97/191/284/378 мс) (Ф5).
7. **Стоимость передачи аудио не является аргументом против гибрида.** Измерено здесь: FFI-вызов — 1–2 нс; round-trip по UDS 640 B — 7.2 µs, 6.4 KB — 8.8 µs, 64 KB — 80 µs (1.6 ГБ/с). Для 100-мс чанка 16 кГц это 0.009 % реального времени. При этом «модный» путь JSON+base64 (как в speech-swift) стоит 1.06 мс на 64 KiB — в 120 раз дороже UDS (Ф9).
8. **Swift-серверная часть зрелая:** Hummingbird 2.28.0, Vapor 4.122.3 (и 5.0.0-beta.4 от 2026-10-13), SwiftNIO 2.104.0, `hummingbird-websocket` 2.8.1, UDS из коробки, WebSocket-upgrade в HummingbirdCore. Есть работающий пример реального речевого сервера на Hummingbird (Ф7).
9. **Rust-часть зрелая для control plane и для ONNX/sherpa-движков:** `ort` 2.0.0-rc.15 (ORT 1.31, MSRV 1.92), prebuilt-бинари для macOS arm64 существуют **только** в вариантах `coreml` и `coreml,webgpu`; sherpa-onnx даёт C API + Swift Package (xcframework v2.0.0) + `DecodeStreams` (батчинг нескольких сессий на одной модели) (Ф6, Ф12).
10. **Предварительная рекомендация — гибрид C, но «тонкий»:** Rust как control plane (каталог моделей, скачивание/версии, роутинг сессий, admission, метрики, единый протокол) + Swift worker-процессы как data plane для MLX/CoreML-движков; связь — UDS с бинарным фреймингом, не JSON+base64. Условия смены рекомендации — в разделе «Выводы для Runtime».

---

## Факты

### Ф1. CoreML из Rust

**Ф1.1.** `objc2-core-ml` — автогенерируемые биндинги к framework'у CoreML: `MLModel` (загрузка, `predictionFromFeatures:`, `predictionsFromBatch:`, `compileModelAtURL`, `newState`, `availableComputeDevices`), `MLMultiArray` (включая `initWithDataPointer:…deallocator:` — обёртка чужого буфера без копирования, и `initWithPixelBuffer:` — IOSurface-backed, «can reduce the inference latency by avoiding the buffer copy»), `MLModelConfiguration`, `MLFeatureProvider`, `MLState`.
Версия 0.3.2 (2025-10-04), 288K загрузок всего / 150K за последний период, MSRV 1.71, лицензия `Zlib OR Apache-2.0 OR MIT`.
Источник: https://crates.io/crates/objc2-core-ml ; API: https://docs.rs/objc2-core-ml/latest/objc2_core_ml/struct.MLModel.html , https://docs.rs/objc2-core-ml/latest/objc2_core_ml/struct.MLMultiArray.html

**Ф1.2.** Все методы — `pub unsafe fn` с сырыми указателями и `Retained<…>`; протоколы CoreML (`MLFeatureProvider`) приходят как `ProtocolObject<dyn MLFeatureProvider>`, то есть для «своего» провайдера фич нужно реализовывать ObjC-протокол из Rust. Swift-специфичных обёрток (`MLTensor`, `MLShapedArray`, async-обёрток `MLModel.load(contentsOf:configuration:)`) в `objc2-core-ml` нет — в docs.rs присутствуют только ObjC-формы с completion-handler (`DynBlock`).
Источник: те же docs.rs страницы (раздел `loadContentsOfURL_configuration_completionHandler`).

**Ф1.3.** `coreml-rs` (swarnimarun) — обёртка «CoreML bindings for Rust using swift-bridge»: загрузка `.mlmodel`/`.mlpackage`, батч-инференс, выбор `CpuAndANE`, управление памятью, retry. Версия 0.5.4 (2025-06-20), 13K загрузок всего / 919 за последний период, 16 звёзд, 77 коммитов; на crates.io лицензия помечена как «non-standard», в репозитории — MIT (расхождение фиксируем как есть). Требует находимый в рантайме `libswift_Concurrency.dylib`.
Источники: https://crates.io/crates/coreml-rs ; https://github.com/swarnimarun/coreml-rs

**Ф1.4.** Ещё один Rust-вариант: `coreml-native` 0.2.0 (2026-03-24), 4.3K загрузок, Apache-2.0 OR MIT, «Built on objc2-core-ml — no C bridge».
Источник: https://crates.io/crates/coreml-native

**Ф1.5.** CoreML существует только на Apple-платформах: доступность `MLModel` — iOS 11+, iPadOS, macCatalyst, macOS 10.13+, tvOS, visionOS, watchOS.
Источник: https://developer.apple.com/documentation/coreml/mlmodel

### Ф2. MLX из Rust (`mlx-rs`)

**Ф2.1.** `mlx-rs` 0.32.0, workspace включает `mlx-sys`, `mlx-rs`, `mlx-lm`, `mlx-lm-cli`, `mlx-tests`; лицензия MIT OR Apache-2.0; MSRV 1.88.0; активная разработка (несколько коммитов в день среза 2026-10-25).
Permalink: https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/Cargo.toml#L1-L25

**Ф2.2.** Покрытие: 15 модулей в `mlx-rs/src/ops` (arithmetic, convolution, reduction, quantization, indexing, sort, fft…), 623 публичных функции только в `ops`, полноценный `nn` (linear, normalization, transformer, quantized, recurrent), FFT (`rfftn`), `mlx-lm` с arch/cache/sampling/tokenizer/weights. Отдельного аудио-слоя (STFT-обёрток, mel) нет — только общие примитивы (`fft`, `conv1d`).
Permalinks: https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/mlx-rs/src/ops/mod.rs ; https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/mlx-rs/src/ops/convolution.rs#L109-L170 ; https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/mlx-rs/src/fft/rfftn.rs

**Ф2.3.** Модель потокобезопасности: `unsafe impl Send for Array {}` с комментарием «MLX 0.32.2 supports moving array handles while operations use per-thread default streams», при этом `Sync` **не** реализован.
Permalink: https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/mlx-rs/src/array/mod.rs#L74-L75

**Ф2.4.** Default stream — thread-local: `thread_local! { static THREAD_LOCAL_DEFAULT_STREAM: RefCell<Option<Stream>> }`, документировано: «The value does not propagate across asynchronous task suspension or between operating-system threads». Есть `with_stream(&Stream, f)` — синхронный, thread-local, panic-safe scope.
Permalink: https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/mlx-rs/src/stream.rs#L1-L60

**Ф2.5.** Мейнтейнер объясняет отказ от `Sync`: «We intentionally made it not `Sync` because we've encountered segmentation fault when we initially tried running unit tests multithreaded» (issue #302, комментарий от 2025-12-31).
Источник: https://github.com/oxiglade/mlx-rs/issues/302

**Ф2.6.** Реальный кейс гонок: issue #349 (2026-05-10) — два воспроизводимых race в Metal-бэкенде при конкурентном инференсе (encoder lifecycle и cross-stream signal-event), на vendored MLX 0.30.6, Mac mini M4. Рабочий обход у автора — глобальный `Mutex<()>` на весь инференс («~850 ms p50… bad for high-throughput»). Важно: версия 0.30.6 **до** волны потокобезопасности MLX 0.31.2, но issue остаётся открытым и не перепроверен на 0.32.x.
Источник: https://github.com/oxiglade/mlx-rs/issues/349

**Ф2.7.** Инженерная дисциплина проекта: `CHARTER.md` (правила дизайна API, включая «Stream selection is execution-local context»), `VERIFICATION.md` с FFI leak/use-after-free гейтами (`cargo xtask verify-ffi --guard-malloc`), конформанс-тесты.
Permalinks: https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/CHARTER.md ; https://github.com/oxiglade/mlx-rs/blob/a5205a0039e243fdf29c92b10489b57df05c1028/VERIFICATION.md

### Ф3. MLX в Swift: потоки, `evalLock`, `Sendable`

**Ф3.1.** В mlx-swift есть один глобальный рекурсивный лок на все eval/asyncEval: `let evalLock = NSRecursiveLock()`, с документированным порядком захвата («evalLock is the outermost lock in the library»).
Permalink: https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/Transforms+Eval.swift#L5-L18

**Ф3.2.** `MLXArray` не `Sendable`; официально добавлен `MaterializedArray` — «fully-evaluated, immutable MLXArray that is safe to share across concurrency domains», создаётся только через `materialized()`/`materialize(_:)`, мутирующие операции помечены `unavailable`.
Permalinks: https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/MaterializedArray.swift#L7-L45 , https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/MLXArray.swift#L606-L624

**Ф3.3.** Обсуждение мейнтейнера (issue #417, открыто 2026-05-29): «MLXArray in mlx-swift is a class type and is not Sendable. The backing C++ object is not thread safe. Mostly. An evaluated MLXArray would be Sendable» — то есть даже «просто передать массив между тасками» требует явной материализации.
Источник: https://github.com/ml-explore/mlx-swift/issues/417

**Ф3.4.** Регрессия и позиция мейнтейнера (issue #428, 2026-06-21): удержание `evalLock` вокруг блокирующего `array::wait()` в `description` приводит к дедлоку/крашу при логировании массива во время инференса на другом потоке. Ответ @davidkoski: «You are describing the "desired" behavior — if we don't do this then we can see crashes as the entry to eval (on the C++ side is not thread safe)… until the lower levels are thread safe there is not much we can do».
Источник: https://github.com/ml-explore/mlx-swift/issues/428

**Ф3.5.** Стримы в mlx-swift стали Task-scoped: `@TaskLocal private static var localStreams: StreamPairPromise?`, плюс пул стримов (`StreamPool`) с комментарием про утечку Metal-ресурсов (mlx issue #2118). По словам мейнтейнера в issue #457, «0.32.2 is just released and per #472 Streams and Devices are now Task scoped».
Permalinks: https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/Stream.swift#L70-L110 , https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/Stream.swift#L283
Источник: https://github.com/ml-explore/mlx-swift/issues/457

**Ф3.6.** Со стороны ядра MLX потокобезопасность появилась в 0.31.2: в release notes — «MLX can be used by multiple threads for independent computations (#3405, #3348, #3281, #3423)», «Make each thread have its own default stream», «Make CommandEncoder thread local», «Make Scheduler::enqueue thread safe». До этого (0.24–0.31.1) официальная позиция была «Mlx is in general not thread safe» (issue #2067).
Источники: https://github.com/ml-explore/mlx/releases/tag/v0.31.2 ; https://github.com/ml-explore/mlx/issues/2067

**Ф3.7.** Практическое следствие: конкурентность в MLX — «независимые вычисления на разных потоках с собственными стримами», а не «один граф из нескольких потоков». Для рантайма это значит: параллельные сессии возможны, но каждая должна иметь свой стрим и свою копию состояния модели; гонки на общих буферах/энкодерах всё ещё возможны (Ф2.6).

### Ф4. CoreML: потокобезопасность и compute units

**Ф4.1.** Apple документирует: «Use an MLModel instance on one thread or one dispatch queue at a time. Do this by either serializing method calls to the model, or by creating a separate model instance for each thread and dispatch queue.»
Источник: https://developer.apple.com/documentation/coreml/mlmodel

**Ф4.2.** `MLComputeUnits`: `all` (включая ANE), `cpuOnly`, `cpuAndGPU`, `cpuAndNeuralEngine`; «Use cpuOnly to restrict the model to the CPU, if your app might run in the background or runs other GPU intensive tasks».
Источник: https://developer.apple.com/documentation/coreml/mlcomputeunits

**Ф4.3.** Практика FluidAudio (CoreML-стек для Parakeet/диаризации): дефолт `computeUnits = .cpuAndNeuralEngine`, `allowLowPrecisionAccumulationOnGPU = true`; в комментарии зафиксировано, что `MLOptimizationHints` (reshapeFrequency=.infrequent, specializationStrategy=.fastPrediction) **ухудшили** RTFx 126.6 → 93.3 (−26 %) на их статическом графе — то есть «оптимизации» CoreML надо мерить, а не включать по умолчанию.
Permalink: https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/MLModelConfigurationUtils.swift#L1-L30

### Ф5. Реальные Swift-реализации и их ограничения

**Ф5.1.** speech-swift (`Sources/SpeechRuntime`) — ровно прообраз «локального речевого рантайма»: `InferenceGate` (bounded FIFO, до 32 ожидающих, отменяемый, `@unchecked Sendable` на NSLock), `ModelRuntime<Model>` (ленивая загрузка, один инстанс, все операции через один permit, `unload()`), `ModelCache` (actor, коалесцирует загрузки по типу+идентичности), `ModelDescriptor` (`backend: mlx|coreml|hybrid`, `streaming: buffered|incremental`).
Permalinks: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L5-L13 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelRuntime.swift#L6-L33 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelCache.swift#L6-L20 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelDescriptor.swift#L1-L10

**Ф5.2.** Ограничение зафиксировано в документации: «The audio server shares one bounded inference lane across native HTTP, [OI] audio routes and realtime WebSocket operations» — один шлюз на весь сервер, то есть независимые одновременные сессии **не поддерживаются**, они выстраиваются в очередь.
Permalink: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/inference/model-runtime.md#L63-L70

**Ф5.3.** Транспорт realtime: «All messages are JSON with a "type" field. Audio is base64-encoded PCM16 24kHz», кадры до 50 MiB, разбор через `JSONSerialization` на каждый кадр.
Permalinks: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L967 , .../AudioServer.swift#L1000-L1007 , .../AudioServer.swift#L1103-L1105

**Ф5.4.** Измерения самого проекта (Apple M5 Pro, 48 GiB, macOS 26.6.2, Swift 6.4; 40 уникальных высказываний, 427.28 с аудио, weighted RTF = суммарное время инференса / длительность аудио):

| Модель | weighted RTF | WER |
|---|---:|---:|
| Qwen3-ASR 0.6B MLX INT4 | 0.01378 | 11.936 % |
| Qwen3-ASR full CoreML | 0.13174 | 7.237 % |
Плюс: пиковый footprint MLX 7 834 MiB против CoreML 1 354 MiB; четыре одновременных HTTP-запроса завершились за ~97/191/284/378 мс («This is the intended serial admission behavior»); admission/release гейта — 0.21 µs; HTTP vs WS медиана — 95.48 vs 97.14 мс.
**Важно:** это не сравнение движков «MLX vs CoreML» — это два разных бэкенда одной модели с разным WER и разной архитектурой декодера; использовать как рейтинг нельзя (сам документ это оговаривает).
Permalink: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/runtime-ownership-2026-10-17.md#L47-L95

**Ф5.5.** FluidAudio, Parakeet Unified 0.6B (int8, CoreML/ANE), LibriSpeech test-clean, 2620 файлов: batch — aggregate WER 1.68 %, overall RTFx 143.6; streaming — 1.79 % / 65.9x; отдельно Parakeet TDT v3 — 2.6 % / RTFx 110. Это показывает, что CoreML-путь на маленьких ASR-моделях очень быстрый (RTFx > 100), то есть тезис «Swift/CoreML медленно» неверен как общий.
Permalink: https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Unified/benchmark.md#L1-L30

**Ф5.6.** Паттерн изоляции в Swift-коде: FluidAudio использует `public actor UnifiedAsrManager` (и `StreamingUnifiedAsrManager`) — сериализация доступа к модели через actor isolation; WhisperKit — обычный `open class WhisperKit` без actor, потокобезопасность остаётся на вызывающем; внутри `TextDecoder` используется `DispatchQueue.concurrentPerform` только для копирования KV-cache по головам.
Permalinks: https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedAsrManager.swift#L49 ; https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L11 ; https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/TextDecoder.swift#L240-L260

**Ф5.7.** Утечки/память: в speech-swift зафиксирован реальный дефект — повторные CoreML-вызовы из одной фоновой задачи удерживали autoreleased scratch-буферы: 16 запросов → рост 879 MiB → 2 495 MiB; лечится `autoreleasepool` вокруг каждого синхронного инференса (после фикса рост 1 616 → 102 MiB).
Permalink: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/runtime-ownership-2026-10-17.md#L10-L20

**Ф5.8.** MLX кэширует освобождённые Metal-буферы и не возвращает их ОС сразу; в speech-swift введён общий потолок `min(2 GiB, RAM/4)`, `cacheLimit` управляется через `MLX.Memory.cacheLimit` / `clearCache()`.
Permalinks: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/inference/mlx-memory.md#L1-L20 ; https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/Memory.swift#L251-L356

### Ф6. ONNX Runtime: Rust и Swift

**Ф6.1.** `ort` 2.0.0-rc.15 (ORT 1.31), edition 2024, MSRV 1.92, «actively-developed». Есть типизированный CoreML EP: `ComputeUnits{All, CPUAndNeuralEngine, CPUAndGPU, CPUOnly}`, `ModelFormat{MLProgram, NeuralNetwork}`, `SpecializationStrategy{Default, FastPrediction}`.
Permalinks: https://github.com/pykeio/ort/blob/4fac49ba41dc90313585e09d01e84fb43dabc8e4/Cargo.toml#L24-L32 , https://github.com/pykeio/ort/blob/4fac49ba41dc90313585e09d01e84fb43dabc8e4/src/ep/coreml.rs#L1-L60

**Ф6.2.** Prebuilt-бинари `ort` для `aarch64-apple-darwin` существуют **только** в двух вариантах: `coreml` и `coreml,webgpu`. То есть на Apple Silicon CoreML EP — это дефолтный путь, а «чистый CPU» требует своей сборки ORT.
Permalink: https://github.com/pykeio/ort/blob/4fac49ba41dc90313585e09d01e84fb43dabc8e4/ort-sys/build/download/dist.tsv#L1-L3

**Ф6.3.** `onnxruntime-swift-package-manager` — официальный SPM-пакет Microsoft, но это **ObjC-обёртка** (`objectivec/`, скопирован из ORT) над C/C++ pod-архивом; платформы iOS 15+/macOS 14+; бинарь тянется с `download.onnxruntime.ai` (в срезе — `pod-archive-onnxruntime-c-1.24.2.zip`). CoreML EP присутствует (`ort_coreml_execution_provider.mm`, `appendCoreMLExecutionProviderWithOptionsV2:`). Последний коммит — 2026-02-25, то есть пакет отстаёт от ORT 1.31, который уже использует `ort`.
Permalinks: https://github.com/microsoft/onnxruntime-swift-package-manager/blob/b7fb7f7dea8a2469e6335d95a61b8f36d0dc83b2/Package.swift#L20-L30 , .../Package.swift#L95-L100 , https://github.com/microsoft/onnxruntime-swift-package-manager/blob/b7fb7f7dea8a2469e6335d95a61b8f36d0dc83b2/objectivec/ort_coreml_execution_provider.mm#L12-L46

**Ф6.4.** ORT сам сериализует `Run`, если хотя бы один EP сообщает `ConcurrentRunSupported() == false` (`sequential_run_lock.emplace(session_mutex_)`). CoreML EP этот метод не переопределяет (базовый `ExecutionProvider` возвращает `true`), а внутри `model.mm` вокруг `predictionFromFeatures:` мьютекса нет. Следствие: конкурентный `Run` на одной CoreML-сессии формально разрешён ORT, но Apple требует «один поток на MLModel» — это **риск, требующий измерения**, а не документированная гарантия.
Permalinks: https://github.com/microsoft/onnxruntime/blob/v1.24.2/onnxruntime/core/session/inference_session.cc#L848-L852 , .../inference_session.cc#L3061-L3067 , https://github.com/microsoft/onnxruntime/blob/v1.24.2/include/onnxruntime/core/framework/execution_provider.h#L383-L385 , https://github.com/microsoft/onnxruntime/blob/v1.24.2/onnxruntime/core/providers/coreml/model/model.mm#L594

**Ф6.5.** Опции CoreML EP, важные для рантайма: `ModelCacheDirectory` (без кэша CoreML компилирует сабграф **каждый раз**, «may cost significant time (even minutes)»), `RequireStaticInputShapes`, `ProfileComputePlan` (логирует, на какое железо ушёл каждый оператор), `AllowLowPrecisionAccumulationOnGPU`.
Источник: https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html

### Ф7. Swift-серверные фреймворки и WebSocket

**Ф7.1.** Hummingbird 2.28.0 (тег 2.28.0), swift-tools 6.2, «lightweight… on top of a SwiftNIO based server implementation», SSWG-incubated. `Application(router:server:configuration:)` + `runService()`; в Core есть `BindAddress.unixDomainSocket(path:)`.
Permalinks: https://github.com/hummingbird-project/hummingbird/blob/4f68f1332c9f6e5a4bf7f708e232545d2004611e/Package.swift#L1-L20 , https://github.com/hummingbird-project/hummingbird/blob/4f68f1332c9f6e5a4bf7f708e232545d2004611e/Sources/HummingbirdCore/Server/BindAddress.swift#L18-L33

**Ф7.2.** WebSocket — отдельные пакеты: `hummingbird-websocket` 2.8.1 (2026-10-09) и `swift-websocket`; в speech-swift используется `HTTPServerBuilder.http1WebSocketUpgrade(configuration:)` с ручной маршрутизацией по пути и `autoPing: .disabled` (keepalive делается прикладным сообщением).
Permalinks: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L60-L72 , .../AudioServer.swift#L375-L388

**Ф7.3.** Vapor: стабильная 4.122.3 (2026-10-21), идёт 5.0.0-beta.4 (2026-10-13) — полный рерайт с structured concurrency (по анонсу Swift.org: «removing 6 years of tech debt, adding full structured concurrency support»).
Источники: https://github.com/vapor/vapor/releases ; https://www.swift.org/blog/whats-new-in-swift-september-2026/

**Ф7.4.** SwiftNIO 2.104.0 — базис и для Hummingbird, и для Vapor; «cross-platform asynchronous event-driven network application framework».
Источник: https://github.com/apple/swift-nio (тег 2.104.0)

**Ф7.5.** Swift 6.4 (2026-09-15): `Subprocess` 1.0 — «stable, cross-platform way to run and interact with other programs from Swift… built from the ground up using Swift concurrency». Это готовый инструмент для управления worker-процессами из Swift-части гибрида.
Источник: https://www.swift.org/blog/swift-6.4-released/

### Ф8. Swift concurrency: что реально даёт и чего не даёт

**Ф8.1.** `@unchecked Sendable` + NSLock — обычная практика в продакшн-коде: `InferenceGate`, `ModelState`, `InferencePermit` в speech-swift; `ModelRuntime` документирует инвариант «доступ только под permit».
Permalink: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelRuntime.swift#L1-L10

**Ф8.2.** Swift 6 strict concurrency не спасает от гонок в C++/MLX: компилятор проверяет только Swift-типы, а `MLXArray` — это ручка на небезопасный C++ объект (Ф3.3). Более того, сам speech-swift всё ещё собирается с `swift-tools-version: 5.10` (языковой режим Swift 5), а не Swift 6 mode.
Permalinks: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L1-L3 (отсутствие `swiftLanguageMode`/`StrictConcurrency` в файле проверено поиском)

**Ф8.3.** Акторы сериализуют, но не дают параллелизма: `public actor UnifiedAsrManager` — один вызов за раз; для N сессий нужно N инстансов менеджера/модели.
Permalink: https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Unified/UnifiedAsrManager.swift#L49

### Ф9. FFI Rust↔Swift и стоимость передачи аудиобуферов

**Ф9.1.** `swift-bridge` 0.1.59: генерирует Swift+C по аннотированному `#[swift_bridge::bridge] mod ffi`. Слайсы передаются как ptr+len (`FfiSlice<T>`, на Swift-стороне `UnsafeBufferPointer.toFfiSlice()`), то есть **без копирования**. `Vec<T>` на Swift-стороне — класс `RustVec<T>` с поэлементными `push/pop/get` (каждый — отдельный FFI-вызов), но есть `as_ptr()` для массового доступа; в коде генератора остаётся TODO «Return *const $ty and have that be an UnsafePointer<$ty>».
Permalinks: https://github.com/chinedufn/swift-bridge/blob/050cdb9e989b89a4068353aab525317a48ed0ad3/crates/swift-bridge-build/src/generate_core/rust_vec.swift#L1-L60 , https://github.com/chinedufn/swift-bridge/blob/050cdb9e989b89a4068353aab525317a48ed0ad3/src/lib.rs#L27-L55

**Ф9.2.** Активность `swift-bridge` низкая: 12 коммитов за последние 12 месяцев (413 всего), но поддержка Swift 6 для async-кодогенерации добавлена (коммит `ea55b2a`, 2026-01-06, #362).
Permalink: https://github.com/chinedufn/swift-bridge/blob/050cdb9e989b89a4068353aab525317a48ed0ad3/Cargo.toml

**Ф9.3.** `UniFFI` (Mozilla) — 269 коммитов за 12 месяцев, тег v0.32.2; генерирует Swift/Kotlin/Python/Ruby. Zero-copy только для байтов: `&[u8]` → на Swift `Data` внутри `Data.withUnsafeBytes`, «no copy is made… useful for large payloads (images, network frames, audio buffers)»; ограничения — только аргумент, только foreign→Rust, нельзя вложить в record/Option. Для `Vec<f32>` (аудио как float) копирование неизбежно.
Permalinks: https://github.com/mozilla/uniffi-rs/blob/c31f4bc025c7a31eae56b49107f50e6d38e76bc7/docs/manual/src/types/bytes.md#L1-L45 , https://github.com/mozilla/uniffi-rs/blob/c31f4bc025c7a31eae56b49107f50e6d38e76bc7/docs/manual/src/internals/async-overview.md#L1-L30

**Ф9.4.** Async в UniFFI не приносит свой Rust-рантайм: «UniFFI piggybacks off the runtime from the foreign bindings… generated Rust code schedules work using callbacks provided by the foreign bindings» — Swift `async/await` тянет Rust-future поллингом. Практический плюс для in-process гибрида: не нужен tokio; минус: прогресс future зависит от поллинга с чужой стороны.
Permalink: https://github.com/mozilla/uniffi-rs/blob/c31f4bc025c7a31eae56b49107f50e6d38e76bc7/docs/manual/src/internals/async-overview.md#L5-L15

**Ф9.5.** Измерено здесь (микробенчмарк, не рантайм-бенчмарк): Rust `cdylib` + Swift-вызов через `dlsym`/`@convention(c)`, буфер `float32[16000]` (64 KiB = 1 с 16 кГц):

| Операция | Время |
|---|---:|
| пустой FFI-вызов (`rb_nop`) | 1–2 нс |
| передача слайса без копии + сумма 16k float | 7.5–9.0 µs |
| то же с копированием в `Vec<f32>` | 8.4–9.0 µs |
| `base64` encode+decode 64 KiB (Swift Foundation) | 33 µs |
| `base64` + `JSONSerialization` encode+parse 64 KiB | **1.06 мс** |
Вывод: сам FFI-переход стоит единицы наносекунд; доминирует работа с данными. Копия 64 KiB добавляется к сумме в пределах шума (<1 µs). Масштабирование линейное (6.4 KiB → 106 µs для JSON-пути).

### Ф10. IPC для гибрида

**Ф10.1.** Измерено здесь: round-trip через Unix domain socket (Rust-эхо-сервер, 4-байтовый length-prefix, `send`/`recv` циклами, `Data`-аллокация на приём):

| Полезная нагрузка | Round-trip | Агрегат (в обе стороны) |
|---|---:|---:|
| 640 B (10 мс @16 кГц f32) | 7.2 µs | 178 МБ/с |
| 6.4 KB (100 мс) | 8.8 µs | 1.46 ГБ/с |
| 64 KB (1 с) | 80 µs | 1.60 ГБ/с |
Фиксированная цена сообщения ≈ 7 µs; дальше — полоса ~1.6 ГБ/с. Для потокового ASR чанками 100 мс это 0.009 % реального времени на процесс.

**Ф10.2.** Для сравнения: JSON+base64 через WebSocket (путь speech-swift, Ф5.3) — 1.06 мс на 64 KiB, то есть ~120× дороже UDS при том же объёме. Стоимость «человекочитаемого» протокола доминирует над стоимостью IPC.

**Ф10.3.** XPC из Rust: заголовки `xpc/*.h` есть в SDK (`xpc_connection_create_mach_service` объявлен в `$SDK/usr/include/xpc/connection.h`), то есть технически доступен через `extern "C"`+bindgen. Но готовых крейтов мало: `dfrankland/xpc-connection-rs` — 41 звезда, последний push 2024-08-09.
Источники: локальная проверка `$SDK/usr/include/xpc/connection.h`; https://github.com/dfrankland/xpc-connection-rs

**Ф10.4.** Swift-сторона IPC: Hummingbird умеет `unixDomainSocket(path:)` (Ф7.1), плюс `Network.framework`/`NWListener`, плюс `Subprocess` 1.0 для запуска и стриминга дочерних процессов (Ф7.5).

**Ф10.5.** Прецедент архитектуры «control plane + процесс на модель»: Ollama. Планировщик (`Scheduler.getRunner`, `processPending`, `MaxRunners`) держит загруженные модели, а каждая модель живёт в **отдельном процессе** `llama-server`, которому выделяется свободный TCP-порт и с которым общаются по HTTP; `OLLAMA_KEEP_ALIVE` (по умолчанию «5m») выгружает модель по простою.
Permalinks: https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/llm/llama_server.go#L365-L390 , .../llm/llama_server.go#L447 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L212-L260 , .../server/sched.go#L269 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/envconfig/config.go#L126-L135

### Ф11. Общая память весов между клиентами

**Ф11.1.** MLX **не** использует mmap для safetensors (проверено в v0.32.4: в публичном API загрузки `mlx/io.h` нет параметра mmap/lazy, `MmapReader` отсутствует): `SafetensorsLoad load_safetensors(...)` читает через `ParallelFileReader`, у которого `read()` вызывает `::read(fd_, data, n)` в буфер, предоставленный вызывающим (для Metal — в новый `MTLBuffer`). Значит, два процесса, загрузившие одну модель, получают две независимые копии; разделение страниц ОС не работает.
Permalinks: https://github.com/ml-explore/mlx/blob/v0.32.4/mlx/io/load.h#L63-L67 , https://github.com/ml-explore/mlx/blob/v0.32.4/mlx/io/load.cpp#L401-L410 , https://github.com/ml-explore/mlx/blob/v0.32.4/mlx/io/safetensors.cpp#L216-L218

**Ф11.2.** Следствие для цели «переиспользование загруженных весов между клиентами»: это достижимо **только внутри одного процесса** (один инстанс модели, много сессий) или требует собственного менеджера общей памяти/IOSurface — готового решения в MLX/CoreML нет. Для CoreML множитель памяти на N инстансов одной модели не документирован (см. «Неизвестные»).

### Ф12. Прецедент «одна модель — много сессий» в C++: sherpa-onnx

**Ф12.1.** sherpa-onnx v2.0.0 (C++ core) предоставляет C API (`sherpa-onnx/c-api/c-api.h`), C++ API, Rust-примеры (`rust-api-examples`), Swift Package с готовыми xcframework (static/shared, macOS/iOS) и зависит от `onnxruntime-libs` 1.28.2.
Permalinks: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L1-L40

**Ф12.2.** Модель конкурентности sherpa-onnx — ровно та, что нужна рантайму: `OnlineRecognizer` общий, `CreateStream()` на сессию, `DecodeStreams(OnlineStream **ss, int32_t n)` — пакетное декодирование нескольких потоков за вызов (внутренний батчинг вместо глобального мьютекса).
Permalinks: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer.h#L174-L230

**Ф12.3.** Важное ограничение: в статических сборках ONNX Runtime на macOS CoreML **отключён** — `add_definitions(-DSHERPA_ONNX_DISABLE_COREML)`.
Permalink: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/cmake/onnxruntime-osx-arm64-static.cmake#L60-L61

### Ф13. Rust-ML-фреймворки: candle и burn

**Ф13.1.** candle-core 0.11.0 (2026-06-26), 11M загрузок; Metal-бэкенд — `candle-metal-kernels` (0.11.0), в `candle-transformers` есть `whisper` и `voxtral` (то есть аудио-модели реально портированы), но нет Parakeet/NeMo-семейства.
Permalinks: https://github.com/huggingface/candle/blob/12bb5d1f6350f4ea9827d9086f213feec1f0af6a/Cargo.toml#L18-L42 ; локально: `candle-transformers/src/models/{whisper,voxtral}`

**Ф13.2.** Потокобезопасность candle-Metal: `Commands` не `Send`/`Sync`; шаринг тензора между потоками приводил к `failed assertion 'A command encoder is already encoding to this command buffer'` (issue #2637). Исправление — пул command buffer'ов на поток (PR #3175, смержен 2025-11, «reduces contention by avoiding a single global lock on encoder acquisition»).
Источники: https://github.com/huggingface/candle/issues/2637 ; https://github.com/huggingface/candle/issues/3175

**Ф13.3.** burn 0.22.1 (2026-10-20), MSRV 1.95, 1.9M загрузок; Metal — через CubeCL: `crates/burn-wgpu/Cargo.toml` фича `metal = ["cubecl/metal", "burn-backend/cubecl-metal"]`. То есть Metal-путь burn — это wgpu/CubeCL, а не прямой Metal, и «свой» CoreML-бэкенда отсутствует.
Permalinks: https://github.com/tracel-ai/burn/blob/f0d2b08811fbcdfe877317e19652ce5fdd6d7a25/crates/burn-wgpu/Cargo.toml#L38 ; https://github.com/tracel-ai/burn/blob/f0d2b08811fbcdfe877317e19652ce5fdd6d7a25/README.md#L50-L70

**Ф13.4.** Ни candle, ни burn не умеют CoreML/ANE. Значит, в варианте «всё на Rust» ANE доступен только через `ort` CoreML EP или через `objc2-core-ml` вручную.

### Ф14. Простой, память, запуск (измерено здесь)

**Ф14.1.** Базовая цена пустого процесса: тривиальная программа на Rust — 468 KiB бинарь, 2.0 MB max RSS; на Swift — 51 KiB бинарь (Swift-рантайм в ОС), 6.1 MB max RSS (с `import Foundation`). Это не сервер, а нижняя граница: и Swift, и Rust дают «почти нулевой» простой, пока модель не загружена.

**Ф14.2.** Реальный простой определяется загруженной моделью и кэшами: MLX держит освобождённые Metal-буферы (Ф5.8), CoreML держит скомпилированный граф и scratch-память (Ф5.7). Отсюда практическое правило: «минимальные накладные расходы в простое» = ленивая загрузка + выгрузка по таймауту (`keep_alive`, как в Ollama, Ф10.5) + возможность убить процесс-воркер, чтобы гарантированно вернуть память ОС.

### Ф15. Сборка, подпись, распространение

**Ф15.1.** Notarization (обязательна для распространения Developer ID вне App Store с macOS 10.15): нужен Developer ID-сертификат, Hardened Runtime для **всех** исполняемых целей, secure timestamp, корректные entitlements; «Enable code-signing for all of the executables you distribute, and ensure that executables have valid code signatures».
Источник: https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution

**Ф15.2.** Практическое следствие для гибрида: Rust-воркер — отдельный Mach-O executable (или dylib), он тоже должен быть подписан и включён в нотаризуемый бандл; если воркер грузит неподписанные dylib'ы (например, свой ORT), может понадобиться `com.apple.security.cs.disable-library-validation`.
Источник: там же (раздел про entitlements плагинов).

**Ф15.3.** Ни в speech-swift, ни в FluidAudio нет скриптов codesign/notarization (проверено поиском по репозиториям) — это библиотеки/CLI, а не распространяемые приложения; значит, процесс упаковки придётся строить с нуля в любом варианте.

**Ф15.4.** Homebrew как канал распространения работает для обоих стеков и проверен на практике (локальная проверка `brew info --json=v2`, 2026-10-25):
- `whisperkit-cli` 1.1.1 — «Swift native on-device speech recognition with Whisper for Apple Silicon», bottle `arm64_golden_gate`, **без зависимостей** (Swift-рантайм даёт ОС);
- `whisper.cpp` 1.9.5 — bottle `arm64_golden_gate`, зависимости `ggml`, `llama.cpp`, `sdl2-compat`, build-dep `cmake`.
Плюс `brew services` управляет фоновыми сервисами через `launchctl` (`~/Library/LaunchAgents` для пользовательских, `/Library/LaunchDaemons` с sudo) — то есть запуск демона-рантайма как LaunchAgent укладывается в стандартный путь Homebrew.
Следствие: «Swift-бинарь нельзя распространять пакетным менеджером» — неверно; Homebrew одинаково обслуживает оба варианта, разница только в размере артефакта и наборе зависимостей (Ф14.1).

### Ф16. Кроссплатформенность (Linux)

**Ф16.1.** CoreML — только Apple (Ф1.5). MLX собирается и на Linux: в mlx-swift `Package.swift` есть ветка `#if os(Linux)` с CUDA-плагинами сборки (`CudaBuild`, `encuda`), то есть Swift-обёртка MLX формально кроссплатформенна, но без CoreML.
Permalink: https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Package.swift#L7-L40

**Ф16.2.** Swift на Linux существует (Swift 6.4: Swift Build как дефолт в SwiftPM «so your projects build the same way on Linux, macOS, and Windows»), но экосистема речевых моделей (WhisperKit, FluidAudio, speech-swift) завязана на CoreML/MLX/Apple-фреймворки.
Источник: https://www.swift.org/blog/swift-6.4-released/

**Ф16.3.** Rust-стек переносим по построению: `ort` (Linux/CUDA), sherpa-onnx (Linux/Android), candle (CUDA/Metal), `mlx-rs` (Linux/CUDA через mlx). Для «опции будущего» Rust выигрывает, Swift — нет.

---

## Ограничения и риски

1. **Сравнение бенчмарков между вариантами невозможно без собственных измерений.** Найденные цифры (Ф5.4, Ф5.5) относятся к разным моделям, разным датасетам и разным машинам; использовать их как «Swift быстрее/медленнее Rust» нельзя. Единственное, что они доказывают: движок и формат модели важнее языка.
2. **Микробенчмарки из Ф9.5/Ф10.1 — не рантайм-бенчмарк.** Они измеряют конкретную наивную реализацию (циклы `send`/`recv`, `Data`-аллокации, `NSLock`-free путь) на одной машине (M5 Pro, macOS 27). Порядок величин устойчив, абсолютные значения могут отличаться в 1.5–2× на другой реализации (например, `sendmsg` с `writev`, ring-buffer, `io_uring`-аналогов на macOS нет).
3. **Версии движутся быстро.** MLX-потокобезопасность появилась в 0.31.2 (2026-04), mlx-swift перешёл на Task-scoped стримы в 0.32.2, mlx-rs переписывает API (charter, 0.32.0), `ort` в rc, Vapor 5 в бете. Любой вывод «X не потокобезопасен» может устареть за квартал — нужен регрессионный тест, а не вера в документацию.
4. **Риск `mlx-rs`:** это не тонкая обёртка, а переосмысление API (623 функции, свой дизайн ошибок, `_device`-твины под депрекацией). Догоняющая миграция при каждом бампе MLX — постоянная работа; для рантайма это означает, что MLX-адаптер на Rust будет отставать от MLX-адаптера на Swift.
5. **Риск `swift-bridge`:** 12 коммитов за год; 33 контрибьютора, но 334 коммита из ~413 — у одного автора (chinedufn); TODO в кодогенерации `Vec` (Ф9.1–Ф9.2). Для ABI, который должен жить годами, лучше C ABI + собственный тонкий заголовок, чем зависимость от кодогенератора с низкой активностью.
6. **CoreML и многопоточность:** документированное правило «один поток на MLModel» + отсутствие мьютекса в CoreML EP ORT (Ф6.4) = конкурентный `Run` на одной сессии может как работать, так и падать. Это надо проверять стресс-тестом, а не предполагать.
7. **Память:** N инстансов одной CoreML-модели — множитель неизвестен; MLX не шарит веса между процессами (Ф11.1); кэш Metal-буферов не возвращается ОС мгновенно (Ф5.8). Всё это прямо влияет на «одновременные независимые сессии».
8. **Привязка к Apple.** Любой вариант, где data plane — Swift+CoreML/MLX, не переносится на Linux без потери главных движков; вариант «всё на Rust» переносим, но теряет ANE-оптимизированные Swift-модели (Ф13.4, Ф16).
9. **Notarization/подпись** — организационный, а не технический риск: сертификаты, CI, подпись каждого артефакта (Ф15).

---

## Выводы для Runtime

### Таблица критериев × 3 варианта

Обозначения: **A** — полностью Swift (один процесс, Hummingbird/акторы), **B** — Rust-сервис с нативными адаптерами (CoreML через `objc2-core-ml`, MLX через `mlx-rs`, ORT через `ort`), **C** — гибрид (Rust control plane + Swift worker-процессы, UDS).

| № | Критерий | A: полностью Swift | B: Rust-сервис | C: гибрид |
|---|---|---|---|---|
| 1 | CoreML/ANE | Нативно, самый короткий путь: `MLModel`/`MLTensor`, async API, готовые движки (Ф5.5) | Только сырой `objc2-core-ml` (`unsafe` ObjC, без `MLTensor`/async) или сторонняя обёртка на 16 звёзд (Ф1.1–Ф1.3) | Swift-воркер = нативный CoreML; Rust-часть про CoreML не знает (Ф1, Ф5) |
| 2 | MLX | `mlx-swift` 0.32.x + `mlx-swift-lm` — эталонная реализация, Task-scoped стримы (Ф3.5) | `mlx-rs` 0.32.0 — полное покрытие ops, но своя переделка API и догоняющая миграция (Ф2.1–Ф2.2) | Swift-воркер, вариант A внутри воркера (Ф3) |
| 3 | ONNX Runtime | Официальный SPM, но ObjC-обёртка и ORT 1.24.2 (отстаёт) (Ф6.3) | `ort` 2.0-rc.15 на ORT 1.31, типизированный CoreML EP (Ф6.1–Ф6.2) | Rust-воркер для ONNX-движков; Swift-воркер — только если нужен ObjC-путь (Ф6) |
| 4 | Покрытие речевых движков «из коробки» | Максимальное: WhisperKit, FluidAudio (Parakeet, диаризация, VAD, эмбеддинги), speech-swift (Qwen3, Kokoro, CosyVoice…) (Ф5.4–Ф5.6) | Минимальное: whisper/voxtral в candle, sherpa-onnx через C API; Parakeet/Qwen3-ASR/Kokoro надо портировать (Ф13.1, Ф12) | Наследует покрытие A для Swift-движков + Rust-движки добавляются независимо (Ф12, Ф13) |
| 5 | Независимые одновременные сессии | Ограничены: MLX сериализуется глобальным `evalLock`, акторы сериализуют, в speech-swift — один гейт на сервер (Ф3.1, Ф5.2, Ф8.3) | Ограничены так же: thread-local стримы, `Array` не `Sync`, известные гонки (Ф2.3–Ф2.6) | Естественно: N воркеров = N независимых очередей; изоляция по процессу (Ф10.5) |
| 6 | Переиспользование весов между клиентами | Только внутри процесса; один инстанс модели + много сессий (как `CreateStream` в sherpa) (Ф11, Ф12.2) | То же, внутри процесса (Ф11.1) | Внутри воркера — да; между воркерами — нет (Ф11.1). Управляемо: один воркер на модель |
| 7 | Загрузка/выгрузка по требованию | Есть (`ModelRuntime.load/unload`, `ModelCache`) (Ф5.1) | Есть по построению (RAII, `Drop`), плюс процесс можно убить | Сильнее всех: kill воркера гарантированно возвращает ВСЮ память, включая кэши Metal/ANE (Ф14.2) |
| 8 | Простой (idle) | Низкий: пустой процесс ~6 MB (Ф14.1); при загруженной модели — вес модели + кэши | Низкий: ~2 MB (Ф14.1); те же кэши Metal | Низкий + возможность выгрузить всё до нуля, оставив только control plane (Ф14) |
| 9 | Изоляция сбоев | Нет: падение MLX/CoreML или утечка убивает все сессии (Ф5.7, Ф3.4) | Нет | Да: падение воркера затрагивает только его сессии (Ф10.5) |
| 10 | Стоимость передачи аудио | 0 (в процессе) | 0 (в процессе) | UDS: 7–9 µs на чанк 10–100 мс; 80 µs на 1 с (Ф10.1). JSON+base64 (как сейчас) в 120× дороже (Ф10.2) |
| 11 | Потоковый ASR / WebSocket | Hummingbird 2.28 + hummingbird-websocket 2.8.1, есть работающий realtime-сервер (Ф7.1–Ф7.2) | axum/actix + tokio — зрело, но нет готового речевого WS-протокола | WS-фронт на Rust (или на Swift) + стриминг из воркера по UDS; латентность +7–9 µs на чанк (Ф10.1) |
| 12 | FFI между языками | Не нужен | Не нужен (всё внутри Rust) | Не нужен, если IPC, а не FFI. Если FFI — C ABI: 1–2 нс/вызов, слайсы без копий (Ф9.1, Ф9.5) |
| 13 | Кроссплатформенность (Linux) | Формально возможна (Swift+MLX/CUDA), практически — нет речевых движков (Ф16.1–Ф16.2) | Да по построению: ort/sherpa/candle на Linux/CUDA (Ф16.3) | Control plane переносим, data plane — по платформам: Swift-воркеры только на macOS (Ф16) |
| 14 | Сопровождение и сборка | Один язык, один SPM-пакет; но 60+ Swift-модулей в speech-swift — уже тяжело (Ф5.1) | Один язык, cargo workspace; зависимости в rc/бете (ort rc, burn pre) (Ф6.1, Ф13.3) | Два языка + IDL/протокол; зато границы явные и тестируемые по отдельности (Ф10) |
| 15 | Распространение | Один подписанный бинарь; notarization стандартная (Ф15) | То же; статический бинарь без Swift-рантайма (Ф14.1) | Подписывать и нотаризовать надо control plane + все воркеры; сложнее, но управляемо (Ф15.1–Ф15.2) |
| 16 | Риск «мёртвой зависимости» | Низкий для MLX/CoreML (Apple), средний для сторонних Swift-пакетов | Средний: `swift-bridge` (если FFI) — 12 коммитов/год; `mlx-rs` — небольшая команда (2 автора в Cargo.toml) и сторонние fork'и (Ф2.5, Ф9.2) | Сумма рисков A и B, но каждый компонент можно заменить, не ломая API (Ф10) |

### Предварительная рекомендация: **C — гибрид, «тонкий»**

Формулировка, которую я считаю защищаемой:

- **Control plane — Rust** (один бинарь, без Swift-рантайма): каталог и версии моделей, скачивание с проверкой хэшей, `ModelDescriptor`-подобные манифесты, роутинг `session → worker`, admission/backpressure, keep-alive/выгрузка, метрики, единый HTTP/WS API для клиентов. Обоснование: это чистая логика без ML, Rust даёт статический бинарь, `tokio`, кроссплатформенность и лучший шанс на Linux в будущем (Ф16.3), а цена — ноль ML-зависимостей.
- **Data plane — Swift worker-процессы**, по одному на «движковую семью» (MLX-воркер, CoreML-воркер, при необходимости ONNX-воркер): внутри воркера живут ровно те Swift-пакеты, которые уже решают задачу (WhisperKit, FluidAudio, speech-swift, mlx-swift-lm). Обоснование: все конкурентные преимущества Apple Silicon (ANE, MLX, готовые модели) доступны только из Swift, а `objc2-core-ml`/`mlx-rs` заставят переписывать модели (Ф1, Ф2, Ф13.4).
- **Связь — UDS с бинарным фреймингом** (длина + тип + payload, `float32`/`int16` без base64): измеренная цена 7–9 µs на чанк 10–100 мс делает IPC нерелевантным по латентности (Ф10.1), а JSON+base64 — наоборот, самой дорогой частью текущего Swift-сервера (Ф10.2).
- **Изоляция и память** — главный аргумент за процессы, а не за язык: MLX не делит веса между процессами и не отдаёт кэш ОС сразу (Ф5.8, Ф11.1), а CoreML требует один поток на инстанс (Ф4.1). Процесс-воркер — единственный способ гарантированно освободить память и пережить падение движка; это ровно архитектура Ollama (Ф10.5).

### Условия, при которых рекомендация меняется

1. **→ A (полностью Swift)**, если: (а) продукт живёт только на macOS; (б) допустимо, что все сессии делят одну очередь MLX (или модели настолько малы, что клонируются по инстансу); (в) нет требований к изоляции сбоев и к гарантированному освобождению памяти; (г) команда — Swift-only. Тогда C добавляет второй язык и протокол без выигрыша. Порог: если 95-й перцентиль очереди при пиковой нагрузке укладывается в SLA при одном гейте — гибрид не нужен.
2. **→ B (полностью Rust)**, если: (а) ANE/CoreML не нужен (например, ставка на sherpa-onnx/ORT-модели или на candle-порты); (б) нужен Linux/NVIDIA как first-class; (в) готовы портировать/поддерживать модели на Rust. Порог: если целевой набор моделей покрывается `ort` + sherpa-onnx + candle и не требует `mlx-swift-lm`/WhisperKit/FluidAudio.
3. **→ «A + изоляция» (Swift control plane + Swift воркеры)**, если команда Swift-only, но изоляция процессов нужна: это C без Rust, протокол тот же, control plane на Hummingbird. Дешевле по людям, дороже по переносимости.
4. **→ in-process FFI (без IPC)**, если появляется жёсткое требование «аудио не покидает процесс» или p99 стриминга < ~2 мс: тогда Rust-ядро как `cdylib` в Swift-процессе через C ABI (1–2 нс/вызов, слайсы без копий — Ф9.1, Ф9.5). UniFFI даёт zero-copy только для `u8` (Ф9.3), поэтому для `float32` — свой C ABI.

### Что взять / не брать

**Взять:**
- Модель «одна модель — много сессий с внутренним батчингом» из sherpa-onnx (`CreateStream` + `DecodeStreams`, Ф12.2) как эталон API адаптера.
- `InferenceGate`/`ModelRuntime`/`ModelCache` из speech-swift (Ф5.1) как эталон семантики admission, отменяемости, коалесцинга загрузок — но **на уровень воркера**, а не сервера.
- `ModelDescriptor` (`backend`, `input_sample_rate`, `streaming: buffered|incremental`) — как основу манифеста модели: клиент явно указывает модель и операцию, рантайм не угадывает.
- `keep_alive`-выгрузку (Ollama, Ф10.5) + `cacheLimit`/`clearCache` для MLX (Ф5.8) + `autoreleasepool` вокруг CoreML-вызовов (Ф5.7) как обязательные правила адаптеров.
- Task-scoped стримы mlx-swift 0.32.2+ и `MaterializedArray` для передачи результатов между акторами (Ф3.2, Ф3.5).
- `ProfileComputePlan` ORT CoreML EP и `ModelCacheDirectory` (без кэша CoreML компилирует граф каждый раз, Ф6.5).

**Не брать:**
- JSON+base64 для аудио на внутренних границах (Ф10.2) — только на внешнем WS-API, если этого требует совместимость с OpenAI Realtime.
- Глобальный `evalLock`/единый гейт на весь сервер как архитектуру (Ф3.1, Ф5.2) — это сериализация всех клиентов.
- `swift-bridge` как основу долгоживущего ABI (Ф9.2); `coreml-rs` как продакшн-зависимость (16 звёзд, Ф1.3).
- Попытку «общих весов между процессами» без собственного shm-менеджера (Ф11.1).
- Включение CoreML-«оптимизаций» без замеров: зафиксированный регресс −26 % RTFx от `MLOptimizationHints` (Ф4.3).

---

## Неизвестные / что измерить

**Не удалось подтвердить:**
1. Множитель памяти при N инстансах одной CoreML-модели (`MLModel`) на разных потоках — Apple этого не документирует. Неизвестно, шарятся ли веса между инстансами.
2. Поведение MLX при **нескольких процессах**, одновременно работающих с GPU (не потоках). Issue #349 и релиз-ноты 0.31.2 говорят о потоках; межпроцессная конкуренция за GPU не описана.
3. Актуальность гонок из `mlx-rs` #349 на MLX 0.32.x (issue не перепроверен, версия 0.30.6).
4. Реальная производительность `objc2-core-ml`-пути: ни одного публичного бенчмарка CoreML-инференса из Rust не найдено.
5. Пропускная способность Hummingbird/Vapor на 100+ одновременных WebSocket-соединениях с бинарными кадрами (публичных бенчмарков именно для WS не нашёл).
6. Насколько `swift-transformers`/`Hub` (используется в speech-swift для скачивания) пригоден как основа «менеджера моделей» с версиями/хэшами — не изучал глубоко (вне задачи).
7. XPC из Rust в проде: крейты незрелы (Ф10.3), реальных внедрений не найдено.

**Что измерить (с методикой):**

1. **FFI vs IPC под нагрузкой (не микробенчмарк).** Один и тот же ASR-движок (например, Parakeet CoreML) в трёх конфигурациях: (а) в процессе, (б) через UDS с бинарным фреймингом, (в) через UDS с JSON+base64. Метрики: p50/p95/p99 latency на чанк 100 мс, CPU %, RTF при 1/2/4/8 одновременных сессиях. Аудио: 10-минутный русско-английский микс (IT-термины), 16 кГц.
2. **Предел параллелизма MLX.** Один процесс, N независимых `Task` с разными стримами (0.32.x) vs N процессов. Метрики: суммарный RTF, p99, число падений Metal-ассертов на 10 000 запросов. Это прямой тест утверждения «MLX можно использовать из нескольких потоков» (Ф3.6).
3. **CoreML: N инстансов.** Одна модель, 1/2/4/8 инстансов на разных потоках; мерить RSS до/после, RSS после `unload`, и RTF. Ответить: линейно ли растёт память и есть ли выигрыш в throughput (ANE может сериализовать).
4. **Память при переключении моделей.** Загрузить A, выгрузить, загрузить B — мерить RSS и «возврат» памяти ОС; отдельно для MLX (кэш буферов) и CoreML (autorelease, скомпилированный граф). Критерий: сколько памяти остаётся «в заложниках» после `unload` и помогает ли kill процесса.
5. **Стоимость простоя.** RSS и CPU% процесса через 0/5/30/60 минут после последнего запроса, при `keep_alive=0/5m/∞`, для MLX- и CoreML-воркера.
6. **Стоимость загрузки.** Холодная загрузка (первый запуск, без кэшей ОС) vs тёплая, для каждой модели; отдельно — CoreML-компиляция при отсутствии `ModelCacheDirectory`.
7. **GPU-конкуренция между процессами.** Два воркера, каждый делает инференс; мерить суммарный throughput и p99 против одного воркера с двумя сессиями. Это ключевой тест гипотезы «процессы лучше потоков».
8. **Транспорт.** UDS с `sendmsg`/`writev` и кольцевым буфером против наивного `send`/`recv` (мой замер) — ожидаю улучшение в 2–3× на фиксированной цене сообщения; плюс сравнение с `mach` shared memory для больших батчей (offline ASR часовых файлов).
9. **WS-фронт.** 100/500 одновременных WS-клиентов, бинарные кадры 100 мс: p99 доставки, RSS, CPU. Сравнить Hummingbird 2.28 и axum.

---

## Источники

**Код (локальные клоны, permalink'и с SHA):**
- speech-swift @ `cbad3be29480825707890490f446b19d7288be2d` — https://github.com/soniqo/speech-swift
- FluidAudio @ `1b0ed50d686873128a4129b3510ce3914c849bab` — https://github.com/FluidInference/FluidAudio
- WhisperKit @ `9f99b5cb6a907730543823ad271b82532782a899` — https://github.com/argmaxinc/WhisperKit
- sherpa-onnx @ `532f1b5a8a207bf53b116b747207c1506696c843` — https://github.com/k2-fsa/sherpa-onnx
- mlx-rs @ `a5205a0039e243fdf29c92b10489b57df05c1028` — https://github.com/oxiglade/mlx-rs
- mlx-swift @ `5f8f4d11779cf403a690373cea4965a1178c96a2` — https://github.com/ml-explore/mlx-swift
- ort @ `4fac49ba41dc90313585e09d01e84fb43dabc8e4` — https://github.com/pykeio/ort
- onnxruntime-swift-package-manager @ `b7fb7f7dea8a2469e6335d95a61b8f36d0dc83b2` — https://github.com/microsoft/onnxruntime-swift-package-manager
- hummingbird @ `4f68f1332c9f6e5a4bf7f708e232545d2004611e` — https://github.com/hummingbird-project/hummingbird
- swift-bridge @ `050cdb9e989b89a4068353aab525317a48ed0ad3` — https://github.com/chinedufn/swift-bridge
- uniffi-rs @ `c31f4bc025c7a31eae56b49107f50e6d38e76bc7` — https://github.com/mozilla/uniffi-rs
- candle @ `12bb5d1f6350f4ea9827d9086f213feec1f0af6a` — https://github.com/huggingface/candle
- burn @ `f0d2b08811fbcdfe877317e19652ce5fdd6d7a25` — https://github.com/tracel-ai/burn
- ollama @ `d44c2581904a98430e348d89cc2af1529b1808a0` — https://github.com/ollama/ollama
- swift-nio (тег 2.104.0) — https://github.com/apple/swift-nio

**Документация и обсуждения:**
- Core ML `MLModel` — https://developer.apple.com/documentation/coreml/mlmodel
- Core ML `MLComputeUnits` — https://developer.apple.com/documentation/coreml/mlcomputeunits
- Notarization — https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- ONNX Runtime CoreML EP — https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html
- MLX releases (0.31.2 thread safety) — https://github.com/ml-explore/mlx/releases/tag/v0.31.2
- MLX issue #2067 «thread issues with evaluation» — https://github.com/ml-explore/mlx/issues/2067
- mlx-swift issue #417 «Discussion: Sendable MLXArray» — https://github.com/ml-explore/mlx-swift/issues/417
- mlx-swift issue #428 «evalLock regression» — https://github.com/ml-explore/mlx-swift/issues/428
- mlx-swift issue #457 «thread-local streams break process-global Stream» — https://github.com/ml-explore/mlx-swift/issues/457
- mlx-rs issue #302 «Add Sync trait» — https://github.com/oxiglade/mlx-rs/issues/302
- mlx-rs issue #349 «Two thread-safety races» — https://github.com/oxiglade/mlx-rs/issues/349
- candle issue #2637 «Metal Tensor failed assertion when shared between threads» — https://github.com/huggingface/candle/issues/2637
- candle issue #3175 «command buffer pool» — https://github.com/huggingface/candle/issues/3175
- Swift 6.4 release notes (Subprocess 1.0, Swift Build) — https://www.swift.org/blog/swift-6.4-released/
- What's new in Swift, September 2026 (Vapor 5 beta) — https://www.swift.org/blog/whats-new-in-swift-september-2026/
- Vapor releases — https://github.com/vapor/vapor/releases
- crates.io: objc2-core-ml, coreml-rs, coreml-native, mlx-rs, ort, burn, candle-core

**Собственные измерения (воспроизводимы):**
- Микробенчмарк FFI/UDS/base64: Rust `cdylib` (`rb_nop`, `rb_sum_ref`, `rb_sum_copy`) + Rust-эхо-сервер на UDS + Swift-драйвер (`swiftc -O`, `dlsym`, `@convention(c)`, `send`/`recv` циклами). Машина: M5 Pro, macOS 27.0, Swift 6.4, rustc 1.98.1. Сырые числа — в разделах Ф9.5, Ф10.1, Ф14.1.
