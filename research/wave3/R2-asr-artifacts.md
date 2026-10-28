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
