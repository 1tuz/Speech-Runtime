# K. Лицензии и сопровождение

Дата исследования: 2026-10-25. Все данные о лицензиях получены из файлов `LICENSE`/манифестов в клонах `/tmp/srr/repos` (SHA зафиксированы) либо из HF API (`/api/models/<id>` → `cardData.license`, `gated`) и официальных текстов лицензий. Активность — из `git log` в клонах + GitHub API на 2026-10-25.

## Итог

1. **Все 8 проектов — permissive, кроме одного**: Apache-2.0 (speech-swift, sherpa-onnx, WhisperLiveKit, FluidAudio), MIT (SpeechRail, speaches, WhisperKit/argmax-oss-swift), **GPL-3.0 — только gotranscribesrv**. GPLv3 в этой выборке — единственная копилефт-лицензия и главный ограничитель для форка.
2. **Модели делятся на четыре юридических класса**: (a) полностью свободные для коммерции и редистрибуции (Apache-2.0/MIT: Whisper, GigaAM v3, T-one, Qwen3-ASR, Voxtral, Moonshine, Silero VAD, WeSpeaker/CAM++ коды); (b) требующие атрибуции при редистрибуции (CC-BY-4.0: Parakeet TDT v3, Canary, Kyutai STT, pyannote community-1, WeSpeaker-веса); (c) требующие текста лицензии + notice (NVIDIA Open Model License, OpenMDW-1.1); (d) **непригодные**: CC-BY-NC-4.0 (Sortformer v1) и TEN VAD (запрет «конкурирующего» развёртывания).
3. **TEN VAD — блокер для платформенного runtime**: его «Open Source License» (Apache-2.0 + доп. условия Agora) прямо запрещает Deploy способом, «позволяющим третьим лицам разрабатывать или развёртывать приложения». Это ровно модель использования «Ollama для речи». Рекомендация: не включать в дефолтный каталог.
4. **Sortformer — ловушка версий**: `nvidia/diar_sortformer_4spk-v1` = **cc-by-nc-4.0** (некоммерческая), `diar_streaming_sortformer_4spk-v2` = cc-by-4.0, `v2.1` = NVIDIA Open Model License. FluidAudio качает `FluidInference/diar-streaming-sortformer-coreml` (на HF заявлен cc-by-4.0), но README FluidAudio утверждает «NVIDIA Open Model License» — расхождение в самом проекте. Runtime обязан фиксировать лицензию **по артефакту**, а не по семейству моделей.
5. **Gated-модели (pyannote, Cohere Transcribe) нельзя зеркалировать без юридического разбора**: доступ выдаётся по click-through с передачей компании/use case и согласием на маркетинговые письма. Собственное зеркало обходит согласие пользователя с правообладателем, даже когда сама лицензия (CC-BY-4.0) редистрибуцию разрешает.
6. **Активность расслоена**: живая разработка — speech-swift (104 коммита/30 дней), sherpa-onnx (52), FluidAudio (60), SpeechRail (524, но 3 автора); **speaches фактически заморожен** (0 коммитов за 180 дней на `master`, последний тег 2025-12-27, 149 открытых issues+PR); gotranscribesrv — 1 автор, 0 релизов.
7. **WhisperKit переименован**: `argmaxinc/WhisperKit` → `argmaxinc/argmax-oss-swift` (MIT, продукты WhisperKit/TTSKit/SpeakerKit). Ссылки на старое имя редиректятся, но в манифестах/доках это надо учитывать.
8. **Практика редистрибуции уже существует и её можно копировать**: `FluidInference/speaker-diarization-coreml` публикует `NOTICE.md` с точным перечнем артефактов под CC-BY-4.0, отдельным списком legacy-артефактов под MIT/CC-BY-4.0 и атрибуцией pyannote + WeSpeaker + BUT Speech@FIT + Fluid Inference. Это эталон для нашего каталога.
9. **Метаданные лицензий на HF ненадёжны**: `openai/whisper-large-v3` → apache-2.0, `whisper-large-v3-turbo` → mit, апстрим-репозиторий → MIT; у многих моделей `k2-fsa/*` лицензия вообще не указана. Вывод: собственный манифест с хешем артефакта, а не доверие `cardData`.
10. **Минимальный безопасный набор для v1 каталога**: GigaAM v3 (MIT, ru), T-one (Apache-2.0, ru streaming), Whisper (MIT/Apache-2.0), Parakeet TDT v3 + Canary (CC-BY-4.0), Qwen3-ASR/Voxtral (Apache-2.0), Silero VAD (MIT), Smart Turn (BSD-2), pyannote community-1 (CC-BY-4.0, только download-at-source), WeSpeaker/CAM++ (CC-BY-4.0/Apache-2.0).

## Факты

### Проекты: лицензия кода и ключевые зависимости

| Проект | Лицензия кода | Зависимости (лицензия) | Коммиты 30/90/180 дн | Авторов 90д/всего | Последний релиз | GitHub ★ / открытых issues+PR |
|---|---|---|---|---|---|---|
| **speech-swift** (soniqo) | Apache-2.0 | mlx-swift (MIT), mlx-swift-lm, swift-transformers (Apache-2.0), Hummingbird + hummingbird-websocket (Apache-2.0), swift-websocket, swift-argument-parser (Apache-2.0), swift-system (Apache-2.0), WhisperKit (MIT), xgrammar (Apache-2.0), swift-sdk MCP | 104 / 201 / 655 | 9 / 37 | `v0.0.29` 2026-10-25 | 1248 / 11 |
| **SpeechRail** (hrygo) | MIT | onnxruntime==1.31.0 (MIT), fastapi/uvicorn/pydantic (MIT), mlx-audio v0.5.6 (MIT) — вендоренный оверлей | 524 / 1483 / 1483 | 3 / 3 | `v5.0.0` 2026-10-24 | 3 / 17 |
| **sherpa-onnx** (k2-fsa) | Apache-2.0 | onnxruntime (MIT), kaldi-native-fbank, kaldi-decoder, openfst (Apache-2.0), eigen (MPL-2.0), utf8proc (MIT), websocketpp (BSD-3), portaudio (MIT), pybind11 (BSD-3), asio (Boost-1.0), nlohmann/json (MIT), hclust-cpp, simple-sentencepiece (Apache-2.0) | 52 / 152 / 258 | 55 / 273 | `v2.0.0` 2026-10-23 | 15507 / 643 |
| **WhisperLiveKit** (QuentinFuxa) | Apache-2.0 | faster-whisper (MIT), ctranslate2 (MIT), torch/torchaudio (BSD-3), fastapi/starlette (MIT/BSD-3), huggingface-hub (Apache-2.0), mlx-whisper (MIT, optional), funasr (optional, custom) | 2 / 109 / 147 | 9 / 51 | `v0.2.26` 2026-08-29 | 11174 / 18 |
| **speaches** (speaches-ai) | MIT | ctranslate2, faster-whisper (MIT), kokoro-onnx, piper-tts, pyannote-audio>=4.0.3 (MIT), onnx-asr | **0 / 0 / 0** | — / 32 | `v0.9.0-rc.3` 2025-12-27 | 3736 / 149 |
| **gotranscribesrv** (sagostin) | **GPL-3.0** | Fiber v2 (MIT), gorm (MIT), lib/pq (MIT), prometheus client (Apache-2.0), golang-jwt (MIT); Swift-сайдкар в том же репо → тоже GPLv3 | 0 / 27 / 43 | 1 / 1 | нет тегов | 1 / 0 |
| **FluidAudio** (FluidInference) | Apache-2.0 | нет внешних SPM-зависимостей (чистый Swift + CoreML); ONNX/CoreML-модели — с HF | 60 / 153 / 329 | 35 / 125 | `v0.17.7` 2026-10-07 | 3189 / 31 |
| **WhisperKit** → `argmaxinc/argmax-oss-swift` | MIT (+ `NOTICES` с Apache-2.0 текстом swift-transformers) | swift-argument-parser (Apache-2.0); опционально Vapor (MIT), swift-openapi-generator/runtime/vapor (Apache-2.0) | 9 / 23 / 35 | 10 / 47 | `v0.17.0` 2026-03-13 | 6417 / 143 |

Доказательства по строкам таблицы:

- speech-swift: `LICENSE` (Apache-2.0), зависимости — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L242-L264 (mlx-swift 242, mlx-swift-lm 248, swift-transformers 252, hummingbird 253-254, swift-websocket 258, WhisperKit 260, xgrammar 264). `README.md#L608-L610` — «License Apache 2.0».
- SpeechRail: `pyproject.toml:7` `license = { text = "MIT" }`, `pyproject.toml:29` `onnxruntime==1.31.0`; `vendor/engine-build/engine-build.json` фиксирует upstream `Blaizzy/mlx-audio` @ `4ab7e6f7`, wheel `mlx_audio-0.5.6`; лицензия mlx-audio — MIT (https://raw.githubusercontent.com/Blaizzy/mlx-audio/main/LICENSE). Оверлей `vendor/mlx-audio-incremental` — «additive overlay, not a fork» (https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/vendor/mlx-audio-incremental/README.md#L1-L10).
- sherpa-onnx: `LICENSE` (Apache-2.0). Пины зависимостей: `cmake/kaldi-native-fbank.cmake:4` (v1.22.4), `cmake/openfst.cmake:6` (v1.8.5-2026-07-09), `cmake/utf8proc.cmake:7` (v2.12.0), `cmake/hclust-cpp.cmake:5`, `cmake/portaudio.cmake:4`, `cmake/websocketpp.cmake:5`, `cmake/simple-sentencepiece.cmake:20`, `cmake/onnxruntime*.cmake` (FetchContent, 40+ конфигураций). Лицензии подтверждены чтением COPYING/LICENSE апстримов: openfst — Apache-2.0, utf8proc — MIT, websocketpp — BSD-3 (по тексту «Redistribution and use…»), asio — Boost-1.0, pybind11 — BSD-3, simple-sentencepiece — Apache-2.0. **espeak-ng (GPLv3) удалён**: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L27 — «Remove espeak-ng and piper-phonemize from sherpa-onnx (#4055)».
- WhisperLiveKit: `pyproject.toml:11` `license = "Apache-2.0"`, `version = "0.2.26"` (`:7`), зависимости `:21-L36`. В репозитории **вендорены веса Silero VAD**: `whisperlivekit/silero_vad_models/silero_vad.onnx`, `silero_vad.jit`, `silero_vad_16k_op15.onnx`, `silero_vad_half.onnx` — при этом отдельного NOTICE/атрибуции Silero (MIT) в репозитории не найдено.
- speaches: `LICENSE` MIT (© 2024 Fedir Zadniprovskyi); `pyproject.toml:7,9,11,13,29` — ctranslate2, faster-whisper, kokoro-onnx, piper-tts, pyannote-audio>=4.0.3. `master` HEAD `993994f` от 2026-04-17; `git ls-remote --heads` показывает ~20 незамерженных ветвей (`feat/switch-to-pyannote-audio`, `feat/parakeet-support` и др.) — работа идёт в ветках, не в `master`.
- gotranscribesrv: `LICENSE.md` — GNU GPL v3 (673 строки). Локальный инференс — через Swift-сайдкар FluidAudio в том же репозитории (`sidecar-audio/Sources/Server/EngineManager.swift:107-L116`), т.е. один репозиторий под GPLv3.
- FluidAudio: `LICENSE` Apache-2.0; `README.md#L780` «Apache 2.0»; внешних SPM-зависимостей нет (`Package.swift` без `.package(`). Каталог моделей: `Documentation/Models.md#L105-L110` (Silero VAD, Pyannote diarization, LS-EEND, Sortformer).
- WhisperKit: `LICENSE` MIT (© 2024 argmax, inc.), `NOTICES` — swift-transformers под Apache-2.0 с пометками «Argmax-modification:». Переименование: `https://api.github.com/repositories/748528018` → `full_name: argmaxinc/argmax-oss-swift`, MIT; локальный клон `Package.swift:8` — продукт `argmax-oss-swift`, `:21-L37` — `WhisperKit`, `TTSKit`, `SpeakerKit`. Зависимости: `Package.swift:42-L49`.

### Лицензии движков и фреймворков (проверено по GitHub API)

| Компонент | Лицензия | ★ / открытых |
|---|---|---|
| ml-explore/mlx | MIT | 28931 / 136 |
| ml-explore/mlx-swift | MIT | 2068 / 50 |
| microsoft/onnxruntime | MIT | 22170 / 1828 |
| huggingface/swift-transformers | Apache-2.0 | 1363 / 37 |
| hummingbird-project/hummingbird | Apache-2.0 | 1898 / 14 |
| vapor/vapor | MIT | 26241 / 85 |
| snakers4/silero-vad | MIT | 10522 / 15 |
| TEN-framework/ten-vad | **NOASSERTION / «Other»** | 2292 / 27 |

### Модели

Легенда столбца «Редистрибуция»: «да» — можно класть веса в свой каталог/зеркало при выполнении условий; «нет» — только скачивание у источника; «юридически неясно» — лицензия разрешает, но gate-соглашение создаёт риск.

| Модель | Источник (HF) | Лицензия | Коммерч. | Атрибуция | Редистрибуция весов | Gated |
|---|---|---|---|---|---|---|
| Whisper large-v3 / turbo | `openai/whisper-large-v3`, `openai/whisper-large-v3-turbo` | apache-2.0 (large-v3), **mit** (turbo); апстрим-код — MIT | да | сохранить текст лицензии/notice | да | нет |
| GigaAM v3 (ssl/ctc/rnnt/e2e_*) | `ai-sage/GigaAM-v3` | **mit** | да | © Salute Developers, MIT notice | да | нет |
| T-one (ru streaming) | `t-tech/T-one` | **apache-2.0** («code and pretrained models», `README.md#L158-L160`) | да | NOTICE + Apache-2.0 | да | нет |
| Parakeet TDT 0.6B v3 | `nvidia/parakeet-tdt-0.6b-v3` | **cc-by-4.0** | да | указать авторство NVIDIA + ссылку на CC-BY-4.0, отметить изменения (конвертацию) | да | нет |
| Canary 1B v2 / 180M flash / canary-qwen-2.5b | `nvidia/canary-1b-v2`, `nvidia/canary-180m-flash`, `nvidia/canary-qwen-2.5b` | **cc-by-4.0** | да | то же | да | нет |
| Nemotron 3.5 ASR streaming 0.6B | `nvidia/nemotron-3.5-asr-streaming-0.6b` | **openmdw-1.1** (OpenMDW License Agreement, Linux Foundation) | да | копия соглашения + все copyright/notices | да | **нет** (в доках speech-swift помечена как gated — расхождение) |
| Sortformer v1 (offline) | `nvidia/diar_sortformer_4spk-v1` | **cc-by-nc-4.0** | **нет** | — | только non-commercial | нет |
| Sortformer streaming v2 | `nvidia/diar_streaming_sortformer_4spk-v2` | cc-by-4.0 | да | CC-BY + изменения | да | нет |
| Sortformer streaming v2.1 | `nvidia/diar_streaming_sortformer_4spk-v2.1` | **nvidia-open-model-license** | да | текст соглашения + строка «Licensed by NVIDIA Corporation under the NVIDIA Open Model License» в NOTICE | да | нет |
| pyannote segmentation-3.0 | `pyannote/segmentation-3.0` | **mit** | да | MIT notice (© CNRS) | да (по лицензии) | **auto** (prompt: «we will occasionally email you about premium models and paid services») |
| pyannote community-1 | `pyannote/speaker-diarization-community-1` | **cc-by-4.0** | да | CC-BY + указание изменений | **юридически неясно** — см. риск №5 | **auto**: компания/use case + согласие на письма; «released under CC-BY-4.0 and will always remain freely accessible» |
| WeSpeaker (ResNet34-LM и др.) | `pyannote/wespeaker-voxceleb-resnet34-LM`, `Wespeaker/*` | код — Apache-2.0; веса — **cc-by-4.0** | да | CC-BY | да | нет |
| CAM++ / 3D-Speaker | ModelScope `iic/speech_campplus_sv_zh-cn_16k-common`; код `modelscope/3D-Speaker` | **Apache License 2.0** (и код, и модель на ModelScope) | да | Apache-2.0 NOTICE | да | нет |
| Silero VAD | GitHub `snakers4/silero-vad` (веса в releases; вендорены в WhisperLiveKit и FluidAudio) | **mit** | да | MIT notice © Silero Team | да | нет |
| **TEN VAD** | GitHub `TEN-framework/ten-vad`; веса `TEN-framework/ten-vad` на HF; копия `ten-vad.onnx` в релизах sherpa-onnx | **Apache-2.0 + дополнительные условия Agora** | формально да, **но** | «Powered by ten-vad» (опционально) | **ограничено**: запрещено Deploy, конкурирующее с Agora, и любое развёртывание, «позволяющее третьим лицам разрабатывать или развёртывать приложения»; производные — под той же лицензией | нет |
| Smart Turn v3 | `pipecat-ai/smart-turn-v3` | **bsd-2-clause** | да | BSD-2 notice (© Daily) | да | нет |
| Qwen3-ASR 0.6B / 1.7B | `Qwen/Qwen3-ASR-0.6B`, `Qwen/Qwen3-ASR-1.7B` | **apache-2.0** | да | Apache-2.0 | да | нет |
| Voxtral Mini 3B | `mistralai/Voxtral-Mini-3B-2507` | **apache-2.0** | да | Apache-2.0 | да | нет |
| Kyutai STT 1B en_fr / 2.6B en | `kyutai/stt-1b-en_fr`, `kyutai/stt-2.6b-en` | **cc-by-4.0** | да | CC-BY + изменения | да | нет |
| Moonshine (base / streaming tiny, small) | `moonshine-ai/moonshine-base`, `moonshine-ai/moonshine-streaming-*` | **mit** | да | MIT notice | да | нет |
| *(доп.)* SenseVoiceSmall | `FunAudioLLM/SenseVoiceSmall` | **other / model-license** (FunASR Model License v1.1) | неоднозначно | «attribute the source and author… retain relevant model names» | да, с оговорками | нет |
| *(доп.)* Cohere Transcribe 03-2026 | `CohereLabs/cohere-transcribe-03-2026` | apache-2.0 | да | Apache-2.0 | **юридически неясно** (gated) | **auto** |

Доказательства по моделям (HF API `cardData` + тексты):

- Whisper: `curl https://huggingface.co/api/models/openai/whisper-large-v3` → `license: apache-2.0`; `.../openai/whisper-large-v3-turbo` → `mit`; `.../openai/whisper-small` → `apache-2.0`; код — https://raw.githubusercontent.com/openai/whisper/main/LICENSE («MIT License, Copyright (c) 2022 [OI]»). Конвертированные CoreML-веса: `argmaxinc/whisperkit-coreml` → `license: mit`, 11.68M загрузок.
- GigaAM v3: `.../ai-sage/GigaAM-v3` → `license: mit`, `gated: false`, 447k загрузок, lastModified 2025-11-19; репозиторий обучения `salute-developers/GigaAM` — MIT.
- T-one: `.../t-tech/T-one` → `license: apache-2.0`; карточка: «This project, including the code and pretrained models, is released under the Apache 2.0 License» (`README.md`, раздел `## 📜 License`); апстрим `voicekit-team/T-one` — `LICENSE` «Copyright 2025 T-Software DC / Apache License 2.0».
- Parakeet/Canary: `.../nvidia/parakeet-tdt-0.6b-v3` → `cc-by-4.0`; `nvidia/parakeet-tdt-0.6b-v2` → `cc-by-4.0`; `nvidia/canary-1b-v2`, `canary-180m-flash`, `canary-qwen-2.5b` → `cc-by-4.0`.
- NVIDIA Open Model License (полный текст): https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/ — «Models are commercially usable», «You are free to create and distribute Derivative Models», п.3.1 требует копию соглашения + notice «Licensed by NVIDIA Corporation under the NVIDIA Open Model License»; п.2.1 — прекращение прав при обходе Guardrail; п.2.3 — соответствие NVIDIA Trustworthy AI terms; п.10 — право Делавэра/Санта-Клара; п.8 — indemnity на пользователя. Дата версии: 24.10.2025.
- Nemotron: `.../nvidia/nemotron-3.5-asr-streaming-0.6b` → `license: other`, `license_name: openmdw-1.1`, `license_link: https://openmdw.ai/license/1-1/`, `gated: false`, 1.09M загрузок. Текст OpenMDW-1.1: разрешено «deal in the Model Materials without restriction», при распространении — копия соглашения + notices, patent-retaliation, «does not impose any restrictions… with respect to any use… of any outputs». Copyright The Linux Foundation.
- Sortformer: `nvidia/diar_sortformer_4spk-v1` → `cc-by-nc-4.0`; `diar_streaming_sortformer_4spk-v2` → `cc-by-4.0`; `diar_streaming_sortformer_4spk-v2.1` → `other / nvidia-open-model-license`.
- pyannote: `pyannote/segmentation-3.0` → `license: mit`, `gated: auto`; gate-prompt: «Though this model uses MIT license and will always remain open-source, we will occasionnally email you about premium models and paid services around pyannote». `pyannote/speaker-diarization-community-1` → `cc-by-4.0`, `gated: auto`; gate-prompt: «This pipeline is released under the CC-BY-4.0 license and will always remain freely accessible. By providing your details, you agree that we may email you occasionally…», поля `Company/university`, `Use case` (meeting / conversation / ccaas / agent / media…). pyannote README требует принять условия: https://raw.githubusercontent.com/pyannote/pyannote-audio/develop/README.md (шаг 3). Существует **незакрытый зеркалом** репозиторий `pyannote-community/speaker-diarization-community-1` → `gated: false`, `license: cc-by-4.0`.
- WeSpeaker: `Wespeaker/wespeaker-voxceleb-resnet34-LM` → `cc-by-4.0`; `pyannote/wespeaker-voxceleb-resnet34-LM` → `cc-by-4.0` (5.88M загрузок); код `wenet-e2e/wespeaker` — Apache-2.0.
- CAM++: ModelScope API `https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common` → `License: Apache License 2.0`, 62.36M загрузок; код `modelscope/3D-Speaker` — Apache-2.0.
- Silero VAD: https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE — «MIT License, Copyright (c) 2020-present Silero Team». Вендоринг: `WhisperLiveKit/whisperlivekit/silero_vad_models/` (4 файла весов), `FluidInference/silero-vad-coreml` → `license: mit`.
- TEN VAD: https://raw.githubusercontent.com/TEN-framework/ten-vad/main/LICENSE — «The ten-vad is licensed pursuant to the Apache License v2.0… with the following additional conditions»; п.1: «You may not Deploy the ten-vad in a way that competes with Agora's offerings and/or that allows others to compete with Agora's offerings, including without limitation enabling any third party to develop or deploy Applications»; п.3: «Derivative Works of the ten-vad remain subject to this Open Source License»; «Copyright © 2025 Agora». Веса: `TEN-framework/ten-vad` на HF (292 загрузки); копия `ten-vad.onnx` раздаётся в релизах sherpa-onnx (`sherpa-onnx/go-api-examples/vad/main.go#L18-L31`).
- Smart Turn: `pipecat-ai/smart-turn-v3` → `license: bsd-2-clause`; апстрим `pipecat-ai/smart-turn` `LICENSE` — «BSD 2-Clause License, Copyright (c) 2024–2025, Daily».
- Qwen3-ASR: `Qwen/Qwen3-ASR-0.6B`, `Qwen/Qwen3-ASR-1.7B` → `apache-2.0`, `gated: false`.
- Voxtral: `mistralai/Voxtral-Mini-3B-2507` → `apache-2.0`, `gated: false`.
- Kyutai: `kyutai/stt-1b-en_fr` → `cc-by-4.0`; `kyutai/stt-2.6b-en` → `cc-by-4.0`; speech-swift фиксирует «Weight license | CC-BY-4.0» (`docs/models/kyutai-stt.md#L17`).
- Moonshine: `moonshine-ai/moonshine-base`, `moonshine-ai/moonshine-streaming-tiny`, `moonshine-ai/moonshine-streaming-small` → `mit`, `gated: false`.
- SenseVoice: `FunAudioLLM/SenseVoiceSmall` → `other / model-license`; текст https://raw.githubusercontent.com/modelscope/FunASR/main/MODEL_LICENSE — «You are free to use, copy, modify, and share», обязательная атрибуция, п.3 «provided for reference and learning purposes only», п.4.2 — прекращение лицензии за «unjustified denigration».
- Cohere Transcribe: `CohereLabs/cohere-transcribe-03-2026` → `apache-2.0`, `gated: auto`; CoreML-конверсия `FluidInference/cohere-transcribe-coreml` отдаёт HTTP 401 (gated).

### Примеры практики редистрибуции (что уже делают другие)

- `FluidInference/speaker-diarization-coreml`: `cardData.license_name = scoped-cc-by-4.0`, ссылка на `NOTICE.md`; в NOTICE перечислены ровно те артефакты, что покрыты CC-BY-4.0, отдельная таблица legacy-артефактов (`pyannote_segmentation` — MIT © CNRS 2023; `wespeaker`/`wespeaker_v2`/`wespeaker_int8` — CC-BY-4.0), атрибуция pyannote + WeSpeaker + BUT Speech@FIT + Fluid Inference, требование «indicate that the files are modified Core ML conversions». PLDA-параметры — CC-BY-4.0 с явным разрешением коммерческого использования (ссылка на `BUT-FIT/diarizen-wavlm-large-s80-md/.../plda/LICENSE`).
- `FluidInference/parakeet-tdt-0.6b-v3-coreml` → `cc-by-4.0` (292k загрузок), `FluidInference/nemotron-speech-streaming-en-0.6b-coreml` → `nvidia-open-model-license`, `FluidInference/campplus-coreml` → `campplus-upstream`, `FluidInference/sensevoice-small-coreml` → `sensevoice-upstream` — т.е. конвертер перепубликует лицензию апстрима, а не «свою».
- `aufklarer/Kyutai-STT-1B-MLX-8bit` → `cc-by-4.0`, `aufklarer/Nemotron-3-Diarization-100M-CoreML-INT8` → `openmdw-1.1` — тот же принцип в экосистеме speech-swift.
- `k2-fsa/*`: у части моделей `cardData.license` отсутствует вовсе (например `k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19`, `k2-fsa/sherpa-onnx-streaming-zipformer-korean-2024-06-16`), у части — `apache-2.0`. У самого sherpa-onnx нет файла third-party notices (в репозитории только корневой `LICENSE`).

## Ограничения и риски

### Юридические риски для форка и распространения (по убыванию критичности)

1. **TEN VAD — запрет на платформенное развёртывание.** Условие 1 запрещает Deploy, «allowing others to compete with Agora's offerings, including… enabling any third party to develop or deploy Applications». Speech Runtime по определению даёт сторонним приложениям возможность использовать VAD. Даже условие 2 («solely for your benefit and the benefit of your direct End Users») не спасает: приложения третьих лиц — не «direct End Users» в смысле п.4. Условие 3 распространяет лицензию на производные (обёртки, конвертации, ONNX→CoreML). **Вывод: не включать в каталог по умолчанию; максимум — плагин, устанавливаемый пользователем вручную, с явным EULA-экраном.**
2. **GPLv3 (gotranscribesrv) несовместим с permissive-runtime при линковке.** Если наш сервис — Apache-2.0/MIT и мы включаем код gotranscribesrv (включая его Swift-сайдкар, лежащий в том же репозитории), вся производная работа должна распространяться под GPLv3. Допустимая схема: отдельный процесс/контейнер, общение по HTTP/WebSocket без разделяемых структур — тогда это два независимых произведения (позиция FSF), но границу надо документировать. Практически: **не форкать gotranscribesrv, брать только идеи API**.
3. **CC-BY-NC-4.0 (Sortformer v1) блокирует коммерческое использование.** Риск реализуется при «дрейфе версий»: FluidAudio README называет Sortformer «NVIDIA Open Model License», а HF-репозиторий, который он реально скачивает, заявлен как `cc-by-4.0`, при этом v2.1 — уже NVIDIA OML. Если каталог будет резолвить «sortformer» в v1 — коммерческая поставка становится нарушением. **Нужен пин на конкретный репозиторий + ревизию, а не на семейство.**
4. **CC-BY-4.0 — не программная лицензия.** Нет патентного гранта (в отличие от Apache-2.0), нет условия про «no additional restrictions» в терминах GPL-совместимости; Creative Commons сама не рекомендует CC для ПО. Для весов это приемлемо (так делает NVIDIA), но: (а) обязательна атрибуция и указание, что файлы изменены (конвертация в CoreML/MLX/ONNX); (б) при смешивании в одном дистрибутиве с Apache-2.0-кодом нужно аккуратно разделять «код» и «веса» в NOTICE. Патентный риск (например, для Conformer/RNN-T архитектур) остаётся нераспределённым.
5. **Gated-веса и собственное зеркало.** Для pyannote доступ выдаётся после ввода компании/use case и согласия на письма; для Cohere Transcribe — `gated: auto` при Apache-2.0. Даже когда лицензия разрешает редистрибуцию, зеркало, полученное нашим аккаунтом, лишает правообладателя возможности собрать данные о конечном пользователе — это нарушение условий доступа (договорных), а не копирайта. **Схема: download-at-source с токеном пользователя; в каталоге хранить только манифест + URL + хеш.** Исключение — уже существующие не-gated зеркала (`pyannote-community/...`), но их юридическая чистота не гарантирована.
6. **NVIDIA Open Model License — четыре обязательства, которые легко пропустить.** (а) копия соглашения + notice-файл со строкой «Licensed by NVIDIA Corporation under the NVIDIA Open Model License»; (б) соответствие Trustworthy AI terms; (в) запрет обхода Guardrail — при квантизации/конвертации в ANE надо сохранять эквивалентные ограничения, если они были в модели; (г) лицензия **revocable** и может обновляться NVIDIA в одностороннем порядке («You agree to either comply with any updated license or cease… distribution»). Плюс indemnity на нас и право Делавэра. Это значит: для моделей под NVIDIA OML нужен механизм **отзыва/обновления в каталоге** (иначе после изменения лицензии мы продолжим раздавать веса).
7. **OpenMDW-1.1 — самый мягкий из «специальных», но требует хранения notices.** При распространении: копия соглашения + все copyright/notices; patent-retaliation (прекращение прав при патентном иске против материалов). Ограничений на коммерцию и на использование выходов нет. Для каталога — просто класть `LICENSE` + `NOTICE` рядом с весами.
8. **FunASR Model License (SenseVoice) — не OSI, с двусмысленностью.** «Free to use, copy, modify, and share» сосуществует с «provided for reference and learning purposes only»; отдельный пункт прекращает лицензию за «unjustified denigration» правообладателя. Для коммерческого продукта — только после юридической проверки; как минимум не делать SenseVoice дефолтной моделью.
9. **Отсутствие декларации лицензии = отсутствие прав.** Часть моделей в зоопарке k2-fsa и множество сторонних конверсий (`mlx-community/*`, случайные HF-репозитории) не имеют `license` в карточке. Редистрибуция таких весов — риск; допустимо только скачивание у источника с явным предупреждением в UI.
10. **Атрибуция вендоренных весов внутри permissive-проектов уже нарушается.** WhisperLiveKit распространяет 4 файла Silero VAD (MIT) в Apache-2.0-репозитории без NOTICE/атрибуции Silero. Это иллюстрация того, как легко потерять notice; в нашем каталоге нужен автоматический NOTICE-генератор.
11. **MPL-2.0 (eigen) внутри sherpa-onnx** — file-level copyleft: если мы патчим файлы eigen, патчи должны быть открыты. Мы их не патчим, но при форке sherpa-onnx это надо держать в голове; также требуется отдавать исходники изменённых MPL-файлов.
12. **Голосовые данные и клонирование.** В speech-swift есть модели с non-commercial/research-лицензиями (F5-TTS `cc-by-nc-4.0`, Higgs TTS 3 «Research and Non-Commercial License», Breeze TTS 2 «BreezeBlue Research and Non-Commercial License», Fish Audio S2 Pro) — `README.md#L61-L63`, `docs/models/f5-tts.md#L18`, `docs/models/higgs-tts.md#L20`, `docs/models/breeze-tts.md#L39`. Их нельзя включать в каталог; отдельно — биометрический режим (speaker embeddings) требует оценки по GDPR/152-ФЗ, лицензия этого не покрывает.
13. **Смена владельца/имени репозитория ломает прослеживаемость.** WhisperKit → argmax-oss-swift, `t-bank-ai/T-one` → `t-tech/T-one` (старый id в HF API не существует). Манифест каталога должен хранить и owner/repo, и commit/revision, и хеш файла.

### Риски сопровождения

- **speaches**: `master` не двигался с 2026-04-17 (0 коммитов за 30/90/180 дней), последний тег — RC от 2025-12-27, 149 открытых issues+PR, ~20 незамерженных ветвей. Форк = принятие поддержки на себя.
- **gotranscribesrv**: 1 автор, 1 звезда, 0 релизов, последний коммит 2026-08-26 (0 коммитов за 30 дней). Bus factor = 1.
- **SpeechRail**: 524 коммита/30 дней, но всего 3 автора за всю историю — высокая скорость при крайне узкой команде; 17 открытых issues при 3 звёздах.
- **sherpa-onnx**: 643 открытых issues+PR при 55 авторах/90 дней — большой, но «шумный» бэклог; релизы частые (196 тегов, v2.0.0 2026-10-23).
- **WhisperKit/argmax-oss-swift**: 143 открытых issues+PR, последний тег v0.17.0 от 2026-03-13 (7 месяцев назад) при живых коммитах (9/30 дней) — релизный цикл отстаёт от разработки; смена названия репозитория уже произошла.
- **FluidAudio**: 74 тега, релиз v0.17.7 2026-10-07, 35 авторов/90 дней — здоровый проект, но модели перепубликуются в `FluidInference/*`, т.е. мы зависим от чужого каталога и его лицензионной дисциплины.
- **speech-swift**: 655 коммитов/180 дней, версия `0.0.29` — pre-1.0, API нестабилен.

## Выводы для Runtime

**Что взять**

1. **Лицензионная модель каталога**: каждая запись = (owner/repo, revision/commit, sha256 артефакта, SPDX-идентификатор, текст лицензии, NOTICE, `commercial_ok`, `redistribution` ∈ {allowed, attribution-required, notice-required, gated-download-only, forbidden}, `gated`, `source_url`). Лицензия привязывается к **артефакту**, не к семейству моделей (см. Sortformer v1/v2/v2.1).
2. **Три класса раздачи**: `mirror` (кладём веса у себя: Apache-2.0/MIT/CC-BY-4.0/NVIDIA OML/OpenMDW), `fetch` (скачиваем у источника по URL+hash, без зеркала: gated, undeclared-license), `forbidden` (NC, TEN VAD).
3. **NOTICE-генератор** по образцу `FluidInference/speaker-diarization-coreml/NOTICE.md`: точный перечень артефактов под каждой лицензией, атрибуция всех апстримов (включая промежуточных конвертеров), фраза «modified conversion».
4. **Версионирование лицензии**: хранить `license_version`/`license_sha256` и уметь помечать запись `license_changed` (NVIDIA OML revocable, OpenMDW может обновиться) → каталог должен поддерживать отзыв модели у уже установленных клиентов (минимум — предупреждение при загрузке).
5. **Слой адаптеров движков — только permissive**: CoreML/FluidAudio (Apache-2.0), MLX/mlx-swift (MIT), ONNX Runtime (MIT), sherpa-onnx (Apache-2.0). Никакого GPL в линкуемом слое; gotranscribesrv — максимум внешний процесс по HTTP.
6. **Плагинная изоляция для «серых» лицензий**: TEN VAD, FunASR/SenseVoice, любые NC-модели — отдельный устанавливаемый пользователем плагин с показом лицензии и подтверждением; ядро runtime их не содержит и не раздаёт.
7. **Для русской речи дефолт**: GigaAM v3 (MIT — самая чистая лицензия из всех ru-моделей) + T-one (Apache-2.0, streaming). Обе — без gate, без NC, допускают зеркалирование.

**Что не брать**

- TEN VAD (запрет на платформенное развёртывание) — только явный opt-in плагин.
- Sortformer v1 (`cc-by-nc-4.0`) — исключить из резолвера, оставить только v2 (CC-BY-4.0) и v2.1 (NVIDIA OML).
- Форк gotranscribesrv целиком (GPLv3 + bus factor 1).
- Зеркалирование gated-моделей (pyannote, Cohere Transcribe) — только `fetch` с токеном пользователя.
- NC/research TTS и клонирование голоса (F5-TTS, Higgs, Breeze, Fish Audio S2 Pro).
- Вендоринг весов без NOTICE (антипаттерн WhisperLiveKit + Silero).

**Как интегрировать**

- `sherpa-onnx` — как C-API/ONNX-движок для ONNX-моделей; при форке обязательно: собственный `THIRD_PARTY_NOTICES` (eigen MPL-2.0, portaudio MIT, websocketpp BSD-3, openfst Apache-2.0, utf8proc MIT, asio Boost-1.0, pybind11 BSD-3) — в апстриме такого файла нет.
- `FluidAudio` — как CoreML/ANE-адаптер: Apache-2.0, без внешних SPM-зависимостей, но модели тянутся из `FluidInference/*` → либо зависеть от их каталога, либо конвертировать самим и публиковать свой NOTICE (второй вариант юридически чище и не зависит от чужой лицензионной дисциплины).
- `speech-swift` — Apache-2.0, но `0.0.x`: пинить по тегу и SHA, не тянуть `main`.
- `WhisperKit` — использовать новое имя `argmaxinc/argmax-oss-swift`, MIT; модели — `argmaxinc/whisperkit-coreml` (MIT).
- `mlx-swift`/`mlx-swift-lm` (MIT) — как MLX-адаптер; `swift-transformers` (Apache-2.0) — токенизаторы.
- Серверный слой: Hummingbird (Apache-2.0) предпочтительнее Vapor (MIT) только по критерию лицензии — оба допустимы; выбор по другим критериям.

## Неизвестные / что измерить

1. **Не проверено**: лицензии `mlx-swift-lm`, `modelcontextprotocol/swift-sdk`, `hummingbird-websocket`, `swift-websocket`, `xgrammar`, `hclust-cpp`, `kaldi-decoder` — в отчёте они приведены без подтверждения или не приведены вовсе. Нужен прогон `licensee`/`cargo-deny`-подобного сканера по `Package.resolved`/`uv.lock`/`go.sum`.
2. **Не проверено**: полный текст gate-условий `CohereLabs/cohere-transcribe-03-2026` (HF отдаёт 401 без принятия условий) и `pyannote/speaker-diarization-community-1` (прочитан только `extra_gated_prompt` + поля, но не полный набор пунктов соглашения).
3. **Юридический вопрос без ответа**: считается ли int8/ANE-квантизация «обходом Guardrail» по п.2.1 NVIDIA Open Model License. Нужна позиция юриста; технически — сохранять в метаданных ссылку на safety-документацию апстрима (`safety.md`, `privacy.md` есть в репозиториях NVIDIA).
4. **Юридический вопрос без ответа**: допустимость зеркалирования gated-весов, когда лицензия (CC-BY-4.0/Apache-2.0) это разрешает, но gate-соглашение подписано нашим аккаунтом. Также: легитимность уже существующих не-gated зеркал (`pyannote-community/*`).
5. **Не проверено**: лицензия весов `ten-vad.onnx`, который sherpa-onnx раздаёт в своих GitHub-релизах, — покрывает ли её TEN-лицензия и требуется ли отдельный notice в нашем дистрибутиве.
6. **Не проверено**: происхождение и права на конверсии `mlx-community/*`, `onnx-community/*`, `handy-computer/*` — у многих нет декларации лицензии; нужен аудит перед любым зеркалированием.
7. **Что измерить (инженерия)**:
   - стоимость холодного старта каталога: время проверки sha256 для 1 ГБ весов и накладные расходы idle-демона (целевой бюджет < 50 МБ RSS, < 0.1 % CPU);
   - размер NOTICE/лицензионного манифеста на модель (байты) и стоимость его генерации при установке;
   - частоту релизов и «license drift» — автоматический опрос HF `lastModified` + `cardData.license` раз в сутки по всем записям каталога (метрика: сколько записей изменили лицензию за квартал);
   - для каждой модели — фактический `license` из репозитория (файл `LICENSE`) vs `cardData.license` (расхождение уже подтверждено на Whisper и Sortformer).
8. **Не проверено**: активность/звёзды для `voicekit-team/T-one`, `pipecat-ai/smart-turn`, `modelscope/3D-Speaker` — GitHub API исчерпал лимит (60/час) на момент запроса; для 3D-Speaker известно только `pushed_at: 2025-12-08` (получено до лимита), для T-one/smart-turn — только лицензии.

## Источники

**Клоны (SHA из задания)**
- speech-swift: https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L242-L264 ; `LICENSE`; `README.md#L61-L63,#L608-L610`; `docs/models/kyutai-stt.md#L17`; `docs/models/f5-tts.md#L18`; `docs/models/higgs-tts.md#L20`; `docs/models/breeze-tts.md#L39`; `docs/models/nemotron-asr-streaming.md#L10`
- SpeechRail: https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/pyproject.toml#L7-L29 ; `vendor/engine-build/engine-build.json`; `vendor/mlx-audio-incremental/README.md#L1-L10`
- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L27 ; `cmake/kaldi-native-fbank.cmake#L4`; `cmake/openfst.cmake#L6`; `cmake/utf8proc.cmake#L7`; `cmake/hclust-cpp.cmake#L5`; `cmake/portaudio.cmake#L4`; `cmake/websocketpp.cmake#L5`; `cmake/simple-sentencepiece.cmake#L20`; `go-api-examples/vad/main.go#L18-L31`
- WhisperLiveKit: https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/pyproject.toml#L7-L36 ; `whisperlivekit/silero_vad_models/`; `README.md#L218`
- speaches: https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/pyproject.toml#L7-L29 ; `LICENSE`
- gotranscribesrv: https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/LICENSE.md ; `sidecar-audio/Sources/Server/EngineManager.swift#L107-L116`
- FluidAudio: https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Models.md#L105-L110 ; `README.md#L53,#L449,#L780`; `Sources/FluidAudio/ModelNames.swift#L7,#L12,#L64,#L74`
- WhisperKit: https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L42-L49 ; `LICENSE`; `NOTICES`

**HF API (проверено 2026-10-25)**
- https://huggingface.co/api/models/openai/whisper-large-v3 , `/openai/whisper-large-v3-turbo` , `/openai/whisper-small` , `/argmaxinc/whisperkit-coreml`
- https://huggingface.co/api/models/ai-sage/GigaAM-v3 , `/t-tech/T-one`
- https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v3 , `/nvidia/parakeet-tdt-0.6b-v2` , `/nvidia/canary-1b-v2` , `/nvidia/canary-180m-flash` , `/nvidia/canary-qwen-2.5b` , `/nvidia/parakeet-unified-en-0.6b` , `/nvidia/parakeet_realtime_eou_120m-v1` , `/nvidia/nemotron-3.5-asr-streaming-0.6b`
- https://huggingface.co/api/models/nvidia/diar_sortformer_4spk-v1 , `/nvidia/diar_streaming_sortformer_4spk-v2` , `/nvidia/diar_streaming_sortformer_4spk-v2.1`
- https://huggingface.co/api/models/pyannote/segmentation-3.0 , `/pyannote/speaker-diarization-community-1` , `/pyannote-community/speaker-diarization-community-1` , `/pyannote/wespeaker-voxceleb-resnet34-LM` , `/Wespeaker/wespeaker-voxceleb-resnet34-LM`
- https://huggingface.co/api/models/Qwen/Qwen3-ASR-0.6B , `/Qwen/Qwen3-ASR-1.7B` , `/mistralai/Voxtral-Mini-3B-2507` , `/kyutai/stt-1b-en_fr` , `/kyutai/stt-2.6b-en`
- https://huggingface.co/api/models/moonshine-ai/moonshine-base , `/moonshine-ai/moonshine-streaming-tiny` , `/moonshine-ai/moonshine-streaming-small` , `/pipecat-ai/smart-turn-v3`
- https://huggingface.co/api/models/FunAudioLLM/SenseVoiceSmall , `/CohereLabs/cohere-transcribe-03-2026`
- https://huggingface.co/api/models/FluidInference/parakeet-tdt-0.6b-v3-coreml , `/FluidInference/diar-streaming-sortformer-coreml` , `/FluidInference/speaker-diarization-coreml` , `/FluidInference/silero-vad-coreml` , `/FluidInference/kokoro-82m-coreml` , `/FluidInference/campplus-coreml` , `/FluidInference/nemotron-speech-streaming-en-0.6b-coreml`
- https://huggingface.co/api/models/aufklarer/Kyutai-STT-1B-MLX-8bit , `/aufklarer/Nemotron-3-Diarization-100M-CoreML-INT8`
- https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common

**Тексты лицензий и документы**
- NVIDIA Open Model License (ред. 24.10.2025): https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
- OpenMDW-1.1: https://openmdw.ai/license/1-1/
- TEN VAD License: https://github.com/TEN-framework/ten-vad/blob/main/LICENSE
- Silero VAD License: https://github.com/snakers4/silero-vad/blob/master/LICENSE
- FunASR Model License: https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE
- pyannote gate-текст: https://huggingface.co/pyannote/speaker-diarization-community-1/resolve/main/README.md , https://huggingface.co/pyannote/segmentation-3.0/resolve/main/README.md , https://github.com/pyannote/pyannote-audio (README, шаг 3)
- FluidInference NOTICE/provenance: https://huggingface.co/FluidInference/speaker-diarization-coreml/blob/main/NOTICE.md
- [OI] Whisper: https://github.com/openai/whisper/blob/main/LICENSE
- mlx-audio: https://github.com/Blaizzy/mlx-audio/blob/main/LICENSE
- GitHub API: https://api.github.com/repos/{soniqo/speech-swift, hrygo/SpeechRail, k2-fsa/sherpa-onnx, QuentinFuxa/WhisperLiveKit, speaches-ai/speaches, sagostin/gotranscribesrv, FluidInference/FluidAudio, repositories/748528018 (argmaxinc/argmax-oss-swift), ml-explore/mlx, ml-explore/mlx-swift, microsoft/onnxruntime, huggingface/swift-transformers, hummingbird-project/hummingbird, vapor/vapor, snakers4/silero-vad, TEN-framework/ten-vad, pyannote/pyannote-audio, wenet-e2e/wespeaker, modelscope/3D-Speaker, salute-developers/GigaAM}
