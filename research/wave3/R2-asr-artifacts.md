# R2 — артефакты ASR-кандидатов (волна 3)

Дата проверки: **2026-10-28**. Источник: HF Model API (`/api/models/…`, `/tree/…?recursive=true`), карточки моделей, `processor_config.json`. Все id, ревизии, даты и лицензии ниже сверены с API; предположения помечены `[INFERENCE]`. Результатов прогонов и таймингов здесь нет — они в `experiments/corpus/local/kubernetes-dev/`.

## Реестр

| Кандидат | id / ревизия | Лицензия | Русский | Форматы для macOS | Размер | Локально |
|---|---|---|---|---|---|---|
| GigaAM v3 | `ai-sage/GigaAM-v3`, `ec1dc1f0`, 2025-11-19 | MIT | да (`ru,en`); карточка: WER CTC 9.2 / RNNT 8.4 | PyTorch (ветки `ssl/ctc/rnnt/e2e_ctc/e2e_rnnt`); ONNX `istupakov/gigaam-v3-onnx`; CoreML `smkrv/gigaam-v3-e2e-rnnt-coreml`; MLX `kruatech/gigaam-v3-mlx` (0 загрузок) | `.bin` 441.7–448.9 МБ; ONNX int8 224.7–226.4 МБ; CoreML e2e 445.9 МБ | нет |
| GigaAM Multilingual | `ai-sage/GigaAM-Multilingual`, `2f8a5714`, 2026-07-14 | MIT | да (`ru,en,kk,ky,uz`); карточка: FLEURS ru 4.4 (220M) / 3.0 (600M large) | PyTorch (`ssl/ctc/large_ssl/large_ctc`); ONNX `istupakov/gigaam-multilingual-{,large-}ctc-onnx`; MLX `ai-babai/…-mlx{,-int8,-int6,-int4}`; CoreML `voicely/gigaam-multilingual-ctc-coreml`, `vadimsuhanov/gigaam-multilingual-large-ctc-coreml` | ctc 883.2 МБ, large 2341.6 МБ; ONNX int8 224.8 МБ; CoreML ctc 441.7 МБ | нет |
| Nemotron 3.5 ASR streaming multilingual 0.6B | `nvidia/nemotron-3.5-asr-streaming-0.6b`, `ea30d66d`, 2026-09-10 | OpenMDW-1.1 (`license: other` + `license_name`, коммерческое использование разрешено карточкой) | да (`ru` во frontmatter); `processor_config.json`: `"ru": 11`, `"ru-RU": 11`, `auto: 101`; карточка: FLEURS ru 9.17–10.84 % на 80–1120 мс | CoreML `FluidInference/…-CoreML`; ONNX `soniqo/…-ONNX-{FP16,INT8}`; sherpa `csukuangfj2/sherpa-onnx-…-1120ms-int8`; GGUF; MLX | safetensors 2552.1 МБ; один CoreML-тир ≈ 610.3 МБ; soniqo FP16 1285.4 / INT8 755.1 МБ; sherpa int8 682.1 МБ | **да**, `multilingual/2240ms` (1.2 ГБ с `latin/`) |
| Parakeet TDT 0.6b v3 | `nvidia/parakeet-tdt-0.6b-v3`, `541d1f99`, 2026-08-05 | CC-BY-4.0 | да; model-index FLEURS `ru_ru` 5.51 | CoreML `FluidInference/parakeet-tdt-0.6b-v3-coreml`; MLX `mlx-community/…`; ONNX `ysdede/…-onnx`; GGUF | safetensors 2508.3 МБ; CoreML-бандл ≈ 460 МБ | **да, полный** |
| Parakeet Ultra | `moondream/parakeet-ultra`, `73175eb7`, 2026-09-23 | CC-BY-4.0 | да (25 языков); FLEURS ru 5.21 (в той же таблице v3 = 5.91, а карточка v3 даёт 5.51 — два самоотчёта расходятся) | safetensors + CoreML `FluidInference/parakeet-ultra-coreml` (`95eaa59a`, 2026-09-24); ONNX `Olicorne/…-ultra-onnx`; рантайм Photon | CoreML 631.1 МБ (encoder 594.2) | нет |
| Parakeet Redux | `moondream/parakeet-redux`, `2bf12860`, 2026-09-23 | CC-BY-4.0 | да; FLEURS ru 7.91 | только Photon (публичных ONNX/CoreML/GGUF нет) | 177.8 МБ (1.58-битные веса) | нет |
| Qwen3-ASR | `Qwen/Qwen3-ASR-0.6B` (`5eb14417`), `Qwen/Qwen3-ASR-1.7B` (`7278e1e7`), 2026-01-30 | Apache-2.0 | да, 30 языков по таблице карточки (frontmatter языков не содержит) | CoreML `FluidInference/qwen3-asr-0.6b-coreml` (int8 ≈ 0.7 ГБ); MLX `mlx-community/Qwen3-ASR-1.7B-{bf16,8bit}`; GGUF | 0.6B 1876.1 МБ; 1.7B 4702.5 МБ | нет |
| Voxtral Mini 4B Realtime 2602 | `mistralai/Voxtral-Mini-4B-Realtime-2602`, `2769294d`, 2026-03-11 | Apache-2.0 | да (13 языков, `ru` во frontmatter) | vLLM/transformers (бейдж `inference: false`); MLX 4-bit 3.1 ГБ; ONNX-community; GGUF; **CoreML нет** | consolidated 8859.5 МБ | нет |
| Voxtral Mini 3B 2507 / Small 24B 2507 | `Voxtral-Mini-3B-2507` (`3060fe34`), `Voxtral-Small-24B-2507` (`da5b4240`) | Apache-2.0 | **нет** (8 языков: en, fr, de, es, it, pt, nl, hi) | vLLM, transformers; MLX bf16 | 9356.1 / 48 519.9 МБ | нет |

## Локальное состояние

`~/Library/Application Support/FluidAudio/Models`:

- `parakeet-tdt-0.6b-v3/` — полный бандл (Preprocessor + Encoder + JointDecisionv3 + Decoder + вокабы, ≈ 460 МБ), запускается.
- `nemotron-multilingual/` — **достроен 2026-10-28**: `multilingual/2240ms` содержит 22 файла, encoder `weight.bin` (565 336 640 B) сверен с HF; `.partial`/`.etag` удалены; добавлены `metadata.json`, `tokenizer.json`, `joint.mlmodelc`, верхнеуровневые `config.json`/`manifest.json`. Прогон на 12 фрагментах выполнен (см. `experiments/corpus/local/kubernetes-dev/nemotron-run.md`).
- `nemotron-multilingual/latin/2240ms/encoder.mlmodelc/weights/weight.bin` — sha256 `19c623ca…` против HF `2e00be98…`: локальная копия повреждена, не перекачивалась, `latin/` для русского не нужен.

`~/.cache/huggingface/hub`: ни одного ASR-кандидата (только текстовые LLM и pyannote). Повторный прогон GigaAM из пилота потребует загрузки весов заново.

## Выводы

**Запускается сейчас:** Parakeet TDT 0.6b v3 (CoreML, полный бандл) и Nemotron 3.5 multilingual (CoreML, `multilingual/2240ms`, достроен 2026-10-28).

**Требует загрузки:** GigaAM Multilingual CTC CoreML ≈ **442 МБ**; Parakeet Ultra CoreML ≈ **631 МБ**; Qwen3-ASR 0.6B CoreML int8 ≈ **700 МБ**; GigaAM v3 CoreML ≈ **446 МБ**; Voxtral Mini 4B Realtime — **3.1 ГБ** (MLX 4-bit).

**Непригодно или отложить:**

1. Nemotron CoreML `latin/` — вокаб 2828 токенов получен отсечением CJK/Hangul/**кириллицы**/арабицы (дословно из карточки). Для русского нужен только `multilingual/` (13087 токенов).
2. Parakeet Redux — ru 7.91 % против 5.91 % у v3; единственный рантайм Photon, CoreML-порта нет.
3. Voxtral Mini/Small 2507 — русского нет, batch-модели, `inference: false`.
4. Qwen3-ASR-Flash — публичного артефакта нет (API → 401), только облако.
5. `Voxtral-4B-TTS-2603` — `cc-by-nc-4.0`; это TTS, но совпадение имени ловит автоматический разбор id.

## Лицензионные расхождения конверсий

- `soniqo/…-ONNX-INT8` объявляет `nvidia-open-model-license`, `onnx-community/…-int4` — `mit`, при базе **OpenMDW-1.1**. Корректную лицензию наследуют `FluidInference/…-CoreML` и `handy-computer/…-gguf` (там же `LICENSE`/`NOTICE`). `[INFERENCE]` Производный артефакт не может быть либеральнее оригинала: в реестре хранить лицензию базовой модели, лицензию конверсии — отдельным полем.
- CoreML-бандл Nemotron: `gated: false` в API, но README требует «join the server … and message Sisyphu» — формального gating нет, доступ декларирован через Discord. Операционный риск.
- `manifest.json` конверсии ссылается на `nvidia/nemotron-asr-streaming-multilingual-0.6b`; такого id в API нет (401). Канонический — `nvidia/nemotron-3.5-asr-streaming-0.6b`.
- Карточка Nemotron указывает `Release Date 06/04/2026`, а `createdAt` — 2026-05-15. Хранить `sha` + `lastModified`, не дату из текста.
- `handy-computer/nemotron-…-gguf` несёт пользовательские метрики (`wer_fleurs_ru` q8_0 = 12.61, `rtf_m4_max` 30.25 cpu / 141.1 metal) — не замеры NVIDIA, отдельная колонка в реестре.

## Открытые вопросы

- Качество ru у GigaAM Multilingual (FLEURS 3.0/4.4 по карточке) не проверено на IT-речи; нужен прогон на тех же 12 фрагментах.
- Parakeet Ultra обещает лучше v3 на 25 языках, но CoreML-бандл на 171 МБ больше; проверять на одинаковом аудио.
- Нужны собственные замеры CoreML/ANE для Nemotron 3.5: карточка даёт RTFx с M5 Pro, не с этого хоста.

## Ревизии для итерации 2 (2026-10-28)

Дата проверки: **2026-10-28**. Источник: `https://huggingface.co/api/models/<repo>?blobs=true`, `…/refs`, `…/tree/<branch>?recursive=true`, raw-файлы карточек и `metadata.json`, GitHub API (`/repos/…`, `/commits/…`, raw README). Размеры — байты из API; **МБ = 10⁶ Б**, МиБ указаны отдельно там, где это важно для сверки с карточкой. Ничего не загружалось, кроме текстовых файлов.

### Паспорта

| Кандидат | Ревизия | Лицензия | Файлы и размеры | Русский |
|---|---|---|---|---|
| GigaAM Multilingual `ctc` | `2f8a5714` | MIT (frontmatter + `license:mit`; файла `LICENSE` в репо нет) | 5 файлов: `pytorch_model.bin` 883 170 115 Б (883.2 МБ), `config.json` 2623 Б, `modeling_gigaam.py` 72 778 Б, `README.md` 4454 Б | да: `language: [ru, en, kk, ky, uz]` |
| GigaAM Multilingual `large_ctc` | `3905cd51` | MIT | `pytorch_model.bin` 2 341 592 643 Б (2341.6 МБ) | да (тот же frontmatter) |
| Parakeet Ultra CoreML | `95eaa59a` | CC-BY-4.0 (`base_model: moondream/parakeet-ultra`) | 22 файла, 632 318 254 Б (**632.3 МБ**); Encoder 594 211 328 Б, Decoder 23 604 992 Б, JointDecisionv3 12 642 764 Б, Preprocessor 491 072 Б, две вокабы по 151 122 Б | да: 25 языков во frontmatter, `ru` присутствует |
| Qwen3-ASR-0.6B | `5eb14417` | Apache-2.0 | 10 файлов, 1 880 619 678 Б; `model.safetensors` 1 876 091 704 Б (1876.1 МБ), `vocab.json` 2 776 833 Б, `merges.txt` 1 671 853 Б; `tokenizer.json` нет | да: «Russian (ru)» в таблице 30 языков; во frontmatter поля языков нет |
| `moona3k/mlx-qwen3-asr-0.6b-8bit` | `83ce2a8e` | Apache-2.0 | `weights.safetensors` 835 619 431 Б; репо 840 091 027 Б = **801.2 МиБ** (840.1 МБ) | да: 10 языков во frontmatter, `ru` есть |
| `moona3k/mlx-qwen3-asr-0.6b-4bit` | `4c59c533` | Apache-2.0 | `weights.safetensors` 537 626 967 Б; репо 542 098 973 Б = **517.0 МиБ** (542.1 МБ) | да: 10 языков, `ru` есть |
| Nemotron 3.5 CoreML | `1a41b757` | OpenMDW-1.1 (`license: other` + `license_name: openmdw-1.1`, `license_link` → openmdw.ai) | 180 файлов: `latin/` и `multilingual/` × 4 тира по 22 файла + `manifest.json` 13 462 Б, `README.md` 9068 Б | **в карточке ru нет** (frontmatter: en, es, fr, it, pt, de, zh, ja); в `metadata.json` `"ru-RU": 11` |

Полные ревизии:

- `ai-sage/GigaAM-Multilingual` → `2f8a57144e6ec3adfd32fe0484d9ea9913305bc8` (`main` = `ctc`), `lastModified` 2026-07-14T05:25:01Z, `createdAt` 2026-07-14T05:15:37Z, `gated: false`.
- `FluidInference/parakeet-ultra-coreml` → `95eaa59a39d4394f047a4dc5cce480388a60d1b6`, 2026-09-24T17:45:51Z, 15 437 загрузок.
- `Qwen/Qwen3-ASR-0.6B` → `5eb144179a02acc5e5ba31e748d22b0cf3e303b0`, 2026-01-30T03:00:21Z, 843 149 загрузок.
- `moona3k/mlx-qwen3-asr-0.6b-8bit` → `83ce2a8ef9a382d2ba2209aab383bc51fff366f5`; `…-4bit` → `4c59c533f95c84afb796655e814709034f826f04` (оба 2026-09-19).
- `FluidInference/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b-CoreML` → `1a41b75758b0337ff67db7d5408280aaaf23074e`, 2026-07-03T22:25:50Z.

Поправки к реестру выше: Parakeet Ultra CoreML — **632.3 МБ**, а не 631.1 (632 318 254 Б). Заявленные «801M / 517M» у MLX-квантов — это **МиБ всего репозитория** (840 091 027 и 542 098 973 Б), а не десятичные мегабайты и не размер весов; сами `weights.safetensors` — 835.6 и 537.6 МБ.

### GigaAM Multilingual: что реально есть в ONNX

Ветки (API `…/refs`): `ssl` `ac7c6db0`, `ctc` = `main` `2f8a5714`, `large_ssl` `c459e9d2`, `large_ctc` `3905cd51`. Веток `rnnt`/`e2e_ctc`/`e2e_rnnt`, как у GigaAM v3, **нет**; в самом репо ONNX-файлов нет вообще (5 файлов, только PyTorch). Поиск `?search=gigaam-multilingual` даёт 18 репо, ни одного с rnnt/e2e — у Multilingual только CTC-голова (карточка: «character-wise CTC decoders»).

| ONNX-репо | Ревизия | Лицензия | Файлы (байты) |
|---|---|---|---|
| `istupakov/gigaam-multilingual-ctc-onnx` | `458860e1` | MIT | `multilingual_ctc.int8.onnx` 224 762 204 (224.8 МБ), `multilingual_ctc.onnx` 885 388 622, `multilingual_vocab.txt` 393 |
| `istupakov/gigaam-multilingual-large-ctc-onnx` | `07665ab5` | MIT | `multilingual_large_ctc.int8.onnx` 591 644 782 (591.6 МБ), stub `.onnx` 909 828 + `.onnx.data` 2 343 837 696 |
| `DmitrySharonov/GigaAM-Multilingual-ONNX` | `a74a2ad9` | MIT | `ctc/multilingual_ctc.onnx` 885 369 820; `ssl/multilingual_ssl_encoder.onnx` 885 077 598; `large_ctc/*.onnx.data` 2 341 277 980; `large_ssl/*.onnx.data` 2 340 986 880 |

Русско-способный артефакт присутствует: у всех трёх репо в карточке `language: [ru, en, kk, ky, uz]`; `multilingual_vocab.txt` (393 Б) — 142 char-level токена, среди них вся кириллица (`а`–`я`, `ё`) плюс казахские `ә ғ қ ң ө ұ ү һ і`. Для macOS остаются CoreML-конверсии `voicely/gigaam-multilingual-ctc-coreml` и `vadimsuhanov/gigaam-multilingual-large-ctc-coreml` (не перепроверялись в этой итерации → `не проверено`).

### Nemotron 3.5 CoreML: тиры

Тиров **четыре**, не три: 560 / 1120 / 2240 / 4480 мс. В каждом тире 22 файла (`Encoder/Decoder/JointDecision/Preprocessor .mlmodelc`, `tokenizer.json` 284 969 Б, `metadata.json`, вокаб/конфиги).

| Тир | `chunk_mel_frames` / `total_mel_frames` | `ru-RU` в `prompt_dictionary` | `multilingual/<tier>ms`, Б | `latin/<tier>ms`, Б |
|---|---|---:|---:|---:|
| 560ms | 56 / 65 | 11 | 663 797 881 (663.8 МБ) | 610 993 681 (611.0 МБ) |
| 1120ms | 112 / 121 | 11 | 664 144 423 (664.1 МБ) | 611 340 223 (611.3 МБ) |
| 2240ms | 224 / 233 | 11 | 664 846 846 (664.8 МБ) | 612 042 646 (612.0 МБ) |
| 4480ms | 448 / 457 | 11 | 666 244 017 (666.2 МБ) | 613 439 817 (613.4 МБ) |

Во всех четырёх `multilingual/metadata.json`: `vocab_size` 13087, `blank_idx` 13087, `num_prompts` 128, 121 запись в `prompt_dictionary`, `ru` и `ru-RU` → **11**, `default_prompt_id` тот же; `model` = `nvidia/nemotron-3.5-asr-streaming-0.6b`, `model_class` = `EncDecRNNTBPEModelWithPrompt`, 16 кГц, 128 mel, `att_context_size [42, 13]`. Файлы тиров различаются только `chunk_mel_frames`/`total_mel_frames` и, соответственно, размерами encoder-кэшей. `latin/metadata.json` тоже содержит `"ru-RU": 11`, но при `vocab_size` 2828 — вокаб урезан, для русского он непригоден (см. выводы выше).

### `setCustomVocabulary` в FluidAudio

Документ: `https://github.com/FluidInference/FluidAudio/blob/2faffc237b7b78ae80d938c52cf91700d6709bc5/Documentation/ASR/NemotronMultilingual.md` (коммит `2faffc2`, 2026-10-28, «feat(asr): surface CTC vocabulary rescoring decisions… (#929)»). Цитата:

> «Biasing needs a logits-producing step decoder (`decoder_joint_noencproj` or `decoder_joint`). When a vocabulary is active the fused-argmax `decoder_joint_argmax` asset is bypassed in favor of a logits path; if the load-time tier priority skipped `decoder_joint`, it is loaded lazily from the model directory when the vocabulary is set.»

То есть хотword-биасинг требует **logits-путь** (`decoder_joint` / `decoder_joint_noencproj`); `decoder_joint_argmax` (fused argmax, самый быстрый) при активном словаре обходится, а отсутствующий `decoder_joint` догружается лениво. Если logits-декодера нет вовсе — словарь отклоняется с ошибкой в лог, без «половинного» применения. CTC-головы у экспорта нет, поэтому CTC-ресcore из sliding-window пути здесь не работает; бонус — плоский логпроб на токен (по умолчанию 4.5; значения > 6.0 трактуются как legacy CTC-веса и откатываются к 4.5), декод жадный, термины короче 3 букв отбрасываются.

### `github.com/veche-bench`

- `dataset-assembler` (`7bc440de399303245c4b3a53ad07ae8b550eae63`, 2026-04-26): пайплайн `convert → trim → normalize → anticlip → concat → background → events`; в шаге `concat` перекрытия и паузы задаются одним полем `overlap.value_ms` — **положительное значение = перекрытие (только между разными спикерами), отрицательное = пауза**, режимы `fixed`/`random`/`weighted`, на выходе `.wav` + `.csv` с таймкодами реплик и `speaker_name`/`gender`/`text`; паузы между звуковыми эффектами — `events.gap_ms`.
- `dataset-tg-bot` (`41cceabd5e33e43a171607e19984b87ad92f01df`, 2026-02-12): Telegram-бот раздаёт сценарии (JSON-список реплик с `text`, `gender`, `speaker_id`) и собирает записи дикторов в `data/`, `data_partial/`, `bot.db`.
- Лицензии: GitHub API отдаёт `license: null` для обоих репозиториев, файла `LICENSE` в корне нет → условия использования `не проверены` (для сборки собственного датасета это блокер, для чтения кода — нет).

URL: `https://github.com/veche-bench/dataset-assembler`, `https://github.com/veche-bench/dataset-tg-bot`, конфиг с перекрытиями — `https://raw.githubusercontent.com/veche-bench/dataset-assembler/main/config_hard.yml`.

### Что это меняет для итерации 2

1. **GigaAM Multilingual — только CTC.** RNNT/e2e для мультиязычной ветки не существует ни в оригинале, ни в конверсиях; расчёт на «e2e даст лучше» неприменим, сравнивать нужно `ctc`/`large_ctc` (883.2 / 2341.6 МБ) или их ONNX int8 (224.8 / 591.6 МБ).
2. **Nemotron: тир 4480 мс появился** и стоит на 2.4 МБ дороже 2240 мс при том же `ru-RU = 11` — есть смысл прогнать оба на 12 фрагментах и сравнить задержку/качество, а не фиксировать 2240.
3. **Хотворды для Nemotron возможны только через `decoder_joint`**, то есть ценой отказа от fused-argmax; термины из `experiments/asr_eval/terms.json` (57.8K) придётся урезать — документ прямо предупреждает о false-fire на редких именах.
4. **Qwen3-ASR 0.6B: MLX-кванты 8/4 бит доступны локально-дружелюбно** (801/517 МиБ вместе с токенизатором), качество мерилось только на LibriSpeech en — русский `не проверено`.
5. `dataset-assembler` даёт готовую механику пауз/перекрытий (`value_ms` со знаком, перекрытия только между разными спикерами) — её можно переиспользовать для синтетики, но лицензия не определена.
