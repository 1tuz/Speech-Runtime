# A1. Аудит sherpa-onnx как сервера и как движка-библиотеки

Дата: 2026-10-25. Ревизия: `532f1b5a8a207bf53b116b747207c1506696c843` (ветка master,
версия `2.0.0`, релиз от 2026-10-23). Лицензия репозитория — Apache-2.0.
Все утверждения ниже проверены по исходникам локального клона `/tmp/srr/repos/sherpa-onnx`;
ссылки на веб-источники (docs, GitHub API, crates.io) помечены URL.

Рамка: цель — универсальный локальный Speech Runtime («Ollama для речевых моделей»),
клиент явно указывает модель и операцию. Поэтому sherpa-onnx оценивается в двух ролях:
(а) готовый сервер, который можно запустить как есть; (б) ONNX-движок-библиотека, который
Runtime будет вызывать из своего процесса/адаптера.

---

## Итог

1. **Как «runtime» sherpa-onnx не годится, как ONNX-движок — годится почти идеально.**
   У него нет ни каталога моделей, ни ленивой загрузки/выгрузки, ни выбора модели в запросе:
   модель задаётся путями к файлам в конфиге **на старте процесса** и живёт до выхода.
   Зато библиотека покрывает почти весь чеклист операций (online/offline ASR, VAD,
   диаризация, speaker embedding + identification, KWS, пунктуация, TTS, audio tagging,
   language ID, denoise, source separation) и имеет стабильный C API.
2. **Серверов в репозитории три семейства и все они — «одна модель на процесс».**
   C++ `sherpa-onnx-online-websocket-server` и `sherpa-onnx-offline-websocket-server`
   (websocketpp, **без TLS**), Python `streaming_server.py` / `non_streaming_server.py`
   (библиотека `websockets`, TLS опционально), плюс Java-пример. Ни один не умеет
   переключать модель, грузить её по требованию или выгружать.
3. **Модель конкурентности у C++-сервера — ровно то, что нужно Runtime:**
   один `OnlineRecognizer` (общие веса) + по одному `OnlineStream` на соединение +
   батчирование `DecodeStreams(ss, n)` до `max_batch_size` (по умолчанию 5) в пуле
   work-потоков. Это подтверждает, что «общие веса, независимые сессии, батч-декод» —
   рабочая и уже реализованная схема.
4. **Но `DecodeStreams` — не «бесплатный» параллелизм.** Он батчит несколько потоков в один
   вызов энкодера (трансдьюсер/CTC), а `OnlineStream` внутри защищён мьютексом только для
   фичей; `GetResult()` возвращает **мутабельную ссылку без блокировки**. Параформер
   вообще не батчируется (`DecodeStream` в цикле). Вызывать один `OnlineRecognizer`
   из многих потоков одновременно — на свой риск; официально «thread-safe» заявлено
   только для `DecodeStreams` в offline-сервере (комментарий в коде).
5. **Hotwords/contextual biasing есть только у трансдьюсеров** (icefall-zipformer и
   NeMo-трансдьюсеры), только при `decoding_method=modified_beam_search`, через
   `ContextGraph`; поддерживаются per-stream хотворды (`CreateStream(hotwords)`).
   Для CTC, Paraformer, Whisper, SenseVoice, Moonshine — **нет**. У Qwen3-ASR есть
   отдельное поле `hotwords` в конфиге модели (prompt-подсказка, другой механизм).
   Это критично для русско-английской IT-терминологии: biasing доступен только
   на zipformer-трансдьюсерах (ru-vosk streaming) и NeMo-трансдьюсерах (GigaAM RNNT).
6. **Русский покрыт лучше, чем у большинства конкурентов, но с оговорками по лицензиям.**
   Offline: GigaAM v2/v3 (CTC и RNNT, v3 — с пунктуацией, 163–170 МБ), zipformer-ru-2025-04-20.
   Streaming: `streaming-zipformer-small-ru-vosk-2025-08-16` (int8 24 МБ) и
   `streaming-t-one-russian-2025-09-08` (CTC, **8 кГц**, 128 МБ). GigaAM — MIT с 2024-12,
   но конкретные старые пакеты в релизе содержат `GigaAM License_NC.pdf` (non-commercial).
7. **CoreML EP формально есть, но на macOS он в значительной части сборок выключен.**
   `session.cc` добавляет `OrtSessionOptionsAppendExecutionProvider_CoreML(sess_opts, 0)`
   только при `__APPLE__ && ORT_API_VERSION>=15 && !SHERPA_ONNX_DISABLE_COREML`; все
   `cmake/onnxruntime-osx-*-static.cmake` выставляют `-DSHERPA_ONNX_DISABLE_COREML`.
   То есть в **статических** сборках (в т.ч. xcframework, который тянет Swift Package)
   CoreML недоступен; в shared-сборках — доступен, но без настройки флагов (flags=0) и
   без единого теста в CI (`grep provider=coreml` по `.github` не даёт совпадений).
8. **Модели распространяются как tarball-ассеты GitHub Releases, без менеджера.**
   `asr-models` (498 ассетов), `tts-models` (646), `speaker-recongition-models` (36).
   В коде нет ни манифеста, ни версионирования, ни команды `pull/rm/list`; метаданные
   модели читаются из ONNX-метадаты (`model_type` и т.п.), а не из внешнего дескриптора.
   Для Runtime это значит: каталог моделей и загрузчик придётся строить самим.
9. **Проект очень живой, но с bus-factor 1.** 265 коммитов за 6 месяцев, 155 за 3 месяца,
   релизы примерно раз в месяц (v1.13.0 2026-04-28 → v2.0.0 2026-10-23), 137 из 265
   коммитов — один автор (Fangjun Kuang). v2.0.0 — ломающий: удалён espeak-ng
   (`--vits-data-dir` теперь игнорируется), что напрямую бьёт по русским piper-TTS.
10. **Вывод: брать как ONNX-адаптер (in-process или отдельный процесс на модель), не брать
    как готовый сервер.** Runtime должен сам владеть каталогом моделей, реестром
    загруженных экземпляров, TTL/ref-count, выбором модели в запросе и мультиплексированием
    сессий; sherpa-onnx даёт для этого все нужные примитивы (`CreateStream`, `DecodeStreams`,
    `IsReady`, `IsEndpoint`, `Reset`, C API), но не даёт политики.

---

## Факты

### 1. Серверы: что именно есть в репозитории

| Сервер | Файлы | Транспорт | TLS |
|---|---|---|---|
| C++ online | `sherpa-onnx/csrc/online-websocket-server{,-impl}.cc/.h` | websocketpp + asio | нет |
| C++ offline | `sherpa-onnx/csrc/offline-websocket-server{,-impl}.cc/.h` | websocketpp + asio | нет |
| Python online | `python-api-examples/streaming_server.py` | `websockets` | да (опция `--certificate`) |
| Python offline | `python-api-examples/non_streaming_server.py` | `websockets` | да |
| Python HTTP (статика) | `python-api-examples/http_server.py` | встроен в оба сервера | — |
| Java | `java-api-examples/src/websocketsrv/AsrWebsocketServer.java` | JNI + websocket | нет |

- Сборка серверов включается опцией `SHERPA_ONNX_ENABLE_WEBSOCKET`, по умолчанию `ON`
  (`CMakeLists.txt:55`), цели — `sherpa-onnx/csrc/CMakeLists.txt:762-787`.
- Отсутствие TLS зафиксировано самим кодом: `#include "websocketpp/config/asio_no_tls.hpp"
  // TODO(fangjun): support TLS` в обоих заголовках
  (`online-websocket-server-impl.h:24`, `offline-websocket-server-impl.h:19`).
- CLI-флаги C++ online-сервера: `--port` (6006), `--num-io-threads` (1),
  `--num-work-threads` (3) — `online-websocket-server.cc:51-67`.
  Offline-сервер: те же три флага (`offline-websocket-server.cc:51-67`).

### 2. Протоколы (проверено по коду)

**C++ online** (`online-websocket-server-impl.cc:322-360`):
- клиент → сервер: **бинарные** кадры = сырые `float32` PCM (little-endian, `reinterpret_cast<const float*>`,
  `payload.size()/sizeof(float)` сэмплов); текстовая строка `"Done"` = конец потока;
- сервер → клиент: **текстовые** JSON-кадры — сериализация `OnlineRecognizerResult::AsJsonString()`
  с полями `text, tokens, timestamps, ys_probs, lm_probs, context_scores, segment, words,
  num_trailing_blanks, start_time, is_final, is_eof`
  (`online-recognizer.cc:56-72`); при исчерпании входа и отсутствии готовых фичей отправляется
  строка `"Done!"` (`online-websocket-server-impl.cc:151-153`);
- частота входа задаётся флагом `--input-sample-rate` (по умолчанию 16000,
  `online-websocket-server-impl.h:70`), ресемплинг — внутри `OnlineStream::AcceptWaveform`
  через `resampler_` (`features.cc:85-112`).

**C++ offline** (`offline-websocket-server-impl.cc:196-260`): собственный бинарный формат —
первые 4 байта LE = sample_rate (int32), следующие 4 байта LE = ожидаемый размер данных в
байтах (int32), далее `float32`-сэмплы; поток может быть разбит на произвольное число
сообщений, но первое должно быть ≥ 8 байт; текстовая строка `"Done"` закрывает соединение.
Один и тот же сокет может отправить несколько файлов подряд (после декодирования
`ConnectionData` очищается, `:243-253`). Описание протокола — в комментарии
`offline-websocket-server-impl.h:28-40`.

**Python online** (`streaming_server.py:806-830`): бинарный кадр → `np.frombuffer(dtype=float32)`,
строка `"Done"` → конец; ответ — `{"text": ..., "segment": N}` (`:757-766`).
Частота входа жёстко берётся из конфига модели, TODO на ресемплинг
(«we assume the sampling rate of the received audio samples equal to --sample-rate»).

**Python offline** (`non_streaming_server.py:796-828`): тот же 8-байтовый заголовок,
что и в C++ offline; ответ — **просто строка текста**, пустой результат кодируется
литералом `"<EMPTY>"` (`:931-936`).

Вывод по протоколам: они **несовместимы между собой** (три разных формата кадра и два
разных формата ответа), не версионированы, не имеют поля «какая модель» и не описывают
ошибки структурированно.

### 3. Модель конкурентности: один recognizer, много streams, батч-декод

C++ online-сервер:
- `OnlineWebsocketDecoder` владеет **одним** `std::unique_ptr<OnlineRecognizer> recognizer_`
  (`online-websocket-server-impl.h:110`), создаётся один раз в конструкторе.
- На каждое соединение — свой `OnlineStream`: `GetOrCreateConnection()` вызывает
  `recognizer_->CreateStream()` и складывает в `std::map<connection_hdl, shared_ptr<Connection>>`
  под `mutex_` (`online-websocket-server-impl.cc:64-76`). `Connection` содержит очередь
  `std::deque<std::vector<float>> samples` и собственный `std::mutex`
  (`online-websocket-server-impl.h:31-56`).
- Цикл планировщика: `asio::steady_timer` каждые `loop_interval_ms` (10 мс,
  `online-websocket-server-impl.h:61`). `ProcessConnections()` отбирает соединения, у которых
  `recognizer_->IsReady(stream)` и которые не в `active_`, кладёт их в `ready_connections_`
  и помечает в `active_` (`:120-170`).
- `Decode()` забирает из очереди до `max_batch_size` (5) потоков и вызывает
  **один** `recognizer_->DecodeStreams(s_vec.data(), s_vec.size())` (`:185-215`).
  Это и есть батчирование нескольких сессий в один прогон энкодера.
- `active_` — множество `connection_hdl`, гарантирующее, что один и тот же поток не
  декодируется двумя work-потоками одновременно (`:167`, `:216`).
- Пул: `io_conn` (сеть) и `io_work` (нейросеть+декод), потоки создаются в `main`
  (`online-websocket-server.cc:104-118`), `num_work_threads` по умолчанию 3.

Offline-сервер: один `OfflineRecognizer recognizer_` (`offline-websocket-server-impl.cc:50`),
очередь `streams_` пар (hdl, данные), `Decode()` берёт до `max_batch_size` (5) элементов,
создаёт по `OfflineStream` на элемент и вызывает `recognizer_.DecodeStreams(...)` с
комментарием **«Note: DecodeStreams is thread-safe»** (`:103-104`).

Python-сервер: `ThreadPoolExecutor(max_workers=nn_pool_size)` (`streaming_server.py:540`),
`stream_consumer_task()` копит батч из `asyncio.Queue` до `max_batch_size` и вызывает
`recognizer.decode_streams(stream_list)` в executor'е (`:568-586`); N воркеров-корутин
делают то же самое параллельно. Дефолты CLI: `--nn-pool-size 1`, `--max-batch-size 3`,
`--max-wait-ms 10`, `--max-active-connections 200`, `--max-queue-size 32`,
`--max-message-size 1 MiB` (`streaming_server.py:326-378`) — то есть «из коробки»
Python-сервер однопоточный по NN и с батчем 3.

**Ограничения этой схемы (важно для Runtime):**
- `OnlineStream::GetResult()` возвращает **неконстантную ссылку** `OnlineTransducerDecoderResult&`
  без взятия мьютекса (`online-stream.cc:71-73`), а мьютекс `mutex_` защищает только
  фиче-экстрактор и счётчики (`:24-70`). `SetResult`/`SetStates` тоже без блокировки
  (`:71`, `:108-112`).
- Батч-декод реализован не для всех семейств: трансдьюсер — да
  (`online-recognizer-transducer-impl.h:288-360`), online CTC — при
  `model_->SupportBatchProcessing()` (`online-recognizer-ctc-impl.h:120-126`),
  streaming Paraformer — **нет**, явный `// TODO(fangjun): Support batch size > 1`
  (`online-recognizer-paraformer-impl.h:171-176`).
- Offline CTC: батч только если `SupportBatchProcessing()`; `telespeech` и `wenet` возвращают
  `false` (`offline-telespeech-ctc-model.h:61`, `offline-wenet-ctc-model.h:64`),
  а omnilingual принудительно исключён из батча (`offline-recognizer-ctc-impl.h:218-224`).
- Whisper: `DecodeStreams` вызывает `decoder_->SetConfig(config_.model_config.whisper)`
  **на общем объекте декодера** перед каждым декодированием
  (`offline-recognizer-whisper-impl.h:70-85`), т.е. параллельные вызовы с разными
  language/task на одном recognizer'е — гонка. `OfflineRecognizer::SetConfig` существует
  (`offline-recognizer.h`), но `OnlineRecognizer` такого метода не имеет.
- В Rust-обёртке безопасность задекларирована как «thread-safe for single-object usage»
  через `unsafe impl Send/Sync` на каждый тип (`rust/sherpa-onnx/src/online_asr.rs:355-357,459-461`),
  т.е. это утверждение обёртки, а не гарантия C++-ядра о конкурентных вызовах
  на одном объекте.

### 4. Поддерживаемые операции и типы моделей (по диспетчерам)

**Online ASR** — `OnlineRecognizerImpl::Create` (`online-recognizer-impl.cc:61-140`):
- `transducer` (icefall: conformer / zipformer / zipformer2 / lstm) — по непустому
  `transducer.encoder`; выбор icefall vs NeMo — по числу выходов decoder-модели
  (`GetOutputCount() == 1` → icefall, иначе NeMo, `:112-130`);
- `nemo_transducer` (+ отдельный путь `nemo_parakeet_unified_streaming` по метаданным
  `streaming_model_type`, `:46-52`);
- `paraformer` (streaming);
- `wenet_ctc`, `zipformer2_ctc`, `nemo_ctc`, **`t_one_ctc`** (`:132-140`);
- бэкенды RKNN/QNN — отдельные impl'ы (`:62-108`).

**Offline ASR** — `OfflineRecognizerImpl::Create` (`offline-recognizer-impl.cc:240-300`):
`sense_voice`, `funasr_nano`, `qwen3_asr`, `paraformer`, `nemo_ctc`, `zipformer_ctc`,
`tdnn`, `wenet_ctc`, `omnilingual`, `medasr`, `fire_red_asr_ctc`, `dolphin` (все → CTC-impl),
`whisper`, `cohere_transcribe`, `fire_red_asr`, `moonshine` (v1: `preprocessor`),
`moonshine v2` (`merged_decoder`), `canary`, `transducer`, `nemo_transducer`, `telespeech_ctc`.
Итого **19 семейств** офлайн-ASR, включая Qwen3-ASR (`offline-recognizer-qwen3-asr-impl.h`)
и Canary. Явного `GigaAM`-типа нет — GigaAM конвертируется в `nemo_ctc`/`nemo_transducer`.

**VAD** — `VadModel::Create` (`vad-model.cc:28-56`): ровно два семейства —
`silero_vad` и `ten_vad`. Оба требуют 16 кГц; silero v5 — окно ровно 512 сэмплов
(`silero-vad-model.cc:218-221`), ten-vad — настраиваемое `window_size`
(`ten-vad-model.cc:182`, проверка `sample_rate_ != 16000` на `:196-199`).
Обёртка `VoiceActivityDetector` даёт `AcceptWaveform`, `IsSpeechDetected`, `Front/Pop`,
`Flush`, `Reset`, кольцевой буфер (`voice-activity-detector.h:26-66`).

**Диаризация** — только **offline**: `OfflineSpeakerDiarization`
(`offline-speaker-diarization.h:19-90`), конфиг = pyannote-сегментация + speaker embedding
+ fast clustering (`num_clusters`/`threshold`, `compute_confidence` —
`fast-clustering-config.h:15-33`), плюс `min_duration_on=0.3`, `min_duration_off=0.5`.
Онлайн-диаризации (streaming diarization) в коде **нет**.

**Speaker embedding / identification** — `SpeakerEmbeddingExtractor`
(модели `wespeaker`, `3d-speaker`, `nemo` — по метаданным `model_type`,
`speaker-embedding-extractor-impl.cc:73-83`), `SpeakerEmbeddingManager`
с `Add/Remove/Search/GetBestMatches/Verify/Score/GetEmbedding/GetAllSpeakers`
(`speaker-embedding-manager.h:19-120`). Это готовая база для «идентификация по голосу»
на стороне Runtime.

**KWS** — `KeywordSpotter` только на трансдьюсерах (`keyword-spotter-transducer-impl.h`),
конфиг `keywords_file`, `keywords_score=1.0`, `keywords_threshold=0.25`
(`keyword-spotter.h:53-85`).

**Пунктуация** — offline `ct_transformer` (`offline-punctuation-model-config.h:13-20`),
online `cnn-bilstm` (`online-punctuation-cnn-bilstm-impl.h`). Регистр — отдельно
(`offline-diacritization-catt-impl.h`, `OfflineDiacritization`).

**TTS** — `OfflineTts` с impl'ами `vits`, `matcha`, `kokoro`, `kitten`, `zipvoice`,
`pocket`, `supertonic` (`offline-tts-impl.cc:43-57`); генерация — `Generate(text, sid,
callback)` с потоковым колбэком (`offline-tts.h:131-160`).

**Прочее** — `AudioTagging` (CED и zipformer, `audio-tagging-model-config.h:14-20`),
`SpokenLanguageIdentification` (Whisper-based, `spoken-language-identification.h:18-60`),
`OfflineSpeechDenoiser`/`OnlineSpeechDenoiser` (dpdfnet, gtcrn, voxlock),
`OfflineSourceSeparation` (spleeter, uvr).

### 5. Hotwords / contextual biasing

- Реализация — `ContextGraph` (`context-graph.h`) + `modified_beam_search`.
- Валидация запрещает hotwords при любом другом методе декодирования:
  «Please use --decoding-method=modified_beam_search if you provide --hotwords-file»
  (`online-recognizer.cc:154-160`).
- Работает только в трансдьюсерных impl'ах: `online-recognizer-transducer-impl.h`
  (`InitHotwords`, `hotwords_graph_`, `:444-520`), `online-recognizer-transducer-nemo-impl.h`,
  `offline-recognizer-transducer-impl.h:278-311`, `offline-recognizer-transducer-nemo-impl.h:295-322`.
- **Per-stream** хотворды: `OnlineRecognizer::CreateStream(const std::string &hotwords)`
  (`online-recognizer.h:195`) — граф контекста строится на поток, дефолтные хотворды
  мержатся (`online-recognizer-transducer-impl.h:212-244`). То же для offline
  (`offline-recognizer.h:100`). Это ровно тот примитив, который нужен Runtime для
  per-request словаря терминов.
- **Не поддерживают**: `online-recognizer-ctc-impl.h`, `online-recognizer-paraformer-impl.h`,
  `offline-recognizer-whisper-impl.h`, `offline-recognizer-sense-voice-impl.h`,
  `offline-recognizer-ctc-impl.h` — `grep -c hotwords` по этим файлам даёт 0.
- Исключение: `offline-qwen3-asr-model-config.h:28` имеет поле `hotwords`
  (prompt-подсказка для LLM-декодера, `BuildSourceIds(hotwords, ...)` в
  `offline-recognizer-qwen3-asr-impl.h:48`) — семантически другой механизм.

### 6. Execution providers и CoreML на macOS

- Enum провайдеров: `cpu, cuda, coreml, xnnpack, nnapi, trt, directml, spacemit, openvino`
  (`provider.h:15-26`, парсинг `provider.cc:17-40`).
- CoreML: `case Provider::kCoreML` в `session.cc:462-471`:
  ```cpp
  #if defined(__APPLE__) && (ORT_API_VERSION >= 15) && !defined(SHERPA_ONNX_DISABLE_COREML)
    uint32_t coreml_flags = 0;
    (void)OrtSessionOptionsAppendExecutionProvider_CoreML(sess_opts, coreml_flags);
  #else
    SHERPA_ONNX_LOGE("CoreML is for Apple only since onnxruntime>=1.15. Fallback to cpu!");
  #endif
  ```
  Флаги не настраиваются (`0` = без ANE/MLProgram/`MLComputeUnits`-тюнинга).
- Все статические macOS-конфиги **отключают** CoreML:
  `cmake/onnxruntime-osx-arm64-static.cmake:61`, `onnxruntime-osx-universal-static.cmake:62`,
  `onnxruntime-osx-x86_64-static.cmake:61` — `add_definitions(-DSHERPA_ONNX_DISABLE_COREML)`
  с комментарием «disable coreml when using static onnxruntime lib».
- Выбор статической/динамической сборки — `cmake/onnxruntime.cmake:41-74`
  (`BUILD_SHARED_LIBS` → `onnxruntime-osx-*` vs `*-static`).
- Swift Package (`Package.swift:23-46`) поставляет **static** xcframework
  (`sherpa-onnx-v2.0.0-macos-static.xcframework.zip`), т.е. в дефолтном SPM-сценарии
  CoreML выключен. Shared-вариант существует отдельным продуктом `sherpa-onnx-shared`.
- В CI нет ни одного запуска с `--provider=coreml` (grep по `.github` пуст);
  в тестах macOS собираются оба варианта (`lib_type: [static, shared]`,
  `.github/workflows/macos.yaml:70`), но провайдер не проверяется.
- `GetSessionOptionsImpl` ставит `SetIntraOpNumThreads(num_threads)` и
  `SetInterOpNumThreads(num_threads)` одинаково (`session.cc:209-222`) — при
  `num_threads=1` (дефолт online) это означает однопоточный CPU-инференс на сессию.

### 7. Биндинги

- **C API**: `sherpa-onnx/c-api/c-api.h`, 4861 строка, полный набор
  `SherpaOnnxCreate{Online,Offline}Recognizer`, `SherpaOnnxCreateOnlineStream`,
  `SherpaOnnxDecodeMultipleOnlineStreams`, `SherpaOnnxVoiceActivityDetector*`,
  `SherpaOnnxCreateOfflineSpeakerDiarization`, `SherpaOnnxSpeakerEmbeddingManager*`,
  `SherpaOnnxCreateKeywordSpotter`, `SherpaOnnxCreateOfflineTts`,
  `SherpaOnnxGetVersionStr()`. C++-обёртка — `cxx-api.h` (2124 строки).
- **Swift**: `swift-api-examples/SherpaOnnx.swift` (72 КБ) — ручной wrapper над C API:
  классы `SherpaOnnxRecognizer`, `SherpaOnnxOfflineRecognizer`,
  `SherpaOnnxVoiceActivityDetectorWrapper`, `SherpaOnnxOfflineSpeakerDiarizationWrapper`,
  `SherpaOnnxSpeakerEmbeddingExtractorWrapper`, `SherpaOnnxKeywordSpotterWrapper`,
  `SherpaOnnxOfflineTtsWrapper`, `SourceSeparator` и др. Распространяется как SPM
  binary target (xcframework, см. п.6).
- **Rust**: официальные крейты в самом репозитории — `sherpa-onnx` и `sherpa-onnx-sys`
  версии `2.0.0` (`sherpa-onnx/rust/sherpa-onnx/Cargo.toml:3`, `.../sherpa-onnx-sys/Cargo.toml:3`),
  опубликованы на crates.io: 675 735 загрузок, обновление 2026-10-23
  (https://crates.io/api/v1/crates/sherpa-onnx). Модули: `online_asr, offline_asr, vad,
  kws, tts, speaker_embedding, offline_speaker_diarization, audio_tagging,
  spoken_language_identification, resampler, wave, display, *_punctuation,
  *_speech_denoiser`. `build.rs` **скачивает** преднастроенные архивы
  (`sherpa-onnx-v{version}-osx-arm64-static-lib.tar.bz2` и т.п.) с GitHub Releases
  (`rust/sherpa-onnx-sys/build.rs:130-215, 370-420`), фичи `static` (дефолт) / `shared`.
- **Community**: `sherpa-rs` 0.6.8, последнее обновление 2025-10-05, 98 768 загрузок
  (https://crates.io/api/v1/crates/sherpa-rs) — альтернатива, но отстаёт от 2.0.0.
- Также: Python (pybind11), Java/Kotlin (JNI), Go, Dart/Flutter, .NET, Node addon,
  Pascal, HarmonyOS, WASM.

### 8. Формат моделей и распространение

- Модель = набор файлов (ONNX + `tokens.txt` + опционально `bpe.model`/`lexicon.txt`),
  пути передаются в конфиг. Пример online-трансдьюсера: `encoder.onnx`, `decoder.onnx`,
  `joiner.onnx`, `tokens.txt` (см. любой пример в `cxx-api-examples`).
- Метаданные читаются **из ONNX-модели**: `LookupCustomModelMetaData(meta_data,
  "model_type", ...)` (`offline-recognizer-impl.cc:360-400`), ключи `model_type`,
  `framework`, `streaming_model_type`, `quantization_type`, `num_layers`,
  `max_total_len`, `attention_mask_len`, `prompt_dictionary`.
- Дистрибуция — GitHub Releases: теги `asr-models` (498 ассетов), `tts-models` (646),
  `speaker-recongition-models` (36) (https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models).
  Формат — `.tar.bz2` с каталогом внутри. **Менеджера моделей в коде нет**: нет manifest,
  нет checksum-файла на уровне CLI, нет `download/list/remove`. Скрипты скачивания
  разбросаны по `.github/scripts/*.sh` и `scripts/<model>/run.sh`.
- Python-пакет `sherpa_onnx.cli` умеет только `text2token` (`python/sherpa_onnx/cli.py:33-90`).

### 9. Активность и релизы (git log)

- Всего коммитов в клоне: 2143; за последние 6 месяцев (с 2026-04-25): **265**;
  за 3 месяца (с 2026-07-25): **155**.
- Авторы за 6 месяцев: Fangjun Kuang — 137, далее Kyozzz 6, Hassan Kazi 6, matiaslin 5,
  Paolo Antinori 5, Joseph Mills 5 (остальные ≤4).
- Теги: `v2.0.0` 2026-10-23, `v1.13.8` 2026-09-10, `v1.13.7` 2026-09-01, `v1.13.6`
  2026-08-18, `v1.13.5` 2026-08-11, `v1.13.4` 2026-07-07, `v1.13.3` 2026-06-15,
  `v1.13.2` 2026-05-13, `v1.13.1` 2026-05-08, `v1.13.0` 2026-04-28 — примерно
  один релиз в месяц.
- `CHANGELOG.md` для 2.0.0 содержит, среди прочего: «Remove espeak-ng and piper-phonemize
  from sherpa-onnx (#4055)», «Support modified_beam_search with hotwords for streaming
  NeMo transducer models (#3895)», «Add --input-sample-rate option to online-websocket-server
  (#3977)», «Fix data race on shared PRNG in FunASR-Nano and Qwen3-ASR offline recognizers
  (#3912)», «Catch C++ exceptions in SherpaOnnxCreate* and return NULL in the C API (#4044)».

### 10. Русский язык: что реально доступно в ONNX

Проверено по ассетам релиза `asr-models` (GitHub API, 498 файлов) и документации
k2-fsa.github.io.

| Модель | Тип | Режим | Размер | Источник |
|---|---|---|---|---|
| `sherpa-onnx-nemo-ctc-giga-am-v3-russian-2025-12-16` | NeMo CTC | offline | 163 МБ | asr-models |
| `sherpa-onnx-nemo-ctc-punct-giga-am-v3-russian-2025-12-16` | NeMo CTC + пунктуация | offline | 163 МБ | asr-models |
| `sherpa-onnx-nemo-transducer-giga-am-v3-russian-2025-12-16` | NeMo RNNT | offline | 167 МБ | asr-models |
| `sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16` | NeMo RNNT + пунктуация | offline | 170 МБ | asr-models |
| `sherpa-onnx-nemo-ctc-giga-am-v2-russian-2025-04-19` | NeMo CTC | offline | 167 МБ | asr-models |
| `sherpa-onnx-nemo-transducer-giga-am-v2-russian-2025-04-19` | NeMo RNNT | offline | 172 МБ | asr-models |
| `sherpa-onnx-zipformer-ru-2025-04-20` | icefall zipformer | offline | 248 МБ | asr-models |
| `sherpa-onnx-zipformer-ru-int8-2025-04-20` | icefall zipformer int8 | offline | — | asr-models |
| `sherpa-onnx-streaming-zipformer-small-ru-vosk-2025-08-16` | zipformer2 transducer | **online** | 90 МБ | asr-models |
| `sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16` | то же, int8 | **online** | 24 МБ | asr-models |
| `sherpa-onnx-streaming-t-one-russian-2025-09-08` | T-one CTC | **online**, 8 кГц | 128 МБ | asr-models |
| `sherpa-onnx-nemo-fast-conformer-ctc-be-de-en-es-fr-hr-it-pl-ru-uk-20k` | multilingual CTC | offline | — | asr-models |

- GigaAM-модели конвертируются скриптами `scripts/nemo/GigaAM/` (`run-ctc-v3.sh`,
  `run-rnnt-v3.sh`, `run-ctc-v3-punct.sh`, ...); исходный проект —
  https://github.com/salute-developers/GigaAM (лицензия **MIT**, подтверждено на странице
  репозитория; v3 — 2025-11, multilingual — 2026-06).
- **Лицензионная оговорка**: страница документации для
  `sherpa-onnx-nemo-ctc-giga-am-russian-2024-10-24` явно пишет, что в пакете лежит
  `GigaAM License_NC.pdf` и «It is for non-commercial use only»
  (https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/russian.html).
  Для v2/v3 такой оговорки на странице нет, но проверять лицензию конкретного
  скачанного пакета обязательно.
- T-one: конвертация из https://github.com/voicekit-team/T-one, **ожидает 8 кГц**
  (https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/t-one-ctc-models.html).
- **Русский TTS**: `vits-piper-ru_RU-{ruslan,irina,dmitri,denis}-medium` (+`-int8`, `-fp16`)
  и `vits-mms-rus` в релизе `tts-models` (646 ассетов, проверено через GitHub API).
  Но в 2.0.0 удалён espeak-ng: `--vits-data-dir` теперь «deprecated and ignored»
  (`offline-tts-vits-model-config.cc:19-23, 58-64`), нужен `--vits-lexicon` или внешняя
  фонемизация с передачей `GenerationConfig.phoneme_codepoints`. Для piper-моделей
  `espeak-ng-data` в пакете остаётся, но sherpa-onnx его больше не читает.

---

## Ограничения и риски

1. **Нет управления жизненным циклом моделей.** Модель загружается в конструкторе
   recognizer'а и живёт до разрушения объекта. Нет `unload`, нет TTL, нет ref-count,
   нет LRU, нет лимита памяти. Для «Ollama-подобного» поведения всё это пишется снаружи.
2. **Нет выбора модели в запросе.** Ни один сервер не принимает имя модели в сообщении;
   модель фиксирована флагами командной строки. Мультиплексирование разных моделей
   в одном процессе невозможно без создания нескольких recognizer'ов вручную
   (и тогда память = сумма весов).
3. **Протоколы серверов не пригодны как публичный API.** Нет версии протокола, нет
   идентификатора сессии, нет структурированных ошибок, нет отмены запроса
   (`grep -i cancel` по `c-api.h` пуст), нет backpressure кроме 503 в Python-сервере
   (`streaming_server.py:649-655`), нет TLS в C++-серверах.
4. **Потокобезопасность ядра недокументирована.** `GetResult()`/`SetResult()`/`SetStates()`
   у `OnlineStream` без блокировки; `OfflineRecognizer::SetConfig` мутирует общее состояние
   (Whisper: language/task), а `OnlineRecognizer` такого метода вообще не имеет.
   Официально «thread-safe» помечен только `DecodeStreams` в offline-сервере.
   Практический вывод: **один recognizer — один владеющий поток декодирования**;
   параллелизм — через батч, а не через конкурентные вызовы.
5. **CoreML на macOS в дефолтной поставке недоступен** (static → `SHERPA_ONNX_DISABLE_COREML`),
   а там, где доступен, включается с `flags=0` без настройки compute units и без тестов.
   Ждать от CoreML EP ускорения «из коробки» нельзя — нужен замер и, вероятно,
   собственная сборка с shared onnxruntime.
6. **Батчинг неоднороден.** Paraformer (streaming) не батчируется, Wenet/TeleSpeech CTC
   не батчируются, omnilingual исключён. Для Runtime это означает разные профили
   пропускной способности у разных моделей при одном и том же коде адаптера.
7. **v2.0.0 — ломающий релиз** (удаление espeak-ng). Любой код, завязанный на
   `--vits-data-dir`, деградирует молча (только warning в лог). Пиннинг версии обязателен.
8. **Bus-factor 1.** 137 из 265 коммитов за полгода — один мейнтейнер; вся архитектура
   (facade → `*Impl::Create` → per-model impl) держится на его решениях.
   Риск: смена приоритетов, как это уже случилось с рядом других OSS-проектов.
9. **Размер репозитория и поверхности.** ~600 файлов в `csrc`, 19 семейств offline-ASR,
   63 `*-impl.h`; каждое новое поле конфига нужно протаскивать во все биндинги
   (это прямо описано в `CLAUDE.md` репозитория). Для Runtime это плюс (широта),
   но и риск: обновления могут ломать биндинги.
10. **Лицензии моделей ≠ лицензия кода.** Код Apache-2.0, но конкретные веса
    (GigaAM v1/v2 пакеты 2024, Vosk-производные, piper-голоса) имеют собственные условия;
    их надо проверять пофайлово при наполнении каталога.

---

## Выводы для Runtime

### Брать

1. **C API как единственную точку интеграции.** `sherpa-onnx/c-api/c-api.h` — стабильный
   плоский C-интерфейс, под который уже есть Rust-крейты, Swift-wrapper и Python.
   Runtime-адаптер (Rust или Swift) должен линковаться именно сюда, а не с C++ API:
   это снимает вопрос ABI и позволяет подменять сборку (static/shared, CoreML on/off).
2. **Схему «один recognizer = одни веса + N стримов».** Она уже реализована и проверена
   в `online-websocket-server-impl.cc`: `CreateStream()` на сессию, `IsReady()` как
   условие готовности, `DecodeStreams(ss, n)` как батч, `IsEndpoint()`/`Reset()` как
   сегментация. Runtime должен воспроизвести эту схему у себя, но с явным владением
   потоком декодирования (один decode-loop на модель, сессии — в очередь).
3. **Per-stream hotwords** (`CreateStream(hotwords)` + `modified_beam_search`) — готовый
   механизм для русско-английской IT-терминологии на zipformer-трансдьюсерах и NeMo RNNT.
   Это ключевая фича для целевого сценария (Kubernetes, PostgreSQL, Istio, kubectl).
4. **Набор операций покрывает чеклист почти целиком**: online/offline ASR, VAD (silero,
   ten-vad), offline-диаризация, speaker embedding + manager (identification/verification),
   KWS, пунктуация, TTS, audio tagging, language ID, denoise. Отдельные адаптеры под
   каждую операцию не нужны — нужен один адаптер с разными «ручками».
5. **Rust-крейты `sherpa-onnx` / `sherpa-onnx-sys` 2.0.0** — если Runtime на Rust,
   это кратчайший путь: `build.rs` сам скачивает преднастроенные библиотеки под
   macOS arm64 (`osx-arm64-static-lib` / `osx-arm64-shared-lib`).
6. **Русские модели уже в ONNX**: streaming — `zipformer-small-ru-vosk-int8` (24 МБ,
   16 кГц, трансдьюсер → hotwords работают) и `t-one-russian` (8 кГц, CTC);
   offline — GigaAM v3 CTC/RNNT (с пунктуацией) и `zipformer-ru-2025-04-20`.

### Не брать

1. **C++ websocket-серверы как транспорт Runtime.** Нет TLS, нет версии протокола,
   нет отмены, нет выбора модели, нет структурированных ошибок. Их стоит использовать
   только как референс алгоритма планировщика (`ProcessConnections` + `Decode`).
2. **Python `streaming_server.py`/`non_streaming_server.py` как основу.** Дублируют
   логику, но добавляют GIL, asyncio-очередь и отсутствие контроля частоты
   («we assume the sampling rate ... equal to --sample-rate»).
3. **Идею «модель на процесс» как единственную модель развёртывания.** Нужен реестр
   экземпляров: `model_id → {recognizer, refcount, last_used, thread}`, выгрузка по TTL
   и лимиту памяти — этого в sherpa-onnx нет вообще.
4. **CoreML EP как обещанное ускорение.** Сначала измерить; в дефолтной static-сборке
   его физически нет.

### Как интегрировать (эскиз, не реализация)

- Runtime владеет **каталогом моделей** (`~/.local/share/.../models/<family>/<id>/`),
  манифестом (`id`, `family`, `files[]`, `sha256`, `sample_rate`, `license`, `ops[]`) и
  загрузчиком с GitHub Releases — sherpa-onnx даёт только файлы, каталог строим сами.
- **Адаптер = один процесс-воркер на загруженную модель** (или in-process пул), внутри —
  один `SherpaOnnxOnlineRecognizer` / `SherpaOnnxOfflineRecognizer`, один decode-loop,
  очередь сессий; сессия = `SherpaOnnxOnlineStream` + состояние (`segment`, endpoint).
- **Единый API Runtime** (`asr.transcribe`, `asr.stream`, `vad`, `diarize`, `embed`,
  `tts`, `kws`) маппится на операции sherpa-onnx; выбор модели — параметр запроса,
  а не флаг процесса.
- **VAD и endpointing**: silero (512 сэмплов @16к) как отдельный лёгкий экземпляр,
  общий для всех сессий (он stateless между окнами при `Reset()`), плюс встроенный
  `IsEndpoint()` трансдьюсера.
- **Диаризация и speaker ID** — отдельный offline-путь: `OfflineSpeakerDiarization`
  (pyannote + clustering) и `SpeakerEmbeddingManager` (косинус, `Search`/`Verify`).
- **TTS**: только после решения вопроса фонемизации — либо `--vits-lexicon`,
  либо внешний фонемайзер + `phoneme_codepoints` (espeak-ng из sherpa-onnx удалён).

---

## Неизвестные / что измерить

1. **Реальная пропускная способность батч-декода** на Apple Silicon: сколько
   одновременных сессий `zipformer-small-ru-vosk-int8` держит M-серия при RTF<1
   с `max_batch_size` 1/5/10. В репозитории нет бенчмарков сервера; в docs есть только
   одиночные RTF (например, T-one: RTF 0.32 на одном ядре для 6.4 с аудио,
   https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/t-one-ctc-models.html).
2. **Работает ли CoreML EP на практике** для конкретных ONNX-моделей (zipformer encoder,
   GigaAM conformer, Whisper encoder) и даёт ли выигрыш против CPU при `num_threads=1`.
   Требуется своя сборка с shared onnxruntime и замер; в CI проекта такого теста нет.
3. **Потокобезопасность при конкурентных вызовах одного recognizer'а** — нужно
   экспериментально проверить (или запретить архитектурно): `GetResult()` возвращает
   мутабельную ссылку, а `OfflineRecognizer::SetConfig` меняет общее состояние Whisper.
4. **Качество русского на целевом домене** (IT-терминология, англицизмы, смешанная речь):
   сравнительных WER по `zipformer-small-ru-vosk` vs GigaAM v3 vs T-one в репозитории нет;
   нужен собственный прогон на размеченном наборе.
5. **Совместимость GigaAM v3-пакетов с `nemo_transducer`/`nemo_ctc` путями** —
   скрипты конвертации есть (`scripts/nemo/GigaAM/export-onnx-*-v3*.py`), но в docs
   раздел для v3 на момент проверки отсутствовал (страница russian.html содержит только
   v2 и 2024-10-24), т.е. официальная документация отстаёт от релизов.
6. **Лицензии конкретных весов** (GigaAM v2/v3, Vosk-производные, piper-голоса) —
   пофайловая проверка при наполнении каталога; для старых GigaAM-пакетов прямо указано
   non-commercial.
7. **Поведение при длинном стриме** — в CHANGELOG 2.0.0 есть фикс «Widen VAD sample
   positions to int64_t to fix long-stream SIGSEGV (#4079)», что указывает на недавние
   проблемы с длинными потоками; нужен стресс-тест на многочасовых сессиях.
8. **Стоимость простоя**: сколько RSS занимает загруженный recognizer (веса + ORT-арены)
   для каждой русской модели — от этого зависит политика TTL/выгрузки в Runtime.
   Цифр в репозитории нет, только размеры файлов.

---

## Источники

Код (ревизия `532f1b5a8a207bf53b116b747207c1506696c843`):
- Online-сервер: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-websocket-server-impl.cc#L185-L215
- Online-сервер, заголовок (Connection, batch, loop interval): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-websocket-server-impl.h#L31-L71
- Offline-сервер: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-websocket-server-impl.cc#L103-L112
- Протокол offline (комментарий): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-websocket-server-impl.h#L28-L40
- OnlineRecognizer API: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer.h#L183-L232
- Диспетчер online-моделей: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer-impl.cc#L61-L140
- Диспетчер offline-моделей: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-recognizer-impl.cc#L240-L300
- Батч-декод трансдьюсера: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer-transducer-impl.h#L288-L360
- Paraformer без батча: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer-paraformer-impl.h#L171-L176
- Hotwords: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer-transducer-impl.h#L444-L520
- OnlineStream (мьютексы, GetResult): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-stream.cc#L24-L112
- VAD-диспетчер: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/vad-model.cc#L28-L56
- Диаризация: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-speaker-diarization.h#L19-L90
- SpeakerEmbeddingManager: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/speaker-embedding-manager.h#L19-L120
- CoreML EP: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/session.cc#L462-L471
- Отключение CoreML в static macOS: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/cmake/onnxruntime-osx-arm64-static.cmake#L60-L62
- Swift Package (static xcframework): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L17-L46
- Rust-крейт: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/rust/sherpa-onnx/Cargo.toml
- Rust build.rs (скачивание либ): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/rust/sherpa-onnx-sys/build.rs#L370-L420
- Python streaming server: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/python-api-examples/streaming_server.py#L490-L586
- Python offline server (протокол): https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/python-api-examples/non_streaming_server.py#L796-L828
- TTS-диспетчер: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-tts-impl.cc#L43-L57
- Удаление espeak-ng / deprecated vits-data-dir: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-tts-vits-model-config.cc#L19-L23

Веб:
- Релизы с моделями: https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
- Документация по русским NeMo CTC: https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/russian.html
- Документация T-one (8 кГц, RTF): https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-ctc/t-one-ctc-models.html
- GigaAM (MIT, v3, multilingual): https://github.com/salute-developers/GigaAM
- T-one upstream: https://github.com/voicekit-team/T-one
- crates.io `sherpa-onnx`: https://crates.io/api/v1/crates/sherpa-onnx
- crates.io `sherpa-rs` (community): https://crates.io/api/v1/crates/sherpa-rs
