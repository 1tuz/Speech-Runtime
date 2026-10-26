# B2 — speech-swift: библиотечная часть (модели, движки, возможности, расширяемость)

Аудит репозитория `github.com/soniqo/speech-swift` @ `cbad3be29480825707890490f446b19d7288be2d` (2026-10-25), пакет в манифесте называется **`Qwen3Speech`** (не «speech-swift»).
Дополнительно проверены `argmaxinc/WhisperKit` @ `9f99b5c` (пакет теперь `argmax-oss-swift`) и `FluidInference/FluidAudio` @ `1b0ed50`.
Сервер (`Sources/AudioServer`) читался поверхностно — только чтобы отделить «библиотека умеет» от «сервер экспонирует»; глубокий разбор сервера — в отчёте B1.

Базовый permalink-префикс для ссылок ниже:
`https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/`

---

## Итог

1. Это **не «рантайм», а библиотека-монорепо из 61 библиотечного таргета** (55 library-продуктов, 9 executable, 62 test target, 1 binaryTarget). Единого «движка» нет: каждая модель — отдельный Swift-модуль со своей загрузкой весов, своим `fromPretrained`, своим конфигом.
2. Общий слой — только **`AudioCommon/Protocols.swift`** (14 протоколов: STT, streaming STT, TTS, S2S, VAD, streaming VAD, turn completion, диаризация, эмбеддинги, enhancement, forced alignment, управление памятью). Это именно тот «адаптер-интерфейс», который нужен проекту, но он **неполный и не используется сервером**.
3. **Нет абстракции вычислительного бэкенда.** «CoreML / MLX» — это не подключаемые движки, а два независимых набора реализаций (80 файлов с `import CoreML`, 277 с `import MLX` из 669). Добавить ONNX/новый бэкенд = написать модель заново.
4. `SpeechRuntime` (4 файла, 217 строк) — единственный кусок, похожий на «рантайм»: `ModelDescriptor` (метаданные backend/streaming), `ModelCache` (actor, коалесинг загрузок), `ModelRuntime<Model>` (lazy load + один permit), `InferenceGate` (FIFO, **один** активный запрос). Кэш **никогда не вытесняет** модели.
5. **Конкурентности нет по дизайну.** В `docs/shared-protocols.md:593` прямо: «All model classes are not thread-safe by design… ML inference is inherently sequential». Сервер держит один глобальный `InferenceGate`, т.е. все HTTP/WS-запросы сериализуются; это подтверждено замером в `docs/benchmarks/runtime-ownership-2026-10-17.md` (4 одновременных запроса → 97/191/284/378 мс).
6. **Матрица «модель × операция × бэкенд × сервер» сильно несимметрична**: в реестре сервера 48 строк, из них **19 относятся к видам (music/vad/diarize/speaker/separate/sr/turn), для которых нет ни одного HTTP-роута**; при этом 6 ASR-модулей библиотеки (Whisper, Canary, Cohere, Voxtral, Kyutai, MOSS) не попали ни в реестр, ни в роуты — Whisper достижим только через отдельный `NativeInferenceService` с манифестом локальных каталогов.
7. **Русский язык покрыт неравномерно и частично противоречиво**: заявленный RU есть у Nemotron 3.5 (75 языков, `ru` в карточке HF), Whisper large-v3-turbo (multilingual), Parakeet TDT v3 (25 европейских, RU WER 11.49% в бенчмарке репозитория), Omnilingual (1600 языков). У Qwen3-ASR карточки HF перечисляют **7 языков без `ru`**, хотя `docs/benchmarks/asr-wer.md:125` приводит RU WER 16.35/10.52 и текст утверждает «52 languages».
8. **WhisperKit уже подключён** как зависимость пакета и как benchmark-адаптер (`EngineWhisperKit.swift`, ~40 строк); в версии 1.x у него **нет** зависимостей от `swift-transformers`/`mlx` (только `swift-argument-parser`), конфликтов нет. Реалистичность подключения как адаптера — высокая.
9. **FluidAudio** — 0 внешних зависимостей, swift-tools 6.0, macOS 14+, менеджеры-`actor` (`AsrManager`, `VadManager`), свой загрузчик моделей с `REGISTRY_URL`/`repoOverrides`, каталог `~/Library/Application Support/FluidAudio/Models`. Конфликтов зависимостей нет, но ~50% функционала дублирует speech-swift (Parakeet v3, EOU, Nemotron streaming, Silero VAD, Sortformer, Kokoro, PocketTTS, Chatterbox) — это выбор «свой движок vs чужой», а не дополнение.
10. Все **117** HF-репозиториев `aufklarer/*`, на которые ссылается код, реально существуют (проверено через HF API; у аккаунта 166 публичных моделей). Техдолг не в «мёртвых ссылках», а в дублировании (29 файлов `Configuration.swift`), отсутствии управления моделями (нет pull/delete/LRU/revision-пиннинга) и в расхождении документации с кодом.

---

## Факты

### 1. Пакет: модули, зависимости, минимальные платформы

| Параметр | Значение | Доказательство |
|---|---|---|
| Имя пакета | `Qwen3Speech` (историческое; бренд «speech-swift») | `Package.swift:4` |
| swift-tools | 5.10 | `Package.swift:1` |
| Платформы | macOS **15.0**, iOS **18.0** | `Package.swift:6-9` |
| Таргеты | 61 library target, 9 executable, 62 test, 1 binary | подсчёт по `Package.swift` |
| Продукты | 55 `.library`, 11 `.executable` (в т.ч. deprecated-алиасы `audio`, `audio-server`) | `Package.swift:10-238` |
| C++ | `.cxx17` (нужен XGrammar, LocalVQEAECFrontend) | `Package.swift:1417` |

Зависимости (`Package.swift:241-265`):

| Пакет | Ограничение | Зачем |
|---|---|---|
| `ml-explore/mlx-swift` | `from: "0.30.0"` | MLX-бэкенд (MLX, MLXNN, MLXFast, MLXFFT, MLXRandom) |
| `ml-explore/mlx-swift-lm` | **`exact: "3.31.4"`** | LLM-рантайм (MLXLLM/MLXLMCommon) для Qwen3Chat/VoiceChat/FishAudio/MossTranscribe |
| `apple/swift-argument-parser` | `from: "1.5.0"` | CLI |
| `modelcontextprotocol/swift-sdk` | `exact: "0.12.1"` | MCP-сервер в CLI (`VoiceChatMCP.swift`, 70 КБ) |
| `apple/swift-system` | `from: "1.8.0"` | — |
| `huggingface/swift-transformers` | `from: "1.1.6"` | `Hub` (кэш-каталог), `Tokenizers`, `Transformers` |
| `hummingbird-project/hummingbird` | `2.5.0..<2.17.0` | HTTP-сервер |
| `hummingbird-websocket` | `2.6.0..<2.7.0` | WS `/v1/realtime` |
| `hummingbird/swift-websocket` | `1.5.0..<1.6.0` | явный пин из-за бага 1.6.0 (комментарий `Package.swift:254-259`) |
| `argmaxinc/WhisperKit` | `from: "1.0.0"` | **только** для `AsrBenchmark` (сравнение с нативным WhisperASR) |
| `mlc-ai/xgrammar` | `exact: "0.2.7"` | grammar-constrained decoding для чата |

Важно: **`Package.resolved` в репозиторий не закоммичен** — воспроизводимость разрешения зависимостей держится только на явных пинах (`mlx-swift-lm`, `xgrammar`, `swift-websocket`, `MCP`), а `mlx-swift`/`swift-transformers`/`WhisperKit`/`hummingbird` плывут по semver.

Структура: каждый аудио-модуль зависит только от `AudioCommon` (+`MLXCommon`), межмодульных зависимостей моделей почти нет — исключения: `BreezeTTS→Qwen3TTS` (`Package.swift:450-459`), `IndexTTS2TTS→ChatterboxTTS+SpeechRestoration` (`:485-493`), `KyutaiSTT/CSM/HibikiTranslate→PersonaPlex` (`:495-503`), `Qwen3ASR→SpeechVAD` (`:300-309`).

### 2. Общий протокольный слой (`AudioCommon/Protocols.swift`, 455 строк)

| Протокол | Строки | Сигнатура (сокращённо) | Кто реализует |
|---|---|---|---|
| `SpeechRecognitionModel` | `:222-230` | `inputSampleRate`; `transcribe(audio:sampleRate:language:) -> String` (**синхронный, non-throwing**); `transcribeWithLanguage(...) -> TranscriptionResult` (есть default-реализация) | Qwen3ASRModel, ParakeetASRModel, ParakeetStreamingASRModel, NemotronStreamingASRModel, OmnilingualASRModel, OmnilingualASRMLXModel, KyutaiSTTModel, **WhisperASRModel**, **CanaryASRModel**, **CohereTranscribeModel**, **VoxtralModel**, **MossTranscribeModel** |
| `StreamingRecognitionSession` | `:297-305` | `inputSampleRate`; `push(CapturedAudioChunk) throws -> [StreamingRecognitionUpdate]`; `finish()` | сессии Parakeet EOU, Nemotron (CoreML+MLX), Kyutai |
| `StreamingRecognitionModel` | `:324-326` | `makeStreamingSession(language:) throws -> any StreamingRecognitionSession` | те же 3 модели |
| `SpeechGenerationModel` | `:127-134` | `sampleRate`; `generate(text:language:) async throws -> [Float]`; `generateStream(...)` (default = 1 чанк) | 18 классов TTS (Chatterbox и OmniVoice — **вне** протокола, у них свои API) |
| `SpeechToSpeechModel` | `:339-346` | `respond(userAudio:)`, `respondStream(...)` | PersonaPlexModel |
| `VoiceActivityDetectionModel` | `:367-372` | `detectSpeech(audio:sampleRate:) -> [SpeechSegment]` | SileroVADModel, PyannoteVADModel |
| `StreamingVADProvider` | `:377-389` | `chunkSize`; `processChunk([Float]) -> Float`; `resetState()` | SileroVADModel |
| `TurnCompletionProvider` | `:394-397` | `turnCompleteProbability(audio:sampleRate:)` | SmartTurnModel |
| `SpeakerEmbeddingModel` | `:422-428` | `embeddingDimension`; `embed(...)` | **только** WeSpeakerModel |
| `SpeakerDiarizationModel` | `:442-445` | `diarize(audio:sampleRate:) -> [DiarizedSegment]` | PyannoteDiarizationPipeline, SortformerDiarizer |
| `SpeakerExtractionCapable` | `:452-455` | `extractSpeaker(..., targetEmbedding:)` | PyannoteDiarizationPipeline |
| `SpeechEnhancementModel` | `:434-437` | `enhance(audio:sampleRate:) throws -> [Float]` | SpeechEnhancer, SpeechRestorer, FlashSR |
| `ForcedAlignmentModel` | `:331-333` | `align(audio:text:sampleRate:language:) -> [AlignedWord]` | Qwen3ForcedAligner |
| `ModelMemoryManageable` | `:60-72` | `isLoaded`, `unload()`, `memoryFootprint` | IndexTTS2, Higgs, F5, + VAD (`+Memory.swift`) |

Пробелы протокольного слоя (проверено по grep конформансов):
- `ReDimNet2SpeakerModel` (192-мерные эмбеддинги, `Sources/SpeechVAD/ReDimNet2Speaker.swift:46,59`) и `CamPlusPlusSpeaker` (`Sources/CosyVoiceTTS/CamPlusPlusSpeaker.swift:14`) **не** реализуют `SpeakerEmbeddingModel`.
- `Qwen3ASR.StreamingASR` (`Sources/Qwen3ASR/StreamingASR.swift:60`) — класс со своим `transcribeStream`, но **не** `StreamingRecognitionSession`.
- `MossTranscribeModel` реализует STT, но его диаризационный выход (`transcribeDetailed(...) -> MossTranscription`, `Sources/MossTranscribe/MossCoreMLRuntime.swift:561`) в протокол `SpeakerDiarizationModel` не упакован.
- `SpeechLanguageID`, `SpeechWakeWord`, GLiNER, Clef, MADLAD, Audio2Face3D — вообще вне протоколов (у wake word свой `WakeWordProvider` в `Sources/SpeechWakeWord/SpeechWakeWord+Protocols.swift:7`).
- `docs/shared-protocols.md:70-95` перечисляет «Conforming types» для STT **без** Whisper/Canary/Cohere/Voxtral/MOSS, хотя код их конформит (`Sources/WhisperASR/WhisperASR.swift:5`, `Sources/CanaryASR/CanaryASR+Protocols.swift:4`, `Sources/CohereTranscribeASR/CohereTranscribe.swift:37`, `Sources/VoxtralASR/VoxtralASR.swift:27`, `Sources/MossTranscribe/MossCoreMLRuntime.swift:212`) → документация отстала от кода.

### 3. `SpeechRuntime` — «зародыш» рантайма (4 файла, 217 строк)

- `ModelDescriptor` (`Sources/SpeechRuntime/ModelDescriptor.swift:1-24`): `id`, `modelID`, `backend ∈ {mlx, coreML, hybrid}`, `inputSampleRate`, `streaming ∈ {buffered, incremental}`; `Codable`+`Sendable`. Это **метаданные**, не полиморфизм: ни один код по `backend` ничего не загружает.
- `ModelCache` (`ModelCache.swift:6-33`): `actor`, ключ = `(ObjectIdentifier(Model.self), identity)`, хранит `Task<Box,Error>`; при ошибке удаляет запись. Комментарий `:4-5`: «protects cache metadata, not inference». **Нет** выгрузки/LRU/лимита — «Cached server variants remain resident for the server's lifetime» (`docs/inference/model-runtime.md`).
- `ModelRuntime<Model>` (`ModelRuntime.swift:6-43`): lazy load под permit, `withModel { }`, `unload()`, `deinit` → unloader. Сериализует операции одной модели.
- `InferenceGate` (`InferenceGate.swift:5-117`): `occupied: Bool` + FIFO `waiters`, `maximumPending: Int = 32`, отмена ожидающего, `AdmissionError.queueFull`. **Один активный permit на весь процесс** (если gate шарится) — именно так использует сервер.

### 4. Инвентарь моделей по операциям

Все ID ниже — `static let defaultModelId` соответствующего модуля (файл:строка указан).

#### 4.1 ASR

| Модель (HF repo) | Бэкенд | Streaming | Языки (источник) | Модуль / файл:строка |
|---|---|---|---|---|
| `aufklarer/Qwen3-ASR-0.6B-MLX-4bit` / `-1.7B-MLX-8bit` | MLX (4/5/8-bit) | buffered; отдельный `StreamingASR` | карточка HF: 7 языков (en,zh,ja,ko,fr,de,es); репо-док: «52 languages», RU WER 16.35/10.52 | `Qwen3ASR`, `Sources/Qwen3ASR/Qwen3ASR.swift:122-125` |
| `aufklarer/Qwen3-ASR-CoreML` | CoreML (encoder+decoder) | buffered | те же 7 по карточке | `Qwen3ASR/CoreMLASRModel.swift:368` конформ; `CoreMLEncoder.swift:28` |
| `aufklarer/Parakeet-TDT-v3-CoreML-INT8-30s` (+`-iOS-5s`) | CoreML INT8 | batch; длинное аудио режется окнами (30 c по умолчанию, 5 c в iOS-варианте) | 25 европейских, **включая ru** (карточка `nvidia/parakeet-tdt-0.6b-v3`; RU WER 11.49 в `docs/benchmarks/asr-wer.md:125`) | `ParakeetASR`, `Sources/ParakeetASR/ParakeetASR.swift:19-24` |
| `aufklarer/Parakeet-EOU-120M-CoreML-INT8` | CoreML | **streaming** + EOU | en (карточка HF: `en`) | `ParakeetStreamingASR`, `…/ParakeetStreamingASR.swift:15,239` |
| `aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8` (+ MLX 4/5/8-bit, bf16) | CoreML INT8 **и** MLX | **streaming** (язык — one-hot слот из `languages.json`) | **75 языков, `ru` есть** (карточка HF) | `NemotronStreamingASR`, `…/NemotronStreamingASR.swift:8,23`; MLX: `NemotronMLXRuntime.swift:15,341` |
| `aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8-10s` (+MLX 300M…7B) | CoreML **и** MLX | batch (CTC, language-agnostic) | 1600+ языков, `rus_Cyrl` есть (`docs/models/omnilingual-asr.md:1713`) | `OmnilingualASR`, `…/OmnilingualASR.swift:19-20,30` |
| `aufklarer/Whisper-Large-v3-Turbo-CoreML` | CoreML (Mel/Encoder/Decoder/Prefill) | batch с чанкингом, auto-detect языка по логитам | multilingual (карточка: `multilingual`), RU поддержан Whisper large-v3 | `WhisperASR`, `…/WhisperASR.swift:5-7,144`; язык: `WhisperCoreMLRuntime.swift:97-128` |
| `aufklarer/Canary-180M-Flash-CoreML` | CoreML | batch | en,de,es,fr (карточка HF) | `CanaryASR`, `…/CanaryASR.swift:34,41` |
| `aufklarer/Cohere-Transcribe-2B-MLX-{FP16,5bit,8bit}` | MLX | batch | 14 языков (без ru) | `CohereTranscribeASR`, `…/CohereTranscribe.swift:37,40` |
| `aufklarer/Voxtral-Mini-3B-2507-MLX-*` | MLX | batch | 8 языков (без ru) | `VoxtralASR`, `…/VoxtralASR.swift:27,30` |
| `aufklarer/Kyutai-STT-1B-MLX-{FP16,4bit,8bit}` | MLX | **streaming** (delayed-stream, 24 кГц вход) | en,fr | `KyutaiSTT`, `…/KyutaiSTTModel.swift:13`; `KyutaiSTTStreaming.swift:3` |
| `aufklarer/MOSS-Transcribe-Diarize-0.9B-{CoreML-INT8/FP16, MLX-5bit/INT8}` | CoreML **и** MLX | batch + **диаризация в одном проходе** | multilingual (карточка: en,de,fr,ar + multilingual) | `MossTranscribe`, `…/MossCoreMLRuntime.swift:212-213` |

Прочие ASR-сопутствующие: `Qwen3-ForcedAligner-0.6B-*` (MLX/CoreML, `Sources/Qwen3ASR/ForcedAligner+Protocols.swift:5`), `SpeechBrain-ECAPA-VoxLingua107-21M-*` — language ID, 107 закрытых меток, MLX+CoreML (`Sources/SpeechLanguageID/LanguageIDTypes.swift:54,150`), `KWS-Zipformer-3M-CoreML-INT8` — wake word (`Sources/SpeechWakeWord/SpeechWakeWord.swift:20`).

#### 4.2 VAD и turn detection (`SpeechVAD`, 41 файл)

| Модель | Бэкенд | API | Файл:строка |
|---|---|---|---|
| `aufklarer/Silero-VAD-v6.2.1-MLX` / `-CoreML` | MLX **или** CoreML, выбор через `SileroVADEngine` enum | streaming `processChunk` (512 сэмплов = 32 мс), batch `detectSpeech` | `Sources/SpeechVAD/SileroVAD.swift:11-16,28,73-76,334,345` |
| `aufklarer/Pyannote-Segmentation-MLX` | MLX | batch-сегментация | `Sources/SpeechVAD/SpeechVAD.swift:11,33` |
| `aufklarer/FireRedVAD-CoreML` | CoreML | batch | `Sources/SpeechVAD/FireRedVAD.swift:14,23` |
| `aufklarer/Smart-Turn-v3.2-CoreML` | CoreML | `turnCompleteProbability` | `Sources/SpeechVAD/SmartTurn.swift:36-37` |
| `StreamingVADProcessor` | — | склейка streaming VAD + turn completion + pre-roll | `Sources/SpeechVAD/StreamingVADProcessor.swift:25,53` |

#### 4.3 Диаризация и голосовые эмбеддинги

| Модель | Бэкенд | Особенности | Файл:строка |
|---|---|---|---|
| `aufklarer/Pyannote-Community-1-CoreML` | CoreML + нативный VBx-кластеринг | пайплайн сегментация+эмбеддинги+кластеризация | `Sources/SpeechVAD/Community1DiarizationPipeline.swift:15-18` |
| `aufklarer/Sortformer-Diarization-CoreML` (+ `Ultra-Sortformer-…`) | CoreML, end-to-end | streaming-сессии, до 8 слотов (`docs/inference/speaker-diarization.md:517`) | `Sources/SpeechVAD/SortformerDiarizer.swift:26,438`; `SortformerStreamingSession.swift:29,108-127` |
| `aufklarer/Nemotron-3-Diarization-100M-{CoreML,MLX}-INT8` | CoreML **и** MLX | — | `Sources/SpeechVAD/Nemotron3Diarizer.swift`, `Nemotron3CoreMLModel.swift:115` |
| `PyannoteDiarizationPipeline` | MLX (segmentation + WeSpeaker) | `diarize`, `extractSpeaker` | `Sources/SpeechVAD/DiarizationPipeline.swift:68`; конформ `SpeechVAD+Protocols.swift:15` |
| `aufklarer/WeSpeaker-ResNet34-LM-MLX` / `-CoreML` | MLX **или** CoreML | 256-мерные эмбеддинги, **единственный** конформ `SpeakerEmbeddingModel`; заявлен thread-safe (все `let`) | `Sources/SpeechVAD/WeSpeaker.swift:27-29,57-63` |
| `aufklarer/ReDimNet2-B6-CoreML` | CoreML | 192-мерные, «named voice identity», без протокола | `Sources/SpeechVAD/ReDimNet2Speaker.swift:46,59` |
| `aufklarer/CamPlusPlus-Speaker-CoreML` | CoreML | живёт в модуле `CosyVoiceTTS`, без протокола | `Sources/CosyVoiceTTS/CamPlusPlusSpeaker.swift:14,21` |
| `DERScoring.swift` (14.4 КБ) | — | подсчёт DER/collar внутри библиотеки | `Sources/SpeechVAD/DERScoring.swift` |

#### 4.4 TTS и speech-to-speech

| Модель | Бэкенд | Streaming TTS | Языки | Файл:строка |
|---|---|---|---|---|
| `aufklarer/Kokoro-82M-CoreML` | CoreML | нет (буфер) | 9: en,fr,es,ja,zh,hi,pt,it,de (**без ru**) | `Sources/KokoroTTS/Configuration.swift:18`, `KokoroTTS.swift:24` |
| `aufklarer/Pocket-TTS-100M-CoreML-INT8` | CoreML | да (`generateStreaming`) | en; единственный голос `alba` (клонирования в этой реализации нет) | `Sources/PocketTTS/PocketTTSModel.swift:9-13` |
| `aufklarer/Qwen3-TTS-12Hz-1.7B-Base-MLX-bf16` (+0.6B) | MLX | да (`synthesizeStream`) | код: 9 языков c `russian=2069`; карточка HF: 7 без `ru`; RU-спикера нет (fallback на `ryan`) | `Sources/Qwen3TTS/Configuration.swift:196,211`; `Qwen3TTS+Protocols.swift:27-40`; `Qwen3TTS.swift:31,38` |
| `aufklarer/Qwen3-TTS-CoreML` / `Qwen3-TTS-1.7B-CoreML` | CoreML | нет | те же | `Sources/Qwen3TTSCoreML/Qwen3TTSCoreML.swift:11-12` |
| `aufklarer/CosyVoice3-0.5B-MLX-{bf16,8bit,8bit-full}` | MLX | да | 9, **`ru` есть** (карточка HF) | `Sources/CosyVoiceTTS/CosyVoiceTTS.swift:31,37` |
| `aufklarer/VoxCPM2-MLX-{bf16,int8,int4}` | MLX | нет (буфер), 48 кГц | 6 (en,zh,id,ja,ko,+multilingual); параметр `language` игнорируется (`VoxCPM2TTS.swift:1178: _ = language`) | `Sources/VoxCPM2TTS/VoxCPM2TTS.swift:32` |
| `aufklarer/Voxtral-4B-TTS-2603-MLX-4bit` | MLX | нет | голос-пресеты на язык | `Sources/VoxtralTTS/VoxtralTTSModel.swift:36` |
| `aufklarer/Magpie-TTS-Multilingual-357M-MLX-8bit` / `-CoreML-8bit` | MLX **и** CoreML | нет | 9 (без ru) | `Sources/MagpieTTS/Configuration.swift:6,57-70`; `MagpieTTSCoreML/Configuration.swift:71` |
| `aufklarer/Indic-Mio-MLX-fp16` | MLX | нет | hi + эмоции | `Sources/IndicMioTTS/IndicMioConfig.swift:65` |
| `aufklarer/VibeVoice-Realtime-0.5B/1.5B-MLX-INT4` | MLX | да | — | `Sources/VibeVoiceTTS/VibeVoiceTTSModel.swift:22`, `VibeVoice15BTTSModel.swift:23,310` |
| `aufklarer/Chatterbox-Multilingual-MLX-fp16` (+`Chatterbox-Flash-CoreML`) | MLX / CoreML | — | 12, **`ru` есть** | `Sources/ChatterboxTTS/ChatterboxTTSModel.swift:135` |
| `aufklarer/Supertonic-3-CoreML` | CoreML | — | код-токенизатор содержит `ru` (`SupertonicTokenizer.swift:18`), карточка без тегов | `Sources/SupertonicTTS/SupertonicTTS.swift:14` |
| F5TTS, Higgs-TTS-3, IndexTTS2, Fish-Audio-S2-Pro/Drama3, OmniVoice, MOSS-TTS-Nano, Breeze-TTS-2 | MLX / CoreML | частично | клонирование голоса | `Sources/F5TTS/F5TTSModel.swift:9`, `HiggsTTSModel.swift:55`, `IndexTTS2TTSModel.swift:8`, `FishAudioConfig.swift:39`, `MossTTSNanoModel.swift:8`, `BreezeTTSModel.swift:32` |
| `aufklarer/PersonaPlex-7B-MLX-{4,8}bit` | MLX | S2S | en | `Sources/PersonaPlex/PersonaPlex.swift:11,16-19` |
| `aufklarer/Hibiki-Zero-3B-MLX-{4,8}bit` | MLX | S2S-перевод речи | fr→en и др. | `Sources/HibikiTranslate/HibikiTranslateModel.swift:44-45` |

#### 4.5 Прочее (важно для полноты картины)

- Enhancement: `DeepFilterNet3-CoreML` / `-MLX` (`Sources/SpeechEnhancement/SpeechEnhancement.swift:7,30`), `LocalVQE-v1.4-AEC-200K-CoreML` (эхоподавление, C++-фронтенд `LocalVQEAECFrontend`), `Sidon-CoreML` (restoration, `Sources/SpeechRestoration/Configuration.swift:55`), `FlashSR-MLX-{4,8}bit` (super-resolution).
- Разделение источников: `OpenUnmix-HQ/L-MLX`, `HTDemucs-FT-MLX` (`Sources/SourceSeparation/SourceSeparation.swift:20-21`).
- Музыка/звук: MAGNeT Small/Medium 30 s, Stable Audio 3 DiT (`Sources/MAGNeTMusicGen/Configuration.swift:5`, `StableAudio3MusicGen/Configuration.swift:17`).
- Чат/LLM: `Qwen3.5-0.8B-Chat-MLX`, `gemma-4-E2B/E4B-it-MLX`, `VoiceChat-11B-Perception-MLX-int5/int8`, `Clef-flash-9B-MLX-4bit`, `GLiNER2.5-Decide-340M-MLX`, `FunctionGemma-270M-CoreML`, `MADLAD400-3B-MT-MLX`.
- Аватар: `Audio2Face-3D-v2.3.1-{James,Claire}-MLX` (`Sources/Audio2Face3D/Configuration.swift:17`).
- Захват/вывод звука: `AudioIO`, `FullDuplexAudioIO` (525 строк), `SystemAudioTap` (614 строк, ScreenCaptureKit), `StreamingAudioPlayer` (573 строки) — это уже «приложение», а не модель.

### 5. Как выбирается движок: пять разных идиом

1. **Enum-движок в `fromPretrained`**: `SileroVADEngine {mlx, coreml}`, `WeSpeakerEngine`, `SpeechEnhancerEngine` → `fromPretrained(engine: .coreml)` (`Sources/SpeechVAD/SileroVAD.swift:11-16,252`; `WeSpeaker.swift:11,102`; `SpeechEnhancement.swift:7,436`).
2. **`ModelDescriptor.backend` → рантайм-адаптер**: только Qwen3-ASR (`Qwen3ASRRuntime` переключает MLX/CoreML-классы) (`Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift:7-30`).
3. **Два независимых класса на бэкенд**: `OmnilingualASRModel` (CoreML) vs `OmnilingualASRMLXModel`; `NemotronStreamingASRModel` vs `NemotronStreamingASRMLXModel`; `MossTranscribeModel` vs `MossMLXModel`.
4. **Variant-enum с `modelId`** (квантование, не бэкенд): `VoxtralVariant`, `CohereTranscribeVariant`, `KyutaiSTTVariant`, `MossModelVariant`, `MossMLXVariant`, `MagpieTTSVariant`, `FlashSRVariant`, `MAGNeTVariant`, `StableAudio3Variant`, `NemotronMLXVariant`, `ForcedAlignerVariant`, `TTSModelVariant`, `SidonVariant`.
5. **Жёстко один бэкенд**: Parakeet, Whisper, Canary, Kokoro, PocketTTS, Qwen3TTSCoreML — только CoreML; Voxtral/Kyutai/Cohere — только MLX.

**Нет ни одного протокола вида `InferenceBackend`/`ComputeEngine`** (grep по `protocol .*Backend|Engine|Adapter` даёт только `Nemotron3InferenceBackend` — приватный, `Sources/SpeechVAD/Nemotron3Diarizer.swift:91` — и `Qwen35ChatBackend`). CoreML-настройки задаются **глобально** через env `SPEECH_COREML_COMPUTE_UNITS` (`Sources/AudioCommon/CoreMLComputeUnits.swift:22,71`), т.е. нельзя разным моделям дать разные compute units в одном процессе.

MLX-память, наоборот, централизована: `MLXCacheLimitCoordinator` — process-wide кэш-лимит `min(2 GiB, RAM/4)`, учитывающий все живые модели (`Sources/MLXCommon/MLXCacheLimit.swift:9-11,16-40`; `docs/inference/mlx-memory.md`).

### 6. Матрица «модель × операция × бэкенд × доступно через сервер» (acceptance)

Обозначения: ✅ — есть строка в `MODEL_REGISTRY` **и** HTTP/WS-роут; ⚠️ — только CLI/бенчмарк или только через отдельный `NativeInferenceService`; ❌ — в серверный таргет не входит вовсе.
Реестр: `Sources/AudioServer/ModelRegistry.swift:96-350`; роуты: `Sources/AudioServer/AudioServer.swift:96,119,131,144,148,152,200,241,328`; WS: `:67`.

| Операция | Модель | Бэкенд | Сервер |
|---|---|---|---|
| ASR batch | Qwen3-ASR 0.6B MLX INT4 | MLX | ✅ `/transcribe`, `/v1/audio/transcriptions`, WS |
| ASR batch | Qwen3-ASR 1.7B MLX INT8 | MLX | ✅ (алиас `qwen3-1.7b`) |
| ASR batch | Qwen3-ASR CoreML | CoreML | ✅ (`qwen3-asr-coreml`) |
| ASR batch | Parakeet TDT v3 30 s | CoreML | ✅ (default для `/v1/audio/transcriptions`) |
| ASR batch | Parakeet TDT v3 iOS 5 s | CoreML | ✅ |
| ASR batch | Parakeet EOU 120M | CoreML | ✅ (batch-режим; streaming-сессии — нет) |
| ASR batch | Nemotron 3.5 streaming 0.6B | CoreML | ✅ |
| ASR batch | Nemotron 3.5 streaming 0.6B | **MLX** | ❌ (в реестре только CoreML-вариант) |
| ASR batch | Omnilingual CTC 300M 10 s | CoreML | ✅ |
| ASR batch | Omnilingual CTC 300M…7B | MLX | ❌ |
| ASR batch | Whisper large-v3-turbo | CoreML | ⚠️ только `NativeInferenceService` (манифест с локальными путями, `NativeInference.swift:189`) + CLI `--engine whisper`; в `MODEL_REGISTRY` **нет** |
| ASR batch | Canary 180M Flash | CoreML | ❌ |
| ASR batch | Cohere Transcribe 2B | MLX | ❌ |
| ASR batch | Voxtral Mini 3B | MLX | ❌ |
| ASR batch | Kyutai STT 1B | MLX | ❌ |
| ASR + диаризация | MOSS Transcribe Diarize 0.9B | CoreML/MLX | ❌ (модуль не входит в `AudioServer`, `Package.swift:947-983`) |
| ASR streaming (partials) | Parakeet EOU / Nemotron / Kyutai | CoreML/MLX | ❌ (WS отдаёт только финальный текст хода, grep `partial` в `AudioServer.swift` пуст) |
| VAD | Silero v6.2.1 | MLX / CoreML | ✅ (внутри WS turn-detection: `AudioServer.swift:1110-1120`, `RealtimeVAD.swift:36`) |
| VAD | Pyannote segmentation, FireRedVAD | MLX / CoreML | ❌ (строки в реестре есть, роутов нет) |
| Turn completion | Smart-Turn v3.2 | CoreML | ❌ (в реестре есть, роута нет) |
| Диаризация | Sortformer, Ultra-Sortformer, Community-1, Nemotron-3, Pyannote pipeline | CoreML/MLX | ❌ |
| Эмбеддинги | WeSpeaker 256-d (MLX/CoreML), ReDimNet2 192-d, CamPlusPlus | MLX/CoreML | ❌ |
| TTS | Kokoro 82M | CoreML | ✅ `/speak`, `/v1/audio/speech`, WS (буфер) |
| TTS | PocketTTS 100M | CoreML | ✅ (WS стримит) |
| TTS | Qwen3-TTS 1.7B | MLX | ✅ (WS стримит) |
| TTS | Qwen3-TTS CoreML | CoreML | ✅ |
| TTS | CosyVoice3 0.5B (3 варианта) | MLX | ✅ (WS стримит) |
| TTS | VoxCPM2 (bf16/int8) | MLX | ✅ (буфер) |
| TTS | Voxtral 4B TTS | MLX | ✅ (буфер) |
| TTS | Magpie 357M (MLX и CoreML) | MLX/CoreML | ✅ |
| TTS | Indic-Mio | MLX | ✅ |
| TTS | VibeVoice 0.5B / 1.5B | MLX | ✅ |
| TTS | Chatterbox, Fish-Audio, F5, Higgs, IndexTTS2, OmniVoice, Breeze, Supertonic, MOSS-TTS-Nano | MLX/CoreML | ❌ |
| S2S | PersonaPlex 7B (4/8-bit) | MLX | ✅ `/respond`, WS |
| S2S | Hibiki Zero 3B | MLX | ✅ `/respond`, WS |
| Enhancement | DeepFilterNet3 (CoreML/MLX) | CoreML/MLX | ✅ `/enhance` |
| Enhancement/AEC | LocalVQE AEC | CoreML | ❌ |
| Restoration | Sidon | CoreML | ❌ (в серверный таргет не входит) |
| Super-resolution | FlashSR int4/int8 | MLX | ❌ (2 строки реестра, роута нет) |
| Разделение | OpenUnmix HQ/L, HTDemucs | MLX | ❌ (3 строки, роута нет) |
| Музыка | MAGNeT ×4, Stable Audio 3 ×2 | MLX | ❌ (6 строк, роута нет) |
| Language ID | ECAPA VoxLingua107 (107 языков) | MLX/CoreML | ❌ |
| Wake word | KWS Zipformer 3M | CoreML | ❌ |
| Forced alignment | Qwen3-ForcedAligner 0.6B | MLX/CoreML | ❌ |
| NER / routing / перевод текста / аватар | GLiNER, Clef, MADLAD, Audio2Face3D | MLX/CoreML | ❌ |

Сводно по реестру: **48 строк = 8 ASR + 15 TTS + 4 S2S + 2 enhance (достижимы, 29) + 19 недостижимых** (music 6, vad 4, separate 3, sr 2, speaker 2, turn 1, diarize 1). Комментарий в коде (`AudioServer.swift:1028-1034`) утверждает, что эти виды «routed via dedicated HTTP endpoints (/enhance, /compose, /diarize, …)» — **таких роутов в коде нет** (grep `"/diarize"|"/vad"|"/compose"|"/separate"|"/speaker"|"/turn"` по `Sources` — пусто).

### 7. Что сервер реально экспонирует (сверка «библиотека vs сервер»)

Роуты (`AudioServer.swift:96-340`): `GET /health`, `GET /v1/models`, `GET /v1/realtime/models`, `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`, `POST /transcribe`, `POST /speak`, `POST /respond`, `POST /enhance`, `WS /v1/realtime`, плюс подключаемый `NativeInferenceService` (`/v1/inference`, `/v1/inference/capabilities|provenance|cancel`, `NativeInference.swift:278-310`).

Диспетчеризация — ручные `switch` по строке `engine`, а не по протоколам: `dispatchTranscribe` (`AudioServer.swift:1750-1785`) знает 4 движка + qwen3; `dispatchSynthesize` (`:1806-1875`) — 10; `ModelState` объявляет **19 сильно типизированных** `loadXxx(modelId:)` (`AudioServer.swift:392-413`). Протокольные экзистенциалы (`any SpeechRecognitionModel`) в сервере не используются вообще — только `SpeechCore` (`Sources/SpeechCore/VoicePipeline.swift:89,193`).

Следствия:
- чтобы добавить движок в сервер, нужно: HF-константу в модуле → строку в `MODEL_REGISTRY` → метод в `RealtimeModelLoading` → ветку в `dispatchTranscribe`/`dispatchSynthesize` → ветку в WS-`switch` → (для TTS) маппинг языка (`mapToKokoroLanguageCode`, `mapToMagpieLanguageCode`, `mapToHibikiSourceLanguage`, `AudioServer.swift:1899+`);
- «библиотека умеет» ≫ «сервер экспонирует»: диаризация, эмбеддинги, language ID, forced alignment, 10 TTS-модулей, 6 ASR-модулей, streaming-partials доступны только через CLI (`Sources/AudioCLILib/*`: `DiarizeCommand`, `EmbedSpeakerCommand`, `LanguageIDCommand`, `AlignCommand`, `TurnCommand`, `SeparateCommand`, `ComposeCommand`, …) или бенчмарки;
- OpenAI-совместимый слой частичный: `verbose_json` — фиктивная одна сегмент-запись без word timestamps (`Sources/AudioServer/*Transcriptions.swift:109-135`), `speed` поддержан только Kokoro, `stream` — только `pcm` (`Sources/AudioServer/*Speech.swift:78-84,153-155`). (Имена этих двух файлов содержат префикс вендора, который среда выводит как `[OI]`; в тексте ниже использую маску `*`.)

Единственное место в репозитории с настоящей «подключаемостью движков» — **C-ABI втарблы в закрытом `SpeechCore`**: Swift-мосты `STTBridge`/`TTSBridge`/`VADBridge`/`TurnCompletionBridge` оборачивают любые `SpeechRecognitionModel`/`SpeechGenerationModel`/`StreamingVADProvider`/`TurnCompletionProvider` в `sc_*_vtable_t` и передают в `sc_pipeline_create` (`Sources/SpeechCore/VoicePipeline.swift:89-130,193-245,328-333`; протоколы прямо описаны как «Maps directly to speech-core's `sc_vad_vtable_t`», `Sources/AudioCommon/Protocols.swift:373-376,390-392`). Это ровно та архитектура адаптеров, которая нужна проекту, — но она реализована на C-стороне, исходников которой в репозитории нет.

### 8. Потокобезопасность и конкурентность — проверено по коду и докам

- Прямое заявление: «All model classes are **not thread-safe** by design… Adding synchronization primitives would introduce overhead for a scenario no caller exercises» (`docs/shared-protocols.md:591-595`). Список не-thread-safe классов — там же `:596-606`; thread-safe — только `WeSpeakerModel` (`:607-608`).
- Предупреждения в doc-комментариях у 20+ классов: `Qwen3ASRModel` (`Qwen3ASR.swift:117`), `Qwen3TTSModel` (`:31`), `SileroVADModel` (`SileroVAD.swift:28`), `ParakeetASRModel` (`ParakeetASR.swift:11`), `PersonaPlexModel`, `NemotronStreamingASRModel` (`:18`), `OmnilingualASRModel`, `CanaryASRModel`, `FireRedVAD`, `PyannoteDiarizationPipeline`, `SortformerDiarizer`, `StreamingVADProcessor`, `SpeechWakeWord`, `LocalVQEEchoCanceller` и др.
- Единственные модели с внутренней блокировкой: `MossTranscribeModel` (`NSLock`, `MossCoreMLRuntime.swift:248,491,567`) — т.е. диаризация-MOSS безопасна по факту, хотя в доках это не отражено; и `MossTTSNanoModel` объявлен как `public actor` (`Sources/MossTTSNano/MossTTSNanoModel.swift:7`) — единственный актор среди 61 модуля.
- Скрытое состояние, ломающее протокол: `ParakeetASRModel.lastConfidence` / `lastWordConfidences` — `public private(set) var`, пишутся в `transcribeAudio` и читаются в `transcribeWithLanguage` (`Sources/ParakeetASR/ParakeetASR.swift:46-48,161`; `ParakeetASR+Protocols.swift:20`) → два параллельных вызова дадут перепутанные confidence.
- Сервер сериализует всё: один `InferenceGate` в `ModelState` (`AudioServer.swift:538-546`), permit берётся на весь запрос, включая стриминг (`:1199-1201`, `:1803`), тяжёлая работа уводится в `Task.detached` (`runOffloaded`, `:1600-1609`). Замер: «Four simultaneous HTTP requests completed… at approximately 97, 191, 284 and 378 ms. This is the intended serial admission behavior» (`docs/benchmarks/runtime-ownership-2026-10-17.md`, раздел Results).
- VAD специально **не** шарится: каждая WS-сессия получает свой `SileroVADModel` из-за LSTM-состояния (`AudioServer.swift:568-575`), т.е. веса грузятся в память повторно на сессию.
- `ModelCache` не выгружает ничего → простой сервер держит в RSS все загруженные модели (для Kokoro-замера: ~705 MiB MLX / ~1022 MiB CoreML current footprint после прогонов, `runtime-ownership-2026-10-17.md`).

### 9. Проверка существования моделей и русского языка (HF API, 2026-10-25)

- Из кода извлечено 117 уникальных `aufklarer/*` repo-id; **все 117 существуют**; у аккаунта 166 публичных репозиториев. Внешние: `mlx-community/S3TokenizerV2`, `mlx-community/encodec-32khz-float32`, `mlx-community/fish-audio-drama-3-4bit`, `openai/whisper-large-v3`.
- Языковые теги карточек (`cardData.language`): `Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8` — 75 языков, `ru` присутствует; `Chatterbox-Multilingual-MLX-fp16` — 12, `ru` есть; `CosyVoice3-0.5B-MLX-bf16` — 9, `ru` есть; `Qwen3-ASR-*` и `Qwen3-ASR-CoreML` — 7 (en,zh,ja,ko,fr,de,es), **`ru` нет**; `Qwen3-TTS-12Hz-1.7B-Base-MLX-bf16` — 7, **`ru` нет**; `Kokoro-82M-CoreML` — 9 без `ru`; `Magpie` — 9 без `ru`; `Cohere-Transcribe-2B` — 14 без `ru`; `Voxtral-Mini-3B` — 8 без `ru`; `Kyutai-STT-1B` — en,fr; `Parakeet-EOU-120M` — en; `Canary-180M` — en,de,es,fr; `Whisper-Large-v3-Turbo-CoreML` — `multilingual`; `Parakeet-TDT-v3-CoreML-INT8-30s` — тегов нет, но базовый `nvidia/parakeet-tdt-0.6b-v3` = 25 европейских с `ru`,`uk`.
- Внутренние бенчмарки репозитория (самоотчёт, не независимое измерение): RU WER 16.35% (Qwen3 4-bit), 10.52% (Qwen3 8-bit), 11.49% (Parakeet INT8) на FLEURS (`docs/benchmarks/asr-wer.md:113-130`); там же — «Qwen3 supports 52 languages; Parakeet supports ~25 European languages (no CJK)».

### 10. WhisperKit как адаптер: реалистичность

- WhisperKit **уже** зависимость speech-swift (`Package.swift:260`, `from: "1.0.0"`) и используется только в `AsrBenchmark`: `Sources/AsrBenchmark/EngineWhisperKit.swift:4-40` — `WhisperKit(config)`, `transcribe(audioArray:decodeOptions:)`, обёрнут в `@unchecked Sendable`, один инстанс на движок.
- Зависимости WhisperKit 1.x: только `swift-argument-parser` (`WhisperKit/Package.swift:32-33`), Vapor/OpenAPI — опционально за `BUILD_ALL`. Историческая версия 0.18.0 тянула `swift-transformers .upToNextMinor(from: "1.1.6")` — ровно тот же нижний порог, что у speech-swift, т.е. конфликта не было и раньше; в 1.0 зависимость убрана. **Конфликтов с `swift-transformers` и `mlx` нет.**
- Платформы WhisperKit: macOS 13+, iOS 16+ — ниже требований speech-swift (15/18), т.е. не ограничивает.
- Бэкенд — CoreML (`Sources/WhisperKit/Core/Models.swift:14-21` — `MLModelConfiguration.computeUnits`), модели качает собственный менеджер в `downloadBase`/`modelFolder` (`Core/Configurations.swift:11-19`) → **второй каталог моделей** рядом с `~/Library/Caches/qwen3-speech`; это минус для «единого каталога», но `downloadBase` можно указать.
- Функциональное пересечение: Whisper large-v3(-turbo) дублирует нативный `WhisperASR` (CoreML, свой runtime). Репозиторий уже сравнил их: нативный быстрее (RTF 0.077 vs 0.085, загрузка 1.3 s vs 13.2 s), точность в пределах шума (81 vs 80 ошибок), `docs/benchmarks/asr-wer.md:60-62`.
- Бонус: пакет 1.x содержит `SpeakerKit` (pyannote-диаризация, `Sources/SpeakerKit/Pyannote/PyannoteDiarizer.swift:36,73`) и `TTSKit` (Qwen-TTS) — потенциально закрывают часть пробелов, но это ещё одно дублирование.
- Вывод: подключение как **адаптер** тривиально (протокол + ~40 строк, что уже доказано бенчмарком), вопрос — нужен ли второй Whisper-runtime.

### 11. FluidAudio как адаптер: реалистичность

- `Package.swift` (FluidAudio @1b0ed50): swift-tools **6.0**, `platforms: macOS 14 / iOS 17 / visionOS 2 / tvOS 17`, **`dependencies: []`** — ни `swift-transformers`, ни `mlx`. Дополнительно `Package@swift-6.2.swift` с traits (`NemoTextProcessing`, `TTS`, `Diarizer`) — для тулчейнов < 6.2 линкуется всё.
- Бинарные артефакты: `NemoTextProcessing.xcframework` (Rust staticlib, ~8 МБ/слайс, `Package.swift:74-79`), C++-обёртки `FastClusterWrapper`, `MachTaskSelfWrapper`.
- Инвентарь (`Sources/FluidAudio`): ASR 87 файлов (Parakeet TDT v3/CTC/EOU/Unified, Nemotron streaming, SenseVoice, Paraformer, Canary, Cohere), Diarizer 46 (Sortformer, LS-EEND, Nemotron 3, pyannote offline), TTS 157 (Kokoro, PocketTTS, Chatterbox, LuxTTS, Phonon), VAD 6 (Silero, FSMN), Speaker 2, Enhancement 3 (LocalVQE), ITN/Decision.
- Конкурентность — другая философия: `public actor AsrManager` (`Sources/FluidAudio/ASR/Parakeet/SlidingWindow/TDT/AsrManager.swift:6`), `public actor VadManager` (`VAD/VadManager.swift:14`); диаризаторы — обычные `final class` (`Diarizer/Core/DiarizerManager.swift:7`, `Diarizer/Sortformer/SortformerDiarizer.swift:13`).
- Управление моделями: собственный загрузчик с `REGISTRY_URL`/`MODEL_REGISTRY_URL`, `repoOverrides`, `revisionOverrides`, прокси (`Sources/FluidAudio/ModelRegistry.swift:33-120`); каталог — `~/Library/Application Support/FluidAudio/Models` (`Shared/MLModelConfigurationUtils.swift:38-49`), т.е. **третий** формат кэша.
- Пересечение с speech-swift (по `ModelNames.swift`): Parakeet TDT v3 CoreML, Parakeet EOU 120M, Nemotron streaming, Silero VAD, Sortformer, CamPlusPlus, Kokoro 82M, PocketTTS, Chatterbox — но другие пакеты репозиториев (`FluidInference/*` vs `aufklarer/*`), т.е. **те же веса в другой упаковке**.
- Вывод: как зависимость — безопасно (нет конфликтов, платформа ниже), но даёт второй параллельный стек «ASR/VAD/диаризация/TTS» и второй каталог моделей. Реалистично использовать FluidAudio **вместо** части speech-swift (например, диаризация + Parakeet), а не вместе.

### 12. Техдолг

**Дублирование**
- 29 файлов с именем `Configuration.swift` (по одному на модуль) — своя схема декодирования `config.json` в каждом; 7 файлов `WeightLoading.swift`, 5 `Vocabulary.swift`, 4 `Downloader.swift`, 3 `TextEncoder.swift`, 3 `StreamingSession.swift`.
- Загрузка весов: 4 крупных файла в `AudioCommon` (`HuggingFaceDownloader.swift` 835, `HuggingFaceTransfer.swift` 590, `HuggingFaceBackgroundTransfer.swift` 686, `HuggingFaceRepoManifest.swift` 363 = 2474 строки) плюс тонкие обёртки в модулях (`FlashSR/Downloader.swift` 28 строк, `MagpieTTS/Downloader.swift` 58, `MAGNeTDownloader.swift` 56, `StableAudio3Downloader.swift` 87) — обёртки не дублируют логику, но плодят точки входа.
- Две разные Whisper-реализации в одном репозитории (нативная и WhisperKit-бенчмарк), две Kokoro/PocketTTS-реализации при подключении FluidAudio.
- `AudioServer` содержит 11 видов реестра без роутов; `AudioServer` тянет `SourceSeparation`, `FlashSR`, `MAGNeTMusicGen`, `StableAudio3MusicGen` (`Package.swift:947-983`) — мёртвый вес в бинаре.

**Хардкоды и «магические» пути**
- Каталог кэша зашит: `~/Library/Caches/qwen3-speech/models/<org>/<model>/`, с legacy-миграцией старого плоского раскладки (`AudioCommon/HuggingFaceDownloader.swift:57-71`); env-override назван по историческому продукту — `QWEN3_CACHE_DIR` / `QWEN3_ASR_CACHE_DIR` (`:820-834`).
- Скачивание всегда с `revision: "main"` (`HuggingFaceRepoManifest.swift:86`) — пина версии модели нет; при обновлении репозитория на HF поведение меняется молча.
- `NativeInferenceConfiguration` требует **абсолютные локальные каталоги** (`gemmaDirectory`, `voxDirectory`, `whisperDirectory`, `vadDirectory`), сервис «never downloads or substitutes models» (`AudioServer/NativeInference.swift:12-30`), плюс проверка `decoder_layers == 32` для Whisper (`:186-188`).
- `/tmp` в коде: `AsrBenchmark/AsrBench.swift:250`, `AudioCLILib/RespondCommand.swift:90` (`/tmp/personaplex_debug`).
- Глобальный env для CoreML compute units (`SPEECH_COREML_COMPUTE_UNITS`) вместо per-model настройки.

**Тесты**
- Тестов много: 354 файла в `Tests`, 62 test target (у каждого модуля свой), ~1427 тестов в CI-прогоне (`runtime-ownership-2026-10-17.md`), TSan-прогон там же. **Утверждение «нет тестов» неверно.**
- Реальная проблема — 204 места `XCTSkip` в 148 файлах: большинство E2E-тестов требуют скачанных весов и в обычном прогоне пропускаются. Нет тестов на: параллельный вызов одной модели из двух задач, поведение `ModelCache` при вытеснении (его нет), консистентность `MODEL_REGISTRY` с реальными роутами.

**Документация vs код**
- `docs/shared-protocols.md:70-95` не перечисляет 5 фактических конформеров STT.
- `AudioServer.swift:1032-1034` обещает `/diarize`, `/compose` — их нет.
- Реестр: строка `silero-vad-v5-mlx` указывает на `modelId` `aufklarer/Silero-VAD-v6.2.1-MLX` (`ModelRegistry.swift:300-302` vs `SileroVAD.swift:73`).
- `docs/models/asr-wer.md` заявляет 52 языка Qwen3-ASR, карточки HF — 7; в той же таблице подпись «Parakeet is English-only (25 European languages)» (`docs/benchmarks/asr-wer.md:113`) противоречит строке `Russian … Parakeet INT8 11.49` (`:125`) — т.е. «English-only» неверно.

---

## Ограничения и риски

1. **Оценка «сервер экспонирует» сделана по коду, без запуска**: сборка требует macOS 15 + Xcode с MLX/CoreML и скачивания гигабайтов весов; в рамках аудита не выполнялась. Все утверждения о роутах — из чтения `AudioServer.swift`.
2. **Числа производительности — самоотчёт репозитория** (`docs/benchmarks/*.md`), не независимое измерение; железо в документах указано (M5 Pro 48 GiB, M2 Max), методика описана, но воспроизведение не выполнялось.
3. **Языковые метки HF-карточек не равны фактическому качеству**: `Qwen3-ASR` не тегирует `ru`, но внутренний бенчмарк даёт RU WER 16.35/10.52; `Qwen3-TTS` имеет RU-токен 2069, но не имеет RU-спикера. Реальное качество на русской речи с IT-терминами **не измерено ни для одной модели** (FLEURS — общий домен).
4. **WhisperKit-репозиторий переименован** (`argmaxinc/argmax-oss-swift`, продукт `WhisperKit` сохранён); `git describe` клона даёт `v0.17.0-39`, при наличии тегов до `v1.1.1` — история переписана, поэтому привязка «HEAD = 1.x» основана на содержимом `Package.swift`, а не на теге.
5. **FluidAudio traits** требуют Swift 6.2; на более старом тулчейне подтянется «полный» манифест (TTS + Diarizer + NER-движок) — вырастет размер бинаря.
6. **Лицензии моделей разные** (apache-2.0 / mit / cc-by-4.0 / openmdw-1.1 / openrail / other) — я фиксировал их по карточкам HF, юридическая оценка вне моего скоупа (см. отчёт K).
7. `SpeechCore` — **закрытый бинарный** `CSpeechCore.xcframework` (`Package.swift:711-715`, `url …/speech-core/releases/download/v0.0.14/…`), исходников пайплайна в репозитории нет → его поведение (VAD→STT→LLM→TTS, barge-in) проверяемо только эмпирически.

---

## Выводы для Runtime

**Что взять**

1. **Протокольный слой `AudioCommon/Protocols.swift` как отправную точку API.** Набор (`SpeechRecognitionModel`, `StreamingRecognitionSession/Model`, `SpeechGenerationModel`, `VoiceActivityDetectionModel`, `StreamingVADProvider`, `TurnCompletionProvider`, `SpeakerEmbeddingModel`, `SpeakerDiarizationModel`, `ForcedAlignmentModel`, `ModelMemoryManageable`) почти совпадает с требуемыми операциями. Но для сетевого рантайма его нужно расширить: методы должны быть `async throws` (сейчас `transcribe` синхронный и глотает ошибки: `ParakeetASR+Protocols.swift:5-13` возвращает `""` при ошибке), результат — структура с таймстемпами/языком/confidence, а не `String`.
2. **`ModelDescriptor` как основа каталога моделей**: `{id, modelID, backend, inputSampleRate, streaming}` уже `Codable` — это готовый формат строки `/v1/models`. Добавить: `license`, `sizeBytes`, `languages[]`, `quantization`, `revision/sha`, `operations[]`.
3. **`MLXCacheLimitCoordinator`** — правильная идея для мультимодельного процесса: единый process-wide бюджет кэша MLX, учитывающий всех владельцев и не поднимающий лимит при загрузке новой модели (`MLXCommon/MLXCacheLimit.swift:16-70`). Взять как есть.
4. **`InferenceGate` — но per-model, не глобальный.** Текущая реализация допускает ровно один активный permit; для «одновременных независимых сессий» нужен gate на экземпляр модели + пул экземпляров (N копий для конкурентных сессий) либо требование «модель конкурентна» в дескрипторе. Готовый паттерн разделения состояния: VAD-per-session (`AudioServer.swift:568-575`) — веса из кэша, состояние на сессию.
5. **Инфраструктуру загрузки моделей** (`HuggingFaceDownloader` + `RepoManifest`): резюмируемые range-загрузки, sha256 LFS, `HF_ENDPOINT`-зеркало, offline-mode, `cacheDir` — это 80% «model management». Не хватает: revision-пиннинга, удаления/GC, манифеста установленного, проверки свободного места.
6. **WhisperKit — брать как адаптер без опаски**: 0 тяжёлых зависимостей, уже есть работающий пример интеграции. Если нужен RU-ASR «здесь и сейчас» — Whisper large-v3(-turbo) через WhisperKit или нативный `WhisperASR` (CoreML) закрывает русский без вопросов.
7. **FluidAudio — рассматривать как альтернативный поставщик движков** (его `actor`-менеджеры лучше ложатся на «конкурентные сессии», чем «not thread-safe by design»), но не вместе со speech-swift: иначе две копии Parakeet/Kokoro/Silero/диаризации и три каталога моделей.

**Что не брать / переделать**

8. **Не копировать модель «модуль = модель»**. 61 таргет с 29 `Configuration.swift` — это цена «каждый сам себе рантайм». Для проекта нужен один слой `ModelSource` (скачать/проверить/распаковать) + один слой `Engine` (загрузить веса в бэкенд) + N адаптеров.
9. **Не повторять отсутствие backend-абстракции.** Если ONNX/CoreML/MLX должны сосуществовать, нужен интерфейс уровня «тензорный граф» (или, реалистичнее, интерфейс уровня модели: `ASREngine.load(url) -> ASRSession`), иначе каждая новая комбинация «модель×бэкенд» — новый модуль. Опыт репозитория показывает: 80 CoreML-файлов и 277 MLX-файлов не разделяют ни одной строки вычислений.
10. **Не тянуть `AudioServer` как архитектурный образец**: ручные `switch` по строкам движков, 19 сильно типизированных `loadXxx` в протоколе, реестр с недостижимыми видами и комментарии, обещающие несуществующие роуты. Для рантайма нужен реестр-данные + фабрика `engine → adapter`, а не правки в трёх местах на каждый движок.
11. **Не строить на «not thread-safe by design»**: для сервера с независимыми сессиями это означает либо глобальную сериализацию (как сейчас → нет конкурентности), либо N копий весов (память × N). Правильный компромисс: дескриптор объявляет `concurrency: serial | reentrant`, рантайм держит пул экземпляров с бюджетом памяти и вытеснением по LRU.
12. **Русский**: из коробки реально доступны Nemotron 3.5 (75 языков, CoreML+MLX, streaming), Parakeet TDT v3 (25 языков, CoreML), Whisper large-v3-turbo (CoreML), Omnilingual CTC (1600 языков). Для TTS — CosyVoice3 и Chatterbox (в карточках есть `ru`), Supertonic (токенизатор), Qwen3-TTS (RU-токен без RU-спикера — качество под вопросом). RU-IT-домен (Kubernetes/PostgreSQL/kubectl) не покрыт ни одним бенчмарком — это отдельное измерение.

---

## Неизвестные / что измерить

1. **Реальное качество RU-ASR на IT-домене** для Nemotron 3.5, Parakeet v3, Whisper large-v3-turbo, Omnilingual 300M и Qwen3-ASR (8-bit). Нужен собственный набор: 30–60 фраз с терминами (Kubernetes, PostgreSQL, Istio, CI/CD, kubectl, Helm, Grafana), именами сервисов и цифрами; метрики WER + WER по терминам.
2. **Смешение языков (code-switching)**: «задеплой в kubernetes через kubectl» — ни один бенчмарк репозитория такого не меряет.
3. **Пропускная способность при N параллельных сессиях**: сколько памяти стоит вторая копия каждой модели (Qwen3 0.6B ~976 MB peak, 1.7B ~2563 MB по `docs/models/asr-model.md:9-22`; Nemotron/Parakeet/Whisper — не измерялось) и где предел на 16/32/64 GB.
4. **Idle-стоимость**: RSS процесса после загрузки 1/3/10 моделей и после 10 минут простоя; есть ли у CoreML/MLX механизм возврата памяти ОС (в репозитории не нашёл).
5. **Задержка первого запроса** (CoreML specialization) — в доке упоминается «первый запрос после смены модели/compute units платит specialization» (13.8 s на Qwen3 CoreML, `runtime-ownership-2026-10-17.md`); нужно измерить для каждой модели, которую хотим держать «тёплой».
6. **Поведение при нехватке памяти**: репозиторий документирует `MetalBudget.canFit` (`MLXCommon/MetalBudget.swift:50-56`), но политики вытеснения нет. Нужно проверить, что происходит при OOM на macOS (Metal OOM vs CoreML eviction) — не проверял.
7. **WhisperKit vs нативный WhisperASR на русском**: сравнение в репозитории сделано на английском FLEURS (`asr-wer.md:50-64`); RU-паритет не измерен.
8. **FluidAudio `AsrManager`/`VadManager` под нагрузкой**: заявлено `actor`, но реальная пропускная способность (сколько одновременных `transcribe` переживает) не измерялась; в `Documentation/API.md` ограничений не описано.
9. **Существование/стабильность `MOSS-Transcribe-Diarize` на русском**: карточка даёт `multilingual, en, de, fr, ar`; RU не заявлен — проверить DER/WER на RU вручную.
10. **Лицензионная совместимость** конкретных весов (cc-by-4.0 у Parakeet, openmdw-1.1 у Nemotron, openrail у Supertonic) для распространения в составе рантайма — вне моего скоупа, см. отчёт K.

---

## Источники

**Код speech-swift** (sha `cbad3be29480825707890490f446b19d7288be2d`):
- `Package.swift:1-9` (tools/платформы), `:241-265` (зависимости), `:757-762` (CSpeechCore binaryTarget), `:947-983` (AudioServer deps) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift
- `Sources/AudioCommon/Protocols.swift:60-455` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/Protocols.swift
- `Sources/SpeechRuntime/{ModelDescriptor,ModelCache,ModelRuntime,InferenceGate}.swift` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift
- `Sources/AudioServer/ModelRegistry.swift:96-404`, `Sources/AudioServer/AudioServer.swift:96-340,538-700,1028-1034,1199-1400,1600-1900`, `Sources/AudioServer/NativeInference.swift:12-30,180-310`, `Sources/AudioServer/RealtimeVAD.swift:1-60`, `Sources/AudioServer/*Speech.swift` и `*Transcriptions.swift` (префикс имени — вендорный маркер, в выводе инструментов маскируется как `[OI]`)
- `Sources/SpeechVAD/{SileroVAD,WeSpeaker,SortformerDiarizer,SmartTurn,DiarizationPipeline,Community1DiarizationPipeline,ReDimNet2Speaker}.swift`
- `Sources/Qwen3ASR/Qwen3ASR.swift:117-125`, `Sources/ParakeetASR/ParakeetASR.swift:11-48`, `Sources/ParakeetASR/ParakeetASR+Protocols.swift:1-48`, `Sources/WhisperASR/WhisperASR.swift:1-190`, `Sources/WhisperASR/WhisperCoreMLRuntime.swift:52-128`
- `Sources/Qwen3TTS/Configuration.swift:196-211`, `Sources/Qwen3TTS/Qwen3TTS+Protocols.swift:27-40`, `Sources/KokoroTTS/Configuration.swift:18`, `Sources/MagpieTTS/Configuration.swift:57-70`
- `Sources/MLXCommon/MLXCacheLimit.swift:9-70`, `Sources/MLXCommon/MetalBudget.swift:1-58`, `Sources/AudioCommon/CoreMLComputeUnits.swift:22-71`, `Sources/AudioCommon/HuggingFaceDownloader.swift:57-71,820-834`, `Sources/AudioCommon/HuggingFaceRepoManifest.swift:86-163`
- `Sources/AsrBenchmark/Engine.swift:21-40,100-140`, `Sources/AsrBenchmark/EngineWhisperKit.swift:1-40`
- `Sources/SpeechCore/VoicePipeline.swift:1-90,173-340`, `Sources/SpeechCore/MemoryTier.swift:1-40`

**Документация speech-swift** (все — permalink на тот же sha):
- `docs/shared-protocols.md:39-260,580-620` (протоколы, thread safety)
- `docs/inference/model-runtime.md` (ownership/gate/cache), `docs/inference/mlx-memory.md`, `docs/inference/cache-and-offline.md:1-120`
- `docs/benchmarks/asr-wer.md:50-64,110-127` (WhisperKit vs native, RU WER), `docs/benchmarks/runtime-ownership-2026-10-17.md` (сериализация, TSan, 1427 тестов)
- `docs/models/asr-model.md:1-30` (варианты Qwen3-ASR и RSS), `docs/models/tts-model.md:60-225` (языковые токены Qwen3-TTS), `docs/models/omnilingual-asr.md:1676-1716`, `docs/inference/speaker-diarization.md:21-210,517`, `docs/inference/language-identification.md:1-40`, `docs/models/nemotron-asr-streaming.md:65`

**Внешние проекты:**
- WhisperKit / argmax-oss-swift @ `9f99b5cb6a907730543823ad271b82532782a899`: `Package.swift:32-33` (единственная зависимость), `Sources/WhisperKit/Core/Models.swift:14-21` (CoreML), `Sources/WhisperKit/Core/Configurations.swift:11-19` (downloadBase/modelFolder), `Sources/SpeakerKit/Pyannote/PyannoteDiarizer.swift:36-73`, теги `v1.0.0/v1.1.0/v1.1.1`; v0.18.0 `Package.swift:34` — `swift-transformers .upToNextMinor(from: 1.1.6)`
- FluidAudio @ `1b0ed50d686873128a4129b3510ce3914c849bab`: `Package.swift` (deps: []), `Package@swift-6.2.swift` (traits), `Sources/FluidAudio/ModelRegistry.swift:33-120`, `Sources/FluidAudio/ModelNames.swift:6-80`, `Sources/FluidAudio/ASR/Parakeet/SlidingWindow/TDT/AsrManager.swift:6`, `Sources/FluidAudio/VAD/VadManager.swift:14`, `Sources/FluidAudio/Shared/MLModelConfigurationUtils.swift:38-49`, `README.md:43-55`, `Documentation/API.md:13-430`

**Hugging Face (проверено 2026-10-25):**
- `https://huggingface.co/api/models?author=aufklarer&limit=1000` — 166 публичных моделей; все 117 id из кода найдены
- `https://huggingface.co/api/models/aufklarer/Qwen3-ASR-0.6B-MLX-4bit` — language: en,zh,ja,ko,fr,de,es; license apache-2.0; sha `bc441bd`
- `https://huggingface.co/api/models/aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8` — 75 языков, `ru` присутствует
- `https://huggingface.co/api/models/aufklarer/Whisper-Large-v3-Turbo-CoreML` — `multilingual`, license mit
- `https://huggingface.co/api/models/aufklarer/Parakeet-TDT-v3-CoreML-INT8-30s` (тегов языков нет) и `https://huggingface.co/api/models/FluidInference/parakeet-tdt-0.6b-v3-coreml` — 25 языков, `ru`,`uk` присутствуют, license cc-by-4.0
- `https://huggingface.co/api/models/aufklarer/Chatterbox-Multilingual-MLX-fp16` — 12 языков, `ru` есть; `…/CosyVoice3-0.5B-MLX-bf16` — 9 языков, `ru` есть; `…/Qwen3-TTS-12Hz-1.7B-Base-MLX-bf16` — 7 без `ru`; `…/Kokoro-82M-CoreML` — 9 без `ru`; `…/Magpie-TTS-Multilingual-357M-MLX-8bit` — 9 без `ru`; `…/Cohere-Transcribe-2B-MLX-8bit` — 14 без `ru`; `…/Voxtral-Mini-3B-2507-MLX-8bit` — 8 без `ru`; `…/Kyutai-STT-1B-MLX-8bit` — en,fr; `…/Canary-180M-Flash-CoreML` — en,de,es,fr; `…/Parakeet-EOU-120M-CoreML-INT8` — en; `…/SpeechBrain-ECAPA-VoxLingua107-21M-MLX` — multilingual; `…/Sortformer-Diarization-CoreML` — en
