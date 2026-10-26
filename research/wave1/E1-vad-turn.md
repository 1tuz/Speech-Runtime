# E1. VAD и детекция конца реплики (состояние на 2026-10-25)

Волна 1. Исследование, не реализация. Каждая цифра — либо из официального model card / tech report / исходного кода, либо из измерения внутри клона репозитория (тогда указан коммит). Собственные выводы помечены `[INFERENCE]`. Пользовательские впечатления (блоги вендоров) отделены явно.

Проверенные клоны (SHA из задания): `speech-swift@cbad3be`, `SpeechRail@cdccc74`, `sherpa-onnx@532f1b5`, `WhisperLiveKit@eb92c11`, `speaches@993994f`, `gotranscribesrv@a5f4289`, `FluidAudio@1b0ed50`, `WhisperKit@9f99b5c`.

---

## Итог

1. **Дефолтный VAD для runtime — Silero VAD v6.2 (тег v6.2.1 в Apple-портах).** MIT, ~260 K параметров по официальной вики (309 121 в MLX-экспорте), 0.6 МБ fp16 CoreML / 1.2 МБ fp32 MLX / 1.29–2.33 МБ ONNX, 8 и 16 кГц, строго 512 сэмплов (32 мс) + 64 сэмпла контекста на шаг, состояние `(2,1,128)` на поток. Замеры в speech-swift: 0.063 мс/чанк на ANE (RTF 0.00197) и 0.50 мс/чанк на MLX (RTF 0.0156). У неё одновременно есть ONNX (официальный), CoreML и MLX артефакты, и три независимых продакшн-реализации паттерна «общие веса + состояние на сессию».
2. **Разделение «веса общие, состояние на сессию» — не гипотеза, а трижды независимо реализованный паттерн.** `SileroVADModel.makeIndependentStream()` (speech-swift), общий `InferenceSession` + состояние на детектор (SpeechRail), общий `OnnxSession` + `OnnxWrapper` со своим `state` (WhisperLiveKit). Состояние Silero стоит ~1.3 КБ на сессию — это на 3 порядка меньше, чем у любой ASR-модели.
3. **TEN VAD технически лучше по латентности (hop 10/16 мс, RTF 0.0150–0.0160, библиотека 306–313 КБ), но лицензионно непригоден как дефолт открытого runtime.** Apache-2.0 «с дополнительными условиями»: запрещено разворачивать способом, конкурирующим с продуктами Agora, включая предоставление третьим лицам возможности создавать приложения.
4. **FireRedVAD (февраль 2026) — сильнейший «тяжёлый» кандидат:** 588 417 параметров (VAD) / 567 937 (Stream-VAD), 10 мс hop, 100+ языков, Apache-2.0, F1 97.57 % на FLEURS-VAD-102 по собственному бенчмарку; CoreML fp16 1.18 МБ, RTF 0.007 на ANE в замере speech-swift. Но нерекуррентный DFSMN с контекстом 200 мс вперёд (в непоточной версии) и пока без поточного CoreML-порта.
5. **WebRTC VAD — только аварийный fallback.** GMM (6 полос × 2 гауссианы), выход бинарный (нет вероятности), 8/16/32 кГц, 10/20/30 мс, BSD-3. На мульти-доменном наборе Silero: ROC-AUC 0.73 и точность 0.00 на ESC-50 против 0.97/0.87 у Silero v6.
6. **pyannote segmentation-3.0 как VAD — плохая идея для endpointing, хорошая для диаризации.** MIT, но gated (нужен HF-токен), офлайн-окно 10 с, FAR 50 % на VoxConverse в замере speech-swift при F1 98.22 % и MR 0.19 %. Для потока непригодна: би-LSTM поверх 10-секундного окна, RTF 0.358 на MLX.
7. **Smart Turn v3.2 — рекомендованный аудио-классификатор конца реплики.** BSD-2, 8 M параметров (Whisper-Tiny encoder + линейная голова), окно 8 с / 128 000 сэмплов 16 кГц, 23 языка **включая русский и украинский**, 3.5 мс на окно в CoreML на M5 Pro, 16.7 МБ fp16. Требует VAD перед собой — сам речь не детектирует.
8. **Текстовые детекторы конца реплики (LiveKit, Namo) требуют транскрипта, то есть ASR уже должен работать** — это другой класс: они не могут сработать раньше, чем ASR выдал partial. LiveKit: Qwen2.5-0.5B дистилляция, 14 языков включая ru (TPR 99.3 % / TNR 88.0 %), лицензия `livekit-model-license` — **не OSS**.
9. **Новое в 2026: `turn-1-mini`** (p99lab, 2026-10-06) — 5.26 M параметров, поточный (шаг 160 мс, кадр 20 мс), аудио-онли, Apache-2.0, 1.29 мс/шаг на одном ядре M4 Pro. По **своим** замерам на публичном LiveKit `eot-bench` (EN) — лучший среди скачиваемых моделей (Smart Turn v3.2 в той же таблице: 35.2 % false cut-offs @300 мс против 22.6 %). Замеры сделаны конкурентом, независимого воспроизведения нет. Ограничения: **только английский**, обязательный low-pass 4 кГц, крошечная лаборатория.
10. **VAD не решает endpointing, и наоборот.** Внутренний endpointing ASR (EOU-голова Parakeet с дебаунсом 1280 мс; правила sherpa-onnx 2.4/1.2/20 с по trailing silence в декодировании) и внешний VAD дают **разные** сигналы и конфликтуют; в проверенном коде обязателен guardrail от двойного final. Русского публичного EOT-бенчмарка нет: в `livekit/eot-bench` 14 языков, ru отсутствует.

---

## Факты

### 1. Silero VAD v5/v6 — основной кандидат

**Версии и метрики (официальные).** Вики перечисляет v6.0 и v6.2; Apple-порты speech-swift/Aufklarer собраны с upstream-тега `v6.2.1` — [aufklarer/Silero-VAD-v6.2.1-MLX](https://huggingface.co/aufklarer/Silero-VAD-v6.2.1-MLX) («MLX port of snakers4/silero-vad tag v6.2.1»).

| Свойство | Значение | Источник |
|---|---|---|
| Текущая версия | v6.0 — 2025-08-25, v6.2 — 2025-12-10 («ifless ONNX and tinygrad 16k models») | [wiki: Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models) |
| Параметры | «~260K», streaming, 6000+ языков, ONNX opset 15/16 | там же (таблица Available models) |
| Лицензия | MIT | [README](https://github.com/snakers4/silero-vad/blob/master/README.md) (бейдж + раздел «No Strings Attached») |
| Частота | 8000 и 16000 Гц | README, раздел «Flexible sampling rate» |
| Латентность | «One audio chunk (30+ ms) takes less than 1 ms … on a single CPU thread»; JIT ~2 МБ | README, «Fast» / «Lightweight» |
| Качество (мульти-домен, ROC-AUC, кадр 31.25 мс) | v6 **0.97**, v5 0.96, FireRed VAD 0.94, TEN VAD 0.93, WebRTC **0.73** | [wiki: Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics) |
| Шум ESC-50 (точность на всём файле) | v6 **0.87**, FireRed VAD 0.60, v5 0.61, TEN 0.42, WebRTC 0.00 | там же |

**Форма входа и состояние (код официального враппера).** `num_samples = 512 if sr == 16000 else 256`, `context_size = 64 if sr == 16000 else 32`, состояние `torch.zeros((2, batch_size, 128))`, на вход ONNX подаются `input` (512+64), `state` и `sr` — [utils_vad.py#L112-L139](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py#L112-L139). То есть **окно жёстко зашито в модель**: 512 сэмплов при 16 кГц, 256 при 8 кГц, плюс ручное протаскивание последних 64 (32) сэмплов.

**Порты и их отличия (проверено по коду).**

| Порт | Окно | Состояние | Особенности | Ссылка |
|---|---|---|---|---|
| speech-swift (MLX) | 512 + 64 | `h`,`c` `MLXArray` на инстанс | `~260K params`, `chunkSize = 512`, `contextSize = 64`, default `aufklarer/Silero-VAD-v6.2.1-MLX` | [SileroVAD.swift#L18-L28](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift#L18-L28), [#L73-L82](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift#L73-L82) |
| speech-swift (CoreML) | 512 + 64 | `coremlH`, `coremlC` `MLMultiArray` float16 | вход `[1,1,576]` float16, выходы `h_out`,`c_out`,`probability` | [CoreMLSileroInference.swift#L14-L58](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/CoreMLSileroInference.swift#L14-L58) |
| FluidAudio (CoreML) | **4096 + 64** («unified 256 ms» артефакт) | `VadState` — значение, передаётся в `processChunk(_:inputState:)` | `public actor VadManager`, `stateSize = 128`, `chunkSize = 4096` | [VadManager.swift#L14-L26](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadManager.swift#L14-L26), [#L162-L172](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadManager.swift#L162-L172) |
| sherpa-onnx (ONNX, C++) | 512 (+64 оверлап), 1024/1536 только для v4 | `states_`: `[2,1,128]` (v5/v6) или `h`,`c` `[2,1,64]` (v4) | **жёстко 16 кГц** (`SHERPA_ONNX_EXIT(-1)` при другой частоте); v5 требует `window_size == 512` | [silero-vad-model.cc#L43-L46](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/silero-vad-model.cc#L43-L46), [#L205-L245](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/silero-vad-model.cc#L205-L245) |
| WhisperLiveKit (ONNX/JIT) | 512 при 16 кГц, 256 при 8 кГц | `_state` `(2,batch,128)` + `_context` | `OnnxSession` — общая сессия «(stateless)», `OnnxWrapper` — состояние на инстанс | [silero_vad_iterator.py#L27-L64](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/silero_vad_iterator.py#L27-L64), [#L93-L118](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/silero_vad_iterator.py#L93-L118) |
| speaches (ONNX v5) | 512 + 64, batch | `state` создаётся нулевым на каждый вызов (офлайн-режим) | encoder/decoder разделены, `encoder_batch_size = 10000` | [silero_vad_v5.py#L99-L140](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/silero_vad_v5.py#L99-L140) |
| SpeechRail (ONNX) | 512 (+64) | per-detector `_state` `[2,1,128]` + `_context`; v4 — `h`/`c` `[2,1,64]` | авто-детект схемы v4 vs v5/v6 по именам входов | [neural_vad.py#L1-L30](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/neural_vad.py#L1-L30), [#L79-L114](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/neural_vad.py#L79-L114) |

**Размеры артефактов (HF API, фактические байты).** `istupakov/silero-vad-onnx`: `silero_vad.onnx` 2.33 МБ, `silero_vad_16k_op15.onnx` 1.29 МБ, `silero_vad_op18_ifless.onnx` 2.85 МБ. `aufklarer/Silero-VAD-v6.2.1-CoreML`: `weights/weight.bin` 0.62 МБ, плюс отдельный `silero_vad_256ms.mlmodelc` (0.62 МБ). `aufklarer/Silero-VAD-v6.2.1-MLX`: `model.safetensors` 1.2 МБ, 309 121 параметр.

**Измеренная латентность (не upstream, а релизные тесты порта).** 20 с речи, 625 потоковых чанков: MLX 0.4999 мс/чанк (RTF 0.01562), CoreML 0.0630 мс/чанк (RTF 0.00197); расхождение вероятностей CoreML vs MLX — max 0.0606, среднее 0.00167 — [aufklarer/Silero-VAD-v6.2.1-MLX (карточка, Performance)](https://huggingface.co/aufklarer/Silero-VAD-v6.2.1-MLX).

**Расхождение по числу параметров не разрешено:** вики Silero — «~260K», экспорт v6.2.1 — 309 121, бенчмарк speech-swift для v5 — 309K. Вероятная причина (STFT-базис как Conv1d 258×256) — `[INFERENCE]`, абляции не найдено.

**Чувствительность к размеру окна (независимый замер).** На LibriVAD-small test-clean (2 214 файла, 9 семейств шума, −5…20 дБ): Silero при **нативном 32 мс** — AUC 0.920, F1 0.910; при 20 мс — AUC 0.627, F1 0.353; при 40 мс — 0.803/0.283; при 50 мс — 0.738/0.533. TEN VAD при 16/20/30/40/50 мс — 0.916…0.919. Авторы прямо пишут, что падение Silero, вероятно, артефакт их протокола фиксированного кадра (нулевой padding, усреднение подчанков, не сброшенное состояние), абляций не делали — [arXiv:2610.14607](https://arxiv.org/abs/2610.14607), Table V. **Практический вывод для runtime: 512 сэмплов и carry-over состояния — не опция.**

### 2. TEN VAD (Agora)

| Свойство | Значение | Источник |
|---|---|---|
| Вход | 16 кГц, hop 160 или 256 сэмплов (10/16 мс), другие частоты — ресемплинг | [README, «Supported Sampling Rate and Hop Size»](https://github.com/ten-framework/ten-vad) |
| Размер | библиотека 306 КБ (Linux x64 `.so` — 313 240 байт в HF-репо), было 2.16 МБ JIT / 2.22 МБ ONNX у Silero в их же таблице | README, таблица Lib Size; HF `TEN-framework/ten-vad` |
| RTF | 0.0150 (Ryzen 9 5900X), 0.0160 (M1), 0.0086 (Xeon Gold 6348) против 0.0127 у Silero на том же Xeon | README, таблица RTF |
| API | `ten_vad_create(handle, hop_size, threshold)`, `ten_vad_process(handle, int16*, len, &prob, &flag)` — **инстанс на поток** | [include/ten_vad.h#L41-L71](https://github.com/ten-framework/ten-vad/blob/main/include/ten_vad.h#L41-L71) |
| Лицензия | **Apache-2.0 с дополнительными условиями**: нельзя разворачивать так, чтобы конкурировать с продуктами Agora, включая предоставление третьим лицам возможности создавать/разворачивать приложения | [LICENSE#L1-L25](https://github.com/ten-framework/ten-vad/blob/main/LICENSE) |
| Качество (их собственный PR-набор) | «superior precision compared to both WebRTC VAD and Silero VAD» — вендорское утверждение без независимого подтверждения | README, «High-Performance» |
| Качество (независимо) | AUC 0.917, AP 0.968, F1 0.913 при 16 мс; babble −5 дБ ≈ случайность (AUC 0.509) | [arXiv:2610.14607](https://arxiv.org/abs/2610.14607), Table III–IV |
| Качество (третья сторона) | Мульти-домен ROC-AUC 0.93 (Silero v6 — 0.97); ESC-50 0.42 | [wiki Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics) |
| FLEURS-VAD-102 (бенчмарк конкурента) | F1 95.19, FAR 15.47, MR 2.95 (Silero — 95.95/9.41/3.95) | [FireRedVAD README](https://github.com/FireRedTeam/FireRedVAD) |

Вендорские бенчмарки противоречат друг другу: TEN заявляет превосходство над Silero, Silero — над TEN (0.97 против 0.93), FireRed ставит TEN ниже Silero. Ни один не воспроизводим на общем наборе без работы.

### 3. FireRedVAD (Xiaohongshu, февраль–март 2026)

| Свойство | Значение | Источник |
|---|---|---|
| Версии | 2026-02-12 (в составе FireRedASR2S), 2026-03-03 — отдельный репозиторий, 2026-03-12 — tech report | [README, News](https://github.com/FireRedTeam/FireRedVAD) |
| Параметры | VAD 588 417; Stream-VAD 567 937; AED 588 931; 2.2 МБ float32 / 2.37 МБ `model.pth.tar` | README (вывод CLI) + HF blobs |
| Архитектура | DFSMN, 7 блоков, depthwise Conv1d k=20, 80-мерный Kaldi fbank (25 мс окно, 10 мс шаг), CMVN «запечён» | [speech-swift docs/models/fireredvad.md](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/fireredvad.md) |
| Кадр | 400 сэмплов (25 мс), шаг 10 мс, 100 кадров/с | [fireredvad/core/constants.py](https://github.com/FireRedTeam/FireRedVAD/blob/main/fireredvad/core/constants.py) |
| Контекст | 200 мс lookback + 200 мс lookahead (непоточная версия) | speech-swift docs (там же) |
| Языки | 100+ языков, речь/пение/музыка; mVAD (мульти-лейбл) | README |
| Лицензия | **Apache-2.0** (LICENSE + `license: apache-2.0` на HF) | репозиторий, HF `FireRedTeam/FireRedVAD` |
| FLEURS-VAD-102 | AUC 99.60, F1 **97.57**, FAR 2.69, MR 3.62 (Silero 97.99/95.95/9.41/3.95; WebRTC —/52.30/2.83/64.15) | README, Evaluation |
| CoreML-порт | 1.18 МБ fp16; RTF 0.007 на ANE, cold start ~0.5 с | [docs/benchmarks/vad-detection.md](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/vad-detection.md) |
| На VoxConverse (5 файлов) | F1 94.21, FAR 40.12, MR 5.05 | там же |
| На FLEURS (10 языков, 250 файлов, эталон — Python FireRedVAD) | F1 99.12, FAR 2.52, MR 0.47 | там же |

Поточный режим: `FireRedStreamVad.detect_frame(frame)` принимает ровно `FRAME_LENGTH_SAMPLE` = 400 сэмплов; постпроцессор `smooth_window_size=5`, `min_speech_frame=8`, `min_silence_frame=20`, режимы 0–3 меняют эти пары (8/20, 10/15, 15/10, 20/5) — [fireredvad/stream_vad.py#L21-L160](https://github.com/FireRedTeam/FireRedVAD/blob/main/fireredvad/stream_vad.py#L21-L160). В speech-swift порт **только пакетный** (`detectSpeech`), поточного CoreML-экспорта нет.

### 4. WebRTC VAD

| Свойство | Значение | Источник |
|---|---|---|
| Алгоритм | GMM: 6 полос, 2 гауссианы на полосу, hangover-логика | [vad_core.h#L30-L32](https://webrtc.googlesource.com/src/+/refs/heads/main/common_audio/vad/vad_core.h) |
| Вход | 8000, 16000, **32000** Гц; кадры 10/20/30 мс; `int16` | [webrtc_vad.h#L64-L82](https://webrtc.googlesource.com/src/+/refs/heads/main/common_audio/vad/include/webrtc_vad.h) |
| Выход | `1` / `0` / `-1` — **вероятности нет**, только решение | там же |
| Режимы | 0–3, выше — агрессивнее (меньше false accept, больше miss) | там же, `WebRtcVad_set_mode` |
| Состояние | структура `VadInstT` на инстанс (несколько сотен байт) | vad_core.h#L37-L63 |
| Лицензия | BSD-style (WebRTC) | шапка файла |
| Качество | ROC-AUC 0.73 на мульти-домене, точность 0.00 на ESC-50; F1 52.30 на FLEURS-VAD-102 при MR 64.15 | wiki Silero; FireRedVAD README |

### 4a. WhisperKit: VAD как протокол с энергетическим дефолтом

`open class VoiceActivityDetector: @unchecked Sendable` с `sampleRate`, `frameLengthSamples`, `frameOverlapSamples` и единственным обязательным методом `voiceActivity(in: [Float]) -> [Bool]`; явно помечен как «immutable и pure function, поэтому безопасен для совместного использования между доменами конкурентности» — [VoiceActivityDetector.swift#L6-L52](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/VoiceActivityDetector.swift#L6-L52). Единственная реализация — `EnergyVAD`: RMS (`vDSP_rmsqv`) по кадрам 100 мс, порог 0.02, выход бинарный, без состояния между вызовами — [EnergyVAD.swift#L5-L47](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/EnergyVAD.swift#L5-L47), [AudioProcessor.swift#L707-L735](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioProcessor.swift#L707-L735). VAD подключается через `WhisperKitConfig.voiceActivityDetector` и используется для нарезки чанков (`chunkingStrategy: "vad"`).

`[INFERENCE]` Это лучший найденный прецедент формы API: **VAD как сменный протокол с дешёвым дефолтом**, а не как захардкоженная модель. Для runtime это означает, что «WebRTC/энергия/нейросеть» — просто три реализации одного интерфейса, и клиент может выбрать любую, не меняя контракт.

### 5. pyannote segmentation как VAD

| Свойство | Значение | Источник |
|---|---|---|
| Модель | `pyannote/segmentation-3.0`, PyanNet (SincNet → BiLSTM ×4 → Linear ×2 → 7-классовый powerset) | [speech-swift Configuration.swift#L30-L42](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/Configuration.swift#L30-L42) |
| Параметры/размер | ~1.5 M; `pytorch_model.bin` 5 905 440 байт | HF blobs + [aufklarer/Pyannote-Segmentation-MLX](https://huggingface.co/aufklarer/Pyannote-Segmentation-MLX) |
| Окно | 10 с, 589 кадров на окно (~17 мс на кадр), скользящее окно с перекрытием | [VADPipeline.swift#L1-L30](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/VADPipeline.swift#L1-L30) |
| Лицензия | **MIT, но gated** (`gated: auto`, требуется HF-аккаунт и принятие условий) | HF API `pyannote/segmentation-3.0` |
| Как из неё делают VAD | `max` по powerset-классам → `Binarize(onset=offset=0.5)`; для powerset-моделей `min_duration_on/off = 0` | [pyannote-audio voice_activity_detection.py#L105-L160](https://github.com/pyannote/pyannote-audio/blob/develop/src/pyannote/audio/pipelines/voice_activity_detection.py#L105-L160) |
| Метрики (VoxConverse, 5 файлов, кадр 10 мс) | F1 **98.22**, FAR **50.09**, MR 0.19, RTF 0.358, cold start ~2 с | [docs/benchmarks/vad-detection.md](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/vad-detection.md) |
| На FLEURS (10 языков) | F1 94.86, FAR 14.71, MR 2.92 — хуже FireRed и Silero | там же |
| Потоковость | нет: пакетный проход по файлу со скользящим окном | pyannote pipeline `apply(file)` |

### 6. Smart Turn v2 / v3.x (pipecat-ai / Daily)

| Свойство | v3.2 | v2 | Источник |
|---|---|---|---|
| Лицензия | BSD-2-Clause | BSD-2-Clause | [smart-turn README](https://github.com/pipecat-ai/smart-turn), HF карточки |
| Архитектура | Whisper-Tiny encoder + линейный классификатор, ~8 M | тот же класс, крупнее | README «Model architecture» |
| Вход | 16 кГц моно, окно **8 с = 128 000 сэмплов**, паддинг нулями **в начало**, обрезка с начала | 8 с | README «Notes on input format»; [inference.py#L16-L52](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py#L16-L52) |
| Выход | 1 вероятность ∈ [0,1] (сигмоида), порог 0.5 | то же | inference.py |
| Языки | **23**, в т.ч. **русский** и украинский | 14, в т.ч. русский | README (список флагов); HF `pipecat-ai/smart-turn-v2` |
| Размер | 8 МБ int8 ONNX (CPU) / 32 МБ fp32 (GPU); CoreML-бандл 16.71 МБ fp16 | ~360 МБ (v1 был 2.3 ГБ) | README; HF blobs; HF v2 |
| Латентность | «as little as 10 ms on some CPUs», ~65 мс на Pipecat Cloud 1× | ~12 мс на 8 с аудио на NVIDIA L40S | README; HF v2 |
| Латентность на Apple | **3.5 мс на окно** (M5 Pro, CoreML CPU+ANE) | — | [docs/models/smart-turn-v3.md](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/smart-turn-v3.md) |
| Точность | 92.9 % на 1000 клипах (CoreML-бандл, идентично fp32-графу на тех же клипах); upstream заявляет 93.7 % fp32 на 31.5k клипов | — | там же (заявление upstream — вторичный источник) |
| Замер третьей стороны (конкурент, EN) | `eot-bench` EN: false cut-offs @300 мс **35.2 %**, latency @5 % **1051 мс**, AUC 0.845 | — | [p99lab/turn-1-mini, таблица](https://huggingface.co/p99lab/turn-1-mini) |

Семантика использования (проверено по коду speech-swift): классификатор вызывается **один раз на подтверждённую паузу VAD**, получает аудио от `preRollDuration` (0.5 с) до текущего чанка, максимум последние 8 с; при вероятности < порога сегмент **не закрывается** (`held`), возобновление речи продолжает тот же сегмент без повторного `speechStarted`, а `maxSilenceDuration` (2.0 с) закрывает удержанный сегмент принудительно; исключение из классификатора трактуется как «реплика завершена» (fail-open) — [StreamingVADProcessor.swift#L73-L90](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/StreamingVADProcessor.swift#L73-L90), [#L271-L285](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/StreamingVADProcessor.swift#L271-L285), [#L310-L345](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/StreamingVADProcessor.swift#L310-L345).

### 7. LiveKit Turn Detector (текстовый)

| Свойство | Значение | Источник |
|---|---|---|
| База | `Qwen/Qwen2.5-0.5B-Instruct`, дистилляция из дообученного Qwen2.5-7B | HF `livekit/turn-detector`, «Architecture and Training» |
| Вход | **текст** транскрипта: до 6 реплик, ≤128 токенов, chat-шаблон Qwen; последняя реплика без `<|im_end|>` | там же, «How It Works» |
| Выход | вероятность следующего токена `<|im_end|>`; порог **на язык** из `languages.json` | там же |
| Языки | 14, включая **русский**; RU: TPR 99.3 %, TNR 88.0 % | там же, «Supported Languages», «Benchmarks» |
| Формат | INT8 ONNX (`model_q8.onnx`), CPU-only, **<500 МБ RAM** | там же, «Deployment Requirements» |
| Лицензия | **`livekit-model-license`** (поле `license: other`) — не OSI | HF API `cardData.license` |
| Ограничения (заявленные) | нет просодии; зависит от качества/формата STT; 128 токенов / 6 реплик | там же, «Limitations» |
| Родственный | `videosdk-live/Namo-Turn-Detector-v1-Multilingual` — mmBERT-base, ~295 МБ, 23 языка (ru: accuracy 87.5 %, F1 0.889), **Apache-2.0**, <29 мс | HF карточка Namo |

### 8. Новое в 2026

| Модель | Дата | Класс | Размер | Языки | Лицензия | Метрики |
|---|---|---|---|---|---|---|
| `FireRedTeam/FireRedVAD` | 2026-02-11 | VAD (поток + офлайн) | 588 K | 100+ | Apache-2.0 | F1 97.57 на FLEURS-VAD-102 |
| `p99lab/turn-1-mini` | 2026-10-06 | **поточный аудио-EOT** | 5.26 M, 7 МБ int8 ONNX | **только EN** | Apache-2.0 | eot-bench EN: 22.6 % / 9.6 % false cut-offs @300/600 мс, latency @5 % 849 мс, AUC 0.970; 1.29 мс на 160 мс шаг (1 ядро M4 Pro) |
| `livekit/eot-bench-data` | 2026-06-09 | **бенчмарк** EOT | — | 14 языков + сводный конфиг `all`: en, ar, de, es, fr, hi, id, it, ja, ko, nl, pt, tr, zh (**ru нет**) | CC-BY-4.0 | — |
| `fixie-ai/ultraVAD` | 2025-08-28 | контекстный аудио-текстовый EOT | Llama-8B | 26, вкл. **ru** | — | eot-bench EN: 27.7 % @300 мс, 899 мс @5 % |
| `nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0` | 2025-05-08 | VAD (MarbleNet CNN) | 91.5 K | en, es, fr, de, **ru**, zh | **NVIDIA Open Model License** (не OSS) | 20 мс кадр, вероятность на кадр |
| `videosdk-live/Namo-Turn-Detector-v1-Multilingual` | 2025-09-24 | текстовый EOT | ~295 МБ | 23, вкл. ru | Apache-2.0 | avg accuracy 90.25 % |
| `CuriousMonkey7/HumAware-VAD` | 2026 | файн-тюн Silero против «мычания» | = Silero | en | MIT | без публичных метрик |
| `pipecat-ai/smart-turn-v2` | 2025 | аудио-EOT | ~360 МБ | 14, вкл. ru | BSD-2 | ~12 мс/8 с на L40S |

Факты по `turn-1-mini`, важные для дизайна: каузальные свёртки 20 мс + 6 transformer-слоёв (ширина 256, 4 головы, causal attention с окном 1.2 с, ALiBi), шаг 160 мс с кэшем состояния, **обязательный low-pass 4 кГц** (модель обучена на полосе 4 кГц, без фильтра — out of distribution), read-out «any-time» (оценку можно снять на любом кадре паузы), 0.57 GFLOP/с, CoreML-сборки нет — [карточка](https://huggingface.co/p99lab/turn-1-mini).

### 9. Многоклиентный сервис: общие веса, раздельное состояние

Три независимые реализации одного паттерна:

- **speech-swift.** «Share immutable model weights while keeping each stream's state separate» — `makeIndependentStream()` переиспользует тот же `MLX`-модуль / тот же `MLModel` и создаёт новый инстанс с пустыми `h`/`c` и контекстом — [SileroVAD.swift#L138-L149](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift#L138-L149). В сервере: `NativeVADStreams` — actor с `streams: [String: SileroVADModel]`, `streams[id] = model.makeIndependentStream()`, **жёсткий лимит 64 потока** (`429 Too Many Requests`) — [NativeInference.swift#L124-L144](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift#L124-L144). Для WebSocket-сессий комментарий прямо объясняет выбор: «Silero carries recurrent/context state. Give every websocket session its own instance … The downloaded bundle remains in the shared on-disk model cache» — [AudioServer.swift#L568-L577](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L568-L577).
- **SpeechRail.** Модульный кэш `_shared_sessions: dict[str, InferenceSession]` под `threading.Lock`; комментарий: «ORT documents `InferenceSession.run` as thread-safe; the recurrent h/c (or consolidated state + context) lives on each detector … NOT thread-safe, hence the lock» — [neural_vad.py#L24-L53](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/neural_vad.py#L24-L53). `inter_op_num_threads = 1`, `intra_op_num_threads = 1`.
- **WhisperLiveKit.** `OnnxSession` — «Shared ONNX session for Silero VAD model (stateless)»; `OnnxWrapper` — «with per-instance state», `self._state = torch.zeros((2, batch_size, 128))` — [silero_vad_iterator.py#L27-L64](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/silero_vad_iterator.py#L27-L64).

Дополнительно: **FluidAudio** сделал состояние значением (`VadState` in/out, `makeStreamState()`), а модель спрятал в `actor` — [VadManager+Streaming.swift#L4-L25](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadManager+Streaming.swift#L4-L25). Плюс: actor **сериализует** все вызовы, то есть при N клиентах инференс идёт по очереди `[INFERENCE]` — это цена выбранного дизайна, а не свойство CoreML.

**Батчинг.** Официальная ONNX-модель Silero принимает batch: `state` имеет размерность `(2, batch, 128)`, `input` — `(batch, 576)` — [utils_vad.py#L112-L139](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py#L112-L139). Production-пример: speaches разделяет encoder/decoder и батчит **только encoder** (до 10 000 сегментов за вызов), decoder (LSTM) идёт по окнам с общим `state` — [silero_vad_v5.py#L99-L140](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/silero_vad_v5.py#L99-L140). Второй приём — «склейка кадров» под ANE: CoreML-артефакт FluidAudio на 256 мс «takes in 8 chunks of 32 ms and processes it in batches so it's much faster» — [FluidInference/silero-vad-coreml (карточка)](https://huggingface.co/FluidInference/silero-vad-coreml).

**Стоимость состояния на сессию (расчёт по объявленным формам).**

| Компонент | Что хранится | Байты |
|---|---|---|
| Silero (любой порт) | `state (2,1,128)` float32 + контекст 64 float32 | 1024 + 256 = **1.28 КБ** |
| Silero CoreML fp16 | `h`,`c` `(1,1,128)` float16 + контекст | 512 + 256 ≈ **0.77 КБ** |
| WebRTC VAD | `VadInstT` (12 int16 таблиц + 3 вектора по 96 int16) | ≈ **0.5 КБ** |
| FireRedVAD (поток) | fbank-окно 5 кадров + история FSMN 20 кадров × 128 | ≈ 10 КБ `[INFERENCE]` (по гиперпараметрам `smooth_window_size=5`, k=20) |
| Smart Turn | окно 8 с float32 (`recentAudio`) | **512 КБ** (+ ресемплер) |
| pyannote (10 с окно) | 160 000 float32 + активации BiLSTM | ≥ **640 КБ** |

Отсюда: VAD-состояние на сессию ничтожно, а **буфер окна EOT-классификатора — основная статья расхода** у «умного» endpointing.

### 10. VAD + потоковый ASR: кто владеет концом реплики

| Механизм | Как работает | Кто принимает решение | Источник |
|---|---|---|---|
| Endpointing внутри sherpa-onnx | 3 правила по trailing silence в **декодировании**: rule1 — 2.4 с даже без непустого трейсбэка, rule2 — 1.2 с после непустого, rule3 — 20 с длина реплики | ASR (по токенам) | [endpoint.h#L44-L58](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/endpoint.h#L44-L58), [endpoint.cc#L14-L24](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/endpoint.cc#L14-L24) |
| Внешний VAD + потоковый ASR (sherpa) | VAD открывает/закрывает `OnlineStream`, при закрытии досылается **1.28 с tail padding** и стрим финализируется | VAD | [sherpa-onnx-vad-with-online-asr.cc#L144-L180](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/sherpa-onnx-vad-with-online-asr.cc#L144-L180) |
| EOU-голова Parakeet | отдельный класс в joint-сети; `eouDebounceMs` по умолчанию **1280 мс**, EOU должен удержаться несколько чанков | ASR | [docs/inference/parakeet-streaming-asr-inference.md#L115-L155](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/inference/parakeet-streaming-asr-inference.md#L115-L155) |
| VAD как force-finalize | `!vadSpeechActive && vadSilentChunks >= 30` (~960 мс) → `forceEndOfUtterance()`, независимо от дебаунса EOU; **guardrail**: не финализировать, если joint уже выдал final | VAD (поверх ASR) | там же |
| VAD как гейт перед ASR (WhisperLiveKit) | тишина не попадает в ASR; сегментация по событиям `start`/`end` с выравниванием по сэмпловым часам, чтобы не было дрейфа таймстемпов | VAD | [audio_processor.py#L1112-L1155](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/audio_processor.py#L1112-L1155) |
| VAD пересчётом окна (speaches) | на каждый append VAD прогоняется по последним `MAX_VAD_WINDOW_SIZE_SAMPLES = 3000×16` сэмплам (3 с), состояние модели не сохраняется | VAD (без состояния) | [input_audio_buffer.py#L34-L36](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer.py#L34-L36), [input_audio_buffer_event_router.py#L45-L75](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer_event_router.py#L45-L75) |
| Контракт событий (Deepgram-совместимый) | `SpeechStarted` только при `vad_events=true`, `UtteranceEnd` только при заданном `utterance_end_ms`; `speech_final` в результатах | клиент задаёт политику | [deepgram.go#L170-L180](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/deepgram.go#L170-L180), [#L537-L563](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/deepgram.go#L537-L563) |

Машина состояний допуска (проверенный референс, SpeechRail): `IDLE → CANDIDATE → ACTIVE → HANGOVER → IDLE`; `start_frames=3` (96 мс), `stop_frames=12` (384 мс), prefix 4800 сэмплов (300 мс), гистерезис `stop_threshold = threshold − 0.15` — [speech_admission.py#L24-L70](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/realtime/speech_admission.py#L24-L70). Та же логика в speech-swift: 6-состояниевая машина с `held`/`heldPendingSpeech` — [StreamingVADProcessor.swift#L73-L90](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/StreamingVADProcessor.swift#L73-L90).

### 11. Русский язык

| Что | Покрытие | Источник |
|---|---|---|
| Silero VAD | языконезависим, обучен на 6000+ языках | README Silero |
| FireRedVAD | 100+ языков (FLEURS-VAD-102 включает ru) | README FireRedVAD |
| NVIDIA Frame-VAD MarbleNet v2.0 | ru в списке 6 языков | HF карточка |
| Smart Turn v3.2 | **ru** в списке 23 языков | README smart-turn |
| Smart Turn v2 | **ru** в списке 14 языков | HF `pipecat-ai/smart-turn-v2` |
| LiveKit turn-detector | **ru**: TPR 99.3 %, TNR 88.0 % | HF карточка |
| Namo Turn Detector | **ru**: accuracy 87.5 %, F1 0.889 (ниже среднего 90.25 %) | HF карточка |
| UltraVAD | ru в списке 26 языков | HF карточка |
| `turn-1-mini` | **только EN**, другие языки не измерялись | HF карточка |
| Публичный EOT-бенчмарк | `livekit/eot-bench` — **ru отсутствует** (14 языков + `all`) | HF API `livekit/eot-bench-data` |

Отдельных русскоязычных VAD/EOT-моделей не найдено: русский покрывается многоязычными.

---

## Ограничения и риски

1. **Лицензии — главный фильтр, а не качество.** TEN VAD: Apache-2.0 + запрет конкурировать с Agora (нельзя класть в открытый runtime, который будут использовать сторонние приложения). LiveKit turn-detector: `livekit-model-license`. NVIDIA MarbleNet v2.0: NVIDIA Open Model License. pyannote segmentation-3.0: MIT, но **gated** — каталог моделей не сможет скачать её автоматически без пользовательского HF-токена.
2. **Бенчмарки вендоров несравнимы.** TEN заявляет превосходство над Silero; Silero (0.97 vs 0.93) — обратное; FireRed ставит TEN ниже Silero (95.19 vs 95.95 F1). Наборы разные (собственный PR-набор TEN, мульти-домен Silero, FLEURS-VAD-102 FireRed, LibriVAD-small в arXiv:2610.14607). Единственный «нейтральный» набор — FLEURS-VAD-102, но его разметка ещё не открыта («coming soon» в README FireRedVAD).
3. **FAR на реальных разговорах высок у всех.** VoxConverse: pyannote FAR 50.09 %, Silero CoreML 33.29 %, FireRed 40.12 % (5 файлов, кадр 10 мс). Авторы бенчмарка объясняют фоновым шумом; для runtime это значит: **порог и `min_silence` обязаны быть настраиваемыми на сессию**, а «правильный» VAD не отменяет ложных срабатываний.
4. **Silero жёстко привязан к 512 сэмплам (32 мс) и carry-over состояния.** При 20 мс AUC падает до 0.627 в независимом замере. Любой API, который позволяет клиенту подавать произвольные чанки, обязан внутри держать leftover-буфер; иначе качество деградирует молча (в speech-swift это прямо описано: «Dropping the trailing sub-512 remainder would starve Silero of audio that it needs for LSTM state continuity»).
5. **Квантование состояния в fp16 искажает вероятности.** CoreML-порт speech-swift держит `h`/`c` в float16: max расхождение с fp32/MLX 0.0606 при среднем 0.00167. На пороге 0.5 это может переключать отдельные кадры. Долговременный дрейф состояния fp16 не измерен.
6. **Разные порты — разная семантика.** sherpa-onnx: только 16 кГц (жёсткий `exit(-1)`), v5 требует `window_size == 512`; FluidAudio: только 16 кГц и окно 256 мс; speech-swift: 16 кГц. Официальная модель Silero поддерживает 8 кГц, и WhisperLiveKit этот путь реализует (`sample_rates = [8000, 16000]`, 256 сэмплов при 8 кГц), но три из четырёх Apple-ориентированных портов — нет. Обещание «8 кГц» в API runtime будет ложью для большинства портов.
7. **CoreML actor-сериализация и ANE.** FluidAudio выбирает `actor` — корректно, но N клиентов обслуживаются последовательно. Одновременные `MLModel.prediction` из разных потоков формально допустимы, но реальный параллелизм ANE не измерен ни в одном из клонов; заявления о «N сессий без потерь» подтверждать нечем.
8. **Лимиты на число потоков зашиты в код.** `NativeVADStreams`: 64 потока → 429. Это единственный найденный в клонах явный предел; при 100 клиентах поведение не описано.
9. **EOT-классификатор дороже VAD по памяти в ~400 раз** (512 КБ окна против 1.3 КБ состояния) и требует хранения 8 с аудио на сессию. При 32 сессиях это 16 МБ только под окна — приемлемо, но это уже не «ничтожные накладные расходы».
10. **`turn-1-mini` — единственная поточная аудио-EOT с Apache-2.0, но она EN-only и требует low-pass 4 кГц.** Подача широкополосного микрофона без фильтра выводит модель из распределения (заявление авторов). Независимого воспроизведения нет; лаборатория маленькая, версия 1.2.3 от октября 2026.
11. **Smart Turn v3.2 обучался на 23 языках, но русский вклад в обучение не документирован** — только присутствие в списке. Метрик по ru нет ни у pipecat, ни у портов.
12. **Внутренний endpointing ASR и внешний VAD конфликтуют.** Parakeet EOU при комнатном шуме «can delay committal by several seconds», а внешний force-finalize без guardrail даёт дубли final. Оба механизма одновременно — источник багов, а не надёжности.
13. **`eot-bench` не содержит русского** и покрывает 15 языков; метрики EOT для ru (и для смеси «русская речь + английские IT-термины») измерить негде, кроме собственного набора.

---

## Выводы для Runtime

### 4.1. Компоненты: что брать

| Роль | Рекомендация | Обоснование | Что НЕ брать |
|---|---|---|---|
| **VAD по умолчанию** | **Silero VAD v6.2.1** (ONNX + CoreML + MLX порты) | MIT; 0.6–2.3 МБ; состояние 1.3 КБ/сессия; 0.063 мс/чанк на ANE и 0.5 мс на MLX; три независимых продакшн-реализации паттерна «общие веса + состояние на поток»; 8/16 кГц (8 кГц — только на ONNX-пути) | TEN VAD (лицензия Agora), WebRTC VAD (нет вероятности, AUC 0.73) |
| **VAD «тяжёлый» / шумный офлайн-батч** | **FireRedVAD** (VAD или Stream-VAD веса) | Apache-2.0; 100+ языков; F1 97.57 на FLEURS-VAD-102; 10 мс hop; CoreML 1.18 МБ, RTF 0.007 | — |
| **VAD-фолбэк без модели** | WebRTC VAD (BSD-3) или энергетический детектор | Ноль зависимостей, детерминизм, 8/16/32 кГц, 10/20/30 мс | как основной путь |
| **VAD для диаризации** | pyannote segmentation-3.0 — только внутри диаризации, не как endpointing | powerset даёт overlap и смены говорящего; FAR 50 % как VAD | как VAD для endpointing |
| **Конец реплики (аудио)** | **Smart Turn v3.2** (CoreML-порт) | BSD-2; 23 языка вкл. ru; 8 с окно; 3.5 мс/окно на ANE; проверенная семантика «вызывать только на подтверждённой паузе VAD» | — |
| **Конец реплики (аудио, поточный, EN)** | `turn-1-mini` — опционально, за фича-флагом | Apache-2.0; 160 мс шаг; 1.29 мс/шаг; лучший на eot-bench EN среди скачиваемых | как дефолт для ru |
| **Конец реплики (текст)** | LiveKit turn-detector / Namo — **не включать в ядро** | Требует транскрипта (значит ASR уже поработал), лицензия LiveKit не OSS | — |
| **Семантический endpointing** | UltraVAD (8B) — вне scope локального runtime | 8B на каждую паузу несовместимо с «минимальными накладными расходами в простое» | — |

`[INFERENCE]` Итоговая связка для русского: **Silero v6.2.1 (поток, 32 мс) → машина состояний с гистерезисом → Smart Turn v3.2 на подтверждённой паузе → max_turn cap**. FireRedVAD держать как вторую модель каталога для «шумно/офлайн/много языков», WebRTC — как fallback, когда модель не установлена.

### 4.2. API: состояние как ручка, а не как скрытое поле

Паттерн, к которому независимо пришли все четыре реализации, должен стать контрактом:

- `POST /v1/vad/sessions` → `session_id` (+ `model`, `sample_rate`, `threshold`, `min_silence`, `min_speech`, `window`).
- `POST /v1/vad/sessions/{id}/audio` (PCM16/Float32, **любой размер чанка**) → `{probabilities: [...], events: [{type: speech_started|speech_ended, start_ms, end_ms}], state_version}`.
- `POST /v1/vad/sessions/{id}/reset`, `DELETE`.
- Опционально `POST /v1/turn/score` (stateless-ish): `{session_id, audio_so_far}` → `{probability}` — но лучше как часть VAD-сессии: клиент не должен сам склеивать 8 с аудио.

Обязательные свойства контракта, вытекающие из фактов:

1. **Runtime владеет leftover-буфером и carry-over контекста.** Клиент присылает произвольные чанки; 512/576-сэмпловые окна нарезает рантайм (иначе — деградация Silero, п. 4 рисков).
2. **Вероятность на кадр — первичный выход, события — производные.** Клиент с собственной политикой (как SpeechRail/Sona) должен иметь доступ к `prob`, иначе он не сможет применить свои пороги; но 90 % клиентов возьмут события.
3. **Параметры политики — на сессию, а не глобально.** Порог, `min_silence`, `min_speech`, `prefix_padding`, `max_turn` различаются между «субтитрами» (0.4 с) и «встречей» (0.9 с) — это видно в реальной практике SpeechRail.
4. **Явный `max_turn_duration`** — иначе непрерывная речь/шум держат буфер бесконечно: `RealtimeTurnDetectionConfig` в speech-swift задаёт `threshold 0.5`, `prefix_padding 300 мс`, `silence 500 мс`, `max_turn 120 000 мс`, а `RealtimeVADController` при превышении лимита сбрасывает VAD и отдаёт `speechEnded(forcedByDurationLimit: true)` — [RealtimeVAD.swift#L8-L12](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/RealtimeVAD.swift#L8-L12), [#L140-L151](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/RealtimeVAD.swift#L140-L151).
5. **`reset` обязателен и должен быть дешёвым** (обнулить 1.3 КБ + контекст); без него состояние протекает между записями.

### 4.3. Исполнение: веса и состояние

- **ONNX-движок:** один `InferenceSession` на (модель, провайдер, потоки); `inter/intra_op_threads = 1`; состояние — numpy-массив на сессию. Прецедент: SpeechRail (глобальный кэш + лок на мутацию состояния), WhisperLiveKit (shared session + per-wrapper state).
- **CoreML-движок:** один скомпилированный `MLModel` на модель; `h`/`c` — `MLMultiArray` на сессию; предпочтительно **fp32** для состояния (см. п. 5 рисков), fp16 допустим для весов. Не оборачивать VAD-движок в один `actor` на все сессии (как FluidAudio) — это сериализует клиентов.
- **MLX-движок:** веса общие, но вызовы сериализуются — инстанс модели прямо помечен «not thread-safe … create separate instances for concurrent use» ([SileroVAD.swift#L28](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift#L28)); это ограничение движка, а не архитектуры, и его надо документировать.
- **Батчинг:** включать только там, где он измерен: (а) Silero ONNX — batch по encoder-части (speaches), (б) CoreML — склейка 8×32 мс в один вызов 256 мс (FluidAudio). Для одиночных 32 мс чанков на ANE выигрыша не будет.
- **Пределы:** мягкий лимит сессий с явной ошибкой (прецедент — 64 потока → 429), плюс метрика «активные VAD-сессии».
- **Стоимость простоя:** VAD-веса 0.6–1.2 МБ можно держать резидентно; Smart Turn (16.7 МБ) — грузить по требованию и выгружать по TTL, потому что он нужен только в разговорных сценариях.

### 4.4. Что остаётся клиенту (границы ответственности)

| Остаётся клиенту | Почему |
|---|---|
| Финальная политика конца реплики (порог EOT, `min_silence`, `max_turn`, таймауты) | Зависит от сценария (субтитры/встреча/ассистент) и измеряется только на его данных; SpeechRail прямо фиксирует разные профили для разных приложений |
| Barge-in и отмена TTS | Требует состояния воспроизведения, которого у runtime нет; SpeechRail: «сервер только публикует факты, отмену отправляет вызывающий» |
| AEC / AGC / шумоподавление до VAD | Runtime получает уже очищенный поток; иначе эхо TTS детектируется как речь (прямо описано в speaches docs) |
| Pre-roll/prefix-буферизация, если нужна своя | Разные приложения хотят 300 / 500 / 1000 мс префикса |
| Выбор модели и её версии | Runtime не выбирает по смыслу: клиент указывает `model` и `operation` |
| Склейка «VAD-сегмент → ASR-запрос» и дедупликация final | Внутренний endpointing ASR конфликтует с внешним VAD; владелец решения должен быть один и он снаружи |

`[INFERENCE]` Runtime **должен** отдавать сырые вероятности и «удержание» (held/`isHoldingTurn`), но **не должен** сам решать, что «пользователь договорил» в смысле диалога: это политика клиента. Единственное, что runtime обязан гарантировать — детерминированную машину состояний и одинаковые события при одинаковом входе.

### 4.5. Как VAD сочетается со стриминговым ASR

- Если ASR имеет собственный endpointing (sherpa-onnx rules, Parakeet EOU) — runtime **не должен** дублировать финализацию: либо ASR владеет концом (и VAD используется только как гейт и для barge-in), либо VAD владеет (и ASR-эндпоинтер выключается). Проверенный анти-паттерн — двойной final; проверенный guardrail — «не форсить, если joint уже выдал final».
- При VAD-владельце нужен **tail padding** на стыке: sherpa досылает 1.28 с перед финализацией потокового стрима — без этого теряются хвосты слов.
- VAD как гейт (не подавать тишину в ASR) экономит вычисления и убирает галлюцинации на тишине — это то, что делают WhisperLiveKit и SpeechRail («speech admission»), и это дешевле, чем «ASR на всё + VAD для нарезки».
- `min_silence` для endpointing (300–900 мс) и `min_silence` для «закрыть сегмент» — разные параметры; в API их надо разделять.

---

## Неизвестные / что измерить

1. **Реальная латентность детекции конца реплики на русской речи** (задержка от последнего слога до `speech_ended`) при `min_silence` 300/500/700 мс — нет ни одного публичного замера; нужен свой набор с разметкой пауз (включая паузы внутри «kubectl ... деплой в ... production»).
2. **Качество Smart Turn v3.2 на русском + английские IT-термины.** Заявлено 23 языка, метрик по ru нет. Нужен набор 300–500 реплик (в т.ч. с англицизмами) и сравнение с VAD-only baseline.
3. **Стоимость сессии при N = 1, 8, 32, 64**: CPU/с, RSS, ANE-утилизация для Silero CoreML (индивидуальные чанки vs батч 8×32 мс), с параллельными `MLModel.prediction` и без.
4. **Дрейф fp16-состояния CoreML** на 30–60 минутах непрерывного аудио: расхождение вероятностей против fp32 на одном и том же файле.
5. **TEN VAD: число параметров и латентность на Apple Silicon** (вендор даёт только RTF 0.0160 на M1 и размер библиотеки; params не публикуются).
6. **FireRedVAD Stream-VAD в CoreML** (поточный, causal) — существует ли корректный экспорт; в клонах только пакетный порт.
7. **8 кГц путь Silero** в портах: официальная модель поддерживает, WhisperLiveKit реализует, но sherpa-onnx (жёсткий `exit(-1)`), FluidAudio и speech-swift — нет. Нужен либо свой ONNX-путь, либо честный отказ в API для этих движков.
8. **Порог/калибровка на реальном шуме заказчика**: VoxConverse FAR 33–50 % означает, что дефолт 0.5 не переносится; нужен замер на своём микрофоне/комнате.
9. **Поведение при >64 одновременных VAD-сессиях** и стоимость вытеснения состояния (можно ли пересериализовать состояние в стор и восстановить без потери качества).
10. **`turn-1-mini` на Apple Silicon и на русском**: CoreML-сборки нет, ru не измерялся; независимого воспроизведения eot-bench-числа нет.
11. **Совместимость VAD-сегментов с диаризацией**: нужен ли отдельный VAD для диаризации или достаточно одного (граница с E2).

---

## Источники

**Код (клоны, SHA из задания):**
- speech-swift: [`SileroVAD.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroVAD.swift), [`SileroModel.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SileroModel.swift), [`CoreMLSileroInference.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/CoreMLSileroInference.swift), [`StreamingVADProcessor.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/StreamingVADProcessor.swift), [`SmartTurn.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/SmartTurn.swift), [`FireRedVAD.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/FireRedVAD.swift), [`VADPipeline.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/VADPipeline.swift), [`RealtimeVAD.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/RealtimeVAD.swift), [`NativeInference.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/NativeInference.swift), [`AudioServer.swift`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift); доки: [`docs/benchmarks/vad-detection.md`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/vad-detection.md), [`docs/models/smart-turn-v3.md`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/smart-turn-v3.md), [`docs/models/fireredvad.md`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/fireredvad.md), [`docs/inference/parakeet-streaming-asr-inference.md`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/inference/parakeet-streaming-asr-inference.md).
- SpeechRail: [`src/speechrail/backends/neural_vad.py`](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/neural_vad.py), [`src/speechrail/realtime/speech_admission.py`](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/realtime/speech_admission.py), [`docs/architecture/realtime-vad-2026-best-practices.md`](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/docs/architecture/realtime-vad-2026-best-practices.md), [`docs/decisions/0013-realtime-vad-and-diarization-boundary.md`](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/docs/decisions/0013-realtime-vad-and-diarization-boundary.md).
- sherpa-onnx: [`csrc/silero-vad-model.cc`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/silero-vad-model.cc), [`csrc/ten-vad-model.cc`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/ten-vad-model.cc), [`csrc/vad-model.cc`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/vad-model.cc), [`csrc/endpoint.h`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/endpoint.h), [`csrc/sherpa-onnx-vad-with-online-asr.cc`](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/sherpa-onnx-vad-with-online-asr.cc).
- WhisperLiveKit: [`whisperlivekit/silero_vad_iterator.py`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/silero_vad_iterator.py), [`whisperlivekit/audio_processor.py`](https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/audio_processor.py).
- speaches: [`src/speaches/executors/silero_vad_v5.py`](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/silero_vad_v5.py), [`src/speaches/realtime/input_audio_buffer.py`](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer.py), [`src/speaches/realtime/input_audio_buffer_event_router.py`](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer_event_router.py), [`docs/usage/vad.md`](https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/docs/usage/vad.md).
- FluidAudio: [`Sources/FluidAudio/VAD/VadManager.swift`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadManager.swift), [`VadManager+Streaming.swift`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadManager+Streaming.swift), [`VadTypes.swift`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/VAD/VadTypes.swift), [`Documentation/VAD/GettingStarted.md`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/VAD/GettingStarted.md), [`Documentation/VAD/Segmentation.md`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/VAD/Segmentation.md).
- gotranscribesrv: [`internal/handlers/deepgram.go`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/deepgram.go), [`internal/sidecar/client.go`](https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/sidecar/client.go).
- WhisperKit (проверено: только энергетический VAD, без нейросети): [`Sources/WhisperKit/Core/Audio/VoiceActivityDetector.swift`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/VoiceActivityDetector.swift), [`EnergyVAD.swift`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/EnergyVAD.swift), [`AudioProcessor.swift`](https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Audio/AudioProcessor.swift).

**Модели и документы:**
- Silero VAD: [README](https://github.com/snakers4/silero-vad), [Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models), [Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics), [utils_vad.py](https://github.com/snakers4/silero-vad/blob/master/src/silero_vad/utils_vad.py), [aufklarer/Silero-VAD-v6.2.1-MLX](https://huggingface.co/aufklarer/Silero-VAD-v6.2.1-MLX), [FluidInference/silero-vad-coreml](https://huggingface.co/FluidInference/silero-vad-coreml), [istupakov/silero-vad-onnx](https://huggingface.co/istupakov/silero-vad-onnx).
- TEN VAD: [GitHub](https://github.com/ten-framework/ten-vad), [LICENSE](https://github.com/ten-framework/ten-vad/blob/main/LICENSE), [include/ten_vad.h](https://github.com/ten-framework/ten-vad/blob/main/include/ten_vad.h), [HF](https://huggingface.co/TEN-framework/ten-vad).
- FireRedVAD: [GitHub](https://github.com/FireRedTeam/FireRedVAD), [FireRedASR2S README](https://github.com/FireRedTeam/FireRedASR2S), [arXiv:2603.10420](https://arxiv.org/abs/2603.10420), [HF](https://huggingface.co/FireRedTeam/FireRedVAD), [core/constants.py](https://github.com/FireRedTeam/FireRedVAD/blob/main/fireredvad/core/constants.py), [stream_vad.py](https://github.com/FireRedTeam/FireRedVAD/blob/main/fireredvad/stream_vad.py).
- WebRTC VAD: [webrtc_vad.h](https://webrtc.googlesource.com/src/+/refs/heads/main/common_audio/vad/include/webrtc_vad.h), [vad_core.h](https://webrtc.googlesource.com/src/+/refs/heads/main/common_audio/vad/vad_core.h).
- pyannote: [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0), [voice_activity_detection.py](https://github.com/pyannote/pyannote-audio/blob/develop/src/pyannote/audio/pipelines/voice_activity_detection.py), [aufklarer/Pyannote-Segmentation-MLX](https://huggingface.co/aufklarer/Pyannote-Segmentation-MLX).
- Smart Turn: [GitHub](https://github.com/pipecat-ai/smart-turn), [inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py), [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3), [pipecat-ai/smart-turn-v2](https://huggingface.co/pipecat-ai/smart-turn-v2), [aufklarer/Smart-Turn-v3.2-CoreML](https://huggingface.co/aufklarer/Smart-Turn-v3.2-CoreML).
- Turn detection: [livekit/turn-detector](https://huggingface.co/livekit/turn-detector), [videosdk-live/Namo-Turn-Detector-v1-Multilingual](https://huggingface.co/videosdk-live/Namo-Turn-Detector-v1-Multilingual), [p99lab/turn-1-mini](https://huggingface.co/p99lab/turn-1-mini), [fixie-ai/ultraVAD](https://huggingface.co/fixie-ai/ultraVAD), [livekit/eot-bench-data](https://huggingface.co/datasets/livekit/eot-bench-data).
- Прочее: [nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0), [CuriousMonkey7/HumAware-VAD](https://huggingface.co/CuriousMonkey7/HumAware-VAD), [arXiv:2610.14607 «Window Size Matters»](https://arxiv.org/abs/2610.14607).
