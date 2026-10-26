# V5. Сравнение альтернативных архитектур Speech Runtime (A–E)

Дата: 2026-10-26. Машина измерений: **Apple M5 Pro (20 ядер), 48 GiB, macOS 27.0**, Swift 6.4 (`swiftlang-6.4.0.34.1`), rustc/cargo 1.98.1, MLX (mlx-swift `0.32.3-22-g5f8f4d1`).
Обозначения: `[INFERENCE]` — мой вывод, не подтверждённый источником напрямую. Permalink'и — с зафиксированным SHA клонов из `/tmp/srr`.

Задача: сравнить A–E **независимо** от рекомендации `wave1/F-swift-vs-rust.md` (Rust control plane + Swift workers) и от `wave2/00-draft-architecture.md`.

---

## Итог

1. **Рекомендация — B: один язык (Swift), супервизор + воркер-процессы по семействам движков**, с тремя жёсткими правилами: (а) ONNX-воркер (sherpa-onnx) первым, потому что он один закрывает 4 из 5 целевых моделей и все «вторичные» операции; (б) MLX-воркер и CoreML-воркер — отдельные процессы, никогда не in-process; (в) протокол воркера — документированная точка расширения, чтобы супервизор можно было заменить, не трогая воркеры.
2. **Второй язык (Rust) для control plane не оправдан** измерением: выигрыш — **1.98 MB против 6.16 MB RSS** и ~3 мс старта (мои замеры), при цене — второй тулчейн, второй CI/подпись, дублирование типов протокола и потеря общего `Codable`-слоя с Swift-клиентами. Единственный содержательный аргумент за Rust — Linux как first-class; он относится к ~10 % кода, который за протоколом заменяем.
3. **Ключевой факт, ломающий наивную модель конкурентности:** на Apple Silicon **GPU сериализует работу между процессами**. Измерено: одинаковый matmul-нагруз 1/2/4 процесса → 0.133 / 0.257 / 0.450–0.512 мс на операцию, т.е. суммарная пропускная способность не растёт. Значит «N воркеров = N независимых очередей» (F, строка 5 таблицы) верно только про изоляцию и справедливость, но не про throughput.
4. **Вариант A (форк speech-swift) не закрывает 7 из 12 требований и архитектурно тупиковый**: один глобальный `InferenceGate` (`occupied: Bool`) на весь сервер, `ModelCache` без вытеснения, нет маршрутов vad/diarize/embeddings, Qwen3-ASR `streaming: .buffered` на обоих бэкендах. Годится только как 1–2-недельный прототип контракта.
5. **Вариант D (один процесс sherpa-onnx/ORT) — лучший MVP и худший end-state.** Он даёт лучшую модель переиспользования весов (`CreateStream` + `DecodeStreams`) и полный набор GigaAM v3 / Nemotron 3.5 / Whisper / VAD / эмбеддинги / диаризация, но на macOS **CoreML EP выключен в статических сборках** (`SHERPA_ONNX_DISABLE_COREML`), ANE/MLX недоступны, Qwen3-ASR и Voxtral Realtime отсутствуют, изоляции сбоев нет.
6. **Вариант E (LocalAI) архитектурно ближе всех к цели, но не даёт нужных моделей.** LocalAI уже реализует ровно паттерн «супервизор + бэкенды-процессы по gRPC» (`StartProcess`, `backend.proto`), с галереей моделей, watchdog-выгрузкой и `/v1/realtime` — но все бэкенды ggml/Python, без CoreML/MLX/ANE. audio.cpp — наоборот: покрытие моделей отличное (GigaAM, Nemotron 3.5, Voxtral Realtime, Qwen3-ASR, Sortformer, Silero), но Metal-only, без ANE, без эмбеддингов спикера, параллельный сервер помечен экспериментальным и Metal в нём не тестировался.
7. **Три «критичных факта» подтверждены, но третий — с существенной поправкой.** MLX действительно читает safetensors через `read()`/`pread()` в приватный буфер (mmap в MLX нет вообще); CoreML документирует «один поток на инстанс `MLModel`»; краш ASR+диаризация (`EXC_BAD_ACCESS`) реален, но закрыт как **ошибка интегратора** (конкурентные CoreML-вызовы), а не как баг библиотеки — официального фикса нет, есть правило «сериализуй».
8. **Стоимость воркера пренебрежима, но не бесплатна:** пустой Swift-процесс 6.16 MB / 3.4 мс, с линковкой MLX — 7.14 MB / 6.2 мс в простое и 18.5 MB / 17 мс после первого Metal-eval. Первый в жизни matmul на машине стоит ~0.39 с компиляции Metal-ядер (дальше кэш ОС). Для MLX-воркера на чистой машине нужен **отдельный 839 MB Metal Toolchain** (`xcodebuild -downloadComponent MetalToolchain`).
9. **Гранулярность: на семейство движков (3 воркера), не на модель.** Причины — измеренная сериализация GPU, плата за копию весов в каждый процесс (MLX) и компиляция ANE на процесс/кэш (CoreML); исключение — второй MLX-процесс под вторую крупную модель при упоре в бюджет памяти.
10. **Условия пересмотра:** Linux/NVIDIA как first-class в течение ~12 мес → C; сужение продукта до ONNX-моделей → D; приоритет time-to-market с согласием на ggml/Metal-качество → E; «нужно вчера и только для демо» → A.

---

## Проверенные утверждения

| # | Утверждение | Источник-отчёт | Вердикт | Доказательство |
|---|---|---|---|---|
| 1 | MLX читает safetensors через `read()`, не через `mmap` | F (Ф11.1), драфт | **ПОДТВЕРЖДЕНО** | `load_safetensors(file)` создаёт `io::ParallelFileReader` → https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/io/safetensors.cpp#L216-L217 ; reader = `open(O_RDONLY)` + `::read`/`pread` батчами по 32 MiB → `.../mlx/io/load.h#L59-L105`; `grep -rn mmap` по всему дереву mlx (HEAD `8bbef759` и пиннутый `1f8e74e3`) — **ни одного совпадения** в C++/Metal (единственное упоминание — python-тест zero-copy про `asarray`) |
| 2 | Следствие: веса не делятся между процессами | F (Ф11.1) | **ПОДТВЕРЖДЕНО (с уточнением)** | `Load::eval_cpu` делает `out.set_data(allocator::malloc(out.nbytes()))` и читает файл в этот приватный буфер → https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/backend/common/load.cpp#L30-L38 . Уточнение: MLX умеет принимать внешний буфер без копии (`array(void* data, …, deleter)` → `.../mlx/array.h#L60-L69`), т.е. shm+adopt теоретически возможен, но (а) требует своего загрузчика, (б) не отменяет копию в Metal-буфер на первом GPU-вычислении `[INFERENCE]` |
| 3 | CoreML: один поток на инстанс `MLModel` | F (Ф4.1) | **ПОДТВЕРЖДЕНО** | Дословно в документации Apple: «Use an `MLModel` instance on one thread or one dispatch queue at a time. Do this by either serializing method calls to the model, or by creating a separate model instance for each thread and dispatch queue» — https://developer.apple.com/documentation/coreml/mlmodel (текст получен из `developer.apple.com/tutorials/data/documentation/coreml/mlmodel.json`) |
| 4 | Конкурентные CoreML ASR+диаризация → `EXC_BAD_ACCESS` | J (D1) | **ПОДТВЕРЖДЕНО как крэш; ЧАСТИЧНО по причине** | FluidAudio issue #661, создан 2026-06-05, **закрыт 2026-06-05** (в тот же день): «this traces to concurrent Core ML calls in our own integration… We're serializing that on our side». Официального фикса в библиотеке нет; просьба автора issue — добавить caveat в `API.md` — осталась просьбой. https://github.com/FluidInference/FluidAudio/issues/661 |
| 5 | speech-swift: один глобальный гейт инференса на весь сервер | F (Ф5.2), B1 | **ПОДТВЕРЖДЕНО** | `InferenceGate` держит `private var occupied = false` — ровно один permit на инстанс: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L1-L20 ; один экземпляр `ModelState(inferenceGate:)` обслуживает все маршруты: `.../Sources/AudioServer/AudioServer.swift#L539-L542`; документация: «The audio server shares one bounded inference lane across native HTTP, [OI] audio routes and realtime WebSocket operations» — `.../docs/inference/model-runtime.md#L63-L70` |
| 6 | speech-swift: нет маршрутов vad/diarize/embeddings, нет auth | B1 | **ПОДТВЕРЖДЕНО** | Полный список маршрутов: `/health`, `/v1/models`, `/v1/realtime/models`, `/v1/audio/transcriptions`, `/v1/audio/speech`, `/transcribe`, `/speak`, `/respond`, `/enhance` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L96-L152 . Маршрутов VAD/диаризации/эмбеддингов и middleware авторизации в файле нет |
| 7 | speech-swift: `ModelCache` без выгрузки/вытеснения | B1 | **ПОДТВЕРЖДЕНО** | В `ModelCache` только `load(key:loader:)` с коалесингом; нет `remove`/`evict`/TTL: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelCache.swift#L1-L29 . `unload()` есть только на уровне `ModelRuntime` (`Sources/SpeechRuntime/ModelRuntime.swift#L35`) |
| 8 | Qwen3-ASR в speech-swift не потоковый | B1/B2 | **ПОДТВЕРЖДЕНО** | Оба дескриптора `streaming: .buffered`: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift#L14-L18 ; словарь: «`buffered` denotes complete-utterance output» (`docs/inference/model-runtime.md#L60-L61`) |
| 9 | sherpa-onnx: CoreML EP выключен в macOS-сборках | A1 (п.7) | **ПОДТВЕРЖДЕНО** | `cmake/onnxruntime-osx-arm64-static.cmake#L61-L62`: `# disable coreml when using static onnxruntime lib` + `add_definitions(-DSHERPA_ONNX_DISABLE_COREML)` (то же для x86_64 и universal) — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/cmake/onnxruntime-osx-arm64-static.cmake#L61-L62 . Опции включить CoreML EP в CMake нет (grep `COREML` по CMake-файлам даёт только эти три `add_definitions`) |
| 10 | sherpa-onnx поддерживает GigaAM | A1, D1 | **ПОДТВЕРЖДЕНО** | Спец-обработка `IsGigaAM()` в NeMo CTC и трансдьюсере: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-nemo-enc-dec-ctc-model.cc#L91 , `.../offline-transducer-nemo-model.cc#L167` |
| 11 | sherpa-onnx поддерживает Nemotron 3.5 streaming (мультиязычный, ru) | D2, драфт | **ПОДТВЕРЖДЕНО** | CHANGELOG: «Add multilingual Nemotron-3.5 streaming ASR support (#3671)», «Re-export nemotron-3.5-asr-streaming-0.6b to ONNX (#3732)» — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L182 , `#L116-L117` |
| 12 | FluidAudio Nemotron streaming = только английский | — (моя проверка) | **ПОДТВЕРЖДЕНО** | Имена артефактов: `FluidInference/nemotron-speech-streaming-en-0.6b-coreml/{560,1120,2240}ms` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ModelNames.swift#L50-L52 . Мультиязычного (ru) Nemotron в CoreML-каталоге FluidAudio нет → CoreML-путь не закрывает требование «ru streaming Nemotron 3.5» |
| 13 | «N воркеров = N независимых очередей» (конкурентность) | F (таблица, строка 5) | **ОПРОВЕРГНУТО в части throughput** | Моё измерение (см. §4): 200 matmul 1024×1024 на процесс, 1/2/4 процесса → 0.133 / 0.257 / 0.450–0.512 мс на операцию. Изоляция и справедливость — да; рост суммарной пропускной способности — нет |
| 14 | Пустой процесс: ~6 MB Swift, ~2 MB Rust | F (Ф14.1) | **ПОДТВЕРЖДЕНО** | Мои замеры: Swift `-O` noop 6 160 384 B RSS / 50 202 B бинарь; Rust release 1 982 464 B RSS / 420.7 KB бинарь. Разница 4.2 MB — на фоне весов моделей (сотни MB) нерелевантна |
| 15 | LocalAI: бэкенды — отдельные процессы по gRPC | — (моя проверка) | **ПОДТВЕРЖДЕНО** | `StartProcess(grpcProcess, id, serverAddress, envVars)` — https://github.com/mudler/LocalAI/blob/f7011f18702f107a5c24e53ab0914b37523215b0/pkg/model/process.go#L243-L246 ; контракт: `service Backend { rpc LoadModel…, rpc AudioTranscription…, rpc AudioTranscriptionLive(stream)… }` — `.../backend/backend.proto#L10-L36` |
| 16 | audio.cpp: параллельный сервер экспериментальный, Metal не тестировался | — (моя проверка) | **ПОДТВЕРЖДЕНО** | «Concurrent inference | Partially tested | … **No** catalogue multi-slot admission, real concurrent streams/batches or **Metal checkpoint execution**» — https://github.com/0xShug0/audio.cpp/blob/1cada9b7045cc6935bcafb3f5a26806d133535e7/docs/maintainers/parallel_server_behavior_contract.md#L169 |
| 17 | sherpa-onnx распространяется как Swift Package с prebuilt macOS xcframework | F (Ф12) | **ПОДТВЕРЖДЕНО** | `Package.swift`: `binaryTarget SherpaOnnxMacOS → sherpa-onnx-v2.0.0-macos-static.xcframework.zip`, зависимость `onnxruntime-libs 1.28.2` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L1-L40 |
| 18 | audio.cpp покрывает GigaAM / Qwen3-ASR / Voxtral Realtime / Nemotron / Sortformer / Silero | — (моя проверка) | **ПОДТВЕРЖДЕНО** | `docs/models/gigaam_asr.md`; `docs/asr.md#L27` (`qwen3_asr`, offline+streaming); `docs/models/voxtral_realtime.md#L1-L20` (GGUF, `offline`/`streaming`); `docs/speech_analysis.md#L16-L18` (Silero VAD, Sortformer diar v2.1); `docs/models/nemotron_3_diar.md` |
| 19 | «MLX превращает любую ошибку в `fatalError`/SIGTRAP» (падение демона) | J (D3) | **ПОДТВЕРЖДЕНО (с уточнением механизма)** | Дефолтный обработчик MLX C — `printf` + `exit(-1)`: «MLX error: %s…exit(-1)» — https://github.com/ml-explore/mlx-c/blob/ebc88f10caa1b625e6b581437a8dea6df8a70085/mlx/c/error.cpp#L12-L15 . mlx-swift перехватывает его только внутри `withError`/`withErrorHandler`: «The default error handler will simply print out the error, then exit» — https://github.com/ml-explore/mlx-swift/blob/5f8f4d11779cf403a690373cea4965a1178c96a2/Source/MLX/ErrorHandler.swift#L3-L6 . Практический вывод: любой необёрнутый вызов убивает **процесс**, но ошибку можно превратить в `throws` — значит воркер обязан оборачивать инференс в `withError` `[INFERENCE]` |

---

## 1. Как считались «12 требований»

Канонического списка в репозитории нет; беру декомпозицию из `wave2/V7-critique.md` §1 (она совпадает с постановкой задачи) и фиксирую её здесь, чтобы матрица была проверяемой:

| # | Требование |
|---|---|
| 1 | Один локальный сервис (демон, а не «приложение + headless») |
| 2 | Общий каталог моделей для всех клиентов |
| 3 | Единый стабильный API для независимых приложений (версия протокола, auth, UDS/TCP) |
| 4 | Ленивая загрузка моделей |
| 5 | Переиспользование весов между клиентами |
| 6 | Одновременные независимые сессии |
| 7 | Потоковый **и** пакетный ASR |
| 8 | VAD |
| 9 | Диаризация |
| 10 | Голосовые эмбеддинги |
| 11 | Несколько движков через адаптеры (CoreML/MLX/ONNX) |
| 12 | install/remove/update/версии моделей |
| NFR | Минимальные накладные расходы (простой, per-chunk) |
| C | Runtime не выбирает модель по смыслу |

---

## 2. Матрица: альтернативы × критерии

Обозначения: **✓** — закрыто по существу; **~** — частично/с оговоркой; **✗** — не закрыто; **×** — архитектурно исключено.

| Критерий | A: форк speech-swift (in-process) | B: Swift-супервизор + Swift-воркеры | C: Rust-супервизор + Swift/Rust-воркеры | D: один процесс sherpa-onnx/ORT | E: надстройка над LocalAI / audio.cpp |
|---|---|---|---|---|---|
| **Покрытие 12 требований** | **~4.5/12**: 1✓ 2~ 3~ 4✓ 5✓ 6✗ 7✗ 8✗ 9✗ 10✗ 11✓ 12✗ | **12/12** достижимо; на старте 8–9 (без 9/10/12 до 4-й фазы) | **12/12** (то же, что B) | **10 полных + 2 частичных**: 1✓ 2✓ 3~ (свой API поверх sherpa) 4✓ 5✓ 6✓ 7✓ 8✓ 9✓ 10✓ 11~ (только ONNX) 12✓ | LocalAI **~9/12**: 1✓ 2✓ 3~ (контракт [OI], не наш) 4✓ 5✓ 6✓ 7✓ 8✓ 9~ (зависит от бэкенда) 10✗ (текстовые эмбеддинги) 11✗ (нет CoreML/MLX) 12✓; audio.cpp **~8/12**: нет 10, нет версий/отката, параллельность экспериментальная, нет выбора движка |
| **Доступ к 5 целевым моделям + лучший бэкенд** | Qwen3-ASR ✓ (MLX 0.0138 RTF / CoreML), Parakeet ✓; **GigaAM ✗, Nemotron 3.5 ru ✗, Whisper ✗, Voxtral ASR ✗** | все 5: GigaAM v3 (ONNX, `IsGigaAM`), Nemotron 3.5 (ONNX, CHANGELOG #3671), Whisper turbo (ONNX/WhisperKit-CoreML), Voxtral Realtime (MLX-порт или ONNX), Qwen3-ASR (MLX/CoreML) | то же, что B | GigaAM ✓, Nemotron 3.5 ✓, Whisper ✓, Parakeet ✓; **Qwen3-ASR ✗, Voxtral ✗**; бэкенд только CPU-ONNX (CoreML EP выключен) → Whisper turbo RTF≈1.02 на CPU (J A4) | LocalAI: зависит от бэкенда (whisper.cpp/audio-cpp), GigaAM/Nemotron 3.5 — только если через audio-cpp; **ANE/MLX нет**; audio.cpp: 4 из 5 ✓, Qwen3-ASR ✓, Voxtral ✓, GigaAM ✓ — **лучшее покрытие моделей без Apple-ускорения** |
| **Изоляция сбоев** | ✗ один процесс: `fatalError` MLX или крэш CoreML убивает все сессии | ✓ процесс на семейство; падение MLX/CoreML не трогает ONNX и супервизор | ✓ (как B) | ✗ один процесс на всё | LocalAI ✓ (бэкенды-процессы, watchdog); audio.cpp ✗ (один процесс, модели внутри) |
| **Возврат памяти при выгрузке** | ~ `ModelRuntime.unload()` есть, `ModelCache` без вытеснения; кэш Metal/ANE остаётся | ✓ kill воркера возвращает всё, включая Metal/ANE-кэши; цена — повторная загрузка (для ANE — компиляция, J E5) | ✓ | ~ разрушение recognizer'а; ONNX arena и VRAM освобождаются не всегда (J G8) | LocalAI ✓ (`/backend/shutdown`, watchdog TTL); audio.cpp ~ (bulk unload есть, кэши ggml/Metal — нет гарантии) |
| **Конкурентность** | ✗ 1 инференс на сервер (измерено авторами: 4 запроса → 97/191/284/378 мс) | ✓ сессии параллельны между воркерами; внутри — по правилам движка (MLX сериализация, CoreML N инстансов, ONNX батч). **Throughput не масштабируется** (§4) | ✓ | ✓ лучшая модель: 1 веса, N стримов, `DecodeStreams`; но всё на CPU | LocalAI ✓ (процессы + gRPC); audio.cpp ~ (`--parallel-jobs` экспериментальный, Metal не тестировался) |
| **Накладные расходы в простое** | 1 процесс, ~6–10 MB + NIO | супервизор ~6–10 MB + 3 воркера по 7–18 MB (измерено) | супервизор 2 MB + те же воркеры | 1 процесс, ~6–10 MB + sherpa-модели | LocalAI: Go-супервизор + бэкенды-процессы (десятки MB); audio.cpp: 1 процесс + модели |
| **Сборка/распространение** | ✓ один тулчейн, одна подпись | ✓ один тулчейн; 1 бинарь-супервизор + 3 воркера (можно один multi-call бинарь), одна нотаризация пайплайном | ✗ два тулчейна, два CI, две подписи/нотаризации, дублирование типов протокола | ✓✓ проще всех: prebuilt xcframework, ноль C++ сборки | LocalAI: Go + OCI-образы бэкендов (нужен Docker/podman либо распаковка OCI), audio.cpp: CMake + ggml (Metal), GGUF-конвертация моделей |
| **Сопровождение 1–2 разработчиками** | ✗ форк живого репозитория на 60+ модулей с одним мейнтейнером; вся upstream-грязь ваша | ~ один язык, но 4 бинаря и свой протокол; зато общий `Codable`-слой с Swift-клиентами | ✗ два языка + IDL/кодоген + два набора зависимостей (ort rc, tokio, SwiftNIO) | ✓✓ один код, один движок, минимум решений | ✗ вы не владеете ни протоколом, ни roadmap; обновления upstream ломают контракт |
| **Расширяемость сторонними движками** | ✗ только пересборкой демона | ✓✓ протокол воркера = точка расширения (новый бинарь, ноль изменений в супервизоре) | ✓✓ то же | ✗ только модели, поддержанные sherpa | ~ LocalAI: да, через gRPC-бэкенд (можно встроить свой Swift-воркер!); audio.cpp: только C++-порт в их дерево моделей |
| **Порт на Linux** | ✗ | ✗ data plane; control plane переносим переписыванием (~10 % кода) | ✓ control plane из коробки; data plane — ONNX-воркер | ✓✓ | LocalAI ✓✓; audio.cpp ✓✓ |

---

## 3. Три критичных факта — разбор

### 3.1 «MLX читает safetensors через `read()`, не mmap → веса не делятся между процессами»

**ПОДТВЕРЖДЕНО.** Цепочка в коде (пиннутый в mlx-swift `0.32.3` коммит `1f8e74e3`):

1. `load_safetensors(const std::string& file, …)` → `std::make_shared<io::ParallelFileReader>(file)` ([safetensors.cpp#L216-L217](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/io/safetensors.cpp#L216-L217)).
2. `ParallelFileReader` — это `open(path, O_RDONLY)`, `lseek`, `::read(fd_, …)` и `pread(fd, …, offset)` батчами по `1 << 25` (32 MiB) ([load.h#L59-L105](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/io/load.h#L59-L105), [load.cpp#L351](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/io/load.cpp#L351), [#L365](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/io/load.cpp#L365)).
3. Каждый тензор становится ленивым `Load`-примитивом, а его `eval_cpu` **выделяет новую память** и читает в неё файл: `out.set_data(allocator::malloc(out.nbytes()))` ([load.cpp#L30-L38](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/backend/common/load.cpp#L30-L38)).
4. `mmap` в дереве MLX отсутствует полностью: `grep -rn mmap` по `mlx/` (C++/Metal) на HEAD `8bbef759` и на пиннутом `1f8e74e3` — ноль совпадений; единственное упоминание `mmap`-семантики — python-тест `test_zero_copy.py` про `mx.asarray(copy=False)`.

**Уточнения, которые важны для дизайна** `[INFERENCE]`:
- MLX **умеет** принять внешний буфер без копии: `array(void* data, Shape, Dtype, deleter)` — «will attempt to use the input data without a copy» ([array.h#L60-L69](https://github.com/ml-explore/mlx/blob/1f8e74e3f12f31365464a6867c6579f0e9b29d85/mlx/array.h#L60-L69)), и в C API есть `mlx_array_new_data_managed`. То есть «общие веса через shm» технически не запрещены — но требуют своего загрузчика и **не отменяют** копию в Metal-буфер на первом GPU-вычислении `[INFERENCE]`: API, который обернул бы существующий `MTLBuffer` вокруг чужой памяти, я в дереве MLX не нашёл (grep `MTLBuffer` + `wrap|external|from` — пусто).
- Практический вывод для рантайма: **не проектировать общие веса между процессами**; делить веса внутри процесса (один инстанс модели + N сессий), а между процессами — только через «один воркер на модель».

### 3.2 «CoreML `MLModel` — один поток на инстанс»

**ПОДТВЕРЖДЕНО** дословной цитатой Apple (см. таблицу, п. 3). Следствия:
- Параллелизм CoreML = **N инстансов** `MLModel`, память × N (множитель не документирован — неизвестное №1).
- Альтернатива — сериализация вызовов, что и делает FluidAudio (`actor UnifiedAsrManager`) и что делает speech-swift глобальным гейтом.
- Для рантайма это означает: в CoreML-воркере нужен **пул инстансов с явным лимитом из бюджета памяти**, а не «потокобезопасный менеджер». Пул виден в `/v1/ps` как `instances: N`.

### 3.3 «Конкурентные CoreML ASR+диаризация → `EXC_BAD_ACCESS`»

**Крэш — ПОДТВЕРЖДЕНО, причина — ЧАСТИЧНО, официального фикса нет.**
- Issue: FluidAudio #661, заголовок «Concurrent SlidingWindowAsrManager + DiarizerManager prediction corrupts shared E5RT/BNNS state (EXC_BAD_ACCESS in libBNNS) on Apple Silicon», создан 2026-06-05, воспроизводился на M1–M4 и macOS 14.6.1/15.7.4/26.3.1/26.5.0.
- Резолюция: закрыт **в тот же день** комментарием автора: «After more digging, this traces to concurrent Core ML calls in our own integration: we were running ASR re-transcription and diarization in parallel during post-session processing. We're serializing that on our side, which resolves it for us». Там же: «API.md says managers are thread-safe and can be used concurrently across queues, which is what led us to run them in parallel».
- Что это значит: это **не** доказанный баг Apple/CoreML и не баг библиотеки, а подтверждение правила 3.2 на практике: два CoreML-инстанса, дёргаемые параллельно из разных очередей, повреждают общий E5RT/BNNS-стейт. Правило для рантайма: **все CoreML-вызовы в процессе — через один последовательный исполнитель, либо через пул с пиннингом «инстанс ↔ поток/очередь»**.
- Важная деталь: оба участника крэша — **акторы** (`public actor SlidingWindowAsrManager` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/SlidingWindow/SlidingWindowAsrManager.swift#L10 , `public actor UnifiedAsrManager` — `.../ASR/Parakeet/Unified/UnifiedAsrManager.swift#L49`). То есть **actor isolation не защищает от межмодельной CoreML-конкурентности**: каждый актор сериализует свои методы, но два актора работают одновременно. Это прямой аргумент против «сделаем всё акторами и всё будет хорошо».
- Риск сохраняется: «сериализация помогла одному интегратору» ≠ «гарантия для всех моделей/чипов». Нужен стресс-тест на нашей матрице моделей (см. «Неизвестные»).

---

## 4. Мои измерения (воспроизводимо)

Каталог: `/tmp/srr/probe/V5/`. Машина: M5 Pro, 20 ядер, 48 GiB, macOS 27.0, Swift 6.4, MLX из `mlx-swift 0.32.3-22-g5f8f4d1` (сабмодуль `ml-explore/mlx@1f8e74e3`).

### 4.1 Стоимость старта процесса и память в простое

| Профиль | Бинарь | Старт (20 запусков, p50 / p90 / min) | RSS (max resident) |
|---|---|---|---|
| `swiftc -O` noop (Foundation) | 50 202 B | 3.37 / 3.87 / 2.67 мс | 6 160 384 B (**6.16 MB**) |
| + `import CoreML` | 50 488 B | 3.42 / 3.87 / 2.87 мс | 6 291 456 B (**6.29 MB**) |
| + `import MLX`, без вычислений | 25.9 MB (MLX статикой) | 6.24 / 11.77 / 5.29 мс | 7 143 424 B (**7.14 MB**) |
| + MLX, первый eval (инициализация Metal) | — | 17.02 / 23.16 / 13.82 мс | 18 497 536 B (**18.50 MB**) |
| Rust `--release`, sleep | 420.7 KB | — | 1 982 464 B (**1.98 MB**) |

Команды: `swiftc -O noop.swift -o noop`; `/usr/bin/time -l ./noop`; старт — 20× `subprocess.run` с `perf_counter` (Python, тот же хост).
Вывод: **воркер-процесс стоит 3–17 мс и 6–18 MB** — на фоне загрузки модели (сотни MB, сотни мс) это шум. Аргумент «процессы дороги» не работает; работает аргумент «процессы не дают throughput» (4.2).

### 4.2 GPU сериализует работу между процессами (ключевое)

Нагрузка: 200 итераций `matmul(1024×1024) * 0.5` с прогревом, MLX/Metal, 3 прогона на конфигурацию:

| Одновременных процессов | мс на операцию (steady) | Комментарий |
|---|---|---|
| 1 | 0.133 | базline |
| 2 | 0.257 (оба процесса) | ×1.9 задержка, throughput ≈ const |
| 4 | 0.450 / 0.484 / 0.512 / 0.470 | ×3.5 задержка, throughput ≈ const |

То есть **4 воркера не дают 4× пропускной способности** — GPU-очередь одна на устройство. Процессы дают изоляцию, справедливость и независимость состояний, но не масштабирование. Это меняет планирование admission: бюджет задаётся не «числом воркеров», а **суммарным GPU-временем** `[INFERENCE]`.

Первый в жизни matmul на машине: 0.435 с против 0.049 с в прогретом процессе → **~0.39 с компиляции Metal-ядер**, дальше кэш ОС переиспользуется между процессами (повторные холодные запуски — 0.02–0.03 с).

### 4.3 Сборка MLX-воркера на «чистой» машине

`xcrun -sdk macosx metal -c dot.metal` → `error: cannot execute tool 'metal' due to missing Metal Toolchain; use: xcodebuild -downloadComponent MetalToolchain`. Установка — **838.9 MB** (`Metal Toolchain 27A266a`). До установки `swift build` пакета с MLX падает на каждом `.metal`-файле. Это реальная строка в чек-листе сборки/CI для MLX-воркера (и причина, почему «просто Swift Package» не равно «просто собрать»).

---

## 5. Разбор альтернатив

### A. Форк speech-swift, доработка `AudioServer` in-process

Что даёт бесплатно: работающий сервер (Hummingbird + WS), `InferenceGate` с отменой и bounded-очередью, `ModelRuntime`/`ModelCache` с коалесингом загрузок, `ModelDescriptor` (backend, sample_rate, streaming), реальные MLX/CoreML реализации Qwen3-ASR и Parakeet, TTS.

Что придётся сделать самому: диаризацию, эмбеддинги, VAD-маршрут, потоковый ASR (сейчас `buffered`), GigaAM/Nemotron/Whisper/Voxtral, install/remove/update моделей, auth, per-session изоляцию, замену глобального гейта на планировщик.

Почему это тупик: чтобы получить требования 6, 9, 10, 12 и изоляцию, нужно **переписать** ровно те части, из-за которых форк и брали (сервер и модель конкурентности), оставаясь в одном процессе, где крэш MLX/CoreML убивает всё. Плюс вы наследуете форк репозитория с одним мейнтейнером.

Когда уместно: 1–2-недельный прототип, чтобы проверить контракт API на реальных клиентах, с явным решением «выбрасываем».

### B. Новый Swift-супервизор + Swift-воркеры по семействам

Закрывает всё, что закрывает C, кроме Linux-control-plane. Даёт один тулчейн, один `Codable`-слой протокола (общий с Swift-SDK клиентов), одну нотаризацию, один CI. Цена — свой протокол и 4 бинаря.

Ключевой риск, который надо принять сознательно: **данные не переносятся на Linux**, а control plane на Swift — единственное, что мешает Linux-порту. Митигация: протокол воркера как контракт → супервизор заменяем на Rust/Go за недели, воркеры остаются.

### C. Rust-супервизор + Swift-воркеры + Rust/C-воркер sherpa-onnx

Что реально выигрывает: статический бинарь без Swift-рантайма (2 MB против 6 MB), `tokio` для супервизии, Linux «из коробки», независимость control plane от Apple-тулчейна. Что реально теряет: второй язык навсегда, второй CI/подпись, дублирование типов протокола (или IDL + кодоген), невозможность отдать клиентам Swift-типы напрямую, две культуры логирования/метрик/тестов. Для команды 1–2 человека это не «+10 % работы», а постоянный налог `[INFERENCE]`.

Отдельно: «Rust/C-воркер sherpa-onnx» не даёт ничего, чего не даёт Swift-воркер — sherpa-onnx распространяется как prebuilt macOS xcframework с Swift Package ([Package.swift#L1-L40](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L1-L40)). Rust здесь оправдан только на Linux.

### D. Один процесс вокруг sherpa-onnx/ORT (CoreML EP), без MLX

Сильные стороны: лучшая модель весов (`CreateStream` + `DecodeStreams` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer.h#L206-L223 , `offline-recognizer.cc#L173-L174`), минимум движков, полный набор «вторичных» операций в C++ API (`OfflineSpeakerDiarization` — `sherpa-onnx/csrc/offline-speaker-diarization.h#L55`; `SpeakerEmbeddingExtractor` — `sherpa-onnx/csrc/speaker-embedding-extractor.h#L38`; `SileroVadModel`/`TenVadModel` — `sherpa-onnx/csrc/silero-vad-model.h`, `ten-vad-model.h`), простая сборка (prebuilt xcframework), переносимость.

Блокеры для нашей цели:
1. **CoreML EP выключен** в macOS-сборках sherpa-onnx (`SHERPA_ONNX_DISABLE_COREML`), опции включения нет → нет ANE, нет ускорения на Apple GPU через CoreML; остаётся CPU (и Metal только если ORT-сборка своя).
2. Нет Qwen3-ASR и Voxtral Realtime (это как раз «качественный» tier для смешанного ru/en).
3. Нет изоляции: один процесс на ASR+VAD+диаризацию+эмбеддинги.
4. Whisper turbo на CPU — RTF ≈ 1.02 (J A4), т.е. потоково бесполезен.

Правильная роль D: **воркер №1 внутри B** — ровно как в черновике (шаг 1: `worker-onnx` + GigaAM + Silero, batch).

### E. Надстройка над LocalAI или audio.cpp

**LocalAI** (v4.12.1, 2026-10-23; есть `darwin-arm64` ассет): архитектурно это уже «наш» паттерн — супервизор на Go, бэкенды отдельными процессами по gRPC, галерея моделей (`/models/apply`, `/models/delete/:name`, `/models/available`), выгрузка (`/backend/shutdown`, watchdog/TTL), `/vad`, `/v1/realtime`, запрос диаризации с `NumSpeakers/Min/MaxSpeakers`, `/v1/audio/transcriptions`. Формально можно встроить **свой Swift-воркер** как gRPC-бэкенд.
Против: (а) бэкенды в галерее — ggml/Python (whisper.cpp, audio-cpp, faster-whisper, parakeet-cpp), **ни одного CoreML/MLX/ANE**; (б) API — [OI]-образный, не наш контракт (нет `model@revision`, нет `stable_prefix_chars`-ревизий, нет выбора движка как оси); (в) embeddings — текстовые, не спикерные; (г) вы принимаете чужие релизы, миграции конфигов и 100+ неречевых функций; (д) бэкенды тянутся как OCI-образы (нужен container runtime либо распаковка), что для «локального демона на mac» лишний слой.

**audio.cpp** (v0.9.2, Apache-2.0, C++/ggml): лучшее покрытие моделей вне Apple-стека — GigaAM (`docs/models/gigaam_asr.md`), Qwen3-ASR (`docs/asr.md#L27`, offline+streaming), Voxtral Realtime GGUF (`docs/models/voxtral_realtime.md#L1-L20`), Nemotron 3.5, Nemotron-3-Diarization, Sortformer v2.1, Silero/FireRed VAD; сервер с `/v1/audio/transcriptions`, `/v1/audio/transcriptions/live` (chunked PCM + SSE), C API для in-process.
Против: Metal-only (ANE/MLX нет), нет эндпоинта спикерных эмбеддингов (эмбеддинг — внутренний артефакт пайплайна), параллельный сервер помечен экспериментальным и **Metal в нём не тестировался**, один процесс на всё, GGUF-конвертация каждой модели (свой риск качества/квантования), быстрый upstream с одним мейнтейнером.

Итог по E: как **MVP ради покрытия моделей** — сильнее всех; как **продукт с ANE/MLX и своим контрактом** — не подходит.

---

## 6. Оправдан ли второй язык (Rust) для control plane

| Аргумент F / черновика | Проверка | Вердикт |
|---|---|---|
| «малый RSS» | измерено: 1.98 MB (Rust) против 6.16 MB (Swift) — **разница 4.2 MB**; при загруженной модели 1–8 GB это 0.05–0.4 % | не аргумент |
| «кроссплатформенность / Linux» | data plane всё равно Apple-only; переносится только control plane | единственный настоящий аргумент, но он про ~10 % кода |
| «tokio / process supervision» | SwiftNIO + `Foundation.Process` + launchd закрывают то же; в Swift есть structured concurrency | паритет |
| «статический бинарь без Swift-рантайма» | Swift-рантайм в macOS системный (`/usr/lib/swift/*`), вендорить не нужно; бинарь 50 KB | не аргумент для macOS |
| «лучший шанс на Linux в будущем» | верно, но цену платите сразу и навсегда | отложить до решения о Linux |
| Цена | 2 тулчейна, 2 CI, 2 подписи/нотаризации, дублирование типов протокола (или IDL+кодоген), 2 набора зависимостей в движении (`ort` rc, tokio, SwiftNIO), 2 культуры тестов/метрик | высокая для 1–2 человек |

**Вывод:** для macOS-first продукта второй язык не оправдан. Правильная форма компромисса — **протокол воркера как стабильный контракт**: тогда «Rust-супервизор» — это отдельное решение, принимаемое в момент, когда Linux становится требованием, без переписывания воркеров. Это же снимает главный риск B.

Что было бы настоящим аргументом за Rust (и чего в источниках нет): измеренная неспособность Swift-супервизора держать 500+ WS-соединений с бинарными кадрами, или требование запуска без системного Swift-рантайма. Ни того, ни другого не наблюдается (F Ф7: Hummingbird 2.28 + WS 2.8.1 — зрелые).

---

## 7. Гранулярность воркеров

| Вариант | Плюсы | Минусы | Вердикт |
|---|---|---|---|
| Всё in-process (A, D) | минимум IPC, минимум памяти, проще всего | крэш движка = крэш демона; нельзя выгрузить «всё»; сторонние движки невозможны | ✗ |
| Процесс на модель | максимальная изоляция, точный возврат памяти по модели | платите копией весов в каждый процесс (MLX: `read()` в приватный буфер), компиляцией ANE на процесс/кэш, дублированием фреймворков; throughput не растёт (§4.2); N процессов на 5 моделей = 5×(7–18 MB) + 5 загрузок | ✗ как правило |
| **Процесс на семейство движков (3)** | изоляция по источнику сбоев (MLX-`fatalError`, CoreML-конкурентность, C++-ассерты sherpa); один набор фреймворков на процесс; модели внутри — refcount; выгрузка = kill воркера | падение воркера убивает сессии всех моделей этого семейства (приемлемо: они и так делят движок) | **✓ рекомендую** |
| Процесс на семейство + пул инстансов внутри CoreML-воркера | единственный способ дать CoreML-параллелизм (правило «1 поток на инстанс») | память × N (множитель неизвестен) | ✓ обязательно внутри воркера |

Дополнительно: **второй процесс MLX-воркера** имеет смысл только когда нужна вторая крупная модель и бюджет памяти не позволяет держать обе в одном процессе (веса не делятся — §3.1), либо когда нужна «горячая» изоляция длинного batch от realtime. Ожидания по throughput при этом нулевые (§4.2).

---

## 8. Рекомендация и условия пересмотра

**Рекомендация: B, с уточнениями.**

```
клиенты (Swift SDK / Rust SDK / CLI)
   │ HTTP+WS (loopback, bearer) | UDS
   ▼
speechd (Swift, Hummingbird/NIO, launchd LaunchAgent)   ← ~6–10 MB idle
   - реестр моделей (CAS + манифесты + revision pin + лицензия)
   - планировщик: refcount + TTL + бюджет GPU-времени и памяти
   - сессии, отмена, backpressure, лимиты
   - супервизор воркеров (spawn/health/restart, lease-файлы)
   │ UDS, len-prefixed header (JSON/CBOR) + raw PCM
   ▼
worker-onnx   (Swift + sherpa-onnx xcframework)  ← первый
worker-coreml (Swift + FluidAudio/speech-swift, пул инстансов, 1 очередь)
worker-mlx    (Swift + mlx-swift, сериализованный eval)
```

Обязательные правила:
1. **ONNX-воркер первым** — он один даёт GigaAM v3, Nemotron 3.5 (ru), Whisper, Silero/FireRed, спикерные эмбеддинги и диаризацию; это 4 из 5 целевых моделей и 4 требования (8, 9, 10, 12) без единой строчки ML.
2. **CoreML-воркер: один последовательный исполнитель + пул инстансов с пиннингом «инстанс ↔ поток»** (§3.2, §3.3). Никаких параллельных CoreML-вызовов из разных очередей.
3. **MLX-воркер: сериализованный eval, `withError` вокруг каждого инференса** (иначе дефолтный обработчик MLX делает `exit(-1)` и воркер падает — но, обернув, ошибку можно вернуть клиенту как событие `error`), `cacheLimit` ≤ min(2 GiB, RAM/4), kill как штатная выгрузка (§3.1: веса не делятся, значит «выгрузить» = убить).
4. **Admission по GPU-времени, а не по числу воркеров** (§4.2).
5. **Протокол воркера — версионированный публичный контракт** (handshake с версией, `capabilities`), чтобы (а) сторонние движки подключались как новый бинарь, (б) супервизор можно было заменить на Rust при переезде на Linux.
6. **Не обещать «потоковую точность»**: у CTC-emission ~330 мс (J B2), чанкование стоит ~6 pp WER (J B3) — API должен отдавать `partial` с ревизией и стабильным префиксом (как SpeechRail), а не «финал по чанку».

**Условия пересмотра:**
- **→ C (Rust control plane)**, если Linux/NVIDIA становится first-class в течение ~12 месяцев, либо если появится требование запуска супервизора без системного Swift-рантайма, либо если измеренный idle-CPU/память Swift-супервизора упрутся в бюджет (сейчас 6–10 MB, <1 % CPU — не упрутся).
- **→ D (один процесс sherpa-onnx)**, если продуктовый scope сужается до «GigaAM + Nemotron + Whisper + VAD/диаризация/эмбеддинги», а Qwen3-ASR/Voxtral не нужны: тогда D дешевле B на два воркера и переносим на Linux. Порог: p95 качества на целевом корпусе ru+IT приемлем без MLX/ANE-моделей.
- **→ E (LocalAI/audio.cpp)**, если time-to-market важнее качества на Apple Silicon и вы согласны на ggml/Metal без ANE/MLX; для LocalAI — только если готовы принять чужой контракт и чужие релизы; для audio.cpp — если готовы к GGUF-конвертации всех моделей.
- **→ A (форк speech-swift)**, только как прототип контракта ≤ 2 недель; как продукт — нет.
- **Пересмотр факта 3.3**: если появится официальный фикс/подтверждение Apple по конкурентным CoreML-инстансам, пул инстансов в CoreML-воркере можно будет расширить без пиннинга — это поднимет потолок конкурентности без новых процессов.

---

## Неизвестные / что измерить

1. **Множитель памяти при N инстансах одной CoreML-модели** (ANE/GPU) — не документирован; мерить 1/2/4/8 инстансов: RSS до/после, RTF, выигрыш throughput (ANE может сериализовать сам).
2. **Межпроцессная конкуренция за ANE**: два CoreML-воркера одновременно — растёт ли throughput или падает до одного. Аналог моего §4.2, но для ANE.
3. **Стресс-тест «ASR + диаризация параллельно»** на нашей матрице моделей/чипов (Mac Studio M-серия, M1/M4) — воспроизводится ли #661 при пуле с пиннингом.
4. **Возврат памяти после `kill` воркера**: сколько реально возвращает ОС для MLX (кэш Metal), CoreML (E5RT/IOSurface) и ONNX (arena) — мерить `footprint` до/после 1/5/20 циклов.
5. **Цена повторной загрузки после выгрузки**: для CoreML — компиляция ANE (J E1/E5: 440–560 с на large-v3-turbo, «неудачная специализация не кэшируется»); для ONNX — `ModelCacheDirectory`; для MLX — чтение весов (`read()` — то есть полный I/O модели в память).
6. **Пропускная способность WS-фронта** на Swift (Hummingbird 2.28): 100/500 клиентов, бинарные кадры 100 мс, p99 и RSS — против axum (единственный аргумент, который мог бы оживить Rust).
7. **Стоимость per-chunk IPC** для бинарного фрейминга на реальном профиле (F измерил 7–9 µs на 10–100 мс чанк; проверить на нашем фрейминге с заголовком и батчингом).
8. **Порт Voxtral Realtime на MLX**: существует ли рабочая реализация (mlx-audio?) и её качество на ru — иначе Voxtral доступен только через GGUF (audio.cpp) и ломает «все 5 моделей в двух воркерах».
9. **GigaAM v3 в sherpa-onnx end-to-end** (не только `IsGigaAM()` в коде): конвертация, vocab, пунктуация, ITN, скорость на M-чипе — проверить до того, как ONNX-воркер станет «шагом 1».
10. **Лицензии артефактов**: OpenMDW-1.1 (Nemotron), CC-BY-4.0 (Parakeet v3, Sortformer v2), Apache-2.0 (Voxtral, Qwen3, FluidAudio, audio.cpp), MIT (GigaAM, sherpa, MLX) — проверить, что модель-лицензия не конфликтует с распространением весов в каталоге (вне этой задачи, но блокирует требование 12).

---

## Источники

**Код (локальные клоны, permalink'и с SHA):**
- mlx (сабмодуль mlx-swift, пиннутый) @ `1f8e74e3f12f31365464a6867c6579f0e9b29d85` — https://github.com/ml-explore/mlx (`mlx/io/safetensors.cpp`, `mlx/io/load.h`, `mlx/io/load.cpp`, `mlx/backend/common/load.cpp`, `mlx/array.h`); проверено также на HEAD `8bbef75910e962be74b03c29d9faa336c6745508`
- mlx-swift @ `5f8f4d11779cf403a690373cea4965a1178c96a2` (`0.32.3-22`) — https://github.com/ml-explore/mlx-swift (`Source/MLX/Transforms+Eval.swift`, `Source/MLX/IO.swift`, `Package.swift`)
- speech-swift @ `cbad3be29480825707890490f446b19d7288be2d` — https://github.com/soniqo/speech-swift (`Sources/AudioServer/AudioServer.swift`, `Sources/SpeechRuntime/{InferenceGate,ModelCache,ModelRuntime,ModelDescriptor}.swift`, `Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift`, `docs/inference/model-runtime.md`)
- FluidAudio @ `1b0ed50d686873128a4129b3510ce3914c849bab` — https://github.com/FluidInference/FluidAudio (`Sources/FluidAudio/ModelNames.swift`, `Sources/FluidAudio/{ASR,Diarizer,Speaker,VAD}`, `LICENSE`)
- sherpa-onnx @ `532f1b5a8a207bf53b116b747207c1506696c843` — https://github.com/k2-fsa/sherpa-onnx (`Package.swift`, `cmake/onnxruntime-osx-*.cmake`, `CHANGELOG.md`, `sherpa-onnx/csrc/offline-nemo-enc-dec-ctc-model.cc`, `offline-transducer-nemo-model.cc`, `online-recognizer.h`)
- audio.cpp @ `1cada9b7045cc6935bcafb3f5a26806d133535e7` — https://github.com/0xShug0/audio.cpp (`README.md`, `docs/asr.md`, `docs/speech_analysis.md`, `docs/models/{gigaam_asr,voxtral_realtime,qwen3_asr,nemotron_3_diar}.md`, `docs/maintainers/parallel_server_behavior_contract.md`, `docs/model_manager.md`, `docs/c_api.md`, `LICENSE`)
- LocalAI @ `f7011f18702f107a5c24e53ab0914b37523215b0` — https://github.com/mudler/LocalAI (`pkg/model/{process,loader}.go`, `backend/backend.proto`, `backend/index.yaml`, `core/http/routes/{localai,openai}.go`, `core/backend/diarization.go`, `pkg/oci`)

**Документация и обсуждения:**
- Core ML `MLModel` (правило «один поток на инстанс») — https://developer.apple.com/documentation/coreml/mlmodel
- FluidAudio #661 (конкурентные CoreML-вызовы, `EXC_BAD_ACCESS`) — https://github.com/FluidInference/FluidAudio/issues/661
- mlx-swift #428 (`evalLock`, поведение при ошибках) — https://github.com/ml-explore/mlx-swift/issues/428
- LocalAI releases (v4.12.1, `darwin-arm64` ассет) — https://github.com/mudler/LocalAI/releases
- sherpa-onnx xcframework release (v2.0.0) — https://github.com/k2-fsa/sherpa-onnx/releases/tag/v2.0.0

**Собственные измерения (`/tmp/srr/probe/V5/`):**
- `noop.swift`, `coremlprobe.swift`, `empty.swift` → `swiftc -O`, `/usr/bin/time -l`, 20 запусков через Python `perf_counter` (§4.1)
- `mlxprobe/` (SPM-пакет на `mlx-swift` из локального клона, `swift build -c release`) → RSS в простое/после первого eval, старт (§4.1)
- `mlxbench/` (200× `matmul(1024×1024)*0.5` с прогревом) → 1/2/4 одновременных процесса (§4.2)
- `rustidle/` (`cargo build --release`) → RSS пустого Rust-процесса (§4.1)
- `xcodebuild -downloadComponent MetalToolchain` → 838.9 MB, без него `metal`-компиляция MLX невозможна (§4.3)
