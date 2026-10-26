# D2. Потоковые и новые ASR-модели (состояние на 2026-10-25)

Волна 1. Исследование, не реализация. Все цифры — либо из официального model card / tech report / кода, либо из измерений в клоне репозитория (тогда указан коммит). Собственные выводы помечены `[INFERENCE]`.

Проверенные клоны (SHA из задания):
`speech-swift@cbad3be`, `SpeechRail@cdccc74`, `sherpa-onnx@532f1b5`, `WhisperLiveKit@eb92c11`, `speaches@993994f`, `gotranscribesrv@a5f4289`, `FluidAudio@1b0ed50`, `WhisperKit@9f99b5c`.

---

## Итог

1. **Нативный стриминг в 2026 — это уже не «Whisper + VAD-нарезка», а отдельный класс моделей.** Реально потоковые (cache-aware / causal encoder): NVIDIA Nemotron 3.5 ASR Streaming 0.6B, Nemotron Speech Streaming EN 0.6B, Parakeet EOU 120M, Parakeet Unified EN (streaming export), Mistral Voxtral Mini 4B Realtime 2602, Moonshine Streaming (tiny/small/medium), Kyutai STT, T-one (русский), VibeVoice-ASR-Streaming 1.5B/7B. Остальные (Whisper turbo, Parakeet TDT v3/Ultra, Canary, Qwen3-ASR, Granite, Phi-4-mm) — пакетные, стриминг только эмуляцией.
2. **Для русской речи с английской IT-терминологией есть ровно четыре пригодных нативных потоковых кандидата:** Nemotron 3.5 Streaming 0.6B (`ru-RU` в tier «transcription-ready», FLEURS ru WER 9.60 % при 560 мс), Voxtral Mini 4B Realtime (FLEURS ru 6.02 % при задержке 480 мс), VibeVoice-ASR-Streaming (ru WER 15.67 %, но с атрибуцией спикеров и задержкой 2 с) и T-one (русский телефония-домен, 300 мс чанки, WER 8.63 % на call-center). Whisper large-v3-turbo остаётся лучшим «пакетным» вариантом для ru.
3. **«Parakeet Ultra» у NVIDIA не существует.** Ultra — это `moondream/parakeet-ultra`: пост-тренировка `parakeet-tdt-0.6b-v3` сторонней компанией (moondream), та же архитектура TDT, 25 языков, CC-BY-4.0. FluidAudio поставляет её как `AsrModelVersion.ultra` и рекомендует по умолчанию. Пакетная, не потоковая.
4. **Задержка почти всегда «запечена» в артефакт модели, а не является рантайм-параметром.** У Nemotron каждый chunk-тайр (80/160/320/560/1120 мс; в CoreML-портах 560/1120/2240 мс) — отдельный экспорт; MLX-бандлы speech-swift вообще отказываются работать с геометрией, отличной от заявленной в `config.json`. У Voxtral Realtime задержка задаётся в `tekken.json` (`transcription_delay_ms`, кратно 80 мс) и является частью conditioned-декодирования.
5. **Язык — тоже параметр сессии, а не модели.** Nemotron 3.5 принимает `prompt_id` (int32) на вход энкодера: `ru-RU` → 11, `auto` → 101. Voxtral — текстовый хинт `lang:<code>`. Qwen3-ASR — токен языка. Отсюда требование к API: язык задаётся на сессию, а не глобально на модель.
6. **Переиспользование весов между клиентами технически решено, но с оговорками.** FluidAudio: `preloadShared` даёт 1×~1.5 ГБ моделей + N×~50 МБ состояния вместо N×1.5 ГБ. sherpa-onnx: один `OnlineRecognizer` + N `OnlineStream` + батчевый `DecodeStreams`. MLX (speech-swift): веса общие, но вызовы инференса **сериализуются**. ANE не масштабируется: по комментарию в бенчмарке FluidAudio N=2 даёт ~1.5–1.8×, N=4 «выравнивается».
7. **Документация портов противоречит сама себе и upstream** — это риск для каталога моделей. Примеры: FluidAudio `Models.md` пишет про мультиязычный Nemotron «en/es/fr/it/pt/de/zh/ja, 4 тайра (…4480ms)», а `NemotronMultilingual.md` — «~40 языков включая ru, 3 тайра»; speech-swift пишет «76 языков» в одной таблице и «40 language-locales» в шапке; speech-swift приписывает Parakeet-EOU «25 европейских языков», а карточка NVIDIA — «English only».
8. **Стриминг на LLM-архитектурах платит качеством или вычислениями.** Оконный пересчёт (Whisper/LLM): `+1.46 %` абсолютного WER при чанке 1 с (Simul-Whisper). Каузальный файн-тюн аудио-башни Qwen3-ASR: 42 GFLOPs/с постоянно, но WER 17.2/17.6 против 8.4 у оконного варианта. Parakeet Unified streaming: RTFx 29.1 против 123.3 в batch — «налог на задержку» от пересчёта 7.68-секундного окна каждые 1.04 с.
9. **Порты покрывают не всё.** CoreML (ANE) есть у Nemotron (EN/3.5), Parakeet EOU/Unified/TDT, Whisper turbo, Canary, Qwen3-ASR; **нет** CoreML у Voxtral Realtime, Moonshine Streaming, Kyutai, T-one, VibeVoice. MLX есть у Nemotron 3.5 (bf16/8bit/INT5), Voxtral Realtime (4/6/fp16), Kyutai, Qwen3-ASR, Parakeet (пакетно). ONNX есть у Nemotron 3.5 (int4), Nemotron EN, Parakeet Unified, T-one, Whisper, Canary, Qwen3-ASR; **нет** у VibeVoice-ASR-Streaming и Kyutai STT (только GGUF).
10. **Минимальные накладные расходы в простое достижимы только при выгрузке весов.** Единственный проверенный прецедент «Ollama для речи» — `speaches`: динамическая загрузка/выгрузка модели по запросу + SSE-стриминг + Realtime API; но он построен вокруг faster-whisper, то есть пакетной модели.

---

## Факты

### 1. NVIDIA Nemotron 3.5 ASR Streaming 0.6B — главный кандидат для русского

| Свойство | Значение | Источник |
|---|---|---|
| Репозиторий | `nvidia/nemotron-3.5-asr-streaming-0.6b`, создан 2026-05-15, ~1.09 млн загрузок | HF API |
| Архитектура | FastConformer-CacheAware-RNNT **with Prompt**, 24 слоя энкодера, d_model 1024, ~580–600 M параметров | карточка HF, раздел Model Architecture |
| Языки | 40 language-locales в 3 tier'ах; **19 «transcription-ready» включают `ru-RU` и `uk-UA`**; 13 broad-coverage; 8 adaptation-ready | карточка HF |
| Режимы задержки | chunk 80 / 160 / 320 / 560 / 1.12 с (колонки WER-таблицы); авто-детект языка через `target_lang=auto` | карточка HF |
| FLEURS ru-RU WER (LangID) | 10.84 (80 мс) → 10.73 (160) → 9.87 (320) → **9.60 (560)** → 9.17 (1120) | карточка HF |
| FLEURS ru-RU WER (auto) | 12.47 → 12.09 → 11.01 → 10.57 → 10.03 | там же |
| Средний WER 19 локалей | 10.38 (80 мс) → 8.84 (1120 мс) | там же |
| Лицензия | **OpenMDW-1.1** (`license: other`, `license_name: openmdw-1.1`) | карточка HF, шапка и раздел License |

Кэши (проверено по коду портов): `pre_cache` mel `[1,128,9]`, `cache_last_channel` KV `[24,1,56,1024]`, `cache_last_time` conv `[24,1,1024,8]`, `cache_last_channel_len`; состояние LSTM-предиктора переносится между чанками — [speech-swift `docs/models/nemotron-asr-streaming.md#L34-L48`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L34-L48).

Режимы чанков и целевые задержки (таблица из speech-swift): 80 мс → 1 выходной кадр (right ctx 0), 320 мс → 4 кадра (ctx 3, дефолт опубликованных бандлов), 560 мс → 7 (ctx 6), 1120 мс → 14 (ctx 13) — [`docs/models/nemotron-asr-streaming.md#L104-L112`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L104-L112).

Мультиязычная conditioning: one-hot `language_mask [1,128]` конкатенируется с 1024-мерным выходом энкодера → `Linear(1152→2048) → ReLU → Linear(2048→1024)`; слоты в `languages.json`, `auto` = 101 — [`#L114-L131`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L114-L131). В sherpa-onnx тот же словарь захардкожен, `ru-RU → 11` — [sherpa-onnx `csrc/qnn/online-nemo-transducer-model-qnn.cc#L77-L90`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/qnn/online-nemo-transducer-model-qnn.cc#L77-L90).

Измерения портов (не upstream, но с методикой):

- FluidAudio, CoreML int8, Apple M2, FLEURS, полный тест: среднее по 7 языкам 8.62 % (560 мс) / 8.33 % (1120 мс) / **8.30 %** (2240 мс), aggregate RTFx 40.5× / 66.0× / 73.1× — [`Documentation/ASR/NemotronMultilingual.md#L152-L171`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/NemotronMultilingual.md#L152-L171).
- Важная методическая оговорка: «560/1120/2240 мс» у FluidAudio — это размер обрабатываемого чанка (`chunk_mel_frames`), а не архитектурный lookahead NVIDIA (`att_context_size=[56,0]` = ~80 мс); все их сборки идут с `[56,0]` — [`#L199`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/NemotronMultilingual.md#L199).
- speech-swift, MLX INT5/INT8, 320 мс, 6 языков: mean WER 11.53 (INT5, бандл 538.6 МБ, пик RSS 800 МБ) и 11.01 (INT8, 732.6 МБ, 992 МБ); INT4 отклонён рантаймом из-за потери качества — [`#L190-L210`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L190-L210).

Порты: CoreML — `FluidInference/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b-CoreML`, `aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8`; MLX — `mlx-community/nemotron-3.5-asr-streaming-0.6b` (bf16 ~1.2 ГБ) и `-8bit` (~720 МБ), рантайм `mlx_audio/stt/models/nemotron_asr` с API `create_streaming_session`/`feed`/`step`; ONNX — `onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4`; LiteRT — `soniqo/...-LiteRT-INT8`, `GoodnotesIT/...-litert-fp16`; GGUF — `handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf` (~2 млн загрузок).

Поддержка в sherpa-onnx: «Add multilingual Nemotron-3.5 streaming ASR support (#3671)», «Re-export nemotron-3.5-asr-streaming-0.6b to ONNX (#3732)» — [`CHANGELOG.md#L182`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L182), [`#L116-L117`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L116-L117). Модель-тип `nemo_parakeet_unified_streaming` и отдельный `online-transducer-nemo-model` с `prompt_id`-тензором — [`csrc/online-transducer-nemo-parakeet-unified-model.cc#L34-L40`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-transducer-nemo-parakeet-unified-model.cc#L34-L40), [`csrc/online-transducer-nemo-model.cc#L210-L215`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-transducer-nemo-model.cc#L210-L215).

### 2. NVIDIA Nemotron Speech Streaming EN 0.6B (английский, стабильный)

- 600 M параметров, cache-aware FastConformer-RNNT, **English only**; задержка задаётся `att_context_size`: `[70,0]` → 0.08 с, `[70,1]` → 0.16 с, `[70,6]` → 0.56 с, `[70,13]` → 1.12 с — [карточка HF, раздел Latency](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b).
- Лицензия — **NVIDIA Open Model License** (не Apache/CC) — [карточка, шапка](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b).
- CoreML int8: энкодер ~564 МБ, весь вариант ~600 МБ; чанки 560/1120/2240 мс = 56/112/224 mel-кадра + 9 кадров pre-cache — [`FluidAudio/Documentation/ASR/Nemotron.md#L120-L140`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/Nemotron.md#L120-L140).
- Измерено FluidAudio (M5 Pro, LibriSpeech test-clean, 2620 файлов): WER 2.71 % (560 мс, RTFx 40.7×), 2.58 % (1120 мс, 24.3×), 2.64 % (2240 мс, 87.4×) — [`Documentation/Benchmarks.md#L158-L170`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Benchmarks.md#L158-L170).
- MLX-порт: `mlx_audio/stt/models/nemotron_asr` грузит чекпойнт напрямую, режимы 80/160/560/1120 мс через `transcription_delay_ms` — [README mlx-audio](https://raw.githubusercontent.com/Blaizzy/mlx-audio/main/mlx_audio/stt/models/nemotron_asr/README.md).

### 3. NVIDIA Parakeet: TDT v2/v3, Ultra, Redux, Unified, EOU

| Модель | Архитектура | Стриминг | Языки | Лицензия |
|---|---|---|---|---|
| `parakeet-tdt-0.6b-v3` | FastConformer + TDT, 0.6 B | **нет** (пакетно; NVIDIA даёт скрипт chunked RNNT-инференса) | 25 европейских, **ru включён** | CC-BY-4.0 |
| `moondream/parakeet-ultra` | та же TDT-архитектура, полная точность | нет | те же 25 | CC-BY-4.0 |
| `moondream/parakeet-redux` | тернарный (2-bit) вариант v3, ~178–183 МБ энкодер | нет | 25 | CC-BY-4.0 |
| `parakeet-unified-en-0.6b` | FastConformer-RNNT, один чекпойнт для offline+streaming | **да**, chunked-attention | EN + пунктуация/капитализация | NVIDIA Open Model License |
| `parakeet_realtime_eou_120m-v1` | RNNT + голова EOU, 120 M | **да**, 80–160 мс | **только EN**, без пунктуации | NVIDIA Open Model License |

- v3: «expanding language support from English to 25 European languages», список включает `Russian (ru)`; лицензия CC-BY-4.0 — [карточка v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3).
- Ultra: «A post-trained version of parakeet-tdt-0.6b-v3. Same architecture, same tokenizer, same 0.6B parameters»; OAL 7 английских сетов 6.26 → **5.80**, FLEURS 25 языков 11.62 → **9.55**, TED-LIUM 2.71 → 1.94 — [карточка moondream/parakeet-ultra](https://huggingface.co/moondream/parakeet-ultra). В FluidAudio — рекомендованный дефолт, int8-энкодер 595 МБ, LibriSpeech test-clean 2.13 %, test-other 3.81 %, FLEURS-24 11.67 % — [`Documentation/ASR/ParakeetUltra.md#L1-L20`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/ParakeetUltra.md#L1-L20).
- Unified streaming: «stateless encoder re-encodes a [left|chunk|right] window per step; default `70_13_13` = 2.08 s latency», WER 2.21 % — [`Documentation/Models.md#L34`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Models.md#L34). Цена: streaming RTFx 29.1 против 123.3 в batch, потому что «re-encodes a 7.68 s window per 1.04 s chunk» — [`Documentation/Benchmarks.md#L105-L138`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Benchmarks.md#L105-L138).
- EOU: «achieves low latency (80ms~160 ms) and signals EOU by emitting an `<EOU>` token… supports only English and does not output punctuation or capitalization» — [карточка](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1). В CoreML три тайра 160/320/1280 мс — [`Models.md#L31`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Models.md#L31).

**Расхождение:** speech-swift в `docs/models/parakeet-streaming-asr.md` пишет «Languages: 25 European languages» для Parakeet-EOU-120M, тогда как upstream — English only. Доверять upstream.

### 4. Mistral Voxtral: Mini 3B (пакетно) и Mini 4B Realtime 2602 (нативный стриминг)

Voxtral Mini 3B 2507 (пакетный, `mistralai/Voxtral-Mini-3B-2507`): Whisper-style энкодер (32 слоя, 1280 hid, 375 аудио-токенов на 30 с) + Llama-декодер (30 слоёв, 3072 hid, 8 KV-голов, vocab 131 072); **8 языков: en, fr, de, es, it, pt, nl, hi — русского нет**; окно 30 с; Apache-2.0 — [speech-swift `docs/models/voxtral-asr.md#L1-L20`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/voxtral-asr.md#L1-L20).

Voxtral Mini 4B Realtime 2602 (`mistralai/Voxtral-Mini-4B-Realtime-2602`, создан 2026-01-21, ~2.04 млн загрузок):

- Архитектура: ≈3.4 B LM + ≈970 M аудио-энкодер, **обученный с нуля с каузальным вниманием**; sliding-window attention и в энкодере, и в LLM → «бесконечный» стриминг. Фреймворк — Delayed Streams Modeling + Ada RMS-Norm для conditioning задержки — [tech report arXiv:2602.11298](https://arxiv.org/abs/2602.11298), [карточка HF](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602).
- Задержка: конфигурируемая, «from 80ms to 2.4s», кратно 80 мс (2400 — отдельное значение), рекомендуется **480 мс**; один текстовый токен = 80 мс.
- FLEURS (карточка): avg 8.72 % @480 мс, 12.60 % @160, 10.80 % @240, 7.70 % @960, 6.73 % @2400; **ru: 6.02 % @480 мс**, 9.53 % @160, 7.87 % @240, 5.56 % @960, 5.41 % @2400. Offline-базлайн «Voxtral Mini Transcribe 2.0» — avg 5.90 %, ru 4.75 %.
- Лицензия: **Apache-2.0**, BF16. 13 языков: en fr es de ru zh ja it pt nl ar hi ko.
- Порты: MLX `mlx-community/Voxtral-Mini-4B-Realtime-2602-{fp16,4bit}`, `...-6bit`; ONNX `onnx-community/Voxtral-Mini-4B-Realtime-2602-ONNX`; GGUF `ggml-org/...-GGUF`; отдельный Arabic-файнтюн. **CoreML-порта нет.**
- Реализация в WhisperLiveKit: `--backend voxtral-mlx` (Apple Silicon) и `--backend voxtral` (HF), «uses its own streaming policy and does not use LocalAgreement or SimulStreaming» — [`docs/backends.md#L5-L19`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/docs/backends.md#L5-L19).

Осторожно: README WhisperLiveKit в одном месте утверждает, что Voxtral «natively handles 100+ languages» — это противоречит карточке (13 языков). Считать неподтверждённым.

### 5. Kyutai STT (delayed streams)

- Архитектура: decoder-only Transformer над Mimi-токенами (12.5 Гц, 32 аудио-кода по 80 мс), текст сдвинут относительно аудио на фиксированный delay; выход с капитализацией и пунктуацией — [карточка kyutai/stt-2.6b-en-trfs](https://huggingface.co/kyutai/stt-2.6b-en-trfs).
- Две модели: `stt-1b-en_fr` (1 B, delay **0.5 с**, semantic VAD) и `stt-2.6b-en` (2.6 B, delay **2.5 с**); есть `-trfs`-варианты (2025-06-25).
- Лицензия весов: **CC-BY-4.0**. Языки: en+fr (1B) и en (2.6B) — **русского нет**.
- Параметры конфигурации по коду порта: attention window 750/375 кадров, text vocab 8000/4000, silence prefix 0/13 кадров (1.04 с), Mimi 24 кГц — [speech-swift `docs/models/kyutai-stt.md#L6-L18`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/kyutai-stt.md#L6-L18).
- MLX-порт: `Sources/KyutaiSTT`, дефолт INT8 (`aufklarer/Kyutai-STT-1B-MLX-8bit`), есть INT4/FP16; real-weight валидация 2.6B не проводилась — там же. ONNX нет (только GGUF `cstr/kyutai-stt-*`).

### 6. Moonshine Streaming (ответ на «Moonshine v2?»)

- Релиз: `moonshine-ai/moonshine-streaming-{tiny,small,medium}`, **2026-01-06**, лицензия **MIT**, язык — **только английский**.
- Архитектура: 50 Гц time-domain фронтенд → sliding-window Transformer-энкодер без позиционных эмбеддингов («ergodic»), окна `(16,4)` в первых/последних двух слоях и `(16,0)` в промежуточных → **lookahead 80 мс**; адаптер добавляет позиционные эмбеддинги, декодер — каузальный Transformer с RoPE.
- Размеры: 34 M / 123 M / 245 M. WER: AMI 19.03/12.54/**10.68**, Earnings-22 20.27/13.53/11.90, LibriSpeech clean 4.49/2.49/**2.08**, other 12.09/6.78/5.00; среднее 12.01/7.84/6.65 — [карточка moonshine-streaming-medium](https://huggingface.co/moonshine-ai/moonshine-streaming-medium).
- Ограничения из карточки: декодер авторегрессивный → полная задержка растёт с длиной транскрипта; Transformers-путь не реализует эффективный стриминг; галлюцинации/повторы на коротких и шумных сегментах.
- Порты: GGUF (`handy-computer/moonshine-streaming-*-gguf`), ONNX — только community (`Workmind/moonshine-streaming-small-ONNX`). **В sherpa-onnx Moonshine — только offline**: `offline-moonshine-model.*`, changelog «Add C++ runtime support for Moonshine v2» относится к оффлайн-моделям — [`sherpa-onnx/csrc/offline-model-config.h#L17`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-model-config.h#L17), [`CHANGELOG.md#L443-L446`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L443-L446).
- Русского нет; есть community-файнтюны `-ja`, `-ar`, `-ko`, `-vi`, `-zh`, `-uk` (HF).

### 7. T-one (T-Bank / T-Software DC) — единственная нативная потоковая модель для русского

- `t-tech/T-one`, лицензия **Apache-2.0**, 71 M параметров, Conformer с SwiGLU/RMSNorm/RoPE/U-Net-структурой; **потоковые состояния только в последних двух слоях**; обработка чанками **300 мс**; декодирование greedy или CTC beam search с KenLM — [карточка HF](https://huggingface.co/t-tech/T-one).
- Домен: телефония (57.9k ч телефонных данных из 80k ч), но заявлена устойчивость к другим доменам. WER (карточка): call-center **8.63** против GigaAM-RNNT v2 10.22, GigaAM-CTC v2 10.57, Vosk-ru 0.54 11.28, Whisper large-v3 19.39; named entities 5.83 против 9.55/9.81/17.87; при этом CommonVoice 19 — 5.32 против 2.68 у GigaAM-RNNT v2 (то есть на общем домене GigaAM лучше).
- ONNX: `model.onnx` + `vocab.json` лежат в репозитории; sherpa-onnx экспортирует и поддерживает стриминговый T-one: «Add C++ and Python support for T-one streaming Russian ASR models (#2575)» — [`CHANGELOG.md#L802-L804`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L802-L804), [`scripts/t-one/run.sh#L1-L10`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/scripts/t-one/run.sh#L1-L10), реализация `csrc/online-t-one-ctc-model.cc`, готовый архив `sherpa-onnx-streaming-t-one-russian-2025-09-08`.
- CoreML/MLX портов не найдено. Русский — единственный язык.

### 8. Microsoft VibeVoice-ASR-Streaming (1.5B / 7B)

- Релиз 2026-09-02, лицензия **MIT**, 10 языков включая **ru**; потоковый ASR **с атрибуцией спикера** (who said what), hotwords — [карточка](https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B).
- Механика: фиксированные чанки аудио перемежаются с текстом в одном авторегрессивном контексте + фиксированный lookahead **4 латентных кадра = 0.5 с**; латентный кадр 133.3 мс (3200× downsampling от 24 кГц). Опубликованы конфигурации 15 кадров (2.0 с) и 22 кадра (2.9 с). **Ожидаемая алгоритмическая задержка атрибуции = C/2 + lookahead → 2.00 с (22 кадра) и 1.53 с (15 кадров)** — [tech report arXiv:2609.02812](https://arxiv.org/abs/2609.02812).
- Качество (7B, 22 кадра): MLC-Challenge ru **WER 15.67 / cpWER 19.47**; средние по 5 сетам 24.66 против 25.23 у Gemini 3.5 Transcribe Live и 39–41 у GPT/ElevenLabs realtime. Задержка коммита: 2.00 с против измеренных 8.21 с (Azure CT) и 9.12 с (Google STT).
- Ограничения: контекст хранится без сжатия, чекпойнты рассчитаны на записи **до 8 минут**; одновременная речь сериализуется в последовательные сегменты.
- Порты: GGUF (`audio-cpp/VibeVoice-ASR-Streaming-7B-GGUF`), LiteRT (`litert-community/...-1.5B`). MLX есть только для **оффлайн** `VibeVoice-ASR` (5/6/8bit). CoreML/ONNX для streaming-варианта нет.

### 9. Whisper large-v3-turbo: стриминг только внешними алгоритмами

- Модель: 809 M, «pruned Whisper large-v3, decoding layers 32 → 4», лицензия **MIT**, ~99 языков (ru есть) — [карточка openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo).
- Нативного стриминга нет: окно 30 с, энкодер двунаправленный. Все известные решения — внешние политики:
  - **Simul-Whisper / AlignAtt** (attention-guided): «average absolute WER degradation of only 1.46 % at a chunk size of 1 second» — [arXiv:2406.10052](https://arxiv.org/abs/2406.10052).
  - **LocalAgreement** (whisper_streaming) — коммит стабильного префикса между двумя проходами.
  - **WhisperKit `AudioStreamTranscriber`**: буферизует весь поток и **пере-транскрибирует весь накопленный буфер** каждые ≥1 с, «подтверждая» сегменты по правилу «оставить последние N» (`requiredSegmentsForConfirmation: Int = 2`) — [`Sources/WhisperKit/Core/Audio/AudioStreamTranscriber.swift#L146-L215`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioStreamTranscriber.swift#L146-L215), дефолты — [`#L51-L58`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioStreamTranscriber.swift#L51-L58). Это O(n²) по вычислениям на длинном потоке `[INFERENCE]`.
  - Коммерческий Argmax Pro Local Server даёт WebSocket-стриминг, совместимый с Deepgram — [WhisperKit README#L270-L275](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/README.md#L270-L275).
- Измерения WhisperLiveKit (H100, LibriVox 6 мин, en): LocalAgreement + large-v3-turbo — WER 5.3 %, RTF 0.303 (aware) и WER 5.2 %, RTF 0.090 (unaware); SimulStreaming + large-v3-turbo — WER **18.1 %**, RTF 0.144 (aware) и 11.0 %, RTF 0.037 (unaware) — [`benchmarks/h100_scatter/bench_scatter_en_aware.json`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/benchmarks/h100_scatter/bench_scatter_en_aware.json). Вывод: на больших моделях агрессивная политика бьёт по качеству.
- Порты: CoreML — speech-swift `WhisperASR` (собственный рантайм, не WhisperKit) и WhisperKit; MLX — mlx-whisper (в WLK `--extra mlx-whisper`); ONNX — sherpa-onnx offline-whisper.

### 10. Qwen3-ASR (0.6B / 1.7B)

- Релиз 2026-01-29, **Apache-2.0**, 30 языков + 22 китайских диалекта, **русский в списке**; «streaming / offline unified inference with single model» — [карточка](https://huggingface.co/Qwen/Qwen3-ASR-1.7B), [репозиторий](https://github.com/QwenLM/Qwen3-ASR).
- Стриминг **только через vLLM-бэкенд**, без батча и без таймстемпов; в HF-transformers — нет — карточка, раздел Streaming Inference.
- Альтернативы для стриминга в WhisperLiveKit: `qwen3-streaming` (оконный пересчёт, 12 с левого контекста) и каузальная башня `qfuxa/qwen3-asr-0.6b-streaming` (каждый ~2 с блок кодируется один раз, окно 15 с, apache-2.0, **но language: en — только английский**). Замеры автора: offline 7.6 WER, windowed settled 8.4, causal settled 17.2, causal live 17.6, при 42 GFLOPs/с постоянно против 126–172 у оконного — [карточка qfuxa](https://huggingface.co/qfuxa/qwen3-asr-0.6b-streaming).
- Порты: CoreML+MLX в speech-swift (`Sources/Qwen3ASR`, `Qwen3ASRRuntime`, дескрипторы `qwen3-asr-0.6b-mlx-int4`, `qwen3-asr-1.7b-mlx-int8`, `qwen3-asr-coreml`); MLX `mlx-community/Qwen3-ASR-*`; ONNX community (`andrewleech/qwen3-asr-*-onnx`); LiteRT `litert-community/Qwen3-ASR-0.6B`.

### 11. Остальные кандидаты (все пакетные)

- **NVIDIA Canary 1B v2**: CC-BY-4.0, 25 европейских языков включая **ru** (`language: ru` в шапке карточки), AED-архитектура → оффлайн. CoreML-порт `FluidInference/canary-1b-v2-coreml`, MLX community, ONNX `istupakov/canary-1b-v2-onnx`. Есть также `canary-180m-flash` (2025-03-11, CC-BY-4.0, только en/de/es/fr — русского нет) и `canary-qwen-2.5b`.
- **IBM Granite Speech**: `granite-speech-5.0-470m-turboctc` (Apache-2.0, **7 языков en/fr/de/es/pt/it/ja, без ru**, Conformer + block self-attention, CTC, неавторегрессивный, релиз 2026-08-25, мультиязычное обновление 2026-10-21, English WER 5.51 %); `granite-speech-4.1-2b` (Apache-2.0, en/fr/de/es/pt/ja + AST, LLM-based). Стриминга нет; CTC-модель теоретически нарезается по чанкам, но официального потокового API нет `[INFERENCE]`.
- **Microsoft Phi-4-multimodal-instruct**: MIT, аудио-языки **en, zh, de, fr, it, ja, es, pt — русского нет** (русский есть только в текстовой модальности) — [карточка](https://huggingface.co/microsoft/Phi-4-multimodal-instruct). Оффлайн, 128k контекст.
- **GigaAM v3** (Sber, `ai-sage/GigaAM-v3`): MIT, Conformer 220–240 M, русский, оффлайн/longform; ONNX `istupakov/gigaam-v3-onnx`, MLX `al-bo/gigaam-v3-rnnt-mlx`. Стримингового варианта в карточке нет (заявлен HuBERT-CTC + RNNT/CTC головы).
- **Cohere Transcribe** (порт FluidAudio): 14 языков (en/fr/de/es/it/pt/nl/pl/el/ar/ja/zh/ko/vi) — **без ru**, жёсткий лимит 35 с на вызов, batch — [`Models.md#L20`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Models.md#L20).
- **NVIDIA multitalker-parakeet-streaming-0.6b-v1** (2025-10-15) — потоковый мультиспикерный ASR, есть GGUF и MLX-порты; тема диаризации, детали — в отчёте E2.

### 12. Как устроен стриминг в существующих рантаймах (данные для проектирования API)

| Рантайм | Модель конкурентности | Доказательство |
|---|---|---|
| sherpa-onnx | один `OnlineRecognizer` (общие веса) + N `OnlineStream` + **батчевый** `DecodeStreams(ss, n)` | [`csrc/online-recognizer.h#L183-L223`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer.h#L183-L223) |
| FluidAudio (CoreML) | `preloadShared` → 1×~1.5 ГБ весов + N×~50 МБ состояния; иначе N×1.5 ГБ | [`.../StreamingNemotronMultilingualAsrManager+Shared.swift#L70-L84`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Streaming/Nemotron/StreamingNemotronMultilingualAsrManager+Shared.swift#L70-L84) |
| FluidAudio, ANE | «N=1 baseline → N=2 ~1.5–1.8× → N=4 flattens — ANE dispatch становится бутылочным горлышком» | [`NemotronMultilingualMultiStreamBench.swift#L8-L22`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudioCLI/Commands/ASR/Parakeet/Streaming/NemotronMultilingualMultiStreamBench.swift#L8-L22) |
| speech-swift (MLX) | «Multiple sessions can share one loaded model; their caches remain separate while inference calls are serialized» | [`docs/models/nemotron-asr-streaming.md#L166-L170`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L166-L170) |
| speech-swift (общий) | `InferenceGate` — bounded FIFO на один слот, `maximumPending: 32`, отменяемые ожидающие | [`Sources/SpeechRuntime/InferenceGate.swift#L1-L30`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L1-L30) |
| speech-swift (таксономия) | `enum Streaming { case buffered, incremental }` в `ModelDescriptor` | [`Sources/SpeechRuntime/ModelDescriptor.swift#L9-L22`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelDescriptor.swift#L9-L22) |
| speech-swift (буферизованный стриминг LLM-ASR) | VAD-guided сегментация: `maxSegmentDuration 10 s`, `partialResultInterval 1 s`, `emitPartialResults false` по умолчанию | [`Sources/Qwen3ASR/StreamingASR.swift#L25-L52`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/Qwen3ASR/StreamingASR.swift#L25-L52) |
| speaches | «aims to be Ollama, but for TTS/STT models»; SSE-стриминг транскрипции; динамическая загрузка/выгрузка модели по запросу; Realtime API | [`README.md#L1-L20`](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/README.md#L1-L20) |
| WhisperLiveKit | многопользовательский бэкенд + VAD-гейтинг; политики: LocalAgreement, SimulStreaming (AlignAtt), «своя» для Voxtral, оконная/каузальная для Qwen3 | [`README.md#L29-L34`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/README.md#L29-L34), [`#L118-L132`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/README.md#L118-L132) |

### 13. Сводная матрица (acceptance)

Обозначения: **Н** — нативный стриминг, **Э** — эмуляция (VAD/чанки/окно), **—** — нет. Задержка — заявленная в источнике (не измеренная на Apple Silicon, если не указано).

| Модель (год) | Стриминг | Задержка / чанк | Русский | CoreML | MLX | ONNX | Лицензия |
|---|---|---|---|---|---|---|---|
| Nemotron 3.5 ASR Streaming 0.6B (2026-05) | **Н** | 80/160/320/560/1120 мс chunk; CoreML-порты 560/1120/2240 мс | **да** (ru-RU, FLEURS 9.60 % @560 мс) | да (FluidInference, aufklarer) | да (bf16/8bit, INT5/INT8) | да (int4; sherpa-onnx #3671) | OpenMDW-1.1 |
| Nemotron Speech Streaming EN 0.6B (2025-12) | **Н** | `att_context` → 0.08/0.16/0.56/1.12 с | нет | да | да (mlx-audio) | да (sherpa-onnx) | NVIDIA Open Model License |
| Parakeet EOU 120M (2025-10) | **Н** | 80–160 мс (CoreML тайры 160/320/1280 мс) | нет | да | нет | community ONNX | NVIDIA Open Model License |
| Parakeet Unified EN 0.6B streaming (2026-04) | **Н** (chunked attn) | дефолт 70_13_13 = 2.08 с | нет | да | нет (parakeet-mlx = batch) | да (sherpa-onnx, model type `nemo_parakeet_unified_streaming`, экспорт #3556) | NVIDIA Open Model License |
| Parakeet TDT v3 / Ultra / Redux (2025-08 / 2026) | Э | нет нативного; NeMo chunked-скрипт / окна | **да** (25 EU, ru) | да (Ultra 595 МБ int8) | да для v3 (`mlx-community/parakeet-tdt-0.6b-v3`); для Ultra MLX-порта не найдено | да (istupakov) | CC-BY-4.0 |
| Voxtral Mini 4B Realtime 2602 (2026-01) | **Н** | 80 мс – 2.4 с (кратно 80), рекомендуется 480 мс | **да** (FLEURS 6.02 % @480 мс) | нет | да (fp16/4bit/6bit) | да (onnx-community) | Apache-2.0 |
| Voxtral Mini 3B 2507 | — | окно 30 с | нет (8 языков) | нет | да (bf16) | да | Apache-2.0 |
| Moonshine Streaming tiny/small/medium (2026-01) | **Н** | 50 Гц фронтенд, lookahead 80 мс | нет (en) | нет | нет | частично (community) | MIT |
| Kyutai STT 1B en_fr / 2.6B en (2025) | **Н** | 0.5 с / 2.5 с | нет | нет | да (speech-swift, INT8 default) | нет (GGUF) | CC-BY-4.0 |
| T-one 71M (2025-09) | **Н** | чанк 300 мс | **да** (только ru) | нет | нет | да (model.onnx; sherpa-onnx streaming-t-one-russian-2025-09-08) | Apache-2.0 |
| VibeVoice-ASR-Streaming 1.5B/7B (2026-09) | **Н** | чанк 2.9 с + lookahead 0.5 с → 2.00 с | **да** (ru WER 15.67 / cpWER 19.47) | нет | нет (MLX только offline) | нет (GGUF) | MIT |
| Whisper large-v3-turbo 809M (2024) | Э | политика-зависимо (Simul-Whisper: +1.46 % abs WER при чанке 1 с) | **да** (99 языков) | да (speech-swift, WhisperKit) | да (mlx-whisper) | да (sherpa-onnx offline) | MIT |
| Qwen3-ASR 0.6B/1.7B (2026-01) | Э (vLLM-стриминг; каузальная башня только en) | не документировано upstream | **да** | да (speech-swift) | да | да (community) | Apache-2.0 |
| Canary 1B v2 (2025-08) / 180M Flash (2025-03) | — | окно 30–40 с | **да** у v2 (25 EU); у Flash нет (en/de/es/fr) | да (v2) | да (community) | да | CC-BY-4.0 |
| Granite Speech 5.0 TurboCTC 470M (2026-10) | — | CTC, неавторег. | нет (7 языков) | нет | да (4.1-nar) | да (4.1) | Apache-2.0 |
| Granite Speech 4.1 2B (2025) | — | LLM, оффлайн | нет (en/fr/de/es/pt/ja) | нет | нет | да | Apache-2.0 |
| Phi-4-multimodal (2025) | — | оффлайн, 128k ctx | нет (аудио: 8 языков без ru) | нет | community | да (microsoft onnx) | MIT |
| GigaAM v3 (2025) | — | оффлайн/longform | **да** (только ru) | нет | да (RNNT MLX) | да (istupakov) | MIT |
| Cohere Transcribe (2026-03) | — | 35 с на вызов | нет | да (FluidAudio) | нет | нет | см. карточку Cohere (не проверялась в этой волне) |

---

## Ограничения и риски

1. **Лицензии неоднородны и не «просто Apache».** OpenMDW-1.1 (Nemotron 3.5), NVIDIA Open Model License (Nemotron EN, Parakeet Unified/EOU), CC-BY-4.0 с требованием атрибуции (Parakeet v3/Ultra/Redux, Canary, Kyutai), MIT (Whisper, Moonshine, VibeVoice, Phi-4, GigaAM), Apache-2.0 (Voxtral Realtime, Qwen3-ASR, T-one, Granite). Для каталога моделей лицензия должна быть полем метаданных, а не константой рантайма; CC-BY требует attribution в UI/документации.
2. **Часть репозиториев gated.** speech-swift прямо помечает `nvidia/nemotron-3.5-asr-streaming-0.6b` как gated — значит автоматическая загрузка потребует токена HF; это ломает «модель по требованию» без учёта авторизации.
3. **Расхождение лицензии в community-портах.** `onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4` объявляет в шапке `license: mit` при `license_name: nvidia-open-model-license`; базовые веса — OpenMDW-1.1. Нельзя доверять лицензии порта.
4. **Задержка запечена в артефакт.** Один и тот же Nemotron требует отдельных экспортов под 80/160/320/560/1120 мс (и 560/1120/2240 в CoreML), а MLX-бандлы speech-swift отвергают иную геометрию. Каталог должен моделировать «вариант модели = (движок, точность, chunk/латентность)».
5. **ANE плохо масштабируется по параллельным сессиям.** Ожидаемый профиль: N=2 → 1.5–1.8×, N=4 — плато; плюс разные compute units дают разницу до ~10× (FluidAudio: `.all` уводит int8-энкодер на GPU и работает ~10× медленнее, поэтому менеджер пинит `.cpuAndNeuralEngine`).
6. **MLX-путь сериализует инференс** (общие веса, но один вызов за раз) — это ограничение конкурентности, которое придётся либо принять, либо обходить несколькими процессами `[INFERENCE]`.
7. **Whisper-стриминг по своей природе O(n²) и хрупок.** WhisperKit пере-транскрибирует весь буфер; SimulStreaming на large-v3-turbo дал WER 18.1 % против 5.3 % у LocalAgreement (H100, LibriVox). Агрессивные политики на больших моделях ухудшают качество.
8. **LLM-стриминг ограничен по длине сессии.** VibeVoice-ASR-Streaming: контекст растёт линейно, чекпойнты рассчитаны на ≤8 мин; Moonshine Streaming: полная задержка растёт с длиной транскрипта (авторегрессивный декодер).
9. **Русский + английские IT-термины нигде не измерялся.** FLEURS-ru — это чтение, а не техническая речь с code-switching (`kubectl`, `Istio`, `PostgreSQL`). Ни одна карточка не даёт code-switch метрик для ru/en. Это ключевой пробел для нашего сценария.
10. **Документация портов местами недостоверна** (см. Итог п.7) — при интеграции нужно проверять upstream-карточку и код, а не README порта.

---

## Выводы для Runtime

1. **Ввести два класса стриминга в контракт API**, как это уже сделано в speech-swift: `incremental` (модель сама отдаёт партиалы по мере аудио; Nemotron, Voxtral Realtime, T-one, Moonshine, Kyutai, VibeVoice) и `buffered` (движок нарезает VAD/окнами и пере-декодирует; Whisper, Parakeet TDT, Qwen3-ASR, Canary). Клиент не должен угадывать: класс — поле дескриптора модели, а `partial`/`final` семантика одинаковая.
2. **Дескриптор модели = (семейство, движок, точность, геометрия чанка/задержки, языки, лицензия, размер).** Задержка и язык — свойства варианта, не глобальные настройки. Это прямое следствие того, что Nemotron/Parakeet Unified/Voxtral зашивают их в экспорт.
3. **Языковой хинт — параметр сессии.** Для Nemotron — `prompt_id` (ru-RU = 11, auto = 101); для Voxtral — `lang:<code>`; для Qwen3 — токен языка; для T-one/Parakeet-EOU — язык фиксирован. Рантайм обязан валидировать «язык × модель» и возвращать понятную ошибку вместо тихого переключения на en.
4. **Модель конкурентности: общие веса + состояние на сессию + очередь с backpressure.** Референсы: `preloadShared` (1×веса + N×состояние), `InferenceGate` (bounded FIFO, отменяемые ожидания), `DecodeStreams` в sherpa-onnx (батч по нескольким стримам). Для CoreML/ANE — очередь на 1–2 одновременных инференса и деградация по задержке, а не по качеству.
5. **Не смешивать постобработку с адаптером движка.** Nemotron отдаёт пунктуацию и капитализацию нативно, Parakeet EOU — нет и вообще без пунктуации; Whisper даёт пунктуацию, но галлюцинирует на тишине. Значит: нормализация/пунктуация/ITN — отдельный слой поверх единого текстового контракта, с флагом «модель уже пунктуирует».
6. **EOU/VAD — не доверять модели целиком.** Parakeet EOU шумит на неречевых паузах; практика портов — дебаунс EOU по нескольким кадрам + принудительный finalize по VAD `[INFERENCE]`, подтверждено описанием в speech-swift `parakeet-streaming-asr.md`.
7. **Для русского — приоритетный набор из трёх моделей, а не одной:** (а) Nemotron 3.5 Streaming 0.6B — лучший баланс «нативно + ru + пунктуация + порты на всех трёх движках»; (б) Voxtral Mini 4B Realtime — лучшее качество на ru при 480 мс, но только MLX/ONNX и 4 B параметров; (в) T-one — дешёвый (71 M) и точный на телефонии/созвонах, только ru, только ONNX. VibeVoice-ASR-Streaming — если нужна атрибуция спикеров в реальном времени (цена — 2 с задержки и 1.5–7 B).
8. **Whisper turbo держать как `buffered`-фолбэк**, а не как основной стриминг: качество на ru хорошее, но задержка определяется внешней политикой, а вычислительная стоимость на длинном потоке растёт квадратично при наивной реализации.
9. **Не тащить в рантайм «модель, которая решает, что сказать».** VibeVoice/MOSS/Cohere-подобные LLM-ASR дают богатый выход (спикеры, hotwords), но требуют строгого контракта: клиент указывает модель и операцию, рантайм не интерпретирует смысл — совпадает с исходной постановкой.
10. **Каталог должен поддерживать «выгрузку весов в простое»** (прецедент — speaches): idle-unload по таймауту, ленивая загрузка по запросу, но с сохранением «горячих» весов при активных сессиях. Для CoreML выгрузка дешевле (модели компилируются один раз и кешируются), для MLX — дороже (маппинг safetensors) `[INFERENCE]`.

---

## Неизвестные / что измерить

1. **Задержка «первый партиал» на Apple Silicon ни для одной модели не опубликована.** Все upstream-цифры — либо архитектурный lookahead, либо chunk size. Нужно измерить: t(первый непустой partial), t(стабильный prefix), p50/p95 межтокенного интервала для Nemotron 3.5 (320/560/1120 мс), Voxtral Realtime MLX (240/480/960 мс), T-one (300 мс) на M-серии.
2. **Качество на русском с английской IT-терминологией не измерено нигде.** Нужен собственный тест-сет: 30–60 мин созвонов с `kubectl`, `Istio`, `PostgreSQL`, `CI/CD`, именами сервисов; метрики WER + term-recall (отдельно для имён/аббревиатур). Кандидаты: Nemotron 3.5 (prompt ru-RU и auto), Voxtral Realtime, Whisper turbo (buffered), T-one, GigaAM v3 (offline как ориентир).
3. **Не подтверждено, что ONNX-int4 Nemotron 3.5 работает в sherpa-onnx.** В репозитории есть `genai_config.json` (формат onnxruntime-genai), а не sherpa-onnx-совместимый layout. Нужна проверка загрузки/инференса и сверка WER с CoreML-портом.
4. **Не проверено, как ведёт себя Voxtral Realtime MLX на M-серии**: throughput (заявлено «>12.5 токенов/с на минимальном железе» — без указания платформы), память для 4bit/6bit, реальная задержка при 480 мс, деградация при 80/160 мс.
5. **Нет данных по конкурентности для MLX-портов** (сколько сессий держит один процесс без роста задержки) и по поведению ANE при 2–4 одновременных Nemotron-сессиях на M-серии (есть только комментарий-ожидание в бенчмарке FluidAudio).
6. **Не найдено CoreML-порта Voxtral Realtime и VibeVoice-ASR-Streaming** — вопрос, реализуемо ли это вообще (каузальный энкодер + sliding-window attention в CoreML) или MLX/ONNX останутся единственным путём.
7. **Не подтверждена судьба «Voxtral Mini Transcribe 2.0»** (offline-базлайн из карточки Realtime): открытых весов в HF-организации mistralai не найдено — вероятно, только API. То же по «GPT Realtime Whisper»/«Gemini 3.5 Transcribe Live» — это закрытые сервисы, они фигурируют только как baseline в отчёте VibeVoice.
8. **PDF-статья Moonshine Streaming недоступна** (https://download.moonshine.ai/docs/moonshine_streaming_paper.pdf не отвечает) — заявленные в карточке 80 мс lookahead и WER взяты из карточки, не из статьи.
9. **Не измерена стоимость выгрузки/повторной загрузки весов** (idle-unload): для 4 B Voxtral это ~8 ГБ fp16 / ~2.3 ГБ 4-bit `[INFERENCE]` — нужен замер cold-start на диске Apple Silicon.
10. **Не проверено, поддерживает ли sherpa-onnx `nemo_parakeet_unified_streaming` выбор геометрии `[L,C,R]`** (в CoreML-порте контекст запечён в бандл: `70_13_13`) — от этого зависит, можно ли одной ONNX-моделью давать разные задержки.

---

## Источники

**Код (проверено, permalink с SHA):**
- FluidAudio `1b0ed50`: `Documentation/Models.md`, `Documentation/ASR/Nemotron.md`, `Documentation/ASR/NemotronMultilingual.md`, `Documentation/ASR/ParakeetUltra.md`, `Documentation/Benchmarks.md`, `Sources/FluidAudio/ASR/Parakeet/Streaming/Nemotron/StreamingNemotronMultilingualAsrManager+Shared.swift`, `Sources/FluidAudioCLI/Commands/ASR/Parakeet/Streaming/Nemotron/NemotronMultilingualMultiStreamBench.swift`
- speech-swift `cbad3be`: `docs/models/nemotron-asr-streaming.md`, `docs/models/kyutai-stt.md`, `docs/models/voxtral-asr.md`, `docs/models/whisper-asr.md`, `docs/models/parakeet-streaming-asr.md`, `Sources/SpeechRuntime/ModelDescriptor.swift`, `Sources/SpeechRuntime/InferenceGate.swift`, `Sources/Qwen3ASR/StreamingASR.swift`, `Sources/Qwen3ASRRuntime/Qwen3ASRRuntime.swift`, `Sources/NemotronStreamingASR/Configuration.swift`
- WhisperKit `9f99b5c`: `Sources/WhisperKit/Core/Audio/AudioStreamTranscriber.swift`, `README.md`
- sherpa-onnx `532f1b5`: `CHANGELOG.md`, `sherpa-onnx/csrc/online-recognizer.h`, `sherpa-onnx/csrc/online-transducer-nemo-model.cc`, `sherpa-onnx/csrc/online-transducer-nemo-parakeet-unified-model.cc`, `sherpa-onnx/csrc/qnn/online-nemo-transducer-model-qnn.cc`, `sherpa-onnx/csrc/online-t-one-ctc-model.cc`, `sherpa-onnx/csrc/offline-model-config.h`, `scripts/t-one/run.sh`, `go-api-examples/streaming-decode-files/run-t-one-ctc.sh`
- WhisperLiveKit `eb92c11`: `README.md`, `docs/backends.md`, `benchmarks/h100_scatter/bench_scatter_en_{aware,unaware}.json`
- speaches `993994f`: `README.md`, `model_aliases.json`

**Model cards / tech reports (веб):**
- https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b
- https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b
- https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- https://huggingface.co/nvidia/parakeet-unified-en-0.6b
- https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1
- https://huggingface.co/moondream/parakeet-ultra (и `moondream/parakeet-redux`)
- https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602
- https://arxiv.org/abs/2602.11298 (Voxtral Realtime tech report)
- https://huggingface.co/kyutai/stt-2.6b-en-trfs , https://huggingface.co/kyutai/stt-1b-en_fr-trfs
- https://huggingface.co/moonshine-ai/moonshine-streaming-medium (и `-small`, `-tiny`)
- https://huggingface.co/t-tech/T-one
- https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B , https://arxiv.org/abs/2609.02812
- https://huggingface.co/Qwen/Qwen3-ASR-1.7B , https://github.com/QwenLM/Qwen3-ASR , https://huggingface.co/qfuxa/qwen3-asr-0.6b-streaming
- https://huggingface.co/openai/whisper-large-v3-turbo
- https://arxiv.org/abs/2406.10052 (Simul-Whisper)
- https://huggingface.co/nvidia/canary-1b-v2 , https://huggingface.co/nvidia/canary-180m-flash
- https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc , https://huggingface.co/ibm-granite/granite-speech-4.1-2b
- https://huggingface.co/microsoft/Phi-4-multimodal-instruct
- https://huggingface.co/ai-sage/GigaAM-v3
- https://raw.githubusercontent.com/Blaizzy/mlx-audio/main/mlx_audio/stt/models/nemotron_asr/README.md
- https://github.com/senstella/parakeet-mlx (Apache-2.0, 988★, push 2026-10-01; стриминга нет — chunk 120 с / overlap 15 с)
