# V4. Совместимость зависимостей и проверка лицензий (волна 2)

Статус: части (а) и (б) выполнены; из части (а) не доведён до конца только `swift build` (см. §«Результат
разрешения зависимостей» → «Статус swift build»), `swift package resolve` для Swift и `cargo build` для
Rust — завершены с сырыми выводами ниже.

Окружение всех прогонов: macOS `ProductVersion 27.0`, `BuildVersion 26A428`, `arm64`;
`swift-driver version: 1.168.6`, `Apple Swift version 6.4 (swiftlang-6.4.0.34.1 clang-2103.0.34.1)`,
target `arm64-apple-macosx27.0.0`; `Xcode 27.1 (Build 27A9275)`;
`cargo 1.98.1 (797e8a9bc 2026-08-05)`, `rustc 1.98.1 (48a229cea 2026-09-01)`.
Клоны репозиториев (blob:none): `/tmp/srr/repos`, эксперименты: `/tmp/srr/probe/V4`.

## Итог

1. **Все четыре Swift-пакета разрешаются в один граф без конфликтов версий.** `swift package resolve`
   на пакете, одновременно зависящем от speech-swift, FluidAudio, argmax-oss-swift (WhisperKit) и
   sherpa-onnx, проходит с первого раза: 39 пинов, ни одного version conflict (см. §«Результат
   разрешения зависимостей»). Конфликтов `swift-transformers`/`mlx-swift`/`swift-argument-parser`/
   `hummingbird` нет, потому что FluidAudio и sherpa-onnx вообще не тянут этих зависимостей
   (FluidAudio — `dependencies: []`, sherpa-onnx — только `csukuangfj/onnxruntime-libs`).
   `swift build` **завершён успешно** (EXIT=0, 1004 с): бинарь 125.1 МБ, все четыре движка
   линкуются и запускаются в одном процессе, внешних dylib нет (только системные фреймворки),
   символ `OrtGetApiBase` ровно один. Оба вопроса части (а) закрыты.
2. **Совместимость обеспечивается за счёт «широких» диапазонов, а не за счёт согласования.**
   speech-swift объявляет `hummingbird 2.5.0..<2.17.0`, `swift-websocket 1.5.0..<1.6.0` (пин из-за
   сломанного 1.6.0), `swift-transformers from 1.1.6` — резолвер выбрал 2.16.0 / 1.5.0 / 1.3.5.
   Любой апстрим-бамп hummingbird за 2.17 сломает сборку speech-swift → в своём runtime надо
   фиксировать `Package.resolved` целиком, а не только 4 прямые зависимости.
3. **Минимальные платформы несовместимы «вниз»**: speech-swift `.macOS("15.0")` + tools 5.10;
   FluidAudio tools 6.0, `.macOS(.v14)`; WhisperKit tools 5.10, `.macOS(.v13)`; sherpa-onnx tools 5.9,
   `.macOS(.v10_15)`. Итоговый минимум для одного бинаря — **macOS 15.0 и Swift tools ≥ 6.0**
   (6.0 требуется манифестом FluidAudio). Проба собрана с `swift-tools-version: 6.0`, `.macOS(.v15)`.
4. **Двух ONNX Runtime в одном процессе можно избежать, но по умолчанию их два.** sherpa-onnx
   (Swift SPM) тянет `csukuangfj/onnxruntime-libs` ровно `1.28.2`; sherpa-onnx (Rust crate) статически
   несёт **ORT 1.31.0** (проверено вызовом `sherpa_onnx::onnxruntime_version()` → `1.31.0`), а `ort`
   2.0.0-rc.15 по умолчанию скачивает **prebuilt ORT 1.31.0** (`cdn.pyke.io/.../ms@1.31.0/aarch64-apple-darwin+coreml`).
   → Swift-воркер и Rust-воркер видят разные ORT-версии; внутри одного Rust-бинаря `sherpa-onnx` +
   `ort` дают дубликаты символов (см. §Rust).
5. **Rust: `sherpa-onnx` 2.0.0 + `ort` 2.0.0-rc.15 линкуются, но линкер выдаёт десятки
   `duplicate symbol`** (onnx-ml.pb.cc.o, arenastring.cc.o, graph.cc.o …), потому что оба статически
   вносят ORT. Бинарь собирается и **работает** (проверено запуском), т.к. версии ORT совпали и
   линкер схлопнул символы; но это хрупкое состояние. Безопасная конфигурация — `ort` с
   `default-features = false, features = ["load-dynamic"]`: тогда дубликатов нет (проверено, сборка
   чистая). Рекомендация: **sherpa-onnx — единственный владелец ORT в процессе**, а `ort` — либо
   `load-dynamic` на тот же dylib, либо не использовать вовсе.
6. **Лицензионная гигиена конверсий хуже, чем в wave1.** Найдены три класса проблем, которых нет в
   K-отчёте: (а) `aufklarer/WeSpeaker-ResNet34-LM-{MLX,CoreML}` и `aufklarer/FireRedVAD-CoreML`
   объявляют **MIT** при апстриме **CC-BY-4.0 / Apache-2.0** — это смена лицензии производного
   артефакта (потеря атрибуции и notice-обязательств); (б) `FluidInference/parakeet-ctc-0.6b-coreml`
   (используется FluidAudio как `parakeetCtc06b`) **вообще без лицензии**; (в) README FluidAudio
   заявляет «permissive licenses» для всех моделей, тогда как в его же каталоге есть
   `nvidia-open-model-license`, `other/...-upstream` и один `cc-by-nc-4.0`.
7. **`aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit` (и `-bf16`) подписаны
   `nvidia-software-and-model-evaluation-license`** — это НЕ OpenMDW-1.1, а лицензия, прямо
   запрещающая production-использование и требующая NVIDIA GPU («only for internal test and
   evaluation purposes, without use in production and in systems with NVIDIA GPUs»). При этом
   `-5bit` и `-8bit` того же семейства — `openmdw-1.1`. speech-swift по умолчанию тянет
   `-5bit` (ок) и `-8bit` (ок), но 4bit/bf16 в каталог класть нельзя.
8. **TEN VAD: запрет подтверждён дословно** и он ровно про наш сценарий («enabling any third party to
   develop or deploy Applications»), плюс п.3 распространяет лицензию на производные. Дополнительно:
   sherpa-onnx **сам редистрибутирует** `ten-vad.onnx` (332 211 байт, HTTP 200) в своих релизах,
   т.е. копия весов уже лежит в чужом Apache-2.0-дистрибутиве.
9. **`csukuangfj/*`: 55 из 100 последних моделей без `license` в карточке** (выборка через HF API
   `author=csukuangfj&limit=100`: 55 без тега, 42 `apache-2.0`, 2 `mit`, 1 `cc-by-4.0`). Для GigaAM v3
   официальной конверсии у csukuangfj нет — есть только сторонние (`Smirnov75`, `BahamutRU`,
   `Alexxerm`, `fussraider` и др., все с тегом `mit`), т.е. опора на неподдерживаемые артефакты.
10. **Практический вывод по процессам**: один Swift-бинарь со всеми Apple-движками — допустим
    (конфликтов нет); sherpa-onnx в Swift-процессе безопасен, но добавляет ORT 1.28.2, отличный от
    ORT 1.31.0 в Rust-крате; поэтому **worker-onnx лучше держать на одной стороне** (либо целиком
    Swift/SPM, либо целиком Rust/`sherpa-onnx` crate) и не смешивать ORT-версии в одном процессе.
11. **Скрытые бинарные зависимости, которых не было в wave1**: speech-swift тянет
    `SpeechCore.xcframework` из `soniqo/speech-core` (Apache-2.0, `libspeech_core.a` 2 455 600 байт,
    внутри **нет** символов ONNX Runtime), FluidAudio — `NemoTextProcessing.xcframework` из
    `FluidInference/text-processing-rs` (Apache-2.0). Оба лицензионно чистые, но их надо включать в
    NOTICE-генератор; у `csukuangfj/onnxruntime-libs` файла LICENSE нет вовсе.

## Проверенные утверждения

Вердикты относятся к утверждениям из `wave1/K-licenses-maintenance.md` (столбец «источник-отчёт» = K),
если не указано иное.

| Утверждение | Источник-отчёт | Вердикт | Доказательство |
|---|---|---|---|
| speech-swift — Apache-2.0, tools 5.10, `.macOS("15.0")` | K | ПОДТВЕРЖДЕНО | https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift#L1-L10 ; `head -3 LICENSE` → «Apache License Version 2.0» |
| speech-swift зависит от hummingbird `2.5.0..<2.17.0`, swift-websocket `1.5.0..<1.6.0`, swift-transformers `from 1.1.6`, WhisperKit `from 1.0.0`, mlx-swift-lm `exact 3.31.4` | K (номера строк) | ПОДТВЕРЖДЕНО | .../Package.swift#L242-L268 (см. цитату в §Зависимости) |
| WhisperKit переименован в `argmax-oss-swift`, MIT, продукт `WhisperKit` | K | ПОДТВЕРЖДЕНО | https://github.com/argmaxinc/argmax-oss-swift/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L1-L38 ; `LICENSE` = «MIT License, Copyright (c) 2024 argmax, inc.» |
| WhisperKit имеет `NOTICES` с «Argmax-modification:» для swift-transformers (Apache-2.0) | K | ПОДТВЕРЖДЕНО | https://github.com/argmaxinc/argmax-oss-swift/blob/9f99b5cb6a907730543823ad271b82532782a899/NOTICES#L1-L20 |
| sherpa-onnx — Apache-2.0, tools 5.9, macOS 10.15, SPM есть | K | ПОДТВЕРЖДЕНО | https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L1-L16 |
| sherpa-onnx SPM пинит onnxruntime 1.28.2 | (нет в K) | ПОДТВЕРЖДЕНО | `Package.swift#L17-L19` + `Package.resolved` → `onnxruntime-libs 1.28.2`; артефакт `onnxruntime-macos-shared-xcframework-1.28.2.xcframework.zip` |
| FluidAudio — Apache-2.0, без внешних SPM-зависимостей | K | ПОДТВЕРЖДЕНО | https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Package.swift#L1-L27 (`dependencies: []`), README#L778-L780 |
| FluidAudio тянет бинарный артефакт `NemoTextProcessing.xcframework` из `FluidInference/text-processing-rs` | (нет в K) | ПОДТВЕРЖДЕНО | вывод resolve: `Downloading binary artifact https://github.com/FluidInference/text-processing-rs/releases/download/v0.3.1/NemoTextProcessing.xcframework.zip` |
| В FluidAudio есть `ThirdPartyLicenses/` (vbx, fastcluster, G2P, KokoroANE) | (нет в K) | ПОДТВЕРЖДЕНО | `ls FluidAudio/ThirdPartyLicenses` → `vbx-LICENSE.md`, `fastcluster-LICENSE.md`, `NemoTextProcessing-LICENSE.md`, `LuxTtsEnglishG2P-LICENSE.md`, `KokoroAneSpanishFrenchG2P-LICENSE.md`, `JapaneseG2P-LICENSE.md`, `KokoroANE-v3/` |
| speech-swift имеет `ThirdPartyNotices/` | (нет в K) | ПОДТВЕРЖДЕНО | `ls speech-swift/ThirdPartyNotices` → `BreezeTTS.txt`, `MossTTSNano.txt`, `Qwen3TTSCodec.txt` |
| TTS-модели speech-swift с NC-лицензиями (F5-TTS, Higgs, Breeze) | K | ПОДТВЕРЖДЕНО | https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/README.md#L61-L63 |
| speech-swift `docs/models/nemotron-asr-streaming.md` называет Nemotron 3.5 ASR gated | K (расхождение) | ПОДТВЕРЖДЕНО | https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/models/nemotron-asr-streaming.md#L10 → «(gated)»; HF API: `gated: false` |
| Nemotron 3.5 ASR streaming 0.6B = `openmdw-1.1`, не gated | K | ПОДТВЕРЖДЕНО | HF API `nvidia/nemotron-3.5-asr-streaming-0.6b` → `license: other`, `license_name: openmdw-1.1`, `license_link: https://openmdw.ai/license/1-1/`, `gated: false` |
| Nemotron-3-Diarization = OpenMDW-1.1 (в таблице K отсутствовал) | — | ПОДТВЕРЖДЕНО | HF API `nvidia/Nemotron-3-Diarization` → `license: openmdw-1.1`, `gated: false`, 116 775 загрузок; карточка: «This model is ready for commercial or non-commercial use» |
| OpenMDW-1.1 разрешает коммерцию, требует копию соглашения + notices, patent-retaliation, не ограничивает выходы | K | ПОДТВЕРЖДЕНО | текст https://openmdw.ai/license/1-1/ (цитаты в §OpenMDW) |
| Sortformer v1 = `cc-by-nc-4.0` | K | ПОДТВЕРЖДЕНО | HF API `nvidia/diar_sortformer_4spk-v1` → `license: cc-by-nc-4.0`, `gated: false` |
| Sortformer streaming v2 = `cc-by-4.0` | K | ПОДТВЕРЖДЕНО | HF API `nvidia/diar_streaming_sortformer_4spk-v2` → `cc-by-4.0` |
| Sortformer streaming v2.1 = `nvidia-open-model-license` | K | ПОДТВЕРЖДЕНО | HF API `nvidia/diar_streaming_sortformer_4spk-v2.1` → `license: other`, `license_name: nvidia-open-model-license` |
| FluidAudio README называет Sortformer «NVIDIA Open Model License», а скачивает репозиторий с `cc-by-4.0` | K | ПОДТВЕРЖДЕНО | README#L449 vs `ModelNames.swift#L74` = `FluidInference/diar-streaming-sortformer-coreml`; HF API этого репо → `cc-by-4.0`, `base_model: nvidia/diar_streaming_sortformer_4spk-v2.1` |
| FluidAudio README заявляет «permissive licenses» для всех моделей | K (не выделено) | **ОПРОВЕРГНУТО как обобщение** | README#L53; но каталог FluidInference (115 репо): 59 apache-2.0, 18 cc-by-4.0, 15 other, 12 mit, 7 без лицензии, 2 openmdw-1.1, 1 bigscience-openrail-m, 1 **cc-by-nc-4.0** (`FluidInference/decision-modernbert-base-coreml`) |
| Voxtral Mini 3B = apache-2.0 | K | ПОДТВЕРЖДЕНО | HF API `mistralai/Voxtral-Mini-3B-2507` → `apache-2.0`, `gated: false` |
| Voxtral Mini 4B Realtime = apache-2.0 | (в K только 3B) | ПОДТВЕРЖДЕНО | HF API `mistralai/Voxtral-Mini-4B-Realtime-2602` → `apache-2.0`, `gated: false`, 2 041 370 загрузок; карточка: «released in BF16 under the Apache-2 license» |
| Smart Turn v3 = `bsd-2-clause` | K | ПОДТВЕРЖДЕНО | HF API `pipecat-ai/smart-turn-v3` → `bsd-2-clause`; https://github.com/pipecat-ai/smart-turn/blob/4786657e242dfe77dd138699ac564ee074a2a543/LICENSE#L1-L3 «BSD 2-Clause License, Copyright (c) 2024–2025, Daily» |
| FireRedVAD — лицензия не указана в K | — | ПОДТВЕРЖДЕНО как Apache-2.0 (код) + `apache-2.0` (веса) | GitHub API `FireRedTeam/FireRedVAD` → `license.spdx_id = Apache-2.0`; HF API `FireRedTeam/FireRedVAD` → `apache-2.0`, `gated: false` |
| GigaAM v3 = `mit`, не gated | K | ПОДТВЕРЖДЕНО | HF API `ai-sage/GigaAM-v3` → `license: mit`, `gated: false`; карточка `license: mit`; апстрим https://github.com/salute-developers/GigaAM → MIT («Copyright (c) 2024 GigaChat Team») |
| Qwen3-ASR 0.6B/1.7B = `apache-2.0`, не gated | K | ПОДТВЕРЖДЕНО | HF API обоих репо |
| pyannote community-1 = `cc-by-4.0`, `gated: auto` | K | ПОДТВЕРЖДЕНО | HF API → `cc-by-4.0`, `gated: auto`; полный `extra_gated_prompt` в §pyannote |
| pyannote segmentation-3.0 = `mit`, `gated: auto` | K | ПОДТВЕРЖДЕНО | HF API → `license: mit`, `gated: auto`, поля `Company/university`, `Website` |
| WeSpeaker веса = `cc-by-4.0` | K | **ЧАСТИЧНО** | `pyannote/wespeaker-voxceleb-resnet34-LM` → `cc-by-4.0`, не gated; `Wespeaker/wespeaker-voxceleb-resnet34-LM` → `cc-by-4.0`; но `Wespeaker/wespeaker-voxceleb-resnet34` → **apache-2.0**, и ещё 11 из 16 репо org `Wespeaker` — `apache-2.0` |
| Silero VAD = `mit` | K | ПОДТВЕРЖДЕНО | https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/LICENSE → «MIT License, Copyright (c) 2020-present Silero Team»; теги релизов до `v6.2.3` |
| Silero VAD v6 существует как версия весов | K («Silero v6») | ПОДТВЕРЖДЕНО | `git ls-remote --tags`-эквивалент через GitHub API: `v6.0`, `v6.1`, `v6.2`, `v6.2.1`, `v6.2.2`, `v6.2.3` |
| TEN VAD = Apache-2.0 + доп. условия Agora, запрет конкурентного Deploy | K | ПОДТВЕРЖДЕНО | https://github.com/TEN-framework/ten-vad/blob/d68ab71c8d5fbd8f98e97ae7c9986af76b5d72b1/LICENSE (дословно в §TEN VAD) |
| TEN VAD веса на HF без декларации лицензии | K | ПОДТВЕРЖДЕНО | HF API `TEN-framework/ten-vad` → `license: None`, `gated: false`, 292 загрузки |
| sherpa-onnx раздаёт `ten-vad.onnx` в релизах | K | ПОДТВЕРЖДЕНО | `HEAD https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx` → 302 → 200, `content-length: 332211` |
| FluidInference перепубликует лицензию апстрима, а не свою | K | **ЧАСТИЧНО** | `parakeet-tdt-0.6b-v3-coreml` = cc-by-4.0 ✓, `nemotron-speech-streaming-en-0.6b-coreml` = nvidia-open-model-license ✓, `nemotron-3-diarization-coreml` = openmdw-1.1 ✓, `campplus-coreml`/`sensevoice-small-coreml` = `other/<upstream>` ✓, **но** `parakeet-ctc-0.6b-coreml` — без лицензии, а `speaker-diarization-coreml` — `scoped-cc-by-4.0` (собственная конструкция) |
| `aufklarer/Kyutai-STT-1B-MLX-8bit` = `cc-by-4.0`, `aufklarer/Nemotron-3-Diarization-100M-CoreML-INT8` = `openmdw-1.1` | K | ПОДТВЕРЖДЕНО | HF API обоих |
| `aufklarer/*` конверсии speech-swift сохраняют лицензию апстрима | K (как принцип) | **ОПРОВЕРГНУТО** | `aufklarer/WeSpeaker-ResNet34-LM-MLX` → `license: mit`, `base_model: pyannote/wespeaker-voxceleb-resnet34-LM` (= cc-by-4.0); `aufklarer/FireRedVAD-CoreML` → `mit`, апстрим `apache-2.0`; см. §aufklarer |
| Часть моделей k2-fsa/csukuangfj без `license` | K | ПОДТВЕРЖДЕНО и уточнено | HF API `author=csukuangfj&limit=100`: 55 без тега лицензии, 42 apache-2.0, 2 mit, 1 cc-by-4.0 |
| NVIDIA Open Model License — revocable, копия соглашения + notice, запрет обхода Guardrail | K | ПОДТВЕРЖДЕНО дословно | §NVIDIA OML (цитаты из текста соглашения) |
| `FluidInference/parakeet-ctc-0.6b-coreml` (используется FluidAudio как `parakeetCtc06b`) не имеет лицензии | — | ПОДТВЕРЖДЕНО | HF API: `cardData = {'base_model': 'nvidia/parakeet-ctc-0.6b'}`, тега `license:` нет |
| `aufklarer/WeSpeaker-ResNet34-LM-MLX`/`-CoreML` объявляют MIT при апстриме CC-BY-4.0 | — | **ОПРОВЕРГНУТО** (принцип «конверсия сохраняет лицензию») | HF API: `license: mit`, `base_model: pyannote/wespeaker-voxceleb-resnet34-LM` (= cc-by-4.0); карточка MLX-варианта, строки 1-10 |
| `aufklarer/FireRedVAD-CoreML` объявляет MIT при апстриме Apache-2.0 | — | **ОПРОВЕРГНУТО** (тот же принцип) | HF API `aufklarer/FireRedVAD-CoreML` → `mit`; `FireRedTeam/FireRedVAD` → `apache-2.0` |
| `aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit`/`-bf16` = NVIDIA Software-and-Model-Evaluation License (не OpenMDW) | — | ПОДТВЕРЖДЕНО (новое) | HF API: `license: other`, `license_name: nvidia-software-and-model-evaluation-license`; текст соглашения: «only for internal test and evaluation purposes, without use in production and in systems with NVIDIA GPUs» |
| `soniqo/speech-core` (бинарный `SpeechCore.xcframework`) — Apache-2.0, без символов ORT внутри | — | ПОДТВЕРЖДЕНО | `LICENSE` → Apache 2.0; `nm -gU libspeech_core.a \| grep -c OrtGetApiBase` → `0` |
| `FluidInference/text-processing-rs` (бинарный `NemoTextProcessing.xcframework`) — Apache-2.0 | — | ПОДТВЕРЖДЕНО | `Cargo.toml` → `license = "Apache-2.0"`; `LICENSE` → Apache 2.0 |
| `csukuangfj/onnxruntime-libs` не имеет файла LICENSE | — | ПОДТВЕРЖДЕНО | `GET raw.githubusercontent.com/csukuangfj/onnxruntime-libs/main/LICENSE` → 404 |
| speech-swift + FluidAudio + WhisperKit + sherpa-onnx линкуются в один Swift-бинарь | (открытый вопрос части а) | ПОДТВЕРЖДЕНО | `swift build` EXIT=0; `./.build/debug/V4ProbePath` → «all four engines linked in one binary / sherpa-onnx 2.0.0, onnxruntime 1.28.2»; §«Результат линковки» |
| В Swift-бинаре нет дубликатов символов ONNX Runtime | (открытый вопрос части а) | ПОДТВЕРЖДЕНО | `nm V4ProbePath \| grep OrtGetApiBase` → одна внешняя `T _OrtGetApiBase` + внутренний C++-метод |
| Внешних dylib у Swift-бинаря нет — всё статически | — | ПОДТВЕРЖДЕНО | `otool -L` → только `/System/Library/Frameworks/*` и `/usr/lib/lib{c++,objc,System}.dylib` |
| MPL-2.0 у Eigen внутри sherpa-onnx | K | ПОДТВЕРЖДЕНО (для Eigen 5.0.1) | `cmake/eigen.cmake` качает `eigen-5.0.1`; https://gitlab.com/libeigen/eigen/-/raw/5.0.1/COPYING.MPL2 → «Mozilla Public License Version 2.0» |
| В sherpa-onnx нет файла third-party notices | K | ПОДТВЕРЖДЕНО | `ls sherpa-onnx` → только `LICENSE`, каталогов `NOTICE*`/`THIRD_PARTY*` нет |

## Результат разрешения зависимостей

### Swift: одна проба, четыре пакета

Каталог: `/tmp/srr/probe/V4/pathdeps` (path-зависимости на клоны `/tmp/srr/repos`), манифест:

```swift
// swift-tools-version: 6.0
platforms: [.macOS(.v15)]
dependencies:
  .package(name: "Qwen3Speech",     path: "/tmp/srr/repos/speech-swift")   // @cbad3be
  .package(name: "FluidAudio",      path: "/tmp/srr/repos/FluidAudio")     // @1b0ed50
  .package(name: "argmax-oss-swift",path: "/tmp/srr/repos/WhisperKit")     // @9f99b5c
  .package(name: "sherpa-onnx",     path: "/tmp/srr/repos/sherpa-onnx")    // @532f1b5
target V4ProbePath: Qwen3ASR, NemotronStreamingASR, SpeechVAD, AudioCommon,
                    FluidAudio, WhisperKit, sherpa-onnx
```

Причина path-зависимостей: полный клон speech-swift/WhisperKit/FluidAudio по сети занимает
минуты-десятки минут, а клоны уже лежат в `/tmp/srr/repos` на нужных SHA. Разрешение транзитивных
зависимостей при этом выполняется по сети — идентично обычному сценарию.

Побочный результат (важен для CI): попытка сделать то же по remote-URL с `swift package config
set-mirror` на локальные blob:none-клоны **падает** — частичный клон не может отдать блобы:

```
$ swift package config set-mirror --original https://github.com/soniqo/speech-swift.git \
      --mirror file:///tmp/srr/repos/speech-swift
$ swift package resolve
warning: 'speech-swift': skipping cache due to an error: Failed to clone repository file:///tmp/srr/repos/speech-swift:
    remote: warning: lazy fetching disabled; some objects may not be available
    remote: fatal: could not fetch fecf640fe8d2dadb9a85a61900e944a6a4b6839e from promisor remote
    error: git upload-pack: git-pack-objects died with error.
    fatal: fetch-pack: invalid index-pack output
EXIT=1
```

→ для офлайн-сборки нужны **полные** клоны (`--filter=blob:none` не годится), либо пре-кеш
`~/.swiftpm`/`.build/repositories` целиком.

Команда и результат:

```
$ cd /tmp/srr/probe/V4/pathdeps && swift package resolve
EXIT=0
$ ls Package.resolved   # 10.7K, version 3
```

Пины (39), отсортированы как в файле:

```
async-http-client                 https://github.com/swift-server/async-http-client.git          1.36.2
compress-nio                      https://github.com/adam-fowler/compress-nio.git                1.5.0
eventsource                       https://github.com/mattt/eventsource.git                       1.5.1
hummingbird                       https://github.com/hummingbird-project/hummingbird.git          2.16.0
hummingbird-websocket             https://github.com/hummingbird-project/hummingbird-websocket.git 2.6.0
mlx-swift                         https://github.com/ml-explore/mlx-swift                        0.31.6
mlx-swift-lm                      https://github.com/ml-explore/mlx-swift-lm                     3.31.4
onnxruntime-libs                  https://github.com/csukuangfj/onnxruntime-libs                 1.28.2
swift-algorithms                  .../swift-algorithms.git                                       1.2.1
swift-argument-parser             .../swift-argument-parser.git                                  1.8.2
swift-asn1                        1.7.3     swift-async-algorithms  1.1.7    swift-atomics 1.3.1
swift-certificates                1.21.0    swift-collections       1.7.4    swift-configuration 1.3.0
swift-crypto                      4.5.2     swift-distributed-tracing 1.7.2  swift-http-structured-headers 1.7.0
swift-http-types                  1.8.0     swift-huggingface       1.0.0    swift-jinja 2.5.2
swift-log                         1.16.2    swift-metrics           2.12.0   swift-nio 2.104.0
swift-nio-extras                  1.35.1    swift-nio-http2         1.46.0   swift-nio-ssl 2.37.5
swift-nio-transport-services      1.28.0    swift-numerics          1.1.1    swift-sdk 0.12.1
swift-service-context             1.5.1     swift-service-lifecycle 2.13.0   swift-syntax 603.0.2
swift-system                      1.8.1     swift-transformers      1.3.5    swift-websocket 1.5.0
xgrammar                          0.2.7     yyjson                  0.12.0
```

Скачанные бинарные артефакты (сырой лог `swift build`):

```
Downloading binary artifact .../sherpa-onnx/releases/download/xcframework/sherpa-onnx-v2.0.0-macos-static.xcframework.zip
Downloading binary artifact .../xcframework/sherpa-onnx-v2.0.0-ios-static.xcframework.zip
Downloading binary artifact .../xcframework/sherpa-onnx-v2.0.0-macos-shared.xcframework.zip
Downloading binary artifact .../xcframework/sherpa-onnx-v2.0.0-ios-shared.xcframework.zip
Downloading binary artifact .../onnxruntime-libs/releases/download/v1.28.2/onnxruntime-macos-static-xcframework-1.28.2.xcframework.zip
Downloading binary artifact .../v1.28.2/onnxruntime-ios-static-xcframework-1.28.2.xcframework.zip
Downloading binary artifact .../v1.28.2/onnxruntime-macos-shared-xcframework-1.28.2.xcframework.zip
Downloading binary artifact .../v1.28.2/onnxruntime-ios-shared-xcframework-1.28.2.xcframework.zip
Downloading binary artifact .../v1.28.2/onnxruntime-visionos-static-xcframework-1.28.2.xcframework.zip
Downloading binary artifact .../v1.28.2/onnxruntime-visionos-shared-xcframework-1.28.2.xcframework.zip
Downloading binary artifact https://github.com/soniqo/speech-core/releases/download/v0.0.14/SpeechCore.xcframework.zip
Downloading binary artifact https://github.com/FluidInference/text-processing-rs/releases/download/v0.3.1/NemoTextProcessing.xcframework.zip
```

Обратите внимание: SPM тянет **все** платформенные срезы, объявленные в манифестах (включая iOS и
visionOS ORT и shared-варианты sherpa), хотя для macOS-бинаря нужен один. Это ~10 архивов на
первом резолве; для офлайн-сборки их надо пре-кешировать.

Статус `swift build`: **ЗАВЕРШЁН УСПЕШНО** — все четыре движка линкуются и работают в одном бинаре
(см. ниже, §«Результат линковки»). Разрешение зависимостей — EXIT=0; сборка — EXIT=0 за 1004 с.

**Конфликтов версий нет.** Разбор по названным в задании библиотекам:

| Библиотека | Кто требует | Резолв | Конфликт |
|---|---|---|---|
| swift-transformers | speech-swift `from 1.1.6`; WhisperKit — **не требует** (свой форк в `Sources/ArgmaxCore/External`) | 1.3.5 | нет |
| mlx-swift | speech-swift `from 0.30.0`; mlx-swift-lm 3.31.4 пинит `upToNextMinor(0.31.4)` | 0.31.6 | нет (пересечение) |
| mlx-swift-lm | speech-swift `exact 3.31.4` | 3.31.4 | нет |
| swift-argument-parser | speech-swift `from 1.5.0`; WhisperKit `from 1.3.0` | 1.8.2 | нет |
| hummingbird | только speech-swift `2.5.0..<2.17.0` | 2.16.0 | нет |
| hummingbird-websocket | только speech-swift `2.6.0..<2.7.0` | 2.6.0 | нет |
| swift-websocket | только speech-swift `1.5.0..<1.6.0` | 1.5.0 | нет |
| onnxruntime | только sherpa-onnx `exact 1.28.2` | 1.28.2 | нет |
| Vapor | WhisperKit — **только при `isServerEnabled()`** (переменная окружения), в обычном резолве не тянется | — | нет |

Минимальные платформы и tools:

| Пакет | tools | min macOS | min iOS |
|---|---|---|---|
| speech-swift (`Qwen3Speech`) | 5.10 | 15.0 | 18.0 |
| FluidAudio | **6.0** | 14 | 17 |
| argmax-oss-swift | 5.10 | 13 | 16 |
| sherpa-onnx | 5.9 | 10.15 | 15 |

→ итоговый минимум для общего бинаря: **macOS 15.0, tools-version ≥ 6.0**.

### Rust: `sherpa-onnx` + `ort`

Каталог: `/tmp/srr/probe/V4/rustprobe` (вариант `load-dynamic`) и `/tmp/srr/probe/V4/rustprobe2`
(вариант по умолчанию).

```
$ cargo 1.98.1 / rustc 1.98.1

# вариант A: ort с load-dynamic
sherpa-onnx = "2.0.0"
ort = { version = "=2.0.0-rc.15", default-features = false, features = ["std","ndarray","load-dynamic"] }
$ cargo build
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 1m 56s     # EXIT=0, 129 crates
```

```
# вариант B: ort по умолчанию (download-binaries)
sherpa-onnx = "2.0.0"
ort = "=2.0.0-rc.15"
$ cargo build
   ld: duplicate symbol 'vtable for onnx::ShardingSpecProto' in:
        target/debug/deps/libort_sys-1505492db4418da4.rlib[1189](onnx-ml.pb.cc.o)
        target/debug/deps/libsherpa_onnx_sys-cc37335e048373c1.rlib[1057](onnx-ml.pb.cc.o)
   ld: duplicate symbol 'google::protobuf::internal::ArenaStringPtr::Destroy()' in:
        libort_sys-...rlib[1252](arenastring.cc.o) / libsherpa_onnx_sys-...rlib[1062](arenastring.cc.o)
   ld: duplicate symbol 'onnxruntime::Node::ModelPath() const' in:
        libort_sys-...rlib[594](graph.cc.o) / libsherpa_onnx_sys-...rlib[796](graph.cc.o)
   ... (десятки аналогичных пар: onnx-ml.pb.cc.o, arenastring.cc.o, message_lite.cc.o,
        parse_context.cc.o, graph.cc.o, inference_session.cc.o)
cargo build: 0 errors, 1 warnings (1 crates)   EXIT=0
```

Бинарь собрался (26.9 МБ) и **работает**:

```
$ ./target/debug/rustprobe2
sherpa: 2.0.0 (git Fri Oct 23 12:32:33 2026)
sherpa ORT: 1.31.0
```

`nm` показывает один выживший символ `_OrtGetApiBase` (линкер схлопнул дубликаты):

```
$ nm target/debug/rustprobe2 | grep OrtGetApiBase
00000001000cfb2c T _OrtGetApiBase
00000001001173b8 t __ZN11onnxruntime16ProviderHostImpl13OrtGetApiBaseEv
```

Причина совместимости: обе стороны несут **одну и ту же** ORT 1.31.0.

```
$ cat target/debug/build/ort-sys-*/output | grep -E "prebuilt|downloading"
[ort-sys] [DEBUG] Using prebuilt binaries
[ort-sys] [DEBUG] downloading from 'https://cdn.pyke.io/0/pyke:ort-rs/ms@1.31.0/aarch64-apple-darwin+coreml.tar.lzma2'
cargo:rustc-link-search=native=/Users/antony/Library/Caches/ort.pyke.io/dfbin/aarch64-apple-darwin/4033efca...
```

```
$ ls target/sherpa-onnx-prebuilt/sherpa-onnx-v2.0.0-osx-arm64-static-lib/lib/
libkaldi-decoder-core.a libkaldi-native-fbank-core.a libkissfft-float.a libonnxruntime.a
libsherpa-onnx-c-api.a libsherpa-onnx-core.a libsherpa-onnx-cxx-api.a libsherpa-onnx-fst.a
libsherpa-onnx-fstfar.a libsherpa-onnx-kaldifst-core.a libsherpa-onnx-portaudio_static.a
libssentencepiece_core.a libutf8proc.a
```

**Важное расхождение по каналам поставки ORT:**

| Канал | Версия ORT | Как получена |
|---|---|---|
| sherpa-onnx **Swift SPM** | **1.28.2** | `csukuangfj/onnxruntime-libs` exact 1.28.2 → `onnxruntime-macos-shared-xcframework-1.28.2` |
| sherpa-onnx **Rust crate** 2.0.0 | **1.31.0** | статическая сборка из релиза `sherpa-onnx-v2.0.0-osx-arm64-static-lib` |
| `ort` 2.0.0-rc.15 (default) | **1.31.0** | prebuilt `cdn.pyke.io/.../ms@1.31.0` |

→ Внутри одного процесса нельзя смешивать Swift-SPM-ветку sherpa и Rust-ветку sherpa/ort без
сознательного решения: 1.28.2 против 1.31.0 — это два разных ABI-набора ORT (`OrtGetApiBase`
возвращает структуру с полями до определённой версии; код, собранный против 1.31, ожидает поля,
которых в 1.28.2 нет).

### Результат линковки (Swift)

```
$ cd /tmp/srr/probe/V4/pathdeps && swift build
... (warnings только в исходниках speech-swift: vDSP inout, неиспользуемая centerFreq)
ok (build complete)
EXIT=0                                    # 1004 с (холодная сборка MLX-графа)
```

```
$ ls -la .build/debug/V4ProbePath
-rwxr-xr-x  125.1M  .build/debug/V4ProbePath

$ ./.build/debug/V4ProbePath
V4ProbePath: all four engines linked in one binary
sherpa-onnx 2.0.0, onnxruntime 1.28.2
EXIT=0
```

**Вывод: один процесс, один бинарь — работает.** Qwen3ASR + NemotronStreamingASR + SpeechVAD +
AudioCommon (speech-swift/MLX) + FluidAudio (CoreML) + WhisperKit + sherpa-onnx (ORT 1.28.2)
сосуществуют без конфликтов символов и без общих глобальных состояний на этапе линковки.

Загружаемые библиотеки — **только системные фреймворки** (внешних dylib нет, всё статически):

```
$ otool -L .build/debug/V4ProbePath
/System/Library/Frameworks/Foundation.framework/...
/System/Library/Frameworks/Metal.framework/...
/System/Library/Frameworks/Accelerate.framework/...
/System/Library/Frameworks/AVFAudio.framework/...
/System/Library/Frameworks/AVFoundation.framework/...
/System/Library/Frameworks/CoreML.framework/...
/System/Library/Frameworks/CryptoKit.framework/...
/System/Library/Frameworks/NaturalLanguage.framework/...
/System/Library/Frameworks/Network.framework/...
/usr/lib/libc++.1.dylib, /usr/lib/libobjc.A.dylib, /usr/lib/libSystem.B.dylib
```

Символ ORT ровно один (второй — внутренний C++-метод, не точка входа C API):

```
$ nm .build/debug/V4ProbePath | grep OrtGetApiBase
000000010308c284 T _OrtGetApiBase                                   # единственная точка входа C API
00000001030ae5a8 t __ZN11onnxruntime16ProviderHostImpl13OrtGetApiBaseEv
```

→ дублирования ORT в Swift-бинаре **нет** (в отличие от Rust-варианта с `ort`), потому что ORT
приходит из одного источника — `csukuangfj/onnxruntime-libs` 1.28.2 через sherpa-onnx.
`SpeechCore` его не добавляет (проверено `nm` по её `libspeech_core.a`, 0 совпадений).

Замечания по сборке:
- 125.1 МБ — **debug**-бинарь; release с `-O`/dead-strip будет существенно меньше, но статические
  ORT + MLX-метал-кернелы остаются основным вкладом.
- Предупреждения компилятора — только внутри speech-swift (`Sources/SpeechVAD/MelFeatureExtractor.swift:222`
  — `vDSP_vsub` с inout-выражением `&melSpec + base`, потенциально небезопасный временный указатель;
  `Sources/Qwen3ASR/AudioPreprocessing.swift:128` — неиспользуемая `centerFreq`). Это баги апстрима,
  не нашей интеграции, но первый — реальный риск порчи памяти в VAD-препроцессинге.

### Вывод по процессам/бинарям

- **Swift-бинарь со всеми Apple-движками (speech-swift + FluidAudio + WhisperKit) — можно.** Пересечений
  символов нет: FluidAudio и WhisperKit не тянут ORT, MLX/CoreML — системные фреймворки.
  **Проверено сборкой и запуском** (§«Результат линковки»).
- **sherpa-onnx в том же Swift-бинаре — можно** (единственный владелец ORT 1.28.2 в этом процессе;
  `nm` показывает одну точку входа). Риск: рост размера/RSS на статические xcframework'и и
  невозможность обновить ORT независимо от sherpa. **Проверено сборкой.**
- **Rust-бинарь: sherpa-onnx + ort — технически линкуется**, но только при совпадении версий ORT и с
  потоком `duplicate symbol`-предупреждений. Рабочая схема — `ort` с `load-dynamic` (проверено, чистая
  сборка) **либо** не использовать `ort` вообще, оставив ORT только внутри sherpa.
- **Смешивать Swift-ветку sherpa (ORT 1.28.2) и Rust-ветку sherpa/ort (ORT 1.31.0) в одном процессе —
  нельзя.** Отсюда практическое правило для архитектуры: worker-onnx реализуется целиком на одной
  стороне (Swift/SPM **или** Rust/crate), а не «C-API sherpa, залинкованное и там, и там».
- **Рекомендация по умолчанию:** worker-mlx + worker-coreml как один Swift-процесс (доказано), а
  worker-onnx — отдельный процесс на Swift (тот же бинарь, другой режим) **или** отдельный Rust-процесс
  с `sherpa-onnx` crate. Смешение Swift-sherpa и Rust-sherpa в одном адресном пространстве запрещено
  из-за ORT 1.28.2 vs 1.31.0.

## Лицензии

Все вердикты ниже — по первоисточникам (HF API `cardData`, текст LICENSE в git, текст соглашения),
а не по пересказу wave1.

### TEN VAD — дословная цитата

Источник: https://github.com/TEN-framework/ten-vad/blob/d68ab71c8d5fbd8f98e97ae7c9986af76b5d72b1/LICENSE

> Open Source License
>
> The ten-vad is licensed pursuant to the Apache License v2.0(provided for your convenience below), with the following additional conditions. You may reproduce, prepare Derivative Works of, publicly display, publicly perform, sublicense, distribute, or otherwise make available (together, "Deploy") the ten-vad, for commercial or non-commercial purposes, provided that you agree to abide by the terms below:
>
> 1. You may not Deploy the ten-vad in a way that competes with Agora's offerings and/or that allows others to compete with Agora's offerings, including without limitation enabling any third party to develop or deploy Applications.
> 2. You may Deploy the ten-vad solely to create and enable deployment of your Application(s) solely for your benefit and the benefit of your direct End Users. If you prefer, you may include the following notice in the documentation of your Application(s): "Powered by ten-vad".
> 3. Derivative Works of the ten-vad remain subject to this Open Source License.
> 4. "End Users" shall mean the end-users of your Application(s) who access the ten-vad solely to the extent necessary to access and use the Application(s) you create or deploy using ten-vad.
> 5. "Application(s)" shall mean your software programs designed or developed by using the ten-vad or where deployment is enabled by the ten-vad.
> 6. Note that the project contains derived code from other open source project with BSD-3-Clause and BSD-2-Clause license, refer to the "NOTICES" file in the root directory for detailed information.
>
> Copyright © 2025 Agora

Оценка (совпадает с K, усилена): runtime, который отдаёт VAD сторонним приложениям, попадает под п.1
(«enabling any third party to develop or deploy Applications»). П.3 означает, что даже наша ONNX→CoreML
конверсия и обёртка наследуют эти условия. Веса: HF `TEN-framework/ten-vad` → `license: None`
(декларации нет вовсе), при этом sherpa-onnx **раздаёт** `ten-vad.onnx` (332 211 байт, HTTP 200) в
своём Apache-2.0-релизе — то есть «взять у sherpa» не снимает вопрос, а добавляет вопрос к самому sherpa.

### Sortformer (три артефакта, три лицензии)

| Артефакт | Лицензия (HF API) | Коммерция | Замечание |
|---|---|---|---|
| `nvidia/diar_sortformer_4spk-v1` | `cc-by-nc-4.0` | нет | исключить из резолвера |
| `nvidia/diar_streaming_sortformer_4spk-v2` | `cc-by-4.0` | да (атрибуция) | |
| `nvidia/diar_streaming_sortformer_4spk-v2.1` | `other` / `nvidia-open-model-license` | да (условия OML) | revocable, notice-строка |
| `FluidInference/diar-streaming-sortformer-coreml` (то, что реально качает FluidAudio) | `cc-by-4.0`, `base_model: nvidia/diar_streaming_sortformer_4spk-v2.1` | да (атрибуция) | **не** NVIDIA OML, как утверждает README FluidAudio; в репо **нет** NOTICE-файла (`siblings` = README + mlmodelc/mlpackage) |

Вывод: пин на конкретный HF-репозиторий + revision, а не на «sortformer». Фактическая лицензия
конверсии (CC-BY-4.0) мягче заявленной в README (NVIDIA OML) — расхождение в нашу пользу, но
непредсказуемое: конвертер может переподписать артефакт.

### Nemotron

- `nvidia/nemotron-3.5-asr-streaming-0.6b`: `license: other`, `license_name: openmdw-1.1`,
  `license_link: https://openmdw.ai/license/1-1/`, **`gated: false`**, 1.09M загрузок.
  Расхождение с `speech-swift/docs/models/nemotron-asr-streaming.md#L10` («(gated)») — **подтверждено**:
  в доках speech-swift ошибка.
- `nvidia/Nemotron-3-Diarization`: `license: openmdw-1.1`, `gated: false`, 116 775 загрузок,
  карточка: «This model is ready for commercial or non-commercial use», 8 спикеров, streaming+offline.
- **Найдено новое**: `aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit` и `-MLX-bf16` —
  `license: other`, `license_name: nvidia-software-and-model-evaluation-license`
  (https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-and-model-evaluation-license/).
  Дословно из соглашения:
  > «All the foregoing (a) through (c) are only for internal test and evaluation purposes, without use in production and in systems with NVIDIA GPUs ("Purpose")»
  > «You may not: … Sell, rent, sublicense, transfer, distribute or otherwise make available to others … any portion of the Licensed Materials or Derivatives …»
  > «Use the Licensed Materials or Derivatives for the purpose of developing competing products or technologies or assist a third party in such activities»
  Это **не** OpenMDW: production запрещён, дистрибуция запрещена, требуется NVIDIA GPU. При этом
  `-5bit` и `-8bit` того же семейства — `openmdw-1.1`. speech-swift по умолчанию использует
  `-5bit`/`-8bit` (`Sources/NemotronStreamingASR/NemotronMLXConfiguration.swift#L10-L12`), т.е. сейчас
  всё корректно, но 4bit/bf16-варианты в каталог попасть не должны. Это ровно тот случай «лицензия
  привязана к артефакту, а не к семейству», о котором предупреждал K.

### OpenMDW-1.1 — что именно требует

Текст https://openmdw.ai/license/1-1/ (дословно):

> «Subject to your compliance with this agreement, permission is hereby granted, free of charge, to deal in the Model Materials without restriction, including under all copyright, patent, database, and trade secret rights included or embodied therein.»

> «If you distribute any portion of the Model Materials, you shall retain in your distribution (1) a copy of this agreement, and (2) all copyright notices and other notices of origin included in the Model Materials that are applicable to your distribution.»

> «If you file, maintain, or voluntarily participate in a lawsuit against any person or entity asserting that the Model Materials directly or indirectly infringe any patent or copyright, then all rights and grants made to you hereunder are terminated, unless that lawsuit was in response to a corresponding lawsuit first brought against you.»

> «This agreement does not impose any restrictions or obligations with respect to any use, modification, or sharing of any outputs generated by using the Model Materials.»

> «Copyright The Linux Foundation and its contributors.»

Итого обязательства: (1) при распространении — копия соглашения; (2) сохранить все copyright/notices;
(3) patent-retaliation (не судиться первым); (4) ограничений на коммерцию и на выходы нет. Никаких
gate-условий, никаких «Trustworthy AI»-обязательств (в отличие от NVIDIA OML).

### Voxtral Mini 4B Realtime

`mistralai/Voxtral-Mini-4B-Realtime-2602`: `license: apache-2.0`, `gated: false`, 2 041 370 загрузок,
13 языков (ru в списке), карточка: «This model is released in BF16 under the Apache-2 license».
Отдельно от `Voxtral-Mini-3B-2507` (тоже apache-2.0). Оба — разные артефакты; в K был только 3B.

### Smart Turn v3

`pipecat-ai/smart-turn-v3` → `bsd-2-clause`, `gated: false`; код `pipecat-ai/smart-turn` →
`BSD 2-Clause License, Copyright (c) 2024–2025, Daily`. Карточка: 8M параметров, backbone — Whisper Tiny
encoder, head — линейный классификатор, чекпойнт 8 МБ ONNX int8 / 32 МБ unquantized. Лицензия BSD-2
не содержит патентного гранта — для EOT-классификатора риск минимален.

### FireRedVAD

Код `FireRedTeam/FireRedVAD` → GitHub API `license.spdx_id = Apache-2.0` (555 звёзд, не archived).
Веса `FireRedTeam/FireRedVAD` на HF → `apache-2.0`, `gated: false`. В K этой модели в таблице не было.
Конверсия `aufklarer/FireRedVAD-CoreML` → **`mit`** при апстриме Apache-2.0 → потеря notice-обязательств
Apache-2.0 (см. §aufklarer).

### GigaAM v3

`ai-sage/GigaAM-v3` → `license: mit`, `gated: false`, языки `ru`, `en`. Карточка: Conformer, 220–240M,
варианты `ssl` / `ctc` / `rnnt` / `e2e_ctc` / `e2e_rnnt`, обучение на 700 000 ч русской речи.
Апстрим-код `salute-developers/GigaAM` → MIT («Copyright (c) 2024 GigaChat Team»).
**Официальной ONNX-конверсии в sherpa нет**: `sherpa-onnx/scripts/nemo/GigaAM/export-onnx-ctc-v3-punct.py`
— это скрипт экспорта (скачивает `salute-developers/GigaAM/LICENSE`), а готовые веса в HF под тегом
`sherpa-onnx-gigaam` — сторонние (`Smirnov75/GigaAM-v3-sherpa-onnx`, `BahamutRU/...`,
`Alexxerm/gigaam-v3-e2e-rnnt-sherpa-onnx`, `fussraider/...` — все `mit`, но не сопровождаются апстримом).

### Qwen3-ASR

`Qwen/Qwen3-ASR-0.6B` и `Qwen/Qwen3-ASR-1.7B` → `apache-2.0`, `gated: false`. Конверсии speech-swift
(`aufklarer/Qwen3-ASR-*`): `0.6B-MLX-4bit`, `0.6B-MLX-8bit`, `1.7B-MLX-4bit`, `1.7B-MLX-8bit`,
`Qwen3-ASR-CoreML` → `apache-2.0`; **`Qwen3-ASR-0.6B-MLX-5bit` и `Qwen3-ASR-1.7B-MLX-5bit` — без
тега лицензии** (проверено по списку `author=aufklarer`).

### pyannote community-1 (gated)

`pyannote/speaker-diarization-community-1` → `license: cc-by-4.0`, `gated: auto`, 5 011 165 загрузок.
`extra_gated_prompt` дословно:

> «Your input helps us strengthen the pyannote community and improve our open-source offerings. This pipeline is released under the CC-BY-4.0 license and will always remain freely accessible. By providing your details, you agree that we may email you occasionally with important news about pyannote models, invitations to try premium pipelines, and information about specific services designed for researchers and professionals like you.»

Поля: `Company/university` (text) и `Use case` (select: meeting / conversation / ccaas / agent /
dubbing / training / other). Для `pyannote/segmentation-3.0` (MIT, `gated: auto`) prompt другой:
«Though this model uses MIT license and will always remain open-source, we will occasionnally email you
about premium models and paid services around pyannote»; поля `Company/university`, `Website`.

Практика: лицензия разрешает редистрибуцию, но gate — договорное условие доступа. **Существует
не-gated зеркало**: `aufklarer/Pyannote-Community-1-CoreML` → `cc-by-4.0`, `gated: false`,
`base_model: pyannote/speaker-diarization-community-1`; и `aufklarer/Pyannote-Segmentation-MLX` →
`mit`, `gated: false` (апстрим `segmentation-3.0` — gated). То есть gate обходится через конверсии
третьих лиц — юридическая чистота такого обхода не гарантирована (это ровно риск №5 из K, теперь с
конкретными URL).

### WeSpeaker веса

| Репозиторий | Лицензия | Gated |
|---|---|---|
| `pyannote/wespeaker-voxceleb-resnet34-LM` | `cc-by-4.0` | нет (5 885 764 загрузок) |
| `Wespeaker/wespeaker-voxceleb-resnet34-LM` | `cc-by-4.0` | нет |
| `Wespeaker/wespeaker-voxceleb-resnet34` | **`apache-2.0`** | нет |
| `Wespeaker/wespeaker-voxceleb-resnet152-LM`, `-resnet221-LM`, `-campplus(-LM)`, `-ecapa-tdnn*`, `-cnceleb-resnet34` | `apache-2.0` | нет |
| `Wespeaker/wespeaker-voxceleb-resnet293-LM`, `-ecapa-tdnn512-LM` | `cc-by-4.0` | нет |
| `Wespeaker/wespeaker-voxlink2-samresnet34_ft` | **нет тега лицензии** | нет |

→ Утверждение K «веса — cc-by-4.0» верно только для `-LM`-вариантов ResNet34/293 и ECAPA512;
значительная часть org `Wespeaker` — Apache-2.0, один репо без лицензии. Для runtime: брать
`pyannote/wespeaker-voxceleb-resnet34-LM` (CC-BY-4.0, как у FluidAudio) или apache-2.0-варианты,
но фиксировать конкретный репозиторий.

### Silero VAD v6

`snakers4/silero-vad` → LICENSE «MIT License, Copyright (c) 2020-present Silero Team».
Релизы-теги: `v6.0`, `v6.1`, `v6.2`, `v6.2.1`, `v6.2.2`, `v6.2.3` (последний). Отдельного HF-репо у
`snakers4` нет; вендоренные копии — `FluidInference/silero-vad-coreml` (`mit`, 47 361 загрузок),
`aufklarer/Silero-VAD-v6.2.1-CoreML` и `-MLX` (`mit`), `aufklarer/Silero-VAD-v5-*` (`mit`).
Плюс `sherpa-onnx` релиз `asr-models/silero_vad.onnx` (643 854 байта, HTTP 200).

### FluidAudio CoreML-конверсии: README против HF

- README `FluidAudio` #L53: «**All models are publicly available on HuggingFace — converted and
  optimized by our team; permissive licenses.**»
- README #L449 (Sortformer): «Licensed under NVIDIA Open Model License.»
- Реальность по каталогу `FluidInference/*` (115 репозиториев, HF API):
  59 `apache-2.0`, 18 `cc-by-4.0`, 15 `other`, 12 `mit`, 7 **без лицензии**, 2 `openmdw-1.1`,
  1 `bigscience-openrail-m`, 1 **`cc-by-nc-4.0`**.
- Конкретные расхождения:
  - `FluidInference/diar-streaming-sortformer-coreml` = `cc-by-4.0` (README говорит NVIDIA OML);
  - `FluidInference/nemotron-speech-streaming-en-0.6b-coreml` = `nvidia-open-model-license` — не «permissive»;
  - `FluidInference/parakeet-realtime-eou-120m-coreml` = `nvidia-open-model-license`;
  - `FluidInference/parakeet-ctc-0.6b-coreml` = **лицензия не объявлена**, а это модель, которую
    FluidAudio скачивает (`Sources/FluidAudio/ModelNames.swift#L30`);
  - `FluidInference/campplus-coreml`, `sensevoice-small-coreml`, `fsmn-vad-coreml`,
    `paraformer-large-zh-coreml` = `other` с `license_name: *-upstream` (перепубликация лицензии
    апстрима без текста в карточке — надо открывать upstream);
  - `FluidInference/decision-modernbert-base-coreml` = `cc-by-nc-4.0` (NC внутри «permissive» каталога);
  - `FluidInference/speaker-diarization-coreml` = `other` / `scoped-cc-by-4.0` + NOTICE.md (85 файлов).
- Вывод: нельзя полагаться ни на README FluidAudio, ни на «permissive»-ярлык; нужен собственный
  лицензионный реестр по артефактам с пин-ревизией.

### aufklarer/* — конверсии speech-swift

166 репозиториев. Распределение: 79 `apache-2.0`, 20 `mit`, 17 `other`, 13 `cc-by-nc-4.0`,
9 `cc-by-4.0`, 9 **без лицензии**, 8 `openmdw-1.1`, 8 `gemma`, 2 `openrail`, 1 `bsd-2-clause`.

Проблемы (главное открытие волны):

1. **Смена лицензии на производных**:
   - `aufklarer/WeSpeaker-ResNet34-LM-MLX` → `license: mit`, при `base_model:
     pyannote/wespeaker-voxceleb-resnet34-LM` = **cc-by-4.0**. MIT-перелицензирование CC-BY-4.0
     артефакта снимает обязательство атрибуции и «indicate changes».
   - `aufklarer/WeSpeaker-ResNet34-LM-CoreML` → то же (`mit`).
   - `aufklarer/FireRedVAD-CoreML` → `mit` при апстриме `apache-2.0` (Apache-2.0 требует сохранять
     NOTICE и указывать изменения).
   - `aufklarer/Pyannote-Segmentation-MLX` → `mit` при апстриме `mit` (ок), но апстрим gated.
   Это те самые артефакты, которые speech-swift использует по умолчанию:
   `Sources/SpeechVAD/WeSpeaker.swift#L57-L60` → `aufklarer/WeSpeaker-ResNet34-LM-MLX` и `-CoreML`.
2. **Отсутствие лицензии**: `aufklarer/Parakeet-TDT-v3-CoreML-INT8-30s`,
   `aufklarer/Qwen3-ASR-0.6B-MLX-5bit`, `aufklarer/Qwen3-ASR-1.7B-MLX-5bit`,
   `aufklarer/Hibiki-Zero-3B-MLX-{4,8}bit`, `aufklarer/HTDemucs-FT-MLX` и др. (9 репо).
3. **NC**: 13 репо `cc-by-nc-4.0` (PersonaPlex, MAGNeT, F5-TTS, FlashSR, AudioSR, Voxtral-4B-TTS…).
4. **Самая опасная**: `Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit`/`-bf16` под
   `nvidia-software-and-model-evaluation-license` (см. §Nemotron).

### csukuangfj/* и k2-fsa/*

- `csukuangfj` (HF, `limit=100`): 55 репо **без тега лицензии**, 42 `apache-2.0`, 2 `mit`,
  1 `cc-by-4.0`. Примеры без лицензии: `csukuangfj/sherpa-onnx-whisper-distil-small.en`,
  `csukuangfj/vits-piper-tr_TR-fahrettin-medium`, все `icefall-asr-librispeech-*`.
- `k2-fsa/*` (по K): часть без `cardData.license`, часть `apache-2.0`.
- Чем рискуем: отсутствие декларации = отсутствие явного разрешения на редистрибуцию (по умолчанию
  действует исключительное право автора). Для runtime это означает: такие артефакты — только
  `fetch`-класс (скачивание у источника с показом «лицензия не заявлена»), никогда не `mirror`.
  Отдельно: сама сборка sherpa-onnx (Apache-2.0) не даёт прав на веса, которые она скачивает в
  примерах (`ten-vad.onnx`, `silero_vad.onnx`, `csukuangfj/*`-модели).

### NVIDIA Open Model License — проверено по тексту

Текст https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/ (дословно):

> «NVIDIA models released under this Agreement are intended to be used permissively and enable the further development of AI technologies. Subject to the terms of this Agreement, NVIDIA confirms that: Models are commercially usable. You are free to create and distribute Derivative Models. NVIDIA does not claim ownership to any outputs generated using the Models or Derivative Models.»

> «NVIDIA hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, revocable (as stated in Section 2.1) license…»

> «If You bypass, disable, reduce the efficacy of, or circumvent any technical limitation, safety guardrail or associated safety guardrail hyperparameter, encryption, security, digital rights management, or authentication mechanism (collectively "Guardrail") contained in the Model without a substantially similar Guard[rail]…»

> «NVIDIA may update this Agreement to comply with legal and regulatory requirements at any time and You agree to either comply with any updated license or cease Your copying, use, and distribution of the Model and any Derivative Model.»

> «If you distribute the Model, You must give any other recipients of the Model a copy of this Agreement and include the following attribution notice within a "Notice" text file with such copies: "Licensed by NVIDIA Corporation under the NVIDIA Open Model License"»

> «Use of the Models under the Agreement must be consistent with NVIDIA's Trustworthy AI terms found at https://www.nvidia.com/en-us/agreements/trustworthy-ai/terms/»

→ Все четыре обязательства, названные в K (копия соглашения + notice-строка, Trustworthy AI,
запрет обхода Guardrail, revocable/обновляемость), **подтверждены дословно**. Практическое следствие
для каталога: нужен механизм отзыва/обновления записи (license_sha256 + `license_changed`), иначе
после апдейта соглашения мы продолжим раздавать веса.

### Лицензии бинарных артефактов, которые тянет resolve

Это новые зависимости, которых не было в волне 1, и именно они определяют, можно ли
распространять собранный бинарь:

| Артефакт | Репозиторий | Лицензия | Доказательство |
|---|---|---|---|
| `SpeechCore.xcframework` (v0.0.14) | `soniqo/speech-core` | **Apache-2.0** | https://raw.githubusercontent.com/soniqo/speech-core/main/LICENSE → «Apache License Version 2.0»; GitHub API `license.spdx_id = Apache-2.0`; описание: «On-device VAD / streaming STT / TTS / diarization in C++17 (ONNX + LiteRT)…» |
| `NemoTextProcessing.xcframework` (v0.3.1) | `FluidInference/text-processing-rs` | **Apache-2.0** | `Cargo.toml` → `license = "Apache-2.0"`; `LICENSE` → Apache 2.0; GitHub API `license = None` (в API не заполнено) |
| ORT-срезы | `csukuangfj/onnxruntime-libs` | **файла LICENSE в репозитории нет** (HTTP 404 на `LICENSE`) | репозиторий лишь переупаковывает официальные сборки Microsoft ONNX Runtime (MIT); для NOTICE надо брать MIT-текст из `microsoft/onnxruntime` |
| `sherpa-onnx-*.xcframework` | `k2-fsa/sherpa-onnx` | Apache-2.0 | `LICENSE` репозитория |

Проверка содержимого `SpeechCore.xcframework` (скачан и распакован, `/tmp/srr/probe/V4/speechcore`):

```
$ unzip -l speechcore.zip | head
  2455600  SpeechCore.xcframework/macos-arm64_x86_64/libspeech_core.a
    11426  SpeechCore.xcframework/macos-arm64_x86_64/Headers/speech_core_c.h
$ nm -gU .../libspeech_core.a | grep -c OrtGetApiBase
0
```

→ внутри `SpeechCore` **нет** символов ONNX Runtime: он подключает бэкенды (ONNX/LiteRT) на стороне
хоста. Значит второй ORT он в процесс не приносит — это снимает одно из подозрений о дубликатах
символов в Swift-бинаре.

### Разрешение зависимостей: что осталось непроверенным по Rust

`ort` 2.0.0-rc.15 объявляет `rust-version = "1.92"` и лицензию `MIT OR Apache-2.0`;
`sherpa-onnx` 2.0.0 (crate) — `Apache-2.0`, опубликован 2026-10-23. Локально `rustc 1.98.1` — выше
минимальной, конфликта MSRV нет.

## Неизвестные / что измерить

1. **ЗАКРЫТО (было: `swift build` не завершён).** Сборка EXIT=0 за 1004 с, бинарь 125.1 МБ,
   запуск EXIT=0, `sherpa-onnx 2.0.0 / onnxruntime 1.28.2`. Осталось измерить (не блокирует вывод):
   размер **release**-бинаря с `-O`/dead-strip и фактический RSS процесса после инициализации всех
   четырёх движков.
2. **Не проверен полный текст NVIDIA Software-and-Model-Evaluation License на предмет квантизации
   как «обхода Guardrail»** — читались grant + restrictions (§Nemotron). Текст NVIDIA Open Model
   License прочитан полностью в этой волне (§NVIDIA OML). Для каталога остаётся вопрос: считается ли
   int8/ANE-квантизация «circumvent any technical limitation… Guardrail».
3. **Не проверены лицензии** `mlx-swift-lm`, `modelcontextprotocol/swift-sdk`, `hummingbird-websocket`,
   `swift-websocket`, `xgrammar`, `swift-huggingface`, `swift-jinja`, `yyjson`, `compress-nio`.
   Два бинарных артефакта проверены: `soniqo/speech-core` = Apache-2.0, `FluidInference/text-processing-rs`
   = Apache-2.0; у `csukuangfj/onnxruntime-libs` файла LICENSE нет вообще (переупаковка MIT-сборок
   Microsoft) — это отдельная задача для NOTICE-генератора.
4. **Не проверено**: содержит ли `SpeechCore.xcframework` сторонние веса. Проверено, что внутри нет
   символов ONNX Runtime (`nm | grep -c OrtGetApiBase` → 0) и что репозиторий Apache-2.0; но полный
   состав статической библиотеки (2.4 МБ, `libspeech_core.a`) на предмет вендоренных BSD-компонентов
   не вычитывался.
5. **Не измерено**: RSS одного процесса с четырьмя движками (MLX + CoreML + ORT) и стоимость
   `dlopen`/инициализации каждого; сколько времени занимает холодная загрузка (для решения
   «один бинарь vs воркеры»).
6. **Не проверено**: `parakeet-ctc-0.6b-coreml` — реально ли у апстрима (NVIDIA) лицензия cc-by-4.0,
   которую конвертер «забыл» объявить; и есть ли `NOTICE` внутри `speaker-diarization-coreml` с
   перечнем артефактов (K утверждает, что есть — файл существует, но текст не вычитан в этой волне).
7. **Не проверено**: `aufklarer/Pyannote-Community-1-CoreML` — законность зеркала gated-модели
   (юридический вопрос, не технический).

## Источники

Манифесты и код (пин-ревизии):
- speech-swift @cbad3be — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Package.swift (L1-L10, L242-L268), `Sources/SpeechVAD/WeSpeaker.swift#L57-L60`, `Sources/NemotronStreamingASR/NemotronMLXConfiguration.swift#L10-L12`, `docs/models/nemotron-asr-streaming.md#L10`, `README.md#L61-L63`, `README.md#L608-L610`
- FluidAudio @1b0ed50 — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Package.swift#L1-L27, `Sources/FluidAudio/ModelNames.swift#L30,L74`, `README.md#L53,L449,L778-L780`, `Documentation/Models.md#L109`, `ThirdPartyLicenses/`
- argmax-oss-swift @9f99b5c — https://github.com/argmaxinc/argmax-oss-swift/blob/9f99b5cb6a907730543823ad271b82532782a899/Package.swift#L1-L49, `NOTICES#L1-L20`, `LICENSE`
- sherpa-onnx @532f1b5 — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/Package.swift#L1-L19, `swift-api-examples/SherpaOnnx.swift#L1-L6`, `spm-examples/macOS/Package.swift`, `go-api-examples/vad/main.go#L12-L31`, `scripts/nemo/GigaAM/run-ctc-v3-punct.sh`, `cmake/eigen.cmake`
- TEN-framework/ten-vad @d68ab71 — https://github.com/TEN-framework/ten-vad/blob/d68ab71c8d5fbd8f98e97ae7c9986af76b5d72b1/LICENSE
- pipecat-ai/smart-turn @4786657 — https://github.com/pipecat-ai/smart-turn/blob/4786657e242dfe77dd138699ac564ee074a2a543/LICENSE
- snakers4/silero-vad @066409f — https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/LICENSE
- Eigen 5.0.1 — https://gitlab.com/libeigen/eigen/-/raw/5.0.1/COPYING.MPL2
- salute-developers/GigaAM — https://raw.githubusercontent.com/salute-developers/GigaAM/main/LICENSE

HF API (`https://huggingface.co/api/models/<id>`, поле `cardData`):
`nvidia/diar_sortformer_4spk-v1`, `nvidia/diar_streaming_sortformer_4spk-v2`, `nvidia/diar_streaming_sortformer_4spk-v2.1`,
`nvidia/nemotron-3.5-asr-streaming-0.6b`, `nvidia/Nemotron-3-Diarization`,
`mistralai/Voxtral-Mini-3B-2507`, `mistralai/Voxtral-Mini-4B-Realtime-2602`,
`pipecat-ai/smart-turn-v3`, `FireRedTeam/FireRedVAD`, `ai-sage/GigaAM-v3`,
`Qwen/Qwen3-ASR-0.6B`, `Qwen/Qwen3-ASR-1.7B`,
`pyannote/speaker-diarization-community-1`, `pyannote/segmentation-3.0`, `pyannote/wespeaker-voxceleb-resnet34-LM`,
`Wespeaker/*` (16 репо), `TEN-framework/ten-vad`, `argmaxinc/whisperkit-coreml`,
`FluidInference/*` (115 репо), `aufklarer/*` (166 репо).

Соглашения:
- OpenMDW-1.1 — https://openmdw.ai/license/1-1/
- NVIDIA Open Model License — https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
- NVIDIA Software and Model Evaluation License — https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-and-model-evaluation-license/

Бинарные артефакты (лицензии):
- soniqo/speech-core — https://raw.githubusercontent.com/soniqo/speech-core/main/LICENSE ; `SpeechCore.xcframework.zip` v0.0.14 (2 112 822 байта), `libspeech_core.a` 2 455 600 байт
- FluidInference/text-processing-rs — https://raw.githubusercontent.com/FluidInference/text-processing-rs/main/Cargo.toml (`license = "Apache-2.0"`), `LICENSE`
- csukuangfj/onnxruntime-libs — `Package.swift` (пины v1.28.2), файла LICENSE нет (404)

Пробы (локально, `/tmp/srr/probe/V4`):
- `pathdeps/` — SwiftPM-пакет с 4 path-зависимостями; `swift package resolve` → `Package.resolved` (39 пинов)
- `remote/` — тот же граф по remote-URL с `swift package config set-mirror` на локальные клоны
- `rustprobe/` — `sherpa-onnx 2.0.0` + `ort 2.0.0-rc.15` (`load-dynamic`) → сборка EXIT=0
- `rustprobe2/` — `sherpa-onnx 2.0.0` + `ort 2.0.0-rc.15` (default) → `duplicate symbol` + рабочий бинарь,
  `sherpa_onnx::onnxruntime_version()` → `1.31.0`
