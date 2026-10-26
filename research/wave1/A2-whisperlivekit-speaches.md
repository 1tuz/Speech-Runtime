# A2. Аудит WhisperLiveKit и speaches

Дата: 2026-10-25. Обе ревизии проверены по исходникам локальных клонов:
WhisperLiveKit `eb92c115ee7abc1e161b0b2159da31785b0f5566` (далее WLK),
speaches `993994f7984bf3fe9655b267448328cf66fccb42` (далее SP).

Краткая рамка: цель — универсальный локальный Speech Runtime («Ollama для речевых моделей»),
а не ассистент. Поэтому главный вопрос к каждому проекту: годится ли он как **сервер
моделей** (каталог, ленивая загрузка, выгрузка, несколько моделей, изоляция сессий,
единый API), а не как «приложение для realtime-транскрипции».

---

## Итог

1. **WLK и speaches решают разные половины задачи и вместе покрывают почти весь чеклист.**
   WLK = streaming-пайплайн (LocalAgreement/SimulStreaming/VAD-chunking, Sortformer/diart,
   WebSocket + Deepgram-compat), но **ровно одна модель на процесс, без выгрузки, без выбора
   модели в запросе**. SP = менеджер моделей (per-request `model`, ref-count, TTL, download/delete,
   `/api/ps`), но **только batch-ASR и «псевдо-стриминг»**, и с 2026-04 не развивается.
2. **WLK: `TranscriptionEngine` — синглтон на процесс** (`core.py:38-43`), все сессии делят один
   экземпляр ASR; на старте читается конфиг и загружается одна модель. Переключения модели,
   выгрузки, кэша нескольких весов нет вообще.
3. **WLK: многоклиентность есть, но инференс сериализован.** Для LocalAgreement-бэкендов каждый
   вызов `transcribe()` идёт под общим `threading.Lock`, привязанным к объекту ASR
   (`session_asr_proxy.py:140-158`). То есть N клиентов = N WebSocket-сессий, но одна очередь
   на GPU/CPU. SimulStreaming, наоборот, делит **только веса** (`simul_whisper/backend.py:397,427`)
   и считает параллельно — по сессионному `DecoderState`.
4. **WLK: изоляция сессий сделана не экземпляром модели, а прокси-обёрткой + мьютексом.**
   Язык и prompt-контекст подменяются на общем объекте на время вызова. Это принципиально
   ломает масштабирование и является антипаттерном для Runtime (глобальное мутабельное
   состояние под замком вместо параметра вызова).
5. **WLK: admission control появился только 2026-10-23** (PR #465: `max_concurrent_sessions=64`,
   `session_idle_timeout=60`, `session_limits.py`). До этого любое число WebSocket-клиентов
   спавнило свой `AudioProcessor` + ffmpeg-процесс. REST (`/v1/audio/transcriptions`)
   под лимит **не** попадает (сказано в описании PR).
6. **WLK: `/v1/models` фиктивный, `model` в REST игнорируется** — прямо в docstring:
   «The `model` parameter is accepted but ignored (uses the server's configured backend)»
   (`basic_server.py:488-497`). OpenAI-совместимость здесь — по форме запроса, не по смыслу.
7. **SP: лучший в этой паре образец управления моделями** — `SelfDisposingModel` с ref-count,
   `threading.RLock`, `threading.Timer` на TTL (по умолчанию 300 с для STT/TTS, −1 = никогда),
   `gc.collect()` при выгрузке, `OrderedDict` загруженных моделей (`base_model_manager.py:37-146`).
   Но **LRU/лимита на число одновременно загруженных моделей нет**, и `OrderedDict` не
   используется как LRU.
8. **SP: «стриминг» не является потоковым ASR.** `stream=true` — это SSE по **уже загруженному
   файлу**, дельта отдаётся по мере декодирования сегментов (`whisper.py:187-224`). Realtime API
   (`/v1/realtime`) режет поток VAD-ом по ходу речи и затем **шлёт HTTP-запрос на свой же
   `/v1/audio/transcriptions`** через loopback-клиент (`realtime_ws.py:89-95`,
   `input_audio_buffer.py:134-171`). Частичных гипотез нет.
9. **SP фактически заброшен.** Последний коммит — 2026-04-17, последний релиз — `v0.9.0-rc.3`
   от 2025-12-27 (стабильной 0.9.0 нет, в коде `version="0.8.3"  # TODO: update this on release`).
   Открытые issue «Actively supported?» (#659) и «speaches development status» (#622) с ответом
   мейнтейнера «готов вернуться к полу-активной разработке» (2026-03-25), после чего активность
   снова упала; сообщество ушло в форки (longregen/speaches, dograh-hq/speaches).
10. **Ни один из двух не годится как основа Runtime целиком;** WLK — источник проверенных
    алгоритмов и протоколов, SP — источник модели управления моделями (ref-count + TTL), которую
    надо дополнить LRU/лимитом памяти и вынести из Python-процесса.

---

## Факты

### A. WhisperLiveKit (WLK)

#### A1. Архитектура сервера

- ASGI-приложение FastAPI + uvicorn, один процесс, один воркер по умолчанию
  (`basic_server.py:660-676` — `create_app`). В `main()` явно `"reload": False, "lifespan": "on"`
  и один `uvicorn.run` (`basic_server.py:680-715`).
- Глобальный синглтон движка: `class TranscriptionEngine` с `_instance` и `_lock`,
  double-checked locking в `__new__` (`core.py:38-56`). В `lifespan` создаётся один экземпляр
  на приложение (`basic_server.py:52-56`).
- Конфигурация — dataclass `WhisperLiveKitConfig` (~200 полей), из него делается `Namespace`
  для обратной совместимости (`core.py:90`). Модель/бэкенд выбираются **только на старте**
  (`parse_args.py:252-255`: `--backend {auto,mlx-whisper,faster-whisper,whisper,openai-api,
  funasr,voxtral,voxtral-mlx,qwen3-vllm,qwen3-vllm-metal,qwen3-streaming,canary}`).
- На сессию создаётся `AudioProcessor` (свой набор asyncio-задач, очередей, счётчиков метрик) —
  `audio_processor.py:66-186`, `create_tasks()` на `:944`.
- Документация прямо формулирует модель развёртывания: «Start with one worker. Each worker loads
  its own model weights, and inference locks serialize some backend operations within a process»
  (`docs/deployment.md`, раздел «Local server and remote clients»).

#### A2. Реальный набор эндпоинтов

Проверено по декораторам `basic_server.py`:

| Метод/путь | Строка | Что реально делает |
|---|---|---|
| `GET /` | `basic_server.py:60` | встроенный HTML-UI |
| `GET /health` | `:65` | `{status, backend, ready}` |
| `WS /asr` | `:97` | нативный стриминг: `language`, `mode=full\|diff`, `context`, `target_language` в query |
| `WS /v1/listen` | `:225` | Deepgram-совместимый (`deepgram_compat.py`, 1136 строк) |
| `POST /v1/audio/transcriptions` | `:488` | OpenAI-совместимый batch; `response_format ∈ {json,verbose_json,diarized_json,text,srt,vtt}` |
| `GET /v1/models` | `:645` | отдаёт **один** элемент `"<backend>/<model_size>"` |

- Batch-лимиты: `_MAX_AUDIO_BYTES = 512 MiB` (`:253`), конвертация через дочерний `ffmpeg`
  с bounded output (`_convert_to_pcm`, `:257-320`), таймаут `max(120, 2.5 × длительность)`
  или `--rest-timeout` (`:583-584`).
- `model` в форме принимается, но не используется (`:491`, docstring `:496-499`).
- Нет: `/v1/embeddings`, TTS, отдельного realtime-эндпоинта, `/v1/models/{id}`, выгрузки модели.

#### A3. Управление моделями

- Загрузка на старте, один раз: `backend_factory()` (`local_agreement/whisper_online.py:74-190`)
  или ветки `qwen3-*`/`voxtral-*`/`canary`/`simulstreaming` в `core.py:143-260`.
- Скачивание: `resolve_model_path()` — если локального пути нет, считает строку HF repo id и
  вызывает `huggingface_hub.snapshot_download` (`model_paths.py:195-215`). Для `faster-whisper`
  скачивание делегировано самому CTranslate2 (`backends.py:101-135`, `download_root=cache_dir`).
  Для `mlx-whisper` — `ModelHolder.get_model(...)` (`backends.py:157-200`).
- **Нет** TTL, LRU, выгрузки, переключения модели, нескольких моделей одновременно, `/api/ps`,
  DELETE. Единственный способ сменить модель — рестарт процесса.
- Есть определение формата каталога модели (`detect_model_format`, `model_paths.py:143-193`):
  различает CTranslate2 (`model.bin`), MLX (`weights.npz|safetensors`), PyTorch (sharded
  `model-00001-of-00002.safetensors`). Полезная утилита для Runtime при валидации установки.
- Warmup: при старте скачивает `jfk.wav` с GitHub и делает один прогон
  (`warmup.py:6-41`, вызов `warmup_asr` в `whisper_online.py:186`). Сетевой вызов в старте;
  при провале warmup сервер **падает** намеренно (`warmup.py:58-61`).

#### A4. Одновременные клиенты, изоляция сессий, блокировки

- Admission control: `WebSocketSessionLimitMiddleware` — счётчик в памяти приложения,
  без `await` между проверкой и инкрементом, отказ `close(1013)` до `accept`
  (`session_limits.py:6-31`). Настройки: `max_concurrent_sessions: int = 64`,
  `session_idle_timeout: float = 60.0` (`config.py:196-199`).
- Idle-timeout нативного `/asr`: `asyncio.wait(..., timeout=session_idle_timeout)`, закрытие
  кодом 1001 (`basic_server.py:168`).
- **Изоляция языка/контекста — мутация общего объекта под замком**, а не отдельный экземпляр:
  `SessionASRProxy.transcribe()` сохраняет `self._asr.original_language`, подменяет на язык
  сессии, вызывает `transcribe`, восстанавливает (`session_asr_proxy.py:148-158`). Замок
  создаётся лениво на объекте ASR и общий для всех прокси (`:139-141`). Комментарий в коде:
  «a per-ASR lock serializes `transcribe()` calls, which is acceptable because model inference
  is typically GPU-bound and cannot be parallelized anyway» (`:104-108`).
- SimulStreaming обходит `transcribe()`: у сессии своя копия `cfg` (shallow copy) и свой
  `AlignAtt`/`DecoderState`, а модель — общая (`backend.py:68-72`, `:397,427,441,443`).
  Явного лока вокруг `AlignAtt` нет.
- `thread_safety.py` (глобальный `_model_lock`, `WHISPERLIVEKIT_MODEL_LOCK`) — **мёртвый код**:
  `acquire_model_lock`/`ModelLockContext` не вызываются нигде, кроме самого модуля
  (проверено grep по `whisperlivekit/`).
- Инференс вынесен из event loop через `asyncio.to_thread(method, *args)`
  (`audio_processor.py:514`) — но только для ASR.
- **Диаризация Sortformer блокирует event loop**: `SortformerDiarizationOnline.diarize()` объявлен
  `async def`, но `forward_streaming_step` и mel-препроцессинг выполняются синхронно в корутине
  (`sortformer_backend.py:253-311`), и вызывается напрямую
  (`audio_processor.py:822-870` → `_drain_diarization_buffer`). Открытый PR #461 это чинит
  (замер автора: stall max 662 ms → 6 ms), но он **не влит**.
- Backpressure: `ProcessingQueue(asyncio.Queue)` с `max_samples` (по умолчанию
  `max_buffered_audio=30.0` c аудио) и `backpressure_timeout=30.0`; при переполнении —
  `PipelineOverloaded` и закрытие сессии (`processing_queue.py:22-118`, `config.py:96-97`).

#### A5. Потоковое распознавание: алгоритмы

- **LocalAgreement** (по умолчанию для whisper/faster-whisper/mlx/openai-api/voxtral-hf):
  `HypothesisBuffer.flush()` — «longest common prefix» между предыдущей гипотезой и новыми
  токенами (`online_asr.py:59-85`); есть эвристика снятия дублей по n-граммам 1..5
  (`:38-57`) и опциональная `confidence_validation` (порог 0.95, `:64-70`).
  Буфер аудио обрезается по `buffer_trimming ∈ {sentence, segment}` и
  `buffer_trimming_sec=15.0` (`config.py:109-111`, `online_asr.py:127-165`).
- **SimulStreaming / AlignAtt** (`backend_policy=simulstreaming` — **дефолт**, `config.py:84`):
  `iframe_threshold`/frame_threshold, `beams`, веса через CIF (`load_cif`), аудио-окно
  `audio_max_len=30.0`; своя сессионная политика коммита (`simul_whisper/simul_whisper.py`,
  `align_att_base.py`, 649 строк).
- **VAD-chunking (VAC)**: `vac=True` по умолчанию (`config.py:71`), Silero VAD на чанках 40 мс
  (`vac_chunk_size: float = 0.04`), сегментация PCM по событиям `start`/`end` с клампингом
  к границам чанка, чтобы не накапливать дрейф таймстампов (`audio_processor.py:1095-1156`).
- Пограничные случаи реализованы аккуратно: длинная пауза ≥5 с переинициализирует буфер
  (`online_asr.py:167-186`), короткая — вставляется как нули; `pause_segmentation_seconds=5.0`
  создаёт стабильные границы строк; `asr_coalesce_min_s` позволяет батчить инференс
  (`audio_processor.py:56-67`, `resolve_coalesce_min_s`).
- Промпт-контекст: `prompt()` возвращает последние ~200 символов закоммиченного текста вне
  текущего буфера (`online_asr.py:188-214`) — это то, что даёт устойчивость к IT-терминам при
  подмешивании глоссария через `context`.

#### A6. VAD и диаризация

- Silero VAD: две реализации — ONNX (разделяемая `OnnxSession` + per-instance состояние,
  `silero_vad_iterator.py:28-70`) и JIT (`load_jit_vad`, per-session, если onnxruntime нет;
  предупреждение прямо в коде: «For multi-user scenarios, install onnxruntime»,
  `core.py:100-110`).
- Диаризация, backend `sortformer` (дефолт, `config.py:82`): одна **общая** модель
  `nvidia/diar_streaming_sortformer_4spk-v2` (`sortformer_backend.py:54-68`) + per-session
  `StreamingSortformerState` (`spkcache`, `fifo`, `_previous_chunk_features`) и per-session
  `AudioToMelSpectrogramPreprocessor` (`:151-215`). Параметры стриминга жёстко зашиты:
  `chunk_len=10`, `subsampling_factor=10`, `chunk_left_context=10`, `spkcache_len=188`,
  `fifo_len=188`, `spkcache_update_period=144` (`:120-126`).
- Диаризация, backend `diart` (`diart==0.9.2`, Python <3.13): `WebSocketAudioSource` +
  отдельный `threading.Thread`, шаг 0.5 с (`diart_backend.py:87-140`). В коде честная пометка:
  «Not the best here, since several user/instances will share the same backend, but diart is not
  SOTA anymore and sortformer is recommended» (`core.py:508`).
- Сопоставление «слово ↔ спикер» делается не моделью, а `tokens_alignment.py` (482 строки):
  токены ASR атрибутируются сегментам диаризации по времени.
- **Speaker embeddings: нет.** Ни эндпоинта, ни извлечения эмбеддингов; `pyannote/embedding`
  используется только внутри diart-пайплайна как часть диаризации (`config.py:128-129`).

#### A7. Движки и Apple Silicon

- Локальные движки: `whisper` (вендоренный форк openai-whisper внутри репозитория,
  `whisperlivekit/whisper/`, ≈4.5 тыс. строк, нужен для word timestamps), `faster-whisper`
  (CTranslate2), `mlx-whisper` (MLX), `openai-api` (внешний API), `funasr` (SenseVoiceSmall),
  `voxtral-mlx` (свой MLX-порт модели Voxtral), `voxtral` (HF Transformers),
  `qwen3-streaming`/`qwen3-vllm`/`qwen3-vllm-metal`, `canary` (NVIDIA NeMo).
- Автовыбор: `_normalize_backend_choice` — на Darwin/arm64 с установленным `mlx_whisper`
  выбирается `mlx-whisper`, иначе `faster-whisper`, иначе вендоренный `whisper`
  (`whisper_online.py:196-234`, `backend_support.py:12-30`).
- MLX используется и как **энкодер** для SimulStreaming (`encoder_backend ∈ {mlx-whisper,
  faster-whisper, whisper}`, `simul_whisper/backend.py:357,491`), при этом полный MLX-декодер
  отключён по умолчанию с комментарием «MLXAlignAtt has known issues with token generation
  after punctuation» (`:364-371`).
- CoreML — нет. ONNX — только для Silero VAD (onnxruntime).
- Официальные бенчмарки сняты **на NVIDIA H100** (`README.md:161`), данных по Apple Silicon
  в репозитории нет; сам автор просит сообщество прислать замеры. Никаких RTF для M-серии
  в README/`benchmarks/` я не нашёл.

#### A8. Python-зависимость и её последствия

- `requires-python = ">=3.11, <3.14"`; **обязательные** зависимости включают `torch>=2.6.0`,
  `torchaudio>=2.0.0`, `faster-whisper>=1.2.0`, `fastapi>=0.141.1`, `starlette>=1.3.1`,
  `librosa`, `soundfile`, `tiktoken` (`pyproject.toml:16-40`). То есть даже для MLX-only
  сценария тянется полный PyTorch.
- Экстры конфликтуют между собой декларативно (`[tool.uv].conflicts`, 17 пар): `qwen3-vllm`
  несовместим с `cu129`, `voxtral-hf` несовместим с `diarization-sortformer` и т.д.
  Практический вывод: один процесс = один «профиль» бэкендов, ставить «всё» нельзя.
- Диаризация Sortformer требует `nemo-toolkit[asr]>=3.0,<4` — самая тяжёлая и хрупкая ветка
  (в `sortformer_backend.py:15-20` при отсутствии пакета стоит `raise SystemExit(...)`).
- Метрики сессии есть: `metrics_collector.py`/`metrics.py` (длительности инференса, лаг,
  пик очереди, backpressure) — полезно как образец наблюдаемости.

#### A9. Активность (проверено на 2026-10-25)

- Коммиты: `eb92c11` (2026-10-18, merge PR #465 «bound WebSocket sessions»), далее
  `529c8e7`, `363e4f6` (2026-08-30). Ветка main живёт активнее релизов: последний релиз
  `v0.2.26` — 2026-08-29, при версии в `pyproject.toml` `0.2.26`.
- **Открытых issue всего 2**: #467 (2026-10-25, баг SimulStreaming no-speech check) и
  #456 (2026-09-26, security: AlignAtt sidecar по неаутентифицированному WS).
- История утечек/конкурентности (все закрыты): #455 (нет лимита сессий + ffmpeg на коннект) —
  закрыт PR #465 2026-10-23; #344 «Memory leak» (`TokensAlignment.all_tokens` и
  `AudioProcessor.state.tokens` росли неограниченно) — исправлено 2026-05-20, добавлен
  `_prune_state_tokens` (`audio_processor.py:475-489`); #275 «GPU memory full after 10 min» —
  исправлено в 0.2.18 (утечка cross-attention тензоров в SimulStreaming); #282 «can we use one
  asr model instance?» — модель SimulStreaming стала разделяемой (коммит `7faa21f`).
- Открытый PR #461 (event loop блокируется Sortformer) — подтверждает, что находка из A4
  реальна и на 2026-10-25 не исправлена в main.
- Вывод по активности: **проект живой, мейнтейнер один, релизы отстают от main на ~2 месяца.**

---

### B. speaches (SP)

#### B1. Архитектура сервера

- FastAPI + pydantic-settings, один процесс; `create_app()` собирает ~10 роутеров,
  опционально монтирует Gradio UI на `/` (`main.py:95-205`).
- Ядро расширяемости — `Executor` (pydantic-модель: `name`, `model_manager`, `model_registry`,
  `task`) и `ExecutorRegistry`, который группирует исполнителей по задачам
  (`executors/shared/executor.py:8-18`, `executors/shared/registry.py:32-114`).
- `ModelRegistry` — абстракция над HF: `list_remote_models`, `list_local_models`,
  `get_model_files`, `download_model_files` (`model_registry.py:9-24`). Каталог моделей — это
  **кэш HuggingFace** (`HF_HUB_CACHE`, `hf_utils.py`), а не собственный формат. Плюс:
  нулевая работа по версионированию (ревизии HF). Минус: идентичность модели = repo_id, и
  «установка» неотличима от «случайно оказалась в кэше».
- Декодирование аудио — PyAV (`decode_audio(..., sampling_rate=16000)`), ошибки
  классифицируются в 415/400/500 (`dependencies.py:88-119`).

#### B2. Реальный набор эндпоинтов

| Путь | Строка | Примечание |
|---|---|---|
| `POST /v1/audio/transcriptions` | `routers/stt.py:124` | OpenAI-совместимый; расширения `stream`, `hotwords`, `without_timestamps` |
| `POST /v1/audio/translations` | `routers/stt.py:63` | через whisper `task=translate` |
| `POST /v1/audio/speech` | `routers/speech.py:85` | TTS (Kokoro/Piper) |
| `POST /v1/audio/speech/timestamps` | `routers/vad.py:25` | Silero VAD как отдельная операция |
| `POST /v1/audio/embeddings` (speaker) | `routers/speech_embedding.py:27-30` | wespeaker resnet34 |
| `POST /v1/audio/diarization` | `routers/diarization.py:108` | pyannote, batch |
| `GET/POST/DELETE /v1/models*`, `GET /v1/registry` | `routers/models.py:27-118` | каталог + download + delete |
| `GET /api/ps`, `POST/DELETE /api/ps/{id}` | `routers/misc.py:32-95` | список/принудительная загрузка/выгрузка |
| `WS /v1/realtime`, `POST /v1/realtime` | `routers/realtime_ws.py:57`, `routers/realtime_rtc.py:258` | OpenAI Realtime API, WS и WebRTC |
| `POST /v1/chat/completions` | `routers/chat.py:229` | прокси к внешнему LLM + TTS |

- Т.е. покрытие операций шире, чем у WLK: ASR + translation + VAD + diarization + speaker
  embeddings + TTS + realtime.
- `model_aliases.json` (`whisper-1`, `tts-1`, `tts-1-hd`) резолвится валидатором `ModelId`;
  файл читается из **текущего рабочего каталога** (`model_aliases.py:9-19`).

#### B3. Управление моделями — лучшая часть проекта

- `SelfDisposingModel[T]`: `ref_count`, `threading.RLock`, `expire_timer: threading.Timer`,
  `model` (`base_model_manager.py:37-53`).
  - `unload()` запрещает выгрузку при `ref_count > 0`, отменяет таймер, обнуляет ссылку,
    вызывает `gc.collect()` (`:55-68`).
  - `_decrement_ref()` при `ref_count == 0`: TTL>0 → `Timer(ttl, unload)`, TTL==0 → немедленно,
    TTL<0 → никогда (`:85-100`).
  - `__enter__` грузит при необходимости и инкрементит ref (`:102-110`) — контекстный менеджер
    вокруг каждого запроса.
- `BaseModelManager`: `OrderedDict[str, SelfDisposingModel]` под `threading.Lock`,
  `load_model()` создаёт ленивую обёртку без загрузки весов (`:112-146`).
- TTL конфигурируется раздельно: `stt_model_ttl=300`, `tts_model_ttl=300`, `vad_model_ttl=-1`
  (`config.py:51,58,65`); `preload_models` качает модели на старте (`config.py:139`,
  `main.py:81-92`).
- **Чего нет:** лимита на число загруженных моделей и LRU-вытеснения (grep по
  `max_loaded|lru` в `src/` даёт только `functools.lru_cache` на конфиг), учёта занятой памяти,
  приоритетов, «keep-alive» пиннинга (кроме TTL=-1 глобально), прогресса загрузки.
- **Автоскачивания при запросе нет:** `get_model_card_data_or_raise` бросает 404 «Model is not
  installed locally. You can download the model using `POST /v1/models`»
  (`routers/utils.py:22-38`). README при этом утверждает «Just specify which model you want to
  use in the request and it will be loaded automatically» — расхождение документации и кода.
- Удаление модели = удаление каталога из HF-кэша (`delete_local_model_repo`, `hf_utils.py`),
  безусловно для любой модели, даже не поддержанной speaches (`routers/models.py:101`, TODO
  в самом коде).

#### B4. Одновременные клиенты и изоляция

- Модели разделяются между запросами (один объект `WhisperModel` на model_id при
  `ref_count > 1`), что подтверждает лог из issue #638: два параллельных запроса дали
  `ref_count=1` → `ref_count=2`.
- Инференс — **синхронные** `def`-эндпоинты FastAPI, т.е. выполняются в threadpool
  (`routers/stt.py:124` без `async`). Отдельного лока вокруг `whisper.transcribe` нет;
  потокобезопасность делегирована CTranslate2 (`[INFERENCE]` — в коде явных гарантий нет,
  это свойство CT2, не проверялось здесь по документации CT2).
- VAD вызывается **на каждый** запрос транскрипции до основного ASR
  (`routers/stt.py:154-155`: `executor_registry.vad.model_manager.handle_vad_request`), и
  результат уходит в `clip_timestamps`; это же включено по умолчанию
  (`_unstable_vad_filter: True`, `config.py:103-113`).
- Realtime: **одна** asyncio-сессия на WebSocket, `asyncio.TaskGroup` + `asyncio.timeout(30 мин)`
  (`realtime_ws.py:106-108`); событийная модель через pub/sub (`realtime/pubsub.py`), роутеры
  событий `session_*`, `conversation_*`, `input_audio_buffer_*`, `response_*`. Лимита числа
  сессий нет.
- VAD в realtime пересчитывается на **каждый** `input_audio_buffer.append` по окну последних
  3000 сэмплов × 16 (`MAX_VAD_WINDOW_SIZE_SAMPLES = 3000 * MS_SAMPLE_RATE`,
  `input_audio_buffer.py:36`; вызов — `input_audio_buffer_event_router.py:47-104`) — синхронный
  ONNX-вызов внутри async-обработчика.

#### B5. «Потоковое» распознавание — фактически нет

- `stream=true` → SSE (`StreamingResponse`, `text/event-stream`) с событиями
  `transcript.text.delta` по мере декодирования **полного файла**
  (`routers/stt.py:102-116`, `executors/whisper.py:187-224`). Клиент должен сначала отдать весь
  файл. Это не онлайн-ASR.
- Realtime-транскрипция: `InputAudioBufferTranscriber._handler` пишет накопленный VAD-сегмент в
  WAV и вызывает `self.transcription_client.create(...)` — то есть **HTTP POST на свой же
  `/v1/audio/transcriptions`** через loopback-URL (`input_audio_buffer.py:134-171`,
  `realtime_ws.py:89-95`). Итог: один запрос к API на каждую реплику, без частичных гипотез,
  с полным дублированием аудио через HTTP.
- Turn detection: `server_vad`, `threshold=0.9`, `silence_duration_ms=550`,
  `prefix_padding_ms=0` (`realtime/session.py:62-70`).
- Ни LocalAgreement, ни SimulStreaming, ни AlignAtt, ни какого-либо инкрементального декодера
  в SP нет (grep по `local_agreement|alignatt|simul` в `src/` — пусто).

#### B6. Движки

- ASR: `faster-whisper` (`WhisperModel` + `BatchedInferencePipeline`,
  `executors/whisper.py:133-160`) и `onnx-asr` Parakeet TDT (`executors/parakeet.py`,
  ограничение фильтром `istupakov/parakeet-tdt`).
- VAD: Silero v5 в виде двух ONNX-сессий encoder/decoder, батчевый прогон
  (`executors/silero_vad_v5.py:78-123`), плюс `get_speech_timestamps`.
- Диаризация: `pyannote/speaker-diarization-community-1` через `pyannote.audio.Pipeline`,
  один разрешённый ID (`AVAILABLE_MODELS`), batch (`executors/pyannote_diarization.py:28,66-84`).
- Speaker embedding: `pyannote/wespeaker-voxceleb-resnet34-LM` (`executors/wespeaker_speaker_embedding.py:26-30`).
- TTS: Kokoro-82M ONNX и Piper (`executors/kokoro.py`, `executors/piper.py`).
- **Нет:** MLX, CoreML, Metal, vLLM (в апстриме), любых Apple-специфичных путей. CTranslate2
  на Apple Silicon работает только на CPU; MPS не используется.
- `requires-python = "==3.12.*"` (жёстко одна версия) и в зависимостях одновременно
  `ctranslate2`, `faster-whisper`, `onnx-asr`, `pyannote-audio>=4`, `torch` (через pyannote),
  `gradio>=5.49.1`, `aiortc`, `kokoro-onnx[gpu]`, `piper-tts`, полный набор
  `opentelemetry-*` (`pyproject.toml:5-46`). Docker-база — `nvidia/cuda:12.6.3-base-ubuntu24.04`
  (`Dockerfile:1`), arm64 собирается только как CPU-вариант.

#### B7. Активность и заброшенность (проверено)

- Последний коммит: `993994f` «ci: inject OPENAI_API_KEY», **2026-04-17** (то есть ~6 месяцев
  тишины на 2026-10-25).
- Последний релиз: `v0.9.0-rc.3`, **2025-12-27**; стабильная ветка — `v0.8.3` (2025-09-19).
  В коде `version="0.8.3",  # TODO: update this on release` (`main.py:122`).
- Issue #622 «speaches development status» (2026-03-19): мейнтейнер `fedirz` 2026-03-25 ответил,
  что был занят и «готов возобновить полу-активную разработку», обещал RC с диаризацией и
  закрыл issue как completed. После этого — 3 недели коммитов (апрель) и тишина.
- Issue #659 «Actively supported?» (2026-07-05, открыт): «no commits since April 2026»;
  ответы сообщества: «seems like a dead repo, looking for proper better alternative now».
- Живые форки, на которые ссылается сообщество в #622: `longregen/speaches` (обновлённые
  зависимости + большинство открытых PR), `dograh-hq/speaches` (vLLM/vLLM-Omni/voxtral
  исполнители + «websocket streaming support»). Апстрим эти изменения не принял.
- Открытые проблемы, подтверждающие деградацию: #678 (Gradio 6 ломает запуск, `gradio>=5.49.1`
  не ограничен сверху, 2026-09-23), #671 (инструкция установки не работает, 2026-08-22),
  #638 (память не возвращается ОС после выгрузки модели — glibc mmap, PR #681 открыт,
  влит не был), #679 (RCE через `config.yaml` pyannote-пайплайна, 2026-10-04), #655/#657 (422 на
  `/v1/models/{id}`, неполный снапшот Parakeet).
- Масштаб незакрытого: на 2026-07-05 в #659 зафиксировано 102 открытых issue и 28 открытых PR;
  страница issues без фильтра состояния отдаёт 334 результата.
- **Оценка: заброшен (maintenance mode без мейнтейнера).** Использовать как зависимость
  рискованно; как источник идей — можно и нужно.

---

## Ограничения и риски

**WLK**

1. Одна модель на процесс → нельзя одновременно держать, например, whisper-large-v3 для
   batch и стриминговую модель для realtime. Любая смена = рестарт (и повторный warmup с
   сетью).
2. Глобальный мьютекс на `transcribe()` означает: при 8 клиентах latency линейно растёт,
   «одновременные независимые сессии» существуют только на уровне WebSocket, не на уровне
   инференса. Плюс подмена `original_language` на общем объекте — источник гонок, который
   закрыт локом, а не дизайном.
3. Sortformer блокирует event loop (открытый PR #461): при диаризации деградируют **все**
   сессии процесса, включая health-check и keepalive.
4. REST не под admission control; лимит 512 MiB на файл и таймаут `2.5×длительности` — грубые
   эвристики; при 64 сессиях и REST параллельно память не ограничена ничем, кроме очередей
   (30 с аудио на сессию).
5. Тяжёлая зависимость: `torch` обязателен, экстры частично несовместимы; Sortformer требует
   NeMo, diart — Python <3.13. Один процесс не может обслуживать все бэкенды.
6. Бенчмарков на Apple Silicon нет; все числа в README — H100.
7. Мейнтейнер один; релизы отстают от main; security-issue #456 (неаутентифицированный WS
   к AlignAtt-sidecar с текстом транскрипта) открыт.

**speaches**

1. Заброшен: 6 месяцев без коммитов, RC-ветка не стабилизирована, установка по документации
   не работает (#671), Gradio-зависимость ломает старт (#678), RCE в загрузке диаризации (#679).
2. Нет потокового ASR: SSE по файлу + realtime-«реплики» через loopback HTTP. Для
   realtime-субтитров непригоден.
3. Нет LRU/лимита памяти: TTL=300 с считается per-model, а верхней границы на число загруженных
   моделей нет вовсе — каждый новый встретившийся `model_id` остаётся в RAM до истечения своего
   TTL, и они не вытесняют друг друга (по коду `BaseModelManager.loaded_models` только
   пополняется). Плюс подтверждённая проблема
   невозврата RSS после выгрузки (glibc; фикс в незалитом PR #681).
4. Python-only, Apple Silicon — только CPU (CTranslate2/ONNX/torch без MPS-путей).
5. Модель = ревизия HF; нет собственного формата, нет контроля версий, нет целостности;
   удаление модели удаляет HF-кэш целиком, включая модели, которые speaches не поддерживает.
6. `model_aliases.json` ищется в CWD, версия приложения захардкожена — общая небрежность
   релизного процесса.

**Общее**

- Оба проекта — Python-монолиты с GIL. Для Runtime, который должен держать несколько
  загруженных весов и параллельные сессии, это означает либо процессы-на-модель (и тогда
  нужен межпроцессный роутинг/шейринг), либо не-Python ядро.
- Ни один не даёт «общий каталог моделей с версиями/апдейтами» в смысле пакетного менеджера:
  WLK — вообще нет, SP — «что попало в HF-кэш».

---

## Выводы для Runtime

### Что взять из WLK (по убыванию ценности)

1. **Алгоритмы streaming-политики** — самая ценная часть:
   - `HypothesisBuffer` (LocalAgreement) с n-граммным снятием дублей: `online_asr.py:11-101`.
     Компактно (~90 строк) и переносимо на любой язык.
   - Buffer trimming (`sentence`/`segment`, 15 с) — `online_asr.py:127-165`.
   - Правило «пауза ≥5 с переинициализирует буфер, короткая — вставляется нулями»
     (`online_asr.py:167-186`) — критично для таймстампов и для диаризации.
   - VAD-сегментация с клампингом событий к границам чанка и явным устранением дрейфа
     (`audio_processor.py:1095-1156`) — готовый рецепт для VAD-операции в Runtime.
2. **Протоколы и их семантика**: `mode=full|diff` с `DiffTracker` (`diff_protocol.py`) и
   Deepgram-совместимый слой с явно разделёнными понятиями «interim заменяем / final неизменяем
   / endpointing по VAD / UtteranceEnd отдельно» (`deepgram_compat.py:1-10`). Это хороший образец
   того, как описывать контракт стримингового события в API.
3. **`detect_model_format()`** (`model_paths.py:143-193`) — примитив для каталога моделей:
   по содержимому каталога понять, совместим ли он с CTranslate2/MLX/PyTorch.
4. **Admission control и backpressure**: `WebSocketSessionLimitMiddleware` (счётчик без `await`
   между проверкой и резервированием) + `ProcessingQueue` с `max_samples` и таймаутом
   (`session_limits.py`, `processing_queue.py`). Прямо переносится в дизайн лимитов Runtime.
5. **Наблюдаемость сессии**: `SessionMetrics` (время инференса, лаг, пик очереди, backpressure
   wait) — `metrics_collector.py`.
6. **Не брать**: синглтон-движок, мутацию общего ASR под локом, per-session ffmpeg-процесс как
   путь декодирования по умолчанию, обязательный torch, `raise SystemExit` на отсутствии
   опциональной зависимости.

### Что взять из speaches

1. **Модель жизненного цикла `SelfDisposingModel`** — ближайший к нужному образец:
   ref-count + `RLock` + отложенная выгрузка по таймеру + `gc.collect()` при выгрузке
   (`base_model_manager.py:37-110`). В Runtime это надо расширить: LRU-вытеснение по бюджету
   памяти, пиннинг (keep-alive), учёт размера, приоритеты, отмена таймера при новом запросе
   (в SP уже есть — `_increment_ref`).
2. **Разделение «реестр моделей» / «менеджер экземпляров» / «executor»** — правильная
   трёхслойная декомпозиция для адаптеров движков: `ExecutorRegistry` + `ModelRegistry` +
   `BaseModelManager` (`executors/shared/`). Это ровно та структура, которую просит задача
   «несколько вычислительных движков через адаптеры».
3. **HTTP-контракт `GET /api/ps`, `POST/DELETE /api/ps/{id}`** — удачная идея: «что сейчас
   загружено» отдельно от «что установлено» (`routers/misc.py:32-95`). Runtime должен иметь
   оба пространства явно.
4. **Per-request `model` + TTL** как UX-минимум для batch-операций — то, чего нет в WLK.
5. **Не брать**: loopback-HTTP для внутренних вызовов (realtime → transcriptions), псевдо-стриминг
   SSE по файлу, пересчёт VAD по всему окну на каждый чанк, хранение каталога исключительно в
   HF-кэше без собственного манифеста.

### Как это ложится на архитектуру Runtime

- **Разделение, которого нет ни у кого:** «каталог + установка/версии» (манифест, хэши,
  апдейты) отдельно от «менеджер загруженных весов» (ref-count, LRU по бюджету памяти,
  пиннинг) отдельно от «сессии» (per-session состояние: буферы, VAD-состояние, `DecoderState`).
  WLK смешивает всё в `TranscriptionEngine` + `AudioProcessor`; SP смешивает каталог с HF-кэшем.
- **Ключевое архитектурное требование, вытекающее из аудита:** параметры запроса (язык,
  prompt/глоссарий, температура) должны быть **аргументами вызова**, а не мутацией общего
  объекта под мьютексом (антипаттерн WLK, `session_asr_proxy.py:148-158`). Тогда параллельные
  сессии на одной модели не требуют сериализации, кроме той, что навязывает сам движок.
- **Изоляция сессий = отдельный state-объект**, как сделано в WLK для SimulStreaming
  (`DecoderState`, `spkcache`/`fifo` per session) и Sortformer
  (`StreamingSortformerState`): веса общие, состояние — сессионное. Это подтверждённый рабочий
  паттерн на двух разных моделях в одном проекте.
- **Процессная модель:** учитывая GIL и то, что WLK уже сериализует инференс локом, а SP вообще
  не управляет параллелизмом, для Runtime разумно «процесс (или поток) на модель + роутер
  сверху»; тогда «переиспользование загруженных весов между клиентами» достигается
  естественно, а разные модели не конфликтуют (в отличие от WLK, где конфликт экстр
  декларирован в `pyproject.toml`).
- **Русская речь + IT-термины:** единственный реально поддержанный механизм в обоих —
  `prompt`/`initial_prompt` (WLK: rolling prompt 200 симв. + `context` до 1000 симв.,
  `session_asr_proxy.py:12`; SP: `initial_prompt`/`hotwords` в faster-whisper). Значит Runtime
  обязан иметь в контракте `prompt`/`glossary` как first-class параметр операции, а не как
  строку в query.

---

## Неизвестные / что измерить

1. **Производительность на Apple Silicon — не подтверждено ничем.** Официальных замеров WLK на
   M-серии нет (README: только H100), у SP Apple-путей нет вообще. Нужны собственные замеры:
   RTF + WER на русском (с английскими IT-терминами) для `mlx-whisper` (large-v3 / turbo),
   `faster-whisper` (CT2 CPU), `voxtral-mlx` на M1/M2/M3/M4 (RAM 16/32/64 ГБ).
2. **Реальная цена глобального лока WLK при N клиентах.** Не измерено: сколько сессий держит
   одна модель с `mlx-whisper` при лаге < 1 с. Нужен замер: 1/2/4/8 одновременных WS-сессий,
   лаг коммита, CPU/GPU-утилизация.
3. **Потребление памяти на сессию** (WLK): аудио-буферы (до 30 с × 16 кГц float32),
   `state.tokens` (retention по умолчанию не ограничен в `mode=full`! см. `retention_seconds`
   и `resolve_retention_seconds` в `tokens_alignment.py`) — в `mode=full` история растёт
   безлимитно по дизайну (issue #372 в комментарии `audio_processor.py:86-89`). Нужно измерить
   рост RSS при 8-часовой сессии в `mode=full`.
4. **Точность диаризации Sortformer на русской речи и на двух-трёх спикерах с быстрой сменой
   реплик** — не проверено, в репозитории только английские фикстуры
   (`tests/fixtures/sortformer_2spk`).
5. **Не подтверждено кодом:** потокобезопасность CTranslate2 при параллельных вызовах одного
   `WhisperModel` (в SP на это опираются без лока). Нужен стресс-тест: 4 параллельных запроса
   на одну модель, проверить отсутствие порчи вывода/крашей.
6. **SP: реальный профиль памяти после выгрузки** — утверждение из #638/PR #681
   (RSS 530–780 МБ → 100–140 МБ после `malloc_trim`) — это замер автора PR, не проверенный
   мейнтейнером; сам фикс не влит.
7. **Не найдено**: любых данных о качестве WLK на русском языке (WER/CER), примеров
   использования с русским + IT-глоссарием, подтверждённых цифр «сколько памяти на модель»
   для MLX-бэкендов. Все такие числа в отчёте отсутствуют намеренно.
8. **Не проверялось в этой волне** (передаётся смежным задачам): сам sherpa-onnx как движок,
   FluidAudio/WhisperKit как Swift-пути, качество конкретных русских моделей. Здесь — только
   серверная архитектура двух Python-проектов.

---

## Источники

**Исходный код (локальные клоны, полные permalink-и со sha)**

WhisperLiveKit @ `eb92c115ee7abc1e161b0b2159da31785b0f5566`
(`https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c11…/…`):

- `whisperlivekit/basic_server.py` — эндпоинты, REST/WS/Deepgram, `create_app`, `main`
- `whisperlivekit/core.py` — `TranscriptionEngine` (синглтон), фабрики сессий
- `whisperlivekit/session_asr_proxy.py` — `SessionASRProxy`, общий лок, `MAX_SESSION_CONTEXT_CHARS`
- `whisperlivekit/audio_processor.py` — очереди, `transcription_processor`, `diarization_processor`, VAD-сегментация
- `whisperlivekit/processing_queue.py`, `session_limits.py`, `thread_safety.py`
- `whisperlivekit/config.py` — все поля конфигурации и дефолты
- `whisperlivekit/local_agreement/online_asr.py`, `whisper_online.py`, `backends.py`
- `whisperlivekit/simul_whisper/backend.py`, `simul_whisper/simul_whisper.py`
- `whisperlivekit/diarization/sortformer_backend.py`, `diarization/diart_backend.py`
- `whisperlivekit/model_paths.py`, `warmup.py`, `backend_support.py`, `deepgram_compat.py`, `diff_protocol.py`
- `whisperlivekit/tokens_alignment.py`, `metrics_collector.py`, `metrics.py`
- `docs/API.md`, `docs/deployment.md`, `docs/backends.md`, `benchmarks/README.md`, `pyproject.toml`

speaches @ `993994f7984bf3fe9655b267448328cf66fccb42`
(`https://github.com/speaches-ai/speaches/blob/993994f…/…`):

- `src/speaches/main.py` — сборка приложения, версия, lifespan, preload
- `src/speaches/executors/shared/base_model_manager.py` — `SelfDisposingModel`, `BaseModelManager`
- `src/speaches/executors/shared/executor.py`, `registry.py`, `handler_protocol.py`
- `src/speaches/executors/whisper.py`, `parakeet.py`, `silero_vad_v5.py`, `pyannote_diarization.py`,
  `wespeaker_speaker_embedding.py`, `kokoro.py`, `piper.py`
- `src/speaches/routers/stt.py`, `models.py`, `misc.py`, `vad.py`, `diarization.py`,
  `speech_embedding.py`, `speech.py`, `chat.py`, `realtime_ws.py`, `realtime_rtc.py`, `utils.py`
- `src/speaches/realtime/input_audio_buffer.py`, `input_audio_buffer_event_router.py`, `session.py`
- `src/speaches/config.py`, `dependencies.py`, `hf_utils.py`, `model_registry.py`, `model_aliases.py`,
  `audio.py`, `api_types.py`
- `docs/usage/*.md`, `docs/installation.md`, `pyproject.toml`, `Dockerfile`, `model_aliases.json`

**Веб (проверено 2026-10-25)**

- Релизы WLK: https://github.com/QuentinFuxa/WhisperLiveKit/releases (последний `v0.2.26`, 2026-08-29)
- Открытые issue WLK: https://github.com/QuentinFuxa/WhisperLiveKit/issues?q=is%3Aissue+is%3Aopen (2 шт.)
- WLK #455 «No cap on concurrent WebSocket sessions…»: https://github.com/QuentinFuxa/WhisperLiveKit/issues/455
- WLK PR #465 «Bound concurrent WebSocket sessions…»: https://github.com/QuentinFuxa/WhisperLiveKit/pull/465
- WLK PR #461 «fix: run Sortformer inference off the event loop» (открыт): https://github.com/QuentinFuxa/WhisperLiveKit/pull/461
- WLK #344 «Memory leak»: https://github.com/QuentinFuxa/WhisperLiveKit/issues/344
- WLK #282 «can we use only one asr model instance?»: https://github.com/QuentinFuxa/WhisperLiveKit/issues/282
- WLK #275 «GPU memory is full after 10 min»: https://github.com/QuentinFuxa/WhisperLiveKit/issues/275
- Релизы speaches: https://github.com/speaches-ai/speaches/releases (последний `v0.9.0-rc.3`, 2025-12-27)
- SP #622 «speaches development status»: https://github.com/speaches-ai/speaches/issues/622
- SP #659 «Actively supported?»: https://github.com/speaches-ai/speaches/issues/659
- SP #638 «Memory not released during unload of STT model»: https://github.com/speaches-ai/speaches/issues/638
- SP #678 (Gradio 6), #671 (установка), #679 (RCE pyannote), #657/#655 (models API):
  https://github.com/speaches-ai/speaches/issues/678 ,
  https://github.com/speaches-ai/speaches/issues/671 ,
  https://github.com/speaches-ai/speaches/issues/679
- SP репозиторий (README, MIT, Docker-база): https://github.com/speaches-ai/speaches
