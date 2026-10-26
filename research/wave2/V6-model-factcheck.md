# V6 — Фактчек утверждений о моделях (вторая волна)

Проверка по первоисточникам: HF API (`/api/models/<id>`), сырые `README.md` карточек, arXiv HTML, официальные блоги,
клоны репозиториев на закреплённых SHA. Дата обращений: 2026-10-25/26. Рабочий каталог проб:
`/tmp/srr/probe/V6ModelFactCheck/`. Всё, что я не проверил сам, помечено явно.

Ключевые SHA клонов: `speech-swift` @`cbad3be29480825707890490f446b19d7288be2d`,
`sherpa-onnx` @`532f1b5a8a207bf53b116b747207c1506696c843`, `FluidAudio` @`1b0ed50d686873128a4129b3510ce3914c849bab`.

## Итог

1. **Крупных ошибок в цифрах wave1 не нашлось, но нашлось четыре класса дефектов.** (а) Смешение официальных метрик
   NVIDIA с замерами сторонних портов (Parakeet 11.49 % — это speech-swift, не NVIDIA). (б) Неверные HF-id конверсий
   (`Voxtral-...-6bit` не существует под этим именем; `GoodnotesIT/...-litert-fp16` не существует). (в) Утверждение B2
   «карточки Qwen3-ASR перечисляют 7 языков без ru» **ложно** — `Russian (ru)` есть прямо в таблице карточки.
   (г) Мнимые противоречия между D1 и D2 по Nemotron: 9.17 % и 9.60 % — **разные колонки одной таблицы**, не конфликт.
2. **Nemotron 3.5 ASR Streaming 0.6B — самый прочный кандидат на ru-стриминг**: `nvidia/nemotron-3.5-asr-streaming-0.6b`,
   создан 2026-05-15, **OpenMDW-1.1**, `ru-RU` в 19 transcription-ready локалях, FLEURS ru 9.60 % @560 мс / 9.17 % @1.12 с
   (LangID). Все три факта (id, лицензия, цифры) подтверждены дословно.
3. **Voxtral Mini 4B Realtime 2602 подтверждён целиком**: Apache-2.0, 13 языков с `ru`, FLEURS ru **6.02 % @480 мс**.
   Но карточка внутренне противоречива по допустимым задержкам (240 мс…2.4 с в шапке против 80 мс…1.2 с + 2400 в
   Recommended Settings) — это вход для валидатора API, а не косметика.
4. **Nemotron 3 Diarization существует и это лучший открытый streaming-диаризатор по DIHARD III** (12.73 % DER при
   30.4 с). Дата 23.09.2026 подтверждена (README + блог), но HF-репозиторий создан 2026-09-01 — расхождение
   безобидное, однако в реестре моделей надо хранить дату из карточки, а не `createdAt`.
5. **Русского DER-бенчмарка по-прежнему нет ни у одного диаризатора.** Все числа (DIHARD III, AMI, CALLHOME,
   AliMeeting, NOTSOFAR1, VoiceArena) — английские. Для ru-IT диаризация выбирается по архитектуре и лицензии,
   а не по измеренному качеству.
6. **Лицензионный минный заряд — DiariZen: `cc-by-nc-4.0`** (non-commercial), при том что по качеству он лучший
   офлайн на AMI (14.0 %). Nemotron 3 Diarization (OpenMDW-1.1) и pyannote community-1 (CC-BY-4.0) коммерчески
   пригодны. Это делает DiariZen непригодным дефолтом для продукта.
7. **T-one — единственная модель без латиницы в алфавите**, и это подтверждено машинно: `vocab.json` = 35 записей,
   `[PAD]`, `|` и 33 кириллические буквы; ни одной латинской. Для сценария «русская речь + английские IT-термины»
   это дисквалифицирующее ограничение, а не «особенность».
8. **GigaAM v3 «лимит 25 с» — это ограничение Python-API `.transcribe`, а не модели**: есть `.transcribe_longform`
   через pyannote. Формулировка D1 неточна. Официальный WER: RNNT 8.3 / CTC 9.1 (среднее по 10 наборам), e2e-варианты
   **хуже по WER** (11.2 / 12.0) — потому что выводят пунктуацию и регистр, которые надо снимать перед подсчётом.
9. **Конверсии покрывают почти всё; настоящие дыры — ровно три.** (а) **CoreML нет у Voxtral 4B Realtime** и
   **у Qwen3-ASR** — для Apple Silicon это означает MLX/ONNX-путь либо отсутствие пути. (б) **T-one не имеет ни
   одной конверсии** — только sherpa-ONNX-артефакт. (в) **DiariZen: конверсии есть, но мёртвые** (0 загрузок,
   CoreML — для другой ревизии `-v2`), плюс лицензия non-commercial. Всё остальное — включая Nemotron 3 Diarization
   и Smart Turn v3.2 — закрыто CoreML, MLX и ONNX реальными HF-репозиториями (id в матрице).
10. **Цифры J-community воспроизводятся дословно** из `vakovalskii/stt-ru-benchmark`: GigaAM v3 на аудиокнигах
    **6.57 %** [5.75; 7.42] при n=296; на подмножестве с латиницей Whisper **7.89 %** против GigaAM **10.27 %** при
    n=38. Автор сам маркирует латиницу как наблюдение, а не результат — это корректная передача.

## Проверенные утверждения

| Утверждение | Источник-отчёт | Вердикт | Доказательство |
|---|---|---|---|
| Nemotron 3.5: id `nvidia/nemotron-3.5-asr-streaming-0.6b` | D1, D2 | ПОДТВЕРЖДЕНО | `curl https://huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b` → `"id":"nvidia/nemotron-3.5-asr-streaming-0.6b"` |
| Nemotron 3.5: создан 2026-05-15, ~1.09 млн загрузок | D2 | ПОДТВЕРЖДЕНО | там же: `createdAt=2026-05-15T21:52:25.000Z`, `downloads=1090571`, `likes=1235` |
| Nemotron 3.5: лицензия OpenMDW-1.1 | D2, драфт | ПОДТВЕРЖДЕНО | `cardData.license=other`, `license_name=openmdw-1.1`, `license_link=https://openmdw.ai/license/1-1/` |
| Nemotron 3.5: `ru-RU` в 19 transcription-ready локалях | D1, D2 | ПОДТВЕРЖДЕНО | README, таблица Tier: «Transcription-ready (19 locales) … Russian (ru-RU)» |
| Nemotron 3.5: 40 language-locales в 3 tier'ах | D1, D2 | ПОДТВЕРЖДЕНО | там же: 19 + 13 + 8 = 40; примечание «32 total produce ASR out of the box» |
| Nemotron 3.5: FLEURS ru 9.17 % @1.12 с (LangID) | D1 | ПОДТВЕРЖДЕНО | README, `<tr>Russian (ru-RU)</tr>`: 10.84 / 10.73 / 9.87 / 9.60 / **9.17** |
| Nemotron 3.5: FLEURS ru 9.60 % @560 мс | D2 | ПОДТВЕРЖДЕНО | та же строка, 4-я колонка (560 мс) |
| Nemotron 3.5: D1 и D2 противоречат друг другу | — (проверяемая гипотеза) | **ОПРОВЕРГНУТО** | это две колонки одной таблицы; конфликта нет |
| Nemotron 3.5: auto ru 12.47→10.03 | D1, D2 | ПОДТВЕРЖДЕНО | там же, блок Auto-detect: 12.47 / 12.09 / 11.01 / 10.57 / 10.03 |
| Nemotron 3.5: средний WER 19 локалей 10.38 → 8.84 | D2 | ПОДТВЕРЖДЕНО | строка `<strong>Average</strong>`: 10.38 / 10.00 / 9.49 / 9.12 / 8.84 |
| Nemotron 3.5: оговорка о нормализации текста | D1 | ПОДТВЕРЖДЕНО | README, «Note on text normalization» — «may inflate the reported error rates» |
| Voxtral 4B Realtime: id и дата 2026-01-21 | D1, D2 | ПОДТВЕРЖДЕНО | HF API: `createdAt=2026-01-21T17:22:02Z`, `downloads=2041370` |
| Voxtral 4B Realtime: Apache-2.0, BF16 | D1, D2 | ПОДТВЕРЖДЕНО | `cardData.license=apache-2.0`; README «released in BF16 under the Apache-2 license» |
| Voxtral 4B Realtime: 13 языков с `ru` | D1, D2 | ПОДТВЕРЖДЕНО | `cardData.language = [en,fr,es,de,ru,zh,ja,it,pt,nl,ar,hi,ko]`; README «supports 13 languages» |
| Voxtral 4B Realtime: FLEURS ru **6.02 % @480 мс** | D1, D2 | ПОДТВЕРЖДЕНО | README, таблица Fleurs, строка `480 ms`, колонка Russian = 6.02 % |
| Voxtral 4B Realtime: 9.53/7.87/5.56/5.41 @160/240/960/2400 | D1 | ПОДТВЕРЖДЕНО | там же, все пять строк |
| Voxtral: offline-базлайн «Mini Transcribe 2.0», ru 4.75 %, AVG 5.90 % | D1 | ПОДТВЕРЖДЕНО | там же, первая строка таблицы |
| Voxtral: задержка «from 80 ms to 2.4 s», рекомендуется 480 мс | D2 | ЧАСТИЧНО | шапка карточки: «240ms to 2.4s»; Recommended Settings: «multiple of 80ms between 80 and 1200, as well as 2400». Карточка противоречит сама себе |
| Voxtral: `mlx-community/Voxtral-Mini-4B-Realtime-2602-6bit` существует | D2 | **ОПРОВЕРГНУТО** | HF API → 404. Реальный 6-bit: `mlx-community/Voxtral-Mini-4B-Realtime-6bit` (без `-2602`) |
| Voxtral: MLX fp16/4bit, ONNX, GGUF существуют | D2 | ПОДТВЕРЖДЕНО | `mlx-community/...-2602-fp16`, `...-2602-4bit`, `onnx-community/...-2602-ONNX`, `ggml-org/...-2602-GGUF` (2026-10-16) |
| Voxtral: CoreML-порта нет | D2 | ПОДТВЕРЖДЕНО | поиск `Voxtral-Mini-4B-Realtime` по HF: ни одного coreml-репозитория |
| Nemotron 3.5: LiteRT `GoodnotesIT/...-litert-fp16` существует | D2 | **ОПРОВЕРГНУТО** | такого репозитория нет; реальные: `GoodnotesIT/nemotron-3.5-asr-streaming-multilingual-0.6b-litert-{fp16,w8a16,dynamic-int8}`, `soniqo/Nemotron-3.5-ASR-Streaming-Multilingual-0.6B-LiteRT-{FP16,INT8}` |
| Nemotron 3.5: MLX `mlx-community/nemotron-3.5-asr-streaming-0.6b` (+`-8bit`) | D2 | ПОДТВЕРЖДЕНО | оба репозитория существуют, 2026-06-04, `license: other` |
| Nemotron 3.5: ONNX `onnx-community/...-onnx-int4` | D2 | ПОДТВЕРЖДЕНО | существует, 2026-06-04, `license: mit` (не openmdw — юридическая цепочка спорна, см. ниже) |
| Nemotron-3-Diarization: id `nvidia/Nemotron-3-Diarization` | E2, драфт | ПОДТВЕРЖДЕНО | HF API + README; `downloads=116775`, `gated=false` |
| Nemotron-3-Diarization: релиз 23.09.2026 | E2 | ПОДТВЕРЖДЕНО | README «## Release Date / September 23, 2026»; блог HF «Published September 23, 2026». `createdAt` репозитория = 2026-09-01 |
| Nemotron-3-Diarization: до 8 спикеров, arrival-ordering | E2, драфт | ПОДТВЕРЖДЕНО | README «up to eight speakers», «ordering its output channels according to each speaker's first arrival»; `config.json` → `num_speakers: 8` |
| Nemotron-3-Diarization: DIHARD III DER 12.73 % (30.4 с) / 13.18 % (1.04 с) | E2 | ПОДТВЕРЖДЕНО | блог NVIDIA: «lowering DIHARD III DER from 19.09 % to 12.73 %»; «from 19.60 % to 13.18 %» |
| Nemotron-3-Diarization: baseline Sortformer v2.1 19.09/19.60 | E2 | ПОДТВЕРЖДЕНО | там же, те же две фразы |
| Nemotron-3-Diarization: openmdw-1.1, commercial use разрешён | E2 | ПОДТВЕРЖДЕНО | frontmatter `license: openmdw-1.1`; README «ready for commercial or non-commercial use» |
| Nemotron-3-Diarization: ~99.2M параметров (F32) | E2 | ПОДТВЕРЖДЕНО (косвенно) | `model.safetensors` = 396 954 592 B ÷ 4 ≈ 99.2M; `config.json` hidden 512 × 31 слой, `dtype: float32`. Точное число из заголовка safetensors не извлекал |
| Nemotron-3-Diarization: latency 30.4/1.04/0.64/0.32 с, минимум 80 мс | E2 | ПОДТВЕРЖДЕНО | README, таблица конфигураций + «as low as 80 ms … lowest recommended is 0.32 s» |
| «Parakeet Ultra» = `moondream/parakeet-ultra` | D2 | ПОДТВЕРЖДЕНО | HF API: `moondream/parakeet-ultra`, 2026-09-22, `cc-by-4.0`, 57 001 загрузок |
| Parakeet Ultra: post-trained v3, 6.26→5.80, 11.62→9.55, 2.71→1.94 | D2 | ПОДТВЕРЖДЕНО | карточка: «A post-trained version of parakeet-tdt-0.6b-v3. Same architecture…»; таблицы: OAL 6.26/5.80, FLEURS-25 11.62/9.55, TED-LIUM 2.71/1.94 |
| Parakeet TDT v3: FLEURS ru **5.51 %** | D1 | ПОДТВЕРЖДЕНО | model-index: FLEURS `config: ru_ru` → `value: 5.51` |
| Parakeet TDT v3: CoVoST2 ru 3.00 % | D1 | ПОДТВЕРЖДЕНО | model-index: CoVoST2 `config: ru` → `value: 3` |
| Parakeet TDT v3: CC-BY-4.0, ru в списке языков | D1, D2 | ПОДТВЕРЖДЕНО | frontmatter `license: cc-by-4.0`, `language` содержит `ru` и `uk` |
| Parakeet TDT v3: «ru WER 11.49 %» как свойство модели | B2 | ЧАСТИЧНО | 11.49 — замер **порта** speech-swift INT8, не NVIDIA: [`docs/benchmarks/asr-wer.md#L124`](https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/docs/benchmarks/asr-wer.md#L124). B2 сам это оговаривает, но подпись таблицы «Parakeet is English-only» (там же, `#L114`) противоречит её же строке |
| Canary-1B-v2: FLEURS ru 6.90 %, CoVoST2 ru 5.14 % | D1 | ПОДТВЕРЖДЕНО | model-index: FLEURS `ru_ru` → 6.90; CoVoST2 `ru` → 5.14 |
| Canary-1B-v2: CC-BY-4.0 | D1 | ПОДТВЕРЖДЕНО | frontmatter `license: cc-by-4.0` |
| Qwen3-ASR 0.6B/1.7B: Apache-2.0, янв. 2026 | D1 | ПОДТВЕРЖДЕНО | HF API: `createdAt 2026-01-28`, `license=apache-2.0` для обоих |
| Qwen3-ASR: 52 языка/диалекта, `ru` в таблице | D1 | ПОДТВЕРЖДЕНО | README: «52 languages and dialects», «30 languages and 22 Chinese dialects»; в списке 30 языков есть `Russian (ru)` |
| Qwen3-ASR: «карточки HF перечисляют 7 языков без `ru`» | B2 | **ОПРОВЕРГНУТО** | в README обеих карточек `Russian (ru)` присутствует в таблице Supported Languages. YAML-поля `language` в карточке вообще нет (`cardData.language=None`) — B2, вероятно, смотрел на тег-лист, а не на таблицу |
| Qwen3-ASR: FLEURS ru 9.91 (0.6B) / 5.99 (1.7B) / 4.81 (Flash) | D1 | ПОДТВЕРЖДЕНО | arXiv:2601.21337v1, таблица Fleurs: `ru 9.91 5.99 4.81` |
| Qwen3-ASR: CommonVoice ru 14.07/8.28/5.73 | D1 | ПОДТВЕРЖДЕНО | там же: `ru 14.07 8.28 5.73` |
| Qwen3-ASR: MLC-SLM ru 19.24/15.17/13.16 | D1 | ПОДТВЕРЖДЕНО | там же: `ru 19.24 15.17 13.16` |
| Qwen3-ASR: агрегат FLEURS 7.57 (0.6B) / 4.90 (1.7B), Whisper 5.27 | D1 | ПОДТВЕРЖДЕНО | README, таблица Multilingual ASR Benchmarks: `Fleurs 16.08 / 5.27 / 10.03 / 7.57 / 4.90` |
| GigaAM v3: MIT, 220–240M, 5 вариантов | D1, драфт | ПОДТВЕРЖДЕНО | `cardData.license=mit`; README: «220–240M parameters», варианты `ssl/ctc/rnnt/e2e_ctc/e2e_rnnt` |
| GigaAM v3: не умеет стриминг | D1 | ПОДТВЕРЖДЕНО | в README/репозитории нет streaming-инференса; только `.transcribe` / `.transcribe_longform` |
| GigaAM v3: «лимит 25 с» | D1 | ЧАСТИЧНО | это лимит API, а не модели: «ASR with `.transcribe` function is applicable for audio **only up to 25 seconds**. To enable `.transcribe_longform` install pyannote.audio» — [GigaAM README#L77](https://github.com/salute-developers/GigaAM/blob/main/README.md#L77) |
| GigaAM v3: средний WER CTC 9.1 / RNNT 8.3 | D1 | ПОДТВЕРЖДЕНО | [evaluation.md](https://github.com/salute-developers/GigaAM/blob/main/evaluation.md), строка Average. В карточке HF те же числа округлены до 9.2 / 8.4 — небольшое расхождение карточки с репозиторием |
| GigaAM v3: e2e хуже по WER, чем ctc/rnnt | — (проверяемая гипотеза) | ПОДТВЕРЖДЕНО | evaluation.md: E2E CTC 12.0, E2E RNNT 11.2 против CTC 9.1 / RNNT 8.3 (e2e — с пост-обработкой) |
| T-one: `t-tech/T-one`, Apache-2.0, 71M, чанки 300 мс | D1, D2 | ПОДТВЕРЖДЕНО | HF API `license=apache-2.0`, `createdAt 2025-07-14`; README «71M parameter», «processes audio in 300 ms chunks» |
| T-one: алфавит без латиницы | D1 | ПОДТВЕРЖДЕНО | `curl https://huggingface.co/t-tech/T-one/raw/main/vocab.json` → 35 записей: `[PAD]`, `\|`, `а…я`, `ё`. Ни одной латинской буквы |
| T-one: word-level таймстемпы заявлены, в коде — уровень фразы | D1 | ПОДТВЕРЖДЕНО | README обещает «word-level timestamps», а пример вывода даёт `TextPhrase(text=…, start_time=…, end_time=…)` на фразу |
| T-one: CoreML/MLX-портов нет | D2 | ПОДТВЕРЖДЕНО | поиск по HF: единственный артефакт — `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08` |
| VibeVoice-ASR-Streaming существует (1.5B/7B) | D2 | ПОДТВЕРЖДЕНО | HF API: оба `createdAt 2026-09-02`, `license=mit` |
| VibeVoice-ASR-Streaming: 10 языков с ru, стриминг с атрибуцией спикера, hotwords | D2 | ПОДТВЕРЖДЕНО | README: «Who said What», «Customized Hotwords», «10 languages»; frontmatter `language` содержит `ru` |
| VibeVoice: MLC-Challenge ru WER 15.67 / cpWER 19.47 (7B) | D2 | ПОДТВЕРЖДЕНО | arXiv:2609.02812v1, таблица с атрибуцией: `Russian … 15.67 19.47` |
| VibeVoice: MLX есть только для оффлайн-варианта | D2 | **ОПРОВЕРГНУТО** | существуют `mlx-community/VibeVoice-ASR-Streaming-7B-4bit` и `-7B-bf16` — MLX для **streaming**-варианта |
| VibeVoice: GGUF `audio-cpp/...-7B-GGUF`, LiteRT 1.5B | D2 | ПОДТВЕРЖДЕНО | HF API: `audio-cpp/VibeVoice-ASR-Streaming-7B-GGUF`, `litert-community/VibeVoice-ASR-Streaming-1.5B` |
| Silero VAD v6.2 | E1, драфт | ПОДТВЕРЖДЕНО | `pyproject.toml` репозитория: `version = "6.2.3"`; лицензия MIT (README + LICENSE) |
| Smart Turn v3.2: BSD-2, 23 языка **включая ru**, ~8M, Whisper-Tiny | E1, драфт | ПОДТВЕРЖДЕНО | [smart-turn README#L11-L16](https://github.com/pipecat-ai/smart-turn/blob/main/README.md#L11-L16): «truly open model (BSD 2-clause)», «Support for 23 languages … 🇷🇺 Russian»; `aufklarer/Smart-Turn-v3.2-CoreML` frontmatter `bsd-2-clause`, 23 языка, 8.0M |
| FireRedVAD: Apache-2.0, февраль 2026 | E1 | ПОДТВЕРЖДЕНО | HF API `FireRedTeam/FireRedVAD`, `createdAt 2026-02-11`, `license=apache-2.0`; карточка: standalone-релиз 2026.03.03 |
| FireRedVAD: метрики на FLEURS-VAD-102 | E1 | ПОДТВЕРЖДЕНО | карточка: AUC-ROC 99.60, F1 97.57, False Alarm 2.69 против Silero 97.99 / 95.95 / 9.41 |
| WeSpeaker ResNet34-LM: EER 0.723 % vox1-O | E2 | ПОДТВЕРЖДЕНО | [WeSpeaker README#L95](https://github.com/wenet-e2e/wespeaker/blob/master/README.md#L95): «**0.723%/0.069** (ResNet34) … after LM fine-tuning and AS-Norm» |
| CAM++: EER 0.65 %, 7.2M, 192-d | E2 | ПОДТВЕРЖДЕНО | [3D-Speaker README#L30](https://github.com/modelscope/3D-Speaker/blob/main/README.md#L30): `CAM++ | 7.2 M | 0.65% | 6.78% | 7.75%` |
| pyannote community-1: AMI SDM 19.9, DIHARD3 20.2 | E2 | ПОДТВЕРЖДЕНО | [pyannote-audio README#L85-L99](https://github.com/pyannote/pyannote-audio/blob/develop/README.md#L85-L99): AMI SDM 19.9, DIHARD 3 20.2, CALLHOME p2 26.7, VoxConverse 11.2 |
| DiariZen: лицензия весов `cc-by-nc-4.0` | E2 | ПОДТВЕРЖДЕНО | HF API `BUT-FIT/diarizen-wavlm-large-s80-md`: `license=cc-by-nc-4.0`; карточка «ensure **non-commercial** usage» |
| DiariZen: DIHARD3 14.5 %, AMI 13.9 % | E2 | ЧАСТИЧНО | карточка: DIHARD3 **14.5** ✓; AMI **14.0** (не 13.9) при collar=0 |
| J-community: GigaAM v3 6.57 % на аудиокнигах, n=296 | J | ПОДТВЕРЖДЕНО | [stt-ru-benchmark README#L24](https://github.com/vakovalskii/stt-ru-benchmark/blob/main/README.md#L24): `6.57 % 🥇 [5.75; 7.42]`, `n=296` |
| J-community: латиница Whisper 7.89 % vs GigaAM 10.27 %, n=38 | J | ПОДТВЕРЖДЕНО | [там же, #L90-L94](https://github.com/vakovalskii/stt-ru-benchmark/blob/main/README.md#L90-L94) |
| J-community: Parakeet TDT v3 10.84 % на аудиокнигах | J | ПОДТВЕРЖДЕНО | там же, `#L49`, `#L73` |
| sherpa-onnx поддерживает Nemotron-3.5 streaming | D2 | ПОДТВЕРЖДЕНО | [CHANGELOG#L182](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L182) «Add multilingual Nemotron-3.5 streaming ASR support (#3671)», `#L117` «Re-export … to ONNX (#3732)», `#L116` «… to QNN (#3741)» |
| sherpa-onnx экспортирует GigaAM v3 | D1 | ПОДТВЕРЖДЕНО | [CHANGELOG#L650](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L650) «Export GigaAM v3 to sherpa-onnx (#2901)»; скрипт `scripts/nemo/GigaAM/run-ctc-v3-punct.sh` |
| sherpa-onnx поддерживает стриминговый T-one | D2 | ПОДТВЕРЖДЕНО | [CHANGELOG#L802-L804](https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/CHANGELOG.md#L802-L804) (#2571/#2575/#2576); артефакт `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08` |
| FluidAudio Parakeet Ultra: int8 encoder 595 MB, test-clean 2.13, test-other 3.81, FLEURS-24 11.67 | D2 | ПОДТВЕРЖДЕНО | [`Documentation/ASR/ParakeetUltra.md#L10-L40`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/ParakeetUltra.md#L10-L40) |
| FluidAudio Nemotron multilingual: AVG 8.62/8.33/8.30, RTFx 40.5/66.0/73.1 | D2 | ПОДТВЕРЖДЕНО | [`Documentation/ASR/NemotronMultilingual.md#L170-L171`](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/NemotronMultilingual.md#L170-L171) |

## Nemotron 3.5 ASR Streaming 0.6B — детально

Точный id: **`nvidia/nemotron-3.5-asr-streaming-0.6b`** — целиком в нижнем регистре; формы
`Nemotron-3.5-ASR-Streaming-0.6B` из текста отчётов как id **не существуют** (404), хотя именно так подписаны
некоторые CoreML-конверсии. В реестре моделей id должен быть канонический, алиасы — отдельным полем.

Метаданные HF API: `createdAt 2026-05-15T21:52:25Z`, `lastModified 2026-09-10`, `downloads 1 090 571`,
`likes 1 235`, `library_name: nemo`, `pipeline_tag: automatic-speech-recognition`, `gated: false`, 22 файла.

Лицензия: `license: other` + `license_name: openmdw-1.1` + `license_link: https://openmdw.ai/license/1-1/`.
**Не Apache и не CC.** В манифесте артефакта хранить строку `openmdw-1.1`, а не `other` — иначе UI покажет
бесполезное «other», и пользователь не сможет принять осознанное решение.

Языки. Frontmatter перечисляет 36 кодов языков (`en … sl`), включая `ru` и `uk`. Таблица Tier в README даёт
40 локалей: 19 transcription-ready (включая `ru-RU`, `uk-UA`), 13 broad-coverage, 8 adaptation-ready. Расхождение
«36 в YAML против 40 в тексте» объясняется тем, что YAML не различает варианты локалей (`zh-CN`, `nn-NO`). `[INFERENCE]`
Для реестра хранить именно локаль (`ru-RU`), а не двухбуквенный код: слот `language_mask` заполняется по локали
(`ru-RU → 11` в sherpa-onnx, `auto → 101` в speech-swift), и `ru` без региона может не дать ожидаемого поведения.

FLEURS ru, режим Language Input (колонки 80/160/320/560/1120 мс): **10.84 / 10.73 / 9.87 / 9.60 / 9.17**.
Auto-detect: 12.47 / 12.09 / 11.01 / 10.57 / 10.03. Среднее по 19 локалям (LangID): 10.38 / 10.00 / 9.49 / 9.12 / 8.84.

**Согласование D1 и D2.** D1 приводит 9.17 % @1.12 с, D2 — 9.60 % @560 мс. Это две колонки одной таблицы, а не
противоречие. Для матрицы надо выбрать одну рабочую точку. `[INFERENCE]` Рекомендую 560 мс: 9.60 % против 9.17 %
это +0.43 абс. п. WER за вдвое меньшую задержку, что для интерактивной диктовки выгоднее. Точка 80 мс (10.84 %)
при этом остаётся валидной для субтитров, где задержка критичнее качества.

Оговорка карточки, которую нельзя терять при цитировании: WER считается **после нормализации** (регистр,
пунктуация, числительные), нормализация несовершенна для всех 40 локалей, и «residual mismatches … may inflate the
reported error rates». То есть реальное качество может быть **лучше** заявленного — редкий случай, когда
вендор оговаривает в свою пользу наоборот.

`[INFERENCE]` Практический риск: 9.6 % FLEURS ru — это чтение (FLEURS = записанные книги), а не спонтанная
IT-речь с англицизмами. Ни одного замера Nemotron 3.5 на русской IT-диктовке не существует (см. «Неизвестные»).

**Лицензионная цепочка конверсий — отдельная проблема.** Оригинал под `openmdw-1.1`, но конверсии объявляют
разные лицензии: `onnx-community/nemotron-3.5-asr-streaming-0.6b-onnx-int4` — **`mit`**, `mlx-community/...` —
`other`, `aufklarer/...-CoreML-INT8` — `openmdw-1.1`. `[INFERENCE]` Производный артефакт не может быть либеральнее
оригинала; поле `license` в манифесте нашего реестра должно браться **из исходной модели**, а лицензия
конверсии храниться отдельным полем (`conversion_license`) и не подменять основную. Иначе runtime покажет
пользователю «MIT» на модель, которая на самом деле OpenMDW-1.1.

## Voxtral Mini 4B Realtime 2602 — детально

`mistralai/Voxtral-Mini-4B-Realtime-2602`, `createdAt 2026-01-21T17:22:02Z`, `lastModified 2026-03-11`,
`downloads 2 041 370`, `likes 1 046`, `library_name: vllm`, `gated: false`. Лицензия Apache-2.0, BF16.
Base model: `mistralai/Ministral-3-3B-Base-2512`.

Архитектура по README: ≈3.4 B LM + ≈970 M аудио-энкодер, обученный с нуля с каузальным вниманием; sliding-window
attention и в энкодере, и в LLM → «infinite» streaming. Один текстовый токен = 80 мс.

FLEURS ru: **6.02 % @480 мс**, 9.53 @160, 7.87 @240, 5.56 @960, 5.41 @2400. AVG по 13 языкам: 8.72 % @480.
Offline-базлайн «Voxtral Mini Transcribe 2.0»: ru 4.75 %, AVG 5.90 % — **весов на HF не найдено**, это
маркетинговый ориентир, а не воспроизводимая модель.

Внутреннее противоречие карточки по задержкам (три разных формулировки в одном документе):

| Место в README | Формулировка |
|---|---|
| шапка | «configurable transcription delays (240ms to 2.4s)» |
| Key Features | «from 80ms to 2.4s» |
| Recommended Settings | «any multiple of 80ms between 80 and 1200, as well as 2400 as a standalone value» |

`[INFERENCE]` Единственная операционно-пригодная интерпретация — третья: допустимое множество
`{80, 160, …, 1200} ∪ {2400}`. API runtime должен валидировать именно так и возвращать `invalid_argument` на
2400-кратные значения вроде 1280, иначе клиент молча получит деградацию.

Форматы и рантаймы, заявленные в карточке: vLLM (recommended, Realtime API `/v1/realtime`), `transformers >= 5.2.0`
(`VoxtralRealtimeForConditionalGeneration`), ExecuTorch (**явно помечен untested**), community: `voxtral.c` (C),
mlx-audio, MLX `awni/voxmlx`, Rust `TrevorS/voxtral-mini-realtime-rs`. **CoreML в карточке не упомянут и на HF
не найден** — это дыра для Apple-платформы, которую draft-архитектура закрывает строкой «Voxtral … MLX»,
но не CoreML. Правильно.

## Nemotron 3 Diarization — детально

`nvidia/Nemotron-3-Diarization`, `downloads 116 775`, `likes 808`, `gated: false`. README: релиз
**23 сентября 2026**, `license: openmdw-1.1`, один чекпойнт на streaming и offline, до 8 спикеров, AOSC + FIFO
(как в Streaming Sortformer), выход 10 мс (конфигурируемо кратно 10 мс), «With chunked inference, the maximum audio
duration is not limited».

`config.json` (первоисточник, читается напрямую): `architectures: ["Nemotron3DiarizationForAudioFrameClassification"]`,
`audio_config`: 31 слой, `hidden_size 512`, `num_attention_heads 8`, `intermediate_size 2048`, `num_mel_bins 128`,
`subsampling_factor 8`, `rope_theta 10000`, `max_position_embeddings 5000`; `head_config.num_speakers: 8`;
`dtype: float32`. Размер `model.safetensors` = 396 954 592 B → ≈99.2 M параметров. Это независимое подтверждение
цифры E2 (99 226 504) с точностью до заголовка файла.

Latency (input buffer, `(CHUNK_LEN + RIGHT_CONTEXT) × 80 мс`, без учёта вычислений):

| Конфигурация | Latency | SPKCACHE_LEN | FIFO_LEN | CHUNK_LEN | RIGHT_CONTEXT | UPDATE_PERIOD |
|---|---|---|---|---|---|---|
| Very high (offline) | 30.4 с | 264 | 40 | 340 | 40 | 300 |
| Low | 1.04 с | 264 | 264 | 9 | 4 | 222 |
| Very low | 0.64 с | 264 | 264 | 6 | 2 | 222 |
| Ultra-low | 0.32 с | 264 | 264 | 3 | 1 | 222 |

DIHARD III (полный сет): DER **12.73 %** при 30.4 с и **13.18 %** при 1.04 с; baseline
`diar_streaming_sortformer_4spk-v2.1` — 19.09 / 19.60 %. CALLHOME-Part2: 9.10 % (было 10.32), но на подмножестве
2 спикера модель **хуже** baseline — 5.98 против 5.68 %. VoiceArena Diarization-Bench: 14.72 % DER, 1-е место
среди 12 систем (139 английских диалогов, ~22 ч, collar 0, overlap учитывается). Throughput: 15 113× RTFx
(30.4 с, batch 32, BF16, `torch.compile`, RTX PRO 5000), 865× при 1.04 с.

Две оговорки из блога, которые меняют интерпретацию:

1. В DIHARD III группа «5–9 спикеров» агрегируется в один результат, а **9 спикеров превышают лимит модели в 8** —
   агрегат включает аудио вне заявленного ограничения. То есть 12.73 % — оптимистичная оценка для заявленного
   режима «≤8 спикеров».
2. Улучшение «40 % relative» — это **невзвешенное среднее восьми относительных улучшений**, а не pooled DER.
   Цитировать его как «DER улучшился на 40 %» некорректно.

Отдельный документ карточки `diarization_evaluation.md` — по сути готовый протокол отчётности: требует указывать
модель, commit кода оценки, датасет, источник RTTM, UEM, `ignore_overlap`, collar, пост-обработку, разрешение
выхода, streaming-параметры, точность и железо. `[INFERENCE]` Это ровно тот набор полей, который стоит завести
в отчёте о бенчмарках нашего runtime — вендор сам его специфицировал.

**Русского DER нет.** Все замеры английские. `[INFERENCE]` Для ru-IT сценария выбор Nemotron 3 Diarization —
решение «по архитектуре» (8 спикеров, нативный стриминг, коммерческая лицензия), а не по измеренному качеству
на целевом языке. Это надо явно писать в матрице, иначе выглядит как доказанное превосходство.

## Parakeet: Ultra, TDT v3, Redux

`moondream/parakeet-ultra` (2026-09-22, CC-BY-4.0, 57 001 загрузок) — «Parakeet Ultra» в отчётах D2 это именно он,
не отдельная модель NVIDIA. `moondream/parakeet-redux` (2026-09-18) тоже существует. NVIDIA-родителя у Ultra нет:
это community post-train от `parakeet-tdt-0.6b-v3`, что важно для лицензионной цепочки (CC-BY-4.0 сохраняется,
но атрибуция нужна обеим сторонам).

`nvidia/parakeet-tdt-0.6b-v3` (2025-08-04, CC-BY-4.0, 574 427 загрузок): официальные FLEURS по 24 конфигам.
ru-релевантные: **ru 5.51 / CoVoST2 3.00**, uk 6.79 / 5.10, de 5.04 / 4.84, fr 5.15 / 6.05, en 4.85 / 6.80,
es 5.18-подобные значения в диапазоне 3–6. Полный разброс FLEURS — от 3.00 (it) до 24.03 (sl).

**Про 11.49 %.** Это число живёт в `speech-swift/docs/benchmarks/asr-wer.md`, колонка `Parakeet INT8`, строка
`Russian | WER | 16.35 | 10.52 | 11.49` — то есть замер **порта** (CoreML INT8), а не NVIDIA, на FLEURS.
B2 приводит его с оговоркой «самоотчёт, не независимое измерение» — корректно. Но в той же таблице подпись
«Parakeet is English-only (25 European languages)» фактически ложна (там же `#L114`), и B2 сам это отмечает в
`#L307`. `[INFERENCE]` В матрице моделей держать две отдельные колонки: «официальный WER» (5.51) и «WER порта»
(11.49, INT8/CoreML, self-reported). Смешивать нельзя — разница в 2.1 раза.

## Canary-1B-v2

`nvidia/canary-1b-v2`, CC-BY-4.0, 206 065 загрузок. model-index: FLEURS `ru_ru` = **6.90**, CoVoST2 `ru` = **5.14**.
Оба числа D1 подтверждены дословно. Для сравнения в том же model-index: de 4.40, en 4.50, es 2.90, uk 18.15 (CoVoST2).
То есть Canary на русском заметно слабее Parakeet (6.90 против 5.51) и Nemotron (6.90 против 9.60 — тут наоборот),
и это batch-модель без нативного стриминга.

## Qwen3-ASR

`Qwen/Qwen3-ASR-0.6B` и `Qwen/Qwen3-ASR-1.7B`, оба `createdAt 2026-01-28`, Apache-2.0, `library_name` отсутствует,
теги `qwen3_asr` + `arxiv:2601.21337`.

**Главная находка: утверждение B2 «карточки HF перечисляют 7 языков без `ru`» ложно.** В README обеих карточек
есть таблица «Supported Languages», и в списке 30 языков присутствует `Russian (ru)` — рядом с `Korean (ko)`,
`Thai (th)` и т.д. Поля `language` в YAML-шапке у карточки **нет вообще** (`cardData.language = None`), поэтому
если смотреть только на HF-теги, русский действительно не виден — вероятно, отсюда и взялась ошибка B2.
`[INFERENCE]` Это системный урок для нашего реестра: HF-теги и текст карточки — разные источники, и расхождение
между ними встречается регулярно (тот же паттерн у Nemotron 3.5: 36 тегов против 40 локалей).

Числа по отчёту (arXiv:2601.21337v1, первоисточник, не карточка):

| Набор | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B | Qwen3-ASR-Flash-1208 |
|---|---|---|---|
| CommonVoice ru | 14.07 | 8.28 | 5.73 |
| MLC-SLM ru | 19.24 | 15.17 | 13.16 |
| FLEURS ru | 9.91 | 5.99 | 4.81 |

Агрегаты из карточки (12 языков FLEURS): 16.08 / 5.27 (Whisper-large-v3) / 10.03 / **7.57** (0.6B) / **4.90** (1.7B).
Столбец 5.27 — Whisper, и он **лучше** Qwen3-ASR-0.6B (7.57) на агрегате и лучше 1.7B на FLEURS ru
(5.27 против 5.99 — но это агрегат против отдельного языка, сравнивать напрямую нельзя).

`[INFERENCE]` Для матрицы важно: Qwen3-ASR официально заявляет «Offline / Streaming» в одной модели, но
streaming-режим в отчёте оценивается отдельной таблицей на Fleurs-en/zh (2.69/2.41 для 1.7B), а **не на русском**.
Русского streaming-числа не существует — это надо помечать.

## GigaAM v3

`ai-sage/GigaAM-v3`, MIT, 447 379 загрузок, `createdAt 2025-11-19`. Пять ревизий одного репозитория:
`ssl`, `ctc`, `rnnt`, `e2e_ctc`, `e2e_rnnt` (выбираются параметром `revision=`). 220–240 M параметров,
HuBERT-CTC претрейн на 700 000 ч русской речи.

**WER: официальный источник — `evaluation.md` в GitHub, а не карточка HF.** Средние по 10 наборам:
CTC **9.1**, RNNT **8.3**, E2E CTC **12.0**, E2E RNNT **11.2**, v2 CTC 11.1, v2 RNNT 10.6, T-one+LM 16.3,
Whisper 21.0. Карточка HF даёт 9.2 / 8.4 — небольшое расхождение с репозиторием (округление или другая ревизия).
`[INFERENCE]` В матрице цитировать репозиторий (9.1 / 8.3) как более свежий и детальный, а расхождение с карточкой
держать в примечании.

**Почему e2e хуже по WER.** E2E-модели выводят пунктуацию и регистр, а метрика считается после их снятия; при
этом F1 пунктуации у `e2e_rnnt` — 84.5 (запятая) / 86.7 (точка) / 79.8 (вопрос). То есть 11.2 % — это «цена
end-to-end режима», а не деградация распознавания. `[INFERENCE]` Для продукта это значит: если клиенту нужна
пунктуация, честное сравнение — e2e против «ctc/rnnt + внешний пунктуатор», а не e2e против ctc по WER.

**«Лимит 25 с» — это ограничение API, а не модели.** README: «ASR with `.transcribe` function is applicable for
audio **only up to 25 seconds**. To enable `.transcribe_longform` install the additional pyannote.audio
dependencies». То есть длинное аудио обрабатывается, но через внешний VAD-сегментатор. Формулировка D1
«лимит 25 с» без этой оговорки вводит в заблуждение: runtime обязан реализовать longform сам.

Стриминга нет — подтверждено отсутствием streaming-инференса в README и репозитории.

## T-one

`t-tech/T-one`, Apache-2.0, `createdAt 2025-07-14`, 71 M параметров, Conformer с SwiGLU/RMSNorm/RoPE/U-Net,
потоковые состояния только в последних двух слоях, чанки **300 мс**, обучение на 80 000 ч (57.9k телефония).

**Алфавит без латиницы — подтверждено машинно.** `vocab.json` содержит ровно 35 записей:
`[PAD]`, `|`, `а б в г д е ж з и й к л м н о п р с т у ф х ц ч ш щ ъ ы ь э ю я ё`. Ни одной латинской буквы,
ни цифр, ни дефиса. Для сценария «русская речь с английской IT-терминологией» это означает: любой термин
(`kubectl`, `Istio`, `CI/CD`) физически не может быть выдан в исходном виде — только кириллической
транслитерацией или потерей. Это не «особенность», а дисквалификация для основного сценария.

WER (карточка): call-center **8.63** против GigaAM-RNNT v2 10.22 / Whisper large-v3 19.39; named entities
**5.83** против 9.55 / 17.87; но CommonVoice 19 — 5.32 против **2.68** у GigaAM-RNNT v2. То есть T-one выигрывает
на телефонии и проигрывает на общем домене почти вдвое.

Заявленные «word-level timestamps» в коде оказываются уровнем фразы: вывод — `TextPhrase(text=…, start_time=…,
end_time=…)` на всю фразу. D1 это отметил корректно.

Портов CoreML/MLX нет. Единственный артефакт — `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08`.

## VibeVoice-ASR-Streaming

`microsoft/VibeVoice-ASR-Streaming-1.5B` и `-7B`, оба `createdAt 2026-09-02`, **MIT**, 10 языков включая `ru`,
`library_name: transformers`. README: «transcribes **Who (Speaker)** said **What (Content)**», «Customized Hotwords»,
техотчёт arXiv:2609.02812 (submitted 2 Sep 2026).

Качество (arXiv, таблица с атрибуцией): MLC-Challenge **Russian 15.67 WER / 19.47 cpWER**. Для сравнения в той же
таблице: English 8.44, German 21.83, Italian 17.00, Korean 9.09. То есть русский — средний для модели, и 15.67 %
заметно хуже, чем у Nemotron 3.5 (9.60) и Voxtral (6.02).

**Поправка к D2:** утверждение «MLX есть только для оффлайн `VibeVoice-ASR`» неверно — существуют
`mlx-community/VibeVoice-ASR-Streaming-7B-4bit` и `mlx-community/VibeVoice-ASR-Streaming-7B-bf16`. Это важно:
MLX-путь для streaming-варианта на Apple Silicon есть, пусть и community-сборка.

GGUF: `audio-cpp/VibeVoice-ASR-Streaming-7B-GGUF` (86 344 загрузки). LiteRT: `litert-community/...-1.5B`.
CoreML и ONNX для streaming-варианта не найдены — подтверждает D2.

## VAD и EOT

**Silero VAD.** Версия в репозитории — **6.2.3** (`pyproject.toml`), лицензия **MIT**. Файлы моделей в
`src/silero_vad/data/`: `silero_vad.onnx` (2.33 MB), `silero_vad_16k_op15.onnx`, `silero_vad_16k_sequence.onnx`,
`silero_vad_half.onnx`, `silero_vad_op18_ifless.onnx`, `silero_vad_openvino_16k.onnx`, `silero_vad_16k.safetensors`,
`silero_vad.jit`. Заявление «v6.2» подтверждено; в матрице писать точную версию `6.2.3`, потому что 6.2.1 и 6.2.3
имеют разные опубликованные конверсии.

**Smart Turn v3.2.** BSD-2-clause, «truly open model», **23 языка, и `ru` в списке явно** (🇷🇺 Russian рядом с
Ukrainian, Polish, Turkish и т.д.). База — Whisper Tiny + линейный классификатор, ~8 M параметров, вход — последние
8 с аудио, выход — вероятность завершённости хода. Важно: модель работает **на сыром аудио, не на транскрипте**
(просодия, темп, интонация) — значит русскость обеспечивается не языковой моделью, а покрытием обучающих данных.
Для нашего сценария это плюс: не зависит от качества ASR и от code-switching.

**FireRedVAD.** `FireRedTeam/FireRedVAD`, Apache-2.0, HF-репозиторий создан **2026-02-11**; standalone-репозиторий
анонсирован **2026.03.03** (до этого был частью FireRedASR2S, 2026.02.12). Поддерживает streaming/non-streaming VAD
и non-streaming AED. Метрики на собственном бенчмарке FLEURS-VAD-102 (~100 файлов на язык):

| Метрика | FireRedVAD | Silero-VAD | TEN-VAD | FunASR-VAD | WebRTC-VAD |
|---|---|---|---|---|---|
| AUC-ROC ↑ | **99.60** | 97.99 | 97.81 | — | — |
| F1 ↑ | **97.57** | 95.95 | 95.19 | 90.91 | 52.30 |
| False Alarm ↓ | **2.69** | 9.41 | 15.47 | 44.03 | 2.83 |

`[INFERENCE]` Осторожность: бенчмарк собственный (вендорский), выборка ~100 файлов на язык, и FireRedVAD — модель
той же компании, что и бенчмарк. Числа выглядят правдоподобно (FA в 3.5 раза ниже Silero при сопоставимом F1),
но независимого подтверждения нет.

## Эмбеддинги и диаризация

**WeSpeaker ResNet34-LM.** `pyannote/wespeaker-voxceleb-resnet34-LM` (CC-BY-4.0, 5 885 764 загрузок) — обёртка
для pyannote.audio вокруг WeSpeaker-чекпойнта. EER по первоисточнику (WeSpeaker README): **0.723 % / minDCF 0.069**
на vox1-O-clean после large-margin fine-tuning и AS-Norm; для сравнения ECAPA_TDNN_GLOB_c1024 — 0.728 % / 0.099.
Размерность 256-d. Лицензия весов наследует лицензию датасета (VoxCeleb → CC-BY-4.0) — это написано прямо в
README WeSpeaker, то есть цепочка лицензий здесь неочевидна и её надо фиксировать в манифесте.

**CAM++.** `funasr/campplus` (Apache-2.0, 2 471 загрузок). EER по первоисточнику (3D-Speaker README):
**VoxCeleb1-O 0.65 %**, CNCeleb 6.78 %, 3D-Speaker 7.75 %; 7.2 M параметров, 192-d. То есть CAM++ **лучше**
WeSpeaker ResNet34 по EER (0.65 против 0.723) при вчетверо меньшем размере — но пространства несовместимы
(192-d против 256-d), и E2 правильно это фиксирует.

**pyannote community-1.** `pyannote/speaker-diarization-community-1`, CC-BY-4.0, `createdAt 2025-04-15`.
Официальная таблица DER (полностью автоматическая обработка, без collar и без исключения overlap):

| Бенчмарк | legacy 3.1 | **community-1** | precision-2 |
|---|---|---|---|
| AISHELL-4 | 12.2 | **11.7** | 11.4 |
| AliMeeting (ch1) | 24.5 | **20.3** | 15.2 |
| AMI IHM | 18.8 | **17.0** | 12.9 |
| AMI SDM | 22.7 | **19.9** | 15.6 |
| AVA-AVD | 49.7 | **44.6** | 37.1 |
| CALLHOME p2 | 28.5 | **26.7** | 16.6 |
| DIHARD 3 full | 21.4 | **20.2** | 14.7 |
| Ego4D dev | 51.2 | **46.8** | 39.0 |
| MSDWild | 25.4 | **22.8** | 17.3 |
| RAMC | 22.2 | **20.8** | 10.5 |
| REPERE phase2 | 7.9 | **8.9** | 7.4 |
| VoxConverse v0.3 | 11.2 | **11.2** | 8.5 |

Скорость self-hosted (H100): 31 с на час аудио на AMI IHM, 37 с на час на DIHARD 3.

**DiariZen.** `BUT-FIT/diarizen-wavlm-large-s80-md`, **CC-BY-NC-4.0**, `createdAt 2025-04-15`. Карточка прямо
требует: «please ensure **non-commercial** usage, in accordance with the CC BY-NC 4.0 license». Структурированный
прунинг 80 %: WavLM Large 316.6M → 63.3M параметров, 17.8G → 3.8G MACs/с. DER при collar=0:

| Датасет | pyannote v3.1 | **DiariZen** |
|---|---|---|
| AMI | 22.4 | **14.0** |
| AISHELL-4 | 12.2 | **9.8** |
| AliMeeting | 24.4 | **12.5** |
| NOTSOFAR-1 | — | **17.9** |
| MSDWild | 25.3 | **15.6** |
| DIHARD3 | 21.7 | **14.5** |
| RAMC | 22.2 | **11.0** |
| VoxConverse | 11.3 | **9.2** |

Поправка: E2 приводит AMI **13.9 %**, карточка — **14.0 %**. Расхождение 0.1 абс. п., вероятно другая ревизия
или округление; цитировать надо 14.0.

`[INFERENCE]` Практический вывод для матрицы: по DER DiariZen лучший (AMI 14.0, DIHARD3 14.5), но лицензия
CC-BY-NC делает его непригодным для коммерческого продукта. Nemotron 3 Diarization (OpenMDW-1.1, DIHARD3 12.73
при 30.4 с) — единственный вариант, который одновременно лучший по DIHARD III и коммерчески чистый. DiariZen
стоит держать в каталоге как «необязательную, non-commercial» модель с явным флагом в манифесте.

## Русский IT-бенчмарк сообщества (J-community)

Первоисточник — `vakovalskii/stt-ru-benchmark` (GitHub), прогоны 6–7 августа 2026, WER с 95 % бутстрап-интервалами,
построчные транскрипты в репозитории. Цифры J воспроизводятся дословно:

| движок | студийная начитка n=775 | аудиокниги n=296 | спонтанная речь n=20 |
|---|---|---|---|
| GigaAM v3 | 4.50 % [4.02; 5.01] | **6.57 %** 🥇 [5.75; 7.42] | **6.95 %** 🥇 [4.58; 9.63] |
| Deepgram nova-3 | 6.55 % | 8.88 % | 7.12 % |
| Whisper large-v3-turbo | 4.44 % | 9.89 % | 7.73 % |
| Deepgram nova-2 | **3.97 %** | 10.22 % | 9.27 % |
| Parakeet TDT 0.6b v3 (RTX 4090) | 5.56 % | 10.84 % | 9.27 % |

Подмножество «латиница в эталоне»: Whisper large-v3-turbo/WhisperX **7.89 %** (n=38), GigaAM v3 **10.27 %** (n=38);
на Podlodka Speech — 10.84 % против 11.33 % (n=5).

Методика, которую надо переносить вместе с цифрами: (1) три домена разделены, потому что **ранжирование между
ними переворачивается** — nova-2 первый на начитке (3.97) и предпоследний на аудиокнигах (10.22); (2) доверительные
интервалы бутстрапом, и автор проверяет их пересечение (`gigaam [5.76; 7.49]` против `parakeet [9.75; 12.00]`
не пересекаются при n=296 — разрыв доказан); (3) латиница n=38 и n=5 — автор сам маркирует как наблюдение, а не
результат, и пишет: «Отдельного бенчмарка на смешанную речь не существует ни для русского, ни для английского».
(4) оговорка «Реальный материал стоит вдвое дороже лабораторного» — прямая поддержка тезиса о том, что FLEURS
не показателен для продукта.

`[INFERENCE]` Для нас главное следствие: **Parakeet TDT v3 на аудиокнигах 10.84 % против GigaAM 6.57 %**, при том
что официальный FLEURS у Parakeet 5.51 против «нет официального FLEURS у GigaAM v3». То есть официальные метрики
разных вендоров несопоставимы в принципе, и единственная точка сравнения — сторонний бенчмарк с одинаковым
протоколом. В матрице надо держать обе колонки и явно помечать, что сравнивать между собой можно только
внутри одной колонки.

## Выверенная матрица моделей

Легенда конверсий: ✅ — существует на HF (id в примечании), ⚠️ — только community-сборка, ❌ — не найдено.
«Лучшая официальная ru-метрика» — только из первоисточника вендора, с указанием набора.

| Операция | Модель / HF id | Языки | Стриминг | Лучшая офиц. ru-метрика | CoreML | MLX | ONNX | Лицензия |
|---|---|---|---|---|---|---|---|---|
| ASR batch (ru, точный) | `ai-sage/GigaAM-v3` (rev `rnnt`/`ctc`/`e2e_rnnt`/`e2e_ctc`) | ru (+en в шапке) | ❌ | 8.3 % (RNNT, среднее по 10 наборам, `evaluation.md`); аудиокниги 6.57 % (сторонний) | ⚠️ `smkrv/gigaam-v3-e2e-rnnt-coreml` | ⚠️ `al-bo/gigaam-v3-rnnt-mlx`, `aystream/GigaAM-v3-e2e-rnnt-mlx` | ✅ `istupakov/gigaam-v3-onnx` (MIT, v3_ctc/v3_e2e_*); sherpa #2901 | **MIT** |
| ASR batch (ru, пунктуация) | `ai-sage/GigaAM-v3` rev `e2e_rnnt` | ru | ❌ | 11.2 % WER, F1(,) 84.5 / F1(.) 86.7 | ⚠️ см. выше | ⚠️ `aystream/...e2e-rnnt-mlx` | ✅ `istupakov/gigaam-v3-onnx` | **MIT** |
| ASR batch (мультиязычный) | `nvidia/parakeet-tdt-0.6b-v3` | 25 европ., **ru** | ❌ | **FLEURS ru 5.51**; CoVoST2 ru 3.00 | ✅ `FluidInference/parakeet-tdt-0.6b-v3-coreml` | ✅ `mlx-community/parakeet-tdt-0.6b-v3` | ⚠️ `ysdede/parakeet-tdt-0.6b-v3-onnx` | **CC-BY-4.0** |
| ASR batch (улучш. точность) | `moondream/parakeet-ultra` | 25 европ., **ru** | ❌ | FLEURS-25 avg 9.55 (не ru отдельно) | ⚠️ `erponce/sherpa-onnx-parakeet-ultra-tdt-0.6b-v3-int8` | ❌ (только GGUF `handy-computer/parakeet-ultra-gguf`) | ⚠️ там же | **CC-BY-4.0** |
| ASR batch (мультиязычный) | `nvidia/canary-1b-v2` | 25 европ., **ru** | ❌ | **FLEURS ru 6.90**; CoVoST2 ru 5.14 | ✅ `FluidInference/canary-1b-v2-coreml` | ⚠️ `CogniSoftOrg/canary-1b-v2-mlx-bf16` | ✅ `istupakov/canary-1b-v2-onnx` | **CC-BY-4.0** |
| ASR batch/stream | `Qwen/Qwen3-ASR-1.7B`, `-0.6B` | 30 яз. + 22 диал., **ru** | ⚠️ заявлен, ru не измерен | **FLEURS ru 5.99** (1.7B), 9.91 (0.6B); CV ru 8.28 | ❌ не найдено | ✅ `mlx-community/Qwen3-ASR-1.7B-8bit`, `aufklarer/Qwen3-ASR-1.7B-MLX-8bit` | ❌ не найдено (есть GGUF) | **Apache-2.0** |
| ASR stream (ru) | `nvidia/nemotron-3.5-asr-streaming-0.6b` | 40 локалей, **ru-RU** | ✅ 80–1120 мс | **FLEURS ru 9.60 @560 мс** / 9.17 @1.12 с | ✅ `FluidInference/Nemotron-3.5-ASR-Streaming-Multilingual-0.6b-CoreML`, `aufklarer/...-CoreML-INT8` | ✅ `mlx-community/nemotron-3.5-asr-streaming-0.6b` (+`-8bit`) | ✅ `onnx-community/...-onnx-int4`, `soniqo/...-ONNX-{FP16,INT8}`; sherpa #3671/#3732 | **OpenMDW-1.1** |
| ASR stream (мультиязычный) | `mistralai/Voxtral-Mini-4B-Realtime-2602` | 13 яз., **ru** | ✅ 80–1200 + 2400 мс | **FLEURS ru 6.02 @480 мс** | ❌ **нет** | ✅ `mlx-community/...-2602-fp16`, `...-2602-4bit`, `...-Realtime-6bit` | ✅ `onnx-community/...-2602-ONNX` | **Apache-2.0** |
| ASR stream (ru, телефония) | `t-tech/T-one` | **только ru, без латиницы** | ✅ 300 мс чанки | call-center 8.63; CV19 5.32 | ❌ | ❌ | ✅ `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08`; sherpa #2571/#2575 | **Apache-2.0** |
| ASR stream + атрибуция спикера | `microsoft/VibeVoice-ASR-Streaming-7B`, `-1.5B` | 10 яз., **ru** | ✅ lookahead 0.5 с | MLC-Challenge ru **15.67 WER / 19.47 cpWER** | ❌ | ✅ `mlx-community/VibeVoice-ASR-Streaming-7B-4bit` / `-bf16` | ❌ (есть GGUF, LiteRT) | **MIT** |
| ASR batch (мультиязычный, база) | `openai/whisper-large-v3-turbo` | ~99 яз., ru | ❌ нативно | FLEURS 5.27 (агрегат); аудиокниги ru 9.89 % (сторонний) | ✅ WhisperKit, speech-swift `WhisperASR` | ⚠️ mlx-whisper | ✅ sherpa offline-whisper | **MIT** |
| VAD | Silero VAD **6.2.3** | язык-независим | ✅ | — (нет WER; F1 95.95 на FLEURS-VAD-102) | ✅ `aufklarer/Silero-VAD-v6.2.1-CoreML`, `FluidInference/silero-vad-coreml` (по карточке — baseline **v6.0.0**, не v6.2) | ✅ `aufklarer/Silero-VAD-v6.2.1-MLX`, `mlx-community/silero-vad-v6` | ✅ `istupakov/silero-vad-onnx` | **MIT** |
| VAD | `FireRedTeam/FireRedVAD` | язык-независим | ✅ | — (AUC 99.60 / F1 97.57 / FA 2.69 на FLEURS-VAD-102) | ✅ `aufklarer/FireRedVAD-CoreML` | ❌ (есть GGUF) | ✅ `subthx/firered-vad-onnx` | **Apache-2.0** |
| EOT | Smart Turn **v3.2** | **23 яз., ru есть** | ✅ (8 с окно) | — (не WER; классификатор) | ✅ `aufklarer/Smart-Turn-v3.2-CoreML` | ⚠️ `mlx-community/smart-turn-v3` | ✅ `soniqo/Smart-Turn-v3.2-ONNX`, `onnx-community/smart-turn-v3-ONNX` | **BSD-2-Clause** |
| Диаризация (stream+offline) | `nvidia/Nemotron-3-Diarization` | язык-независим (обучен на en + 21 яз. миксы) | ✅ 0.32–30.4 с | — (DIHARD III **12.73 %** DER @30.4 с, англ.) | ✅ `altic-dev/nemotron-3-diarization-coreml`, `FluidInference/...`, `aufklarer/...-100M-CoreML-INT8` | ✅ `mlx-community/Nemotron-3-Diarization` (+`-8bit`) | ✅ `onnx-community/Nemotron-3-Diarization-ONNX` | **OpenMDW-1.1** |
| Диаризация (offline) | `pyannote/speaker-diarization-community-1` | язык-независим | ❌ | — (AMI SDM 19.9 / DIHARD3 20.2, англ.) | ✅ `FluidInference/speaker-diarization-coreml` (offline-пайплайн = community-1, online = 3.1) | ❌ | ⚠️ community | **CC-BY-4.0** |
| Диаризация (offline, лучший DER) | `BUT-FIT/diarizen-wavlm-large-s80-md` | язык-независим | ❌ | — (AMI 14.0 / DIHARD3 14.5, англ.) | ⚠️ `sl-data-systems/diarizen-v2-coreml` (для `-v2`, 0 загрузок) | ❌ | ⚠️ `christopherthompson81/diarizen_onnx`, `simLeipzig/ultrawhisper-diarizen-onnx` (0 загрузок) | **CC-BY-NC-4.0** ⛔ non-commercial |
| Эмбеддинги (256-d) | `pyannote/wespeaker-voxceleb-resnet34-LM` | язык-независим | — | EER **0.723 %** vox1-O | ✅ `aufklarer/WeSpeaker-ResNet34-LM-CoreML` | ✅ `aufklarer/WeSpeaker-ResNet34-LM-MLX` | ✅ `onnx-community/wespeaker-voxceleb-resnet34-LM` | **CC-BY-4.0** |
| Эмбеддинги (192-d) | `funasr/campplus` (CAM++, 3D-Speaker) | язык-независим | — | EER **0.65 %** vox1-O | ✅ `FluidInference/campplus-coreml` | ⚠️ `mlx-community/campplus_*` | ⚠️ community | **Apache-2.0** |

## Неизвестные / что измерить

1. **Нет ни одного замера ru-IT-диктовки (code-switching) для Nemotron 3.5, Voxtral 4B Realtime, Qwen3-ASR.**
   Существующие числа — FLEURS (чтение книг) и MLC-Challenge (чтение). Основной сценарий не измерен ни у одного
   вендора. Это must-measure №1.
2. **Нет публичного ru-DER ни у одного диаризатора.** Нужны свои RTTM на русской многоголосой записи с
   IT-лексикой; протокол отчётности уже специфицирован NVIDIA в `diarization_evaluation.md` — использовать его.
3. **Веса «Voxtral Mini Transcribe 2.0»** (offline-базлайн с ru 4.75 % в таблице Mistral) на HF не найдены —
   проверить, публичны ли они вообще, иначе строка «offline-базлайн» в матрице невоспроизводима.
4. **Точное число параметров Nemotron-3-Diarization** — 99 226 504 (E2) против ≈99.2M из размера safetensors.
   Достать из заголовка safetensors (`curl` range-запроса к первым байтам файла).
5. **Реальный WER Nemotron 3.5 на 80 мс** в интерактивном режиме на русском: 10.84 % (LangID) — но это FLEURS;
   на спонтанной речи деградация при малом right-context обычно больше.
6. **Smart Turn v3.2 на русском**: 23 языка заявлены, но доля русского в обучающих данных неизвестна;
   метрика EOT-точности на ru не публиковалась.
7. **FireRedVAD vs Silero на русской речи**: бенчмарк FLEURS-VAD-102 вендорский; независимой проверки нет.
8. **Совместимость эмбеддинг-пространств**: 256-d WeSpeaker и 192-d CAM++ несравнимы (E2 прав); нужен замер
   деградации EER при переносе центроида из одного пространства в другое.
9. **Стриминговый режим Qwen3-ASR на русском**: заявлен «Offline / Streaming» в одной модели, но отчёт даёт
   streaming-числа только для Fleurs-en/zh. Русское streaming-число отсутствует.

## Источники

HF API и карточки (все обращения 2026-10-25/26):

- https://huggingface.co/api/models/nvidia/nemotron-3.5-asr-streaming-0.6b
- https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/raw/main/README.md
- https://huggingface.co/api/models/mistralai/Voxtral-Mini-4B-Realtime-2602
- https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602/raw/main/README.md
- https://huggingface.co/api/models/nvidia/Nemotron-3-Diarization (+ `/raw/main/config.json`, `diarization_evaluation.md`)
- https://huggingface.co/blog/nvidia/nemotron-diarization
- https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/raw/main/README.md
- https://huggingface.co/nvidia/canary-1b-v2/raw/main/README.md
- https://huggingface.co/moondream/parakeet-ultra/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3-ASR-0.6B/raw/main/README.md, `Qwen/Qwen3-ASR-1.7B/raw/main/README.md`
- https://huggingface.co/ai-sage/GigaAM-v3/raw/main/README.md
- https://huggingface.co/t-tech/T-one/raw/main/README.md (+ `vocab.json`)
- https://huggingface.co/microsoft/VibeVoice-ASR-Streaming-1.5B/raw/main/README.md
- https://huggingface.co/FireRedTeam/FireRedVAD/raw/main/README.md
- https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md/raw/main/README.md
- https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM/raw/main/README.md
- https://huggingface.co/funasr/campplus/raw/main/README.md
- https://huggingface.co/aufklarer/Smart-Turn-v3.2-CoreML/raw/main/README.md

arXiv:

- https://arxiv.org/abs/2601.21337v1 — Qwen3-ASR Technical Report (submitted 29 Jan 2026); таблицы CommonVoice /
  MLC-SLM / Fleurs по языкам
- https://arxiv.org/abs/2609.02812v1 — VibeVoice-ASR-Streaming Technical Report (submitted 2 Sep 2026)
- https://arxiv.org/abs/2506.01192 — GigaAM (InterSpeech 2025)

GitHub (на закреплённых SHA):

- `sherpa-onnx` @`532f1b5a`: `CHANGELOG.md#L116-L117` (Nemotron-3.5 QNN/ONNX), `#L182` (#3671),
  `#L650` (GigaAM v3 #2901), `#L802-L804` (T-one #2571/#2575/#2576), `scripts/nemo/GigaAM/run-ctc-v3-punct.sh`
- `speech-swift` @`cbad3be2`: `docs/benchmarks/asr-wer.md#L114-L131`
- `FluidAudio` @`1b0ed50d`: `Documentation/ASR/ParakeetUltra.md#L10-L40`,
  `Documentation/ASR/NemotronMultilingual.md#L170-L171`
- https://github.com/salute-developers/GigaAM — `README.md#L77` (лимит 25 с), `evaluation.md` (WER по наборам)
- https://github.com/pipecat-ai/smart-turn — `README.md#L11-L16` (BSD-2, 23 языка)
- https://github.com/wenet-e2e/wespeaker — `README.md#L95` (0.723 % EER ResNet34)
- https://github.com/modelscope/3D-Speaker — `README.md#L30` (CAM++ 0.65 % EER)
- https://github.com/pyannote/pyannote-audio — `README.md#L85-L99` (таблица DER community-1)
- https://github.com/vakovalskii/stt-ru-benchmark — `README.md#L22-L24` (домены), `#L90-L94` (латиница)
- https://github.com/snakers4/silero-vad — `pyproject.toml` (version 6.2.3), `src/silero_vad/data/`
