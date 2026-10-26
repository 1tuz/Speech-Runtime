# D1 — ASR-модели для русской речи с английской IT-терминологией

Дата среза: 2026-10-25. Все цифры — только из указанных источников. Где данных нет — написано «нет данных».
Пометка `[INFERENCE]` — мой вывод, не факт из источника.

---

## Итог

1. **Единственная открытая модель, специально обученная на русский и дающая готовый «читаемый» текст (пунктуация + регистр + числа + латиница) — GigaAM-v3 `e2e_ctc`/`e2e_rnnt`** (MIT, 220–240M, батч). Её e2e-версии формально проигрывают «сырым» CTC/RNNT по WER (12.0/11.2 против 9.1/8.3 в среднем), потому что выводят пунктуацию и англицизмы латиницей — это ожидаемо и объяснено авторами.
2. **GigaAM-v3 не умеет стриминг**: официального streaming-инференса нет, вопрос в issue #18 остаётся открытым (последний ответ — февраль 2026). Ограничение входа — 25 с на вызов `.transcribe`, длинное аудио — через VAD + pyannote-сегментацию (https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/README.md#L77).
3. **T-one (T-Bank) — единственная открытая русская модель с настоящим стримингом на CPU** (300 мс чанки, Apache-2.0, 71M), но её алфавит — 33 кириллические буквы и пробел: латиница, цифры и пунктуация физически невозможны (проверено в коде). Для IT-терминологии это дисквалифицирующе.
4. **Появился мультиязычный стриминг с русским: NVIDIA Nemotron 3.5 ASR Streaming 0.6B** (июнь 2026, OpenMDW-1.1, 40 локалей, `ru-RU` в «transcription-ready»), FLEURS ru 9.17 % при чанке 1.12 с и 10.84 % при 80 мс.
5. **Voxtral для русского расколот**: Mini-3B/Small-24B (2507) официально русский **не** заявляют (в списке языков карточки его нет), а Voxtral-Mini-4B-Realtime-2602 (февраль 2026, Apache-2.0) — заявляет: FLEURS ru 6.02 % при задержке 480 мс.
6. **Parakeet-TDT-0.6B-v3 и Canary-1B-v2 русский поддерживают** (CC-BY-4.0, 25 европейских языков, пунктуация+капитализация, word-level таймстемпы), FLEURS ru 5.51 % и 6.90 %; оба — батч (у Parakeet есть чанковый скрипт, не нативный стриминг).
7. **Qwen3-ASR (0.6B/1.7B, Apache-2.0, янв. 2026) русский поддерживает**, официальные per-language числа: FLEURS ru 9.91/5.99 %, CV ru 14.07/8.28 %. Стриминг только через vLLM (GPU) и без таймстемпов; таймстемпы — отдельной моделью Qwen3-ForcedAligner-0.6B (русский в списке 11 языков).
8. **Vosk и SenseVoice для задачи не годятся**: Vosk ru — старая Kaldi-модель без пунктуации (Golos crowd 4.4 %, но openstt calls 36.0 %); SenseVoice русский **не** поддерживает (только zh/yue/en/ja/ko).
9. **Независимого русского бенчмарка, где есть GigaAM-v3, не существует.** Есть лидерборд Vikhrmodels (8 моделей, включая только GigaAM **v2**), лидерборд HF Open ASR Leaderboard русский **не** покрывает (в нём fr/de/es/it/pt/hi/nl/hy). Поэтому «лучшая» модель для русского IT-языка сейчас не определяется по публичным данным.
10. **Один ONNX-движок покрывает почти весь список**: sherpa-onnx (C/C++/Swift/Go/Python) умеет GigaAM v1–v3 (включая punct-экспорты), T-one streaming, Whisper, Canary, Qwen3-ASR, Parakeet TDT, Omnilingual, Nemotron-3.5 streaming. Это делает ONNX-адаптер естественным «минимальным общим знаменателем» Runtime, а CoreML/MLX — ускорителями для конкретных моделей.

---

## Факты

### 1. Реестр моделей: что реально существует (проверено на HF/GitHub)

| Модель | Параметры (HF safetensors) | Архитектура | Стриминг | Лицензия | Русский официально |
|---|---|---|---|---|---|
| `ai-sage/GigaAM-v3` (`ctc`, `rnnt`, `e2e_ctc`, `e2e_rnnt`, `ssl`) | 220–240M (карточка) | Conformer 16 слоёв, d_model 768, 64 mel, subsampling ×4; HuBERT-CTC претрейн 700k ч, ASR 4k ч | нет | MIT | да (базовый язык) |
| `ai-sage/GigaAM-Multilingual` (`ctc`, `large_ctc`) | 220M / 600M | Conformer, charwise CTC, 2M ч, 70+ языков | нет | MIT | да (ru/kk/ky/uz — целевые) |
| `voicekit-team/T-one` | 71M (карточка) | Streaming CTC Conformer (SwiGLU, RMSNorm, RoPE, U-Net down/up), 80k ч, 300 мс чанки | да | Apache-2.0 | да (только ru) |
| `openai/whisper-large-v3` | 1 543 490 560 | Encoder-decoder Transformer, 99 языков | нет (чанкинг) | Apache-2.0 (HF metadata) | да |
| `openai/whisper-large-v3-turbo` | 808 878 080 | то же, урезанный декодер | нет | MIT (HF metadata) | да |
| `nvidia/parakeet-tdt-0.6b-v3` | 627 057 286 | FastConformer-TDT, 25 европ. языков, Granary | нет (есть чанковый скрипт) | CC-BY-4.0 | да (`ru`) |
| `nvidia/canary-1b-v2` | 978 998 272 | FastConformer + Transformer decoder, multitask | нет | CC-BY-4.0 | да (`ru`) |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | 637 997 088 | Cache-aware FastConformer-RNNT + language-ID prompt, 40 локалей | да (80 мс–1.12 с) | OpenMDW-1.1 | да (`ru-RU`, transcription-ready) |
| `Qwen/Qwen3-ASR-1.7B` / `-0.6B` | 2 349 217 408 / 938 008 576 | Qwen3-Omni-производный LLM-ASR, 52 языка/диалекта | только vLLM | Apache-2.0 | да (`ru`) |
| `mistralai/Voxtral-Mini-3B-2507` | 3B (карточка) | Ministral-3B + audio encoder | нет | Apache-2.0 | **нет в списке языков** |
| `mistralai/Voxtral-Small-24B-2507` | 24B | Mistral Small 3.1 + audio | нет | Apache-2.0 | **нет в списке языков** |
| `mistralai/Voxtral-Mini-4B-Realtime-2602` | 4 429 679 360 | causal audio encoder, Delayed Streams Modeling, 13 языков | да (240 мс–2.4 с) | Apache-2.0 | да |
| `facebook/omniASR-CTC-*` / `-LLM-*` | 325M / 975M / 3.08B / 6.5B (CTC) | wav2vec2-style + CTC / LLM-decoder, 1600+ языков | нет | Apache-2.0 | да (`rus_Cyrl` в `lang_ids.py`) |
| `alphacephei vosk-model-ru-0.42` | 1.8 GB (Kaldi) | Kaldi TDNN/chain + n-gram | да (online API) | Apache-2.0 | да |
| `FunAudioLLM/SenseVoiceSmall` | не указано в карточке (~0.94 GB весов) | FunASR encoder + CTC | нет | custom (`MODEL_LICENSE`) | **нет** (zn/en/yue/ja/ko) |

Доказательства по строкам, которые легко проверить неверно:

- GigaAM v3: 5 вариантов, `e2e_*` «support punctuation and text normalization» — https://huggingface.co/ai-sage/GigaAM-v3 ; архитектура (16 слоёв, d_model 768, 64 mel, hop 160, subsampling_factor 4, vocab 257 для CTC-punct) — docstring экспортёра `scripts/nemo/GigaAM/export-onnx-ctc-v3-punct.py:41-46` (sha `532f1b5a8a207bf53b116b747207c1506696c843`).
- GigaAM Multilingual: «220M / 600M … 2M hours … 70+ languages … character-wise CTC» — https://huggingface.co/ai-sage/GigaAM-Multilingual ; анонс в README («2026/06», arXiv 2607.10371, InterSpeech 2026) — https://github.com/salute-developers/GigaAM.
- T-one: 71M, Conformer со SwiGLU/RMSNorm/RoPE/U-Net, 300 мс чанки, 80k ч, обучение на NeMo 7 дней на 8×A100 — https://huggingface.co/t-tech/T-one (разделы Architecture / Training details).
- Nemotron 3.5: «40 language-locales», `ru-RU` в первой (transcription-ready) группе, «Cache-Aware FastConformer-RNNT», чанки 80/160/320/560/1120 мс, пунктуация и капитализация — https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b ; лицензия `openmdw-1.1` — frontmatter той же карточки.
- Qwen3-ASR: 52 языка/диалекта, `Russian (ru)` в таблице поддерживаемых, «Offline / Streaming», релиз 2026.1.29 — https://github.com/QwenLM/Qwen3-ASR (раздел Released Models).
- Voxtral: Mini-3B и Small-24B перечисляют только `en, fr, de, es, it, pt, nl, hi` (frontmatter `language:`) — https://huggingface.co/mistralai/Voxtral-Mini-3B-2507 , https://huggingface.co/mistralai/Voxtral-Small-24B-2507 ; Realtime-2602 перечисляет 13 языков, включая `ru` — https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602.
- Omnilingual: 1600+ языков, Apache-2.0, размеры 317M…6.5B — https://huggingface.co/facebook/omniASR-CTC-3B (таблица моделей); `rus_Cyrl` присутствует в списке языков — https://github.com/facebookresearch/omnilingual-asr/blob/main/src/omnilingual_asr/models/wav2vec2_llama/lang_ids.py#L1283.
- SenseVoice: `language="auto"` принимает `"zn","en","yue","ja","ko","nospeech"` — https://huggingface.co/FunAudioLLM/SenseVoiceSmall (примеры инференса).

**Про «новые 2026»:** GigaAM Multilingual (июнь 2026), Qwen3-ASR (янв. 2026), Voxtral Realtime (фев. 2026), Nemotron 3.5 ASR (июнь 2026), `moondream/parakeet-ultra` (пост-тренинг Parakeet v3, см. §6). Анонс «Voxtral 3 / 50+ языков» виден в поисковой выдаче, но официальную страницу прочитать не удалось (таймаут), и открытых весов Voxtral 3 в организации `mistralai` на HF **нет** (полный список моделей org: Realtime, Mini-3B, Small-24B, Realtime-Arabic, TTS) — то есть для локального Runtime на 2026-10-25 доступны только перечисленные выше Voxtral.

### 2. Матрица WER: официальные числа вендоров (русские наборы)

**GigaAM-v3, `evaluation.md` (вендорский замер, 10 наборов):** https://github.com/salute-developers/GigaAM/blob/main/evaluation.md

| Набор | V3 CTC | V3 RNNT | E2E CTC* | E2E RNNT* | V2 CTC | T-One + LM | Whisper* |
|---|---|---|---|---|---|---|---|
| Golos Farfield | 4.5 | 3.9 | 6.1 | 5.5 | 4.3 | 12.2 | 16.4 |
| Golos Crowd | 2.8 | 2.4 | 9.7 | 9.1 | 2.5 | 5.7 | 19.0 |
| Russian LibriSpeech | 4.7 | 4.4 | 6.4 | 6.4 | 5.2 | 6.2 | 9.4 |
| Mozilla Common Voice 19 | 1.3 | 0.9 | 3.2 | 3.0 | 1.5 | 5.2 | 5.5 |
| Natural Speech (internal) | 7.8 | 6.9 | 9.6 | 8.5 | 10.8 | 14.5 | 13.4 |
| Disordered Speech (internal) | 20.6 | 19.2 | 22.8 | 23.1 | 28.0 | 51.0 | 58.6 |
| Callcenter (internal) | 10.3 | 9.5 | 13.3 | 12.6 | 13.6 | 13.5 | 23.1 |
| OpenSTT Phone Calls | 18.6 | 17.4 | 20.0 | 19.1 | 20.7 | 19.8 | 27.4 |
| OpenSTT Youtube | 11.6 | 10.6 | 12.7 | 11.8 | 13.9 | 21.9 | 17.8 |
| OpenSTT Audiobooks | 8.7 | 8.2 | 10.3 | 9.3 | 10.8 | 13.4 | 14.3 |
| **Среднее** | **9.1** | **8.3** | **12.0** | **11.2** | 11.1 | 16.3 | 21.0 |

\* с пост-обработкой: снятие пунктуации/регистра, числительные → слова. Версия Whisper в таблице не указана (в тексте — «Whisper»; в карточке HF — «Whisper-large-v3»).

**T-one, карточка HF (вендорский замер, свой набор + публичные):** https://huggingface.co/t-tech/T-one

| Категория | T-one (71M) | GigaAM-RNNT v2 | GigaAM-CTC v2 | Vosk ru 0.54 (65M) | Whisper large-v3 |
|---|---|---|---|---|---|
| Call-center | **8.63** | 10.22 | 10.57 | 11.28 | 19.39 |
| Other telephony | **6.20** | 7.88 | 8.15 | 8.69 | 17.29 |
| Named entities | **5.83** | 9.55 | 9.81 | 12.12 | 17.87 |
| CommonVoice 19 (test) | 5.32 | **2.68** | 3.14 | 6.22 | 5.78 |
| OpenSTT asr_calls_2_val original | 20.27 | **20.07** | 21.24 | 22.64 | 29.02 |
| OpenSTT asr_calls_2_val re-labeled | **7.94** | 11.14 | 12.43 | 13.22 | 20.82 |

Замечание: T-one называет конкурента «Vosk-model-ru 0.54 (65M)», но на официальной странице Vosk такой версии нет (есть 0.42, 0.22, 0.10 и small-ru-0.22) — см. §3.

**Parakeet-TDT-0.6B-v3 / Canary-1B-v2 (NVIDIA, карточки HF):**

| Модель | FLEURS ru | CoVoST2 ru | Источник |
|---|---|---|---|
| parakeet-tdt-0.6b-v3 | 5.51 | 3.00 | https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 (model-index) |
| canary-1b-v2 | 6.90 | 5.14 | https://huggingface.co/nvidia/canary-1b-v2 (model-index) |

**Qwen3-ASR (официальный отчёт, Table A.2):** https://arxiv.org/html/2601.21337v1

| Набор | Qwen3-ASR-0.6B | Qwen3-ASR-1.7B | Qwen3-ASR-Flash-1208 (API) |
|---|---|---|---|
| CommonVoice ru | 14.07 | 8.28 | 5.73 |
| MLC-SLM ru | 19.24 | 15.17 | 13.16 |
| FLEURS ru | 9.91 | 5.99 | 4.81 |
| Агрегат CommonVoice (13 яз.) | 12.75 | 9.18 | — |
| Агрегат FLEURS (12 яз.) | 7.57 | 4.90 | — |

Для сравнения в том же отчёте (Table 5): Whisper-large-v3 — CommonVoice 10.77, FLEURS 5.27 (агрегаты по тем же наборам языков, включая ru).

**Voxtral (Mistral, FLEURS по языкам):** https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602 и https://arxiv.org/html/2602.11298v1 (Table 7)

| Модель | Задержка | FLEURS ru |
|---|---|---|
| Voxtral Mini Transcribe 2.0 (offline; **весов на HF нет**) | — | 4.75 |
| Voxtral Mini 4B Realtime 2602 | 160 мс | 9.53 |
| | 240 мс | 7.87 |
| | 480 мс | 6.02 |
| | 960 мс | 5.56 |
| | 2400 мс | 5.41 |
| Whisper (в таблице Mistral, версия не указана) | offline | 5.13 |

**Nemotron 3.5 ASR Streaming 0.6B, FLEURS ru (карточка):** https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b

| Режим | 80 мс | 160 мс | 320 мс | 560 мс | 1.12 с |
|---|---|---|---|---|---|
| Language Input (LangID) | 10.84 | 10.73 | 9.87 | 9.60 | **9.17** |
| Auto-detect | 12.47 | 12.09 | 11.01 | 10.57 | 10.03 |

Оговорка самой карточки: WER считается после нормализации текста, и «residual mismatches… may inflate the reported error rates».

**GigaAM Multilingual (ru), карточка:** https://huggingface.co/ai-sage/GigaAM-Multilingual

| Набор | 220M | 600M | Omnilingual 1B (LLM) | Seamless M4T large v2 | Whisper large v3 |
|---|---|---|---|---|---|
| CV ru | 7.1 | **5.1** | 13.6 | 9.2 | 9.1 |
| FLEURS ru | 4.4 | **3.0** | 6.4 | 4.6 | 3.1 |
| Internal ru | 7.6 | **6.0** | 14.6 | 16.1 | 10.1 |

Протокол (там же): исключены высказывания >30 с и референсы с цифрами; нормализация — lowercasing, снятие пунктуации, числительные словами; greedy. Это **единственный** найденный источник per-language чисел для Omnilingual и Seamless по русскому.

**Vosk, официальная таблица моделей:** https://alphacephei.com/vosk/models

| Модель | Размер | WER (по наборам) |
|---|---|---|
| vosk-model-ru-0.42 | 1.8 GB | 4.5 (свои аудиокниги), 11.1 (open_stt audiobooks), 19.5 (open_stt youtube), 36.0 (openstt calls), 4.4 (golos crowd), 17.9 (sova devices) |
| vosk-model-ru-0.22 | 1.5 GB | 5.74 / 13.35 / 20.73 / 37.38 / 8.65 / 19.71 |
| vosk-model-small-ru-0.22 | 45 MB | 22.71 (openstt audiobooks), 31.97 (youtube), 29.89 (sova), 11.79 (golos crowd) |
| vosk-model-ru-0.10 | 2.5 GB | 5.71 / 16.26 / 26.20 / 40.15 (узкополосная) |

### 3. Независимые сравнения (что есть и чего нет)

**(а) Лидерборд Vikhrmodels «Russian ASR Leaderboard»** — единственный найденный независимый русский лидерборд с общими тестовыми наборами (RuLS, CV 22.0, Tone Webinars/Books/Speak, Sova RuDevices). Данные — parquet `Vikhrmodels/russian-asr-leaderboard`, приложение — https://huggingface.co/spaces/Vikhrmodels/Russian_ASR_Leaderboard. WER, % (скачано и прочитано 2026-10-25):

| Модель | overall | RuLS | CV 22.0 | Webinars | Books | Speak | Sova |
|---|---|---|---|---|---|---|---|
| openai/whisper-large-v3 | **10.16** | 11.62 | 7.51 | 7.24 | 12.19 | 2.74 | 19.65 |
| bond005/whisper-podlodka-turbo | 10.36 | 11.91 | 6.36 | 15.21 | 8.96 | 3.14 | 16.55 |
| openai/whisper-large-v3-turbo | 11.07 | 11.88 | 8.17 | 9.89 | 13.29 | 2.80 | 20.37 |
| bond005/whisper-large-v3-ru-podlodka | 11.62 | 10.24 | 7.80 | 15.93 | 10.31 | 3.23 | 22.21 |
| nvidia/canary-1b-v2 | 13.55 | 20.16 | 9.12 | 13.71 | 11.45 | 4.97 | 21.89 |
| VOSK-model-ru-0.42 | 13.96 | 12.06 | 11.87 | 27.29 | 10.80 | 2.61 | 19.15 |
| GigaAM-ASR-V2-RNNT | 18.21 | 5.24 | 2.85 | **80.03** | 8.06 | 3.08 | 10.01 |
| GigaAM-ASR-V2-CTC | 18.74 | 5.26 | 3.42 | **80.19** | 7.72 | 3.01 | 12.86 |

Важное: **GigaAM v3 в лидерборде нет** (только v2). Аномалия GigaAM v2 на Webinars (~80 % WER) почти наверняка артефакт харнесса (длинные записи без VAD/лонгформы либо несовпадение нормализации референсов) — на остальных наборах GigaAM v2 лучший (CV 2.85, RuLS 5.24). `[INFERENCE]` Использовать строку GigaAM v2 как «качество модели» нельзя.

**(б) HF Open ASR Leaderboard русский не покрывает.** В пространстве `hf-audio/open_asr_leaderboard` языковые вкладки: fr, de, es, it, pt, hi, nl, hy (`init.py`, реестр `VERSIONS["21-10-2026"]`); датасеты мультиязычного лидерборда содержат `fleurs_ru`/`mcv_ru`, но опубликованных результатов по русскому в CSV нет. Английские таблицы (LibriSpeech/AMI/Earnings22) к русскому неприменимы — по ним нельзя объявлять модель лучшей.

**(в) Комьюнити-бенчмарк ru-speech-bench (сентябрь 2026, CPU-only)** — https://github.com/meshoklv/ru-speech-bench : WER не считался (нет человеческого референса), только «disagreement». Полезные измерения: T-one greedy, 1 поток ONNX Runtime — RTF 0.309; стриминг 99–123 мс на 300-мс чанк (0 из 1091 чанка >300 мс); задержка фразы 1.05–1.09 с (0.64–0.65 с при `MIN_SILENCE_DURATION=6`); память 600 МБ (greedy) / 5.2 ГБ с KenLM 5.46 ГБ. GigaAM-v3 (sherpa-onnx, int8, 4 потока): медиана 12.8 с на файл 7–130 с под нагрузкой.

**(г) Официальные заявления вендоров о превосходстве** (не независимые): GigaAM — LLM-as-a-Judge side-by-side против Whisper-large-v3, «70:30» в пользу e2e (https://habr.com/ru/companies/sberdevices/articles/973160/ и evaluation.md); T-one — WER на телефонии; NVIDIA — сравнение с Parakeet RNNT 1.1B по пропускной способности. Всё это — вендорские замеры.

### 4. Пунктуация, капитализация, нормализация

- **GigaAM-v3 e2e**: пунктуация + регистр + числа + валюта + латиница «из коробки». Качество пунктуации на внутреннем наборе (F1): запятая 83.7 (e2e-CTC) / **84.5** (e2e-RNNT), точка 86.7/86.7, вопрос 78.6/**79.8**; WER 16.0/14.2, CER 8.7/8.8 — https://github.com/salute-developers/GigaAM/blob/main/evaluation.md (таблица «End-to-end ASR»). Для сравнения: разметка Whisper-Punctuator даёт F1(,) 62.2, а обучение на таких метках — 50.3.
- **Почему e2e хуже по WER**: в Habr-статье прямо сказано — «WER > 10 % связан не с ухудшением контента, а с его нормализацией: добавились термины на английском языке, числа и другие символы»; там же перечислены правила пост-обработки (num2words, «youtube»→«ютуб», «hd»→«аш ди», «o2»→«о два») — https://habr.com/ru/companies/sberdevices/articles/973160/
- **GigaAM-v3 CTC/RNNT (не e2e)**: нижний регистр, без пунктуации. Словарь CTC — 34 токена: 33 кириллические буквы (без «ё») + пробел + blank (проверено по `tokens.txt` экспорта sherpa-onnx: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-ctc-giga-am-v3-russian-2025-12-16).
- **T-one**: пунктуации нет и быть не может — алфавит `LABELS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя "` — https://github.com/voicekit-team/T-one/blob/3c5b6c015038173840e62cea99e10cdb1c759116/tone/decoder.py#L23
- **Whisper large-v3 / turbo**: пунктуация и регистр есть (обучен на пунктуированном тексте), но известны галлюцинации на тишине/музыке — предупреждение в карточке модели: https://huggingface.co/openai/whisper-large-v3
- **Parakeet v3, Canary v2, Nemotron 3.5**: «Automatic punctuation and capitalization» — карточки (ссылки выше). Qwen3-ASR: пунктуация в выводе есть, отдельного замера F1 нет.
- **Voxtral Realtime**: пунктуация есть; в отчёте FluidAudio отмечено, что у Nemotron при малом чанке плотность пунктуации падает (это про Nemotron, не Voxtral) — /tmp/srr/repos/FluidAudio/Documentation/ASR/NemotronMultilingual.md:201.
- **Vosk ru**: пунктуации/регистра нет; на странице моделей Vosk для русского **нет** модели пунктуации/recase (grep по странице не находит «punct» вообще).

### 5. Таймстемпы на уровне слов

| Модель | Word-level | Как | Доказательство |
|---|---|---|---|
| GigaAM v3 | да (с 2026/04) | из CTC-кадров жадного декодирования, `frames_to_words` | https://github.com/salute-developers/GigaAM/blob/main/gigaam/timestamps_utils.py (функции `compute_frame_shift`, `frames_to_words`) |
| T-one | **заявлено** word-level в карточке, в коде — уровень фразы (`TextPhrase.start_time/end_time`) | logprob-splitter | https://github.com/voicekit-team/T-one/blob/3c5b6c015038173840e62cea99e10cdb1c759116/tone/pipeline.py#L19-L26 |
| Whisper | да | cross-attention/DTW, `return_timestamps="word"` | https://github.com/openai/whisper#readme ; в WhisperKit — `word`/`segment` granularities: /tmp/srr/repos/WhisperKit/README.md:397 |
| Parakeet v3 | да (char/word/segment) | TDT-декодер | https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 (пример `output[0].timestamp['word']`) |
| Canary v2 | да (word/segment) | — | https://huggingface.co/nvidia/canary-1b-v2 |
| Nemotron 3.5 | нет данных в карточке | — | карточка не описывает таймстемпы |
| Qwen3-ASR | да, но отдельной моделью | Qwen3-ForcedAligner-0.6B (11 языков, ru в списке); в стриминге таймстемпов нет | https://github.com/QwenLM/Qwen3-ASR (таблица моделей + раздел Streaming) |
| Voxtral Realtime | нет данных | — | карточка не описывает |
| Vosk | да | Kaldi word-level | https://alphacephei.com/vosk/models |
| Omnilingual | нет данных | CTC | карточка |

Точность word-таймстемпов ни одна из моделей не публикует в измеримом виде (кроме Qwen3-ForcedAligner: AAS 40.2 мс на русском, MFA-Labeled Raw — Table 9 отчёта https://arxiv.org/html/2601.21337v1).

### 6. Поведение на code-switching (русская речь + английские IT-термины)

Это ключевой для нас пункт, и здесь мало независимых данных. Что подтверждено:

1. **GigaAM-v3 e2e — единственная модель с явным заявлением.** Ответ мейнтейнера в issue #18 (2025-11-20): «new e2e-RNNT and e2e-CTC models that recognize speech with punctuation, capitalization, **English domain terms**, numbers, and other text normalization out of the box» — https://github.com/salute-developers/GigaAM/issues/18
2. **Словарь подтверждает возможность, а не качество.** В `tokens.txt` punct-трансдьюсера (1024 BPE) присутствуют латинские буквы в обоих регистрах, цифры, `₽ € $ % & « » — …`, подтокены вида `Beauty`, `HD`, `BP` — https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16 (`tokens.txt`, проверено локально: 1025 записей).
3. **Независимое наблюдение: качество латиницы слабое.** «Digits and Latin script are possible (unlike T-one), but Latin technical terms and brand names are usually written phonetically in Cyrillic or garbled ("Hap" / "hape" for a product name). A post-processing dictionary repairs recurring cases» — /tmp/srr/repos/ru-speech-bench… (файл `docs/engines/gigaam-v3.md`, https://github.com/meshoklv/ru-speech-bench/blob/master/docs/engines/gigaam-v3.md). Там же: «GigaAM-v3 … weak on Latin terms without a dictionary».
4. **T-one: латиница невозможна** (алфавит), «a Latin brand name becomes a phonetic Cyrillic guess; a language model does not fix this — the symbols are missing from the vocabulary» — https://github.com/meshoklv/ru-speech-bench/blob/master/docs/engines/t-one.md + код `decoder.py:23`.
5. **Habr (Sber) — пример провала на английском названии**: ref «поставь бэк ту ю селена гомез», гипотезы «Поставь бак, что есть, Селена Гомес.» и «Поставь Bach Te Sena Gomez.» — обе оценены как BAD. То есть даже e2e-модель на английских вставках ломается, когда это не «доменный термин», а название.
6. **Прямых замеров code-switching WER нет ни у одной модели** (ни GigaAM, ни Whisper, ни Parakeet/Canary/Nemotron/Voxtral/Qwen3). Ни в карточках, ни в отчётах я не нашёл бенчмарка вида «русский + английские термины» с числом. Это подтверждаемая пустота, а не пропуск поиска: проверены evaluation.md, карточки HF, отчёты arXiv 2506.01192 / 2601.21337 / 2602.11298 / 2509.14128, лидерборды.
7. Косвенный сигнал по Qwen3-ASR: кодек-свитчинг упоминается только для форс-алайнера («supports cross-lingual, code-switched scenarios» — Table 9), для самой ASR-модели — нет.
8. Для Nemotron 3.5 важно архитектурно: язык задаётся one-hot промптом (128-мерный, размазанный по времени), т.е. модель «настраивается» на один язык на высказывание; как она ведёт себя при вставке латиницы — не заявлено (`[INFERENCE]`: скорее всего транслитерирует, как и все CTC/RNNT с кириллическим словарём, но это надо мерить).

### 7. Форматы и порты (ONNX / CoreML / MLX)

**ONNX — покрытие почти полное, и это главный путь для Runtime:**

- sherpa-onnx умеет: `sense_voice`, `canary`, `qwen3_asr` (регистрация в `sherpa-onnx/csrc/offline-model-config.cc:23,26,32`, sha `532f1b5a8a207bf53b116b747207c1506696c843`), а также GigaAM/Parakeet/Nemotron/Whisper/Omnilingual через NeMo- и прочие импортёры.
- Готовые экспорты GigaAM: 8 репозиториев `csukuangfj/sherpa-onnx-nemo-{ctc,transducer}[-punct]-giga-am[-v2|-v3]-russian-*`, включая punct-варианты v3 (2025-12-16).
- T-one streaming: `cxx-api-examples/streaming-t-one-ctc-cxx-api.cc:10-30` (модель `sherpa-onnx-streaming-t-one-russian-2025-09-08`), поддержка добавлена в CHANGELOG (`CHANGELOG.md:802-804`).
- GigaAM v3 в sherpa-onnx: `CHANGELOG.md:650` («Export GigaAM v3 to sherpa-onnx (#2901)»); экспортёр кладёт метаданные `model_type=EncDecCTCModel`, `is_giga_am=1`, лицензию — `scripts/nemo/GigaAM/export-onnx-ctc-v3-punct.py:71-77`.
- Omnilingual: `CHANGELOG.md:530-531` (v2), `725-735` (C/C++/Swift/Go/JS/WASM/Dart/Pascal API).
- Nemotron 3.5 streaming: `CHANGELOG.md:182` («Add multilingual Nemotron-3.5 streaming ASR support (#3671)»), экспорт в ONNX/QNN — `116-117`.
- Официальный экспорт GigaAM в ONNX есть и в самом репозитории (`model.to_onnx`, fp32/fp16) — https://github.com/salute-developers/GigaAM (раздел ONNX Export).

**CoreML (Apple Silicon):**

- Parakeet v3: `FluidInference/parakeet-tdt-0.6b-v3-coreml` — дефолт библиотеки FluidAudio (`/tmp/srr/repos/FluidAudio/README.md:341`, sha `1b0ed50d686873128a4129b3510ce3914c849bab`); FLEURS ru на M4 Pro: **WER 7.2 %, CER 2.2 %, 209.7× RTFx, 350 высказываний** (`Documentation/Benchmarks.md:34`). Там же `parakeet-ultra-coreml` (25 языков, «recommended») и `parakeet-redux-coreml`.
- GigaAM v3: сторонние конвертации `vadimsuhanov/gigaam-v3-{ctc,rnnt,e2e-rnnt}-coreml`, `vadimsuhanov/gigaam-multilingual-large-ctc-coreml`, `smkrv/gigaam-v3-e2e-rnnt-coreml`, `maxhlv/gigaam-v3-coreml`, `inhum/gigaam-v3-coreml` (все существуют на HF; e2e-rnnt — отдельные encoder/predictor/joiner Float32 ML Programs, greedy-декодинг на стороне приложения).
- Whisper: WhisperKit (CoreML) — word/segment granularity (`/tmp/srr/repos/WhisperKit/README.md:397`, sha `9f99b5cb6a907730543823ad271b82532782a899`).
- Nemotron multilingual: CoreML-конвертация в FluidAudio, но модель «local-path-only», HF-репозитория нет (`Documentation/ASR/NemotronMultilingual.md`).

**MLX:**

- GigaAM: `al-bo/gigaam-v3-rnnt-mlx` (MIT, charwise RNNT, «lowercase text without punctuation»), `al-bo/gigaam-v3-ctc-mlx`, `aystream/GigaAM-v3-e2e-{ctc,rnnt}-mlx`, `ai-babai/gigaam-multilingual-mlx` (+ int8).
- Parakeet v3: `mlx-community/parakeet-tdt-0.6b-v3`.
- Qwen3-ASR: `mlx-community/Qwen3-ASR-{0.6B,1.7B}-{bf16,8bit,6bit,5bit,4bit}`.
- Voxtral: `mlx-community/Voxtral-Mini-3B-2507-bf16` (только Mini-3B, без ru в официальном списке).
- Whisper: `mlx-community/whisper-*` (mlx-whisper).
- Nemotron 3.5: `aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-{bf16,8bit}`, `dboris/nemotron-asr-mlx`.

**Прочее:** Voxtral Realtime — `ggml-org/Voxtral-Mini-4B-Realtime-2602-GGUF`, `onnx-community/Voxtral-Mini-4B-Realtime-2602-ONNX`. Qwen3-ASR — несколько community ONNX-экспортов (`louis030195/qwen3-asr-0.6b-onnx`, `andrewleech/...-1.7b-onnx`). T-one — только ONNX (PyTorch→ONNX скрипт `tone/scripts/export.py` в репозитории).

### 8. Лицензии

| Модель/артефакт | Лицензия | Комментарий |
|---|---|---|
| GigaAM v1–v3, Multilingual (код и веса) | MIT | HF metadata `license: mit`, LICENSE «Copyright (c) 2024 GigaChat Team» |
| T-one (код + веса) | Apache-2.0 | README + HF metadata; `kenlm.bin` отдельно не проверен |
| Whisper large-v3 | Apache-2.0 (HF metadata) | turbo — `mit` (HF metadata) |
| Parakeet v3 / Canary v2 | CC-BY-4.0 | «ready for commercial/non-commercial use» |
| Nemotron 3.5 ASR | OpenMDW-1.1 | «ready for commercial use» (карточка) |
| Qwen3-ASR | Apache-2.0 | — |
| Voxtral (все) | Apache-2.0 | — |
| Omnilingual ASR | Apache-2.0 (модель и код), датасет CC-BY-4.0 | — |
| Vosk ru модели | Apache-2.0 | — |
| SenseVoiceSmall | custom `MODEL_LICENSE` (FunASR) | не Apache/MIT |
| sherpa-onnx (код) | Apache-2.0 | экспортёры GigaAM в репозитории — Apache-2.0/Xiaomi |

---

## Ограничения и риски

1. **Все per-language числа по русскому, кроме лидерборда Vikhrmodels и ru-speech-bench, — вендорские.** GigaAM мерил конкурентов сам, T-one мерил конкурентов сам, NVIDIA/Mistral/Alibaba публикуют свои таблицы. Систематическая ошибка в пользу «своей» модели не исключена; протоколы нормализации у всех разные.
2. **Расхождение чисел на одном и том же наборе**: Whisper large-v3 на FLEURS ru — 3.1 (Sber), 5.13 (Mistral), на CV ru — 9.1 (Sber) и 7.51 (Vikhrmodels). Разница — в нормализации (числа словами? снятие пунктуации? версия CV?) и в версии Whisper. Любое сравнение «X vs Y» из разных таблиц некорректно.
3. **GigaAM v3 не проверен ни одним независимым русским бенчмарком.** Единственный независимый лидерборд содержит v2, и там строка v2 на Webinars выглядит сломанной (~80 % WER). Заявление «GigaAM v3 — SOTA на русском» на 2026-10-25 опирается только на вендорские замеры и LLM-as-a-Judge от самого вендора.
4. **e2e-модели GigaAM деградируют на редких словах и названиях** (BPE, «Невинномысску» с одной «c», «Гетеборг» через «и»), плюс склонны «исправлять» речь и менять синонимы («суперспособность» → «суперсила») — Habr. Для технических терминов это риск тихой подмены.
5. **GigaAM требует сегментации**: `.transcribe` — только до 25 с; лонгформа — через `pyannote/segmentation-3.0` с HF-токеном (gated). Для Runtime это означает обязательный VAD/чанкование и внешнюю зависимость при лонгформе.
6. **T-one 8 кГц**: `read_audio` декодирует в 8 кГц (`tone/demo/read_audio.py:50`), т.е. широкополосная речь деградируется по построению; это телефонная модель. Плюс KenLM 5.46 ГБ для beam search и отсутствие wheels для kenlm.
7. **Стриминговые модели с русским — компромисс по качеству**: FLEURS ru 9.17 (Nemotron @1.12 с), 6.02 (Voxtral Realtime @480 мс) против 3.0–4.4 у батчевых GigaAM Multilingual / GigaAM v3 на MCV/RLS. Стриминг на русском сейчас = потеря 2–6 п.п. WER относительно батча.
8. **Латиница/цифры — главный риск именно для нашей задачи.** Единственная модель с заявленной поддержкой английских терминов и латинским словарём — GigaAM v3 e2e; но независимое наблюдение говорит, что бренды и термины всё равно «пишутся кириллицей по слуху или портятся». Все остальные русские CTC/RNNT (T-one, Vosk, Nemotron ru-промпт) латиницу либо не могут, либо не заявляют.
9. **Лицензионные ловушки**: **OpenSTT — CC BY-NC 4.0** («commercial usage available after agreement with dataset authors», https://github.com/snakers4/open_stt#license) — использовать его как корпоративный тест-набор нельзя без договора; Golos — кастомный документ Sber (не стандартный идентификатор, https://voxkitchen.readthedocs.io/en/latest/datasets/golos/); Common Voice — CC0, но с октября 2025 датасеты отдаются только через Mozilla Data Collective, не через HF (https://huggingface.co/datasets/mozilla-foundation/common_voice_17_0). SenseVoice — кастомная лицензия. Nemotron — OpenMDW-1.1 (не OSI, надо читать).
10. **Воспроизводимость вендорских замеров низкая**: у T-one нет скриптов воспроизведения таблицы WER, у GigaAM evaluation.md без скриптов (манифесты внутренних наборов не опубликованы), у NVIDIA WER считается после собственной нормализации.

---

## Выводы для Runtime

**Что брать в первую волну адаптеров (русский):**

1. **GigaAM-v3 `e2e_rnnt` (или `e2e_ctc`) как «качественный батч-движок по умолчанию» для русского.** Причины: MIT, 240M, единственная модель с пунктуацией+регистром+латиницей из коробки, официальные word-таймстемпы, ONNX-экспорт (в т.ч. punct-варианты в sherpa-onnx и официальный `to_onnx`), CoreML/MLX-порты уже существуют. Runtime обязан: (а) сегментировать вход ≤25 с, (б) давать переключатель `e2e` (читаемый текст) vs `ctc/rnnt` (сырой текст, лучше WER), (в) поддержать пост-словарь терминов (замена/нормализация) как конфигурируемый постпроцессор — это ровно то, что делает комьюнити.
2. **T-one как стриминговый «черновик» — только если клиент принимает кириллическую транслитерацию терминов.** Иначе стриминг на русском нужно строить на Nemotron 3.5 (ONNX, 40 локалей, ru transcription-ready) или Voxtral Realtime (Apache-2.0, 480 мс). Оба варианта требуют собственного замера на IT-речи.
3. **ONNX — обязательный первый адаптер, и лучший «носитель» — sherpa-onnx**: один C API, готовые импортёры под GigaAM (v1–v3, punct), T-one streaming, Whisper, Canary, Parakeet, Qwen3-ASR, Omnilingual, Nemotron 3.5. Это снимает необходимость писать по адаптеру на модель: адаптер = «ONNX-движок + манифест модели».
4. **CoreML — второй адаптер, точечно**: Parakeet v3/Ultra через FluidAudio (уже ANE, 200×+ RTFx, ru FLEURS 7.2 %), Whisper через WhisperKit, GigaAM — через сторонние CoreML-бандлы (нужна валидация, они не официальные).
5. **MLX — третий адаптер, для больших моделей и для Qwen3-ASR**: mlx-community уже собрал Qwen3-ASR 0.6B/1.7B в 5 квантований, Parakeet v3 и GigaAM — тоже есть.
6. **Каталог моделей должен хранить не «модель», а «модель × движок × вариант»**: GigaAM v3 = {ctc, rnnt, e2e_ctc, e2e_rnnt} × {onnx-fp32, onnx-int8, coreml, mlx}; у каждой комбинации свои свойства (пунктуация, латиница, таймстемпы, размер). Это же свойство нужно для версионирования (v2 → v3 → multilingual) и для отката.
7. **Не включать в первую волну**: SenseVoice (нет ru), Vosk ru (нет пунктуации, устаревшая точность на телефонии 36 %), Voxtral Mini-3B/Small-24B (русский не заявлен), GigaAM Multilingual (charwise CTC без пунктуации — только если нужен ru+kk/ky/uz). Qwen3-ASR — во вторую волну (нужен GPU/vLLM для стриминга; батч на MLX возможен).
8. **Контракт API должен явно разделять три вещи, которые модели путают**: `text` (как произнесено), `normalized_text` (пунктуация/регистр/числа/латиница) и `terms` (найденные IT-термины с их написанием). Иначе клиент не сможет отличить «модель услышала kubernetes» от «модель написала кюбернетес».
9. **Для code-switching нужен собственный постпроцессор/словарь терминов** — ни одна модель не решает это надёжно; вендорские заявления покрывают «доменные термины», но не бренды и не названия.
10. **Не строить архитектуру на «лучшая модель по английским бенчмаркам»**: Parakeet/Canary/Qwen3 лидируют в английских таблицах, но по русскому у них 5.5–9.9 FLEURS и нет подтверждённой работы с латиницей внутри русской речи.

---

## Неизвестные / что измерить

**Чего не удалось подтвердить:**

1. Официальных per-language чисел русского у Voxtral Mini-3B/Small-24B нет вообще (русского нет в списке языков) — неизвестно, работает ли модель на русском приемлемо.
2. Точность word-level таймстемпов у GigaAM v3 (метод есть, метрики нет), у T-one (в карточке обещано, в коде — фразы), у Parakeet/Canary (обещано, чисел нет).
3. Поведение Nemotron 3.5 и Voxtral Realtime на вставках латиницы в русскую речь — не заявлено и не измерено.
4. Реальная деградация GigaAM v3 e2e на редких терминах/названиях — только качественные примеры из Habr, без числа.
5. Существует ли «Voxtral 3» с открытыми весами (анонс 50+ языков виден в выдаче, страница Mistral не открылась, весов на HF нет).
6. Причина аномалии GigaAM v2 на Tone Webinars в лидерборде Vikhrmodels (~80 % WER) — харнесс или модель.
7. Есть ли у Vosk ru-0.42 поддержка латиницы в словаре (словарь не публикуется отдельно) — неизвестно.
8. Лицензия OpenSTT и точный текст лицензии Golos (кастомный документ Sber).
9. Скорость GigaAM v3 e2e на Apple Silicon через CoreML/MLX — публичных замеров RTFx нет (есть только CPU-оценки сообщества: ~6× real time на CPU-оценке из комментария, медиана 12.8 с/файл на слабой VM).

**Что нужно измерить (наш план замеров):**

- WER/CER по каждой модели в двух режимах: «сырой» и «нормализованный» (единый нормализатор для всех: lowercasing, ё→е, снятие пунктуации, числа словами, латиница → как есть).
- **Term accuracy (главная метрика для нас)**: доля IT-терминов, выданных латиницей точно (`kubernetes`, `kubectl`, `PostgreSQL`, `Istio`, `CI/CD`), доля транслитерированных (`кубернетес`), доля искажённых. Считать по списку из N терминов на высказывание.
- Punctuation F1 (запятая/точка/вопрос) и exact-case accuracy на нашем наборе — сравнимо с методикой GigaAM.
- Hallucination rate на тишине/музыке/фоновом шуме (Whisper и LLM-подобные модели — в первую очередь Qwen3-ASR).
- Latency: RTF, time-to-first-token/фразы, p95 на чанк; для стриминговых — при разных chunk/delay (Nemotron 80/320/1120 мс, Voxtral 240/480/960 мс, T-one 300 мс).
- Память: RSS при загрузке/инференсе, отдельно для int8/fp16/4bit; время холодной загрузки (важно для «минимальных накладных расходов в простое»).
- Поведение при конкурентных сессиях (2–8) на одной модели: T-one уже показал, что дефолтная многопоточность ONNX хуже 1 потока (RTF 0.411 vs 0.309, CPU time ×6.4).

---

## Что проверить на собственном датасете русского IT-языка

1. **Состав набора.** Минимум 3–5 часов, 10+ дикторов, три регистра: (а) техническая лекция/доклад, (б) стендап/созвон инженеров, (в) диктовка команд и конфигов. Обязательные элементы в каждом сегменте: латинские термины (Kubernetes, PostgreSQL, Istio, kubectl, CI/CD, Helm, Grafana), аббревиатуры (k8s, CI, CD, SRE), версии и числа («1.29», «200 мс»), команды (`kubectl get pods -n prod`), русские словоформы от терминов («задеплоить», «куберовский», «в гитлабе»).
2. **Публичные прокси для калибровки (пока своего набора нет):** `bond005/podlodka_speech` — русский IT-подкаст (Podlodka), 67/20/20 сегментов, ~188 МБ (маленький, но домен ровно наш); OpenSTT YouTube (IT-доклады) — **только для внутренних исследований: CC BY-NC 4.0**, коммерческое использование по договору с авторами; Golos crowd/farfield (кастомная лицензия Sber — проверить у юристов); Russian LibriSpeech; Sova RuDevices (для «живой» речи); Common Voice ru (CC0, но с октября 2025 — только через Mozilla Data Collective). Лидерборд Vikhrmodels даёт готовую методику и наборы (RuLS, CV 22.0, Tone Webinars/Books/Speak, Sova) — можно переиспользовать их сплиты, чтобы числа были сравнимы с опубликованными.
3. **Метрики, которые надо считать обязательно** (в таком порядке приоритета): (1) term accuracy латиницей, (2) WER по нормализованному тексту, (3) punctuation F1, (4) WER по «сырому» тексту, (5) CER, (6) доля галлюцинаций на неречевых сегментах.
4. **Что именно проверить по каждой модели:**
   - GigaAM v3 `e2e_rnnt` vs `rnnt` vs `ctc`: даёт ли e2e выигрыш по term accuracy настолько, чтобы простить +2–3 п.п. WER; как ведёт себя на брендах (не «доменных терминах»).
   - T-one (greedy и beam+KenLM): измерить, какая доля терминов уходит в кириллическую транслитерацию — это и есть цена стриминга.
   - Nemotron 3.5 (320 мс и 1.12 с) и Voxtral Realtime (480/960 мс): сравнить со батчем по term accuracy, а не только по WER.
   - Parakeet v3 / Canary v2 / Qwen3-ASR: проверить, не «переводят» ли они английские термины и не теряют ли их при нормализации; у Qwen3-ASR — проверить hotwords (поддержка есть в sherpa-onnx-реализации) как механизм подсказки терминов.
   - Whisper large-v3 / turbo и `bond005/whisper-podlodka-turbo`: базлайн по term accuracy; проверить галлюцинации на тишине.
5. **Отдельный тест «латиница vs транслит».** Один и тот же текст произносится двумя способами («кубернетес» и «kubernetes», «постгрес» и «postgres»), проверяем, что модель выдаёт в каждом случае. Это даст матрицу «как люди говорят → как модель пишет», которой нет ни у одного вендора.
6. **Тест длинных сессий.** 30–60 минут непрерывной речи: проверить деградацию пунктуации к концу сессии (у Nemotron это документированный эффект при малых чанках — FluidAudio), накопление ошибок в таймстемпах и рост памяти.
7. **Тест конкурентности и простоя.** 1/2/4/8 параллельных сессий на одной загруженной модели; RSS и CPU в простое после прогрева (для требования «минимальные накладные расходы в простое»).
8. **Протокол нормализации зафиксировать до замеров** и опубликовать вместе с числами: иначе получим те же расхождения 3.1 vs 5.13 на FLEURS ru, что у Sber и Mistral.

---

## Источники

**Код (локальные клоны, permalink по sha):**
- sherpa-onnx `532f1b5a8a207bf53b116b747207c1506696c843`: `scripts/nemo/GigaAM/export-onnx-ctc-v3-punct.py`, `sherpa-onnx/csrc/offline-model-config.cc`, `cxx-api-examples/streaming-t-one-ctc-cxx-api.cc`, `CHANGELOG.md`.
- FluidAudio `1b0ed50d686873128a4129b3510ce3914c849bab`: `Documentation/Benchmarks.md`, `README.md`, `Documentation/ASR/NemotronMultilingual.md`.
- WhisperKit `9f99b5cb6a907730543823ad271b82532782a899`: `README.md`.
- T-one `3c5b6c015038173840e62cea99e10cdb1c759116` (клон в /tmp/srr/extra/tone): `tone/decoder.py:23`, `tone/pipeline.py:19-26`, `tone/demo/read_audio.py:50`.

**Карточки моделей HF:**
- https://huggingface.co/ai-sage/GigaAM-v3 ; https://huggingface.co/ai-sage/GigaAM-Multilingual
- https://huggingface.co/t-tech/T-one ; https://github.com/voicekit-team/T-one
- https://huggingface.co/openai/whisper-large-v3 ; https://huggingface.co/openai/whisper-large-v3-turbo
- https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 ; https://huggingface.co/nvidia/canary-1b-v2 ; https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b
- https://huggingface.co/Qwen/Qwen3-ASR-1.7B ; https://github.com/QwenLM/Qwen3-ASR
- https://huggingface.co/mistralai/Voxtral-Mini-3B-2507 ; https://huggingface.co/mistralai/Voxtral-Small-24B-2507 ; https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602
- https://huggingface.co/facebook/omniASR-CTC-3B ; https://huggingface.co/FunAudioLLM/SenseVoiceSmall
- https://huggingface.co/bond005/whisper-podlodka-turbo ; https://huggingface.co/antony66/whisper-large-v3-russian ; https://huggingface.co/bond005/podlodka_speech
- Экспорты sherpa-onnx: https://huggingface.co/csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16 (и соседние ctc/punct/v2)

**Бенчмарки и отчёты:**
- GigaAM evaluation: https://github.com/salute-developers/GigaAM/blob/main/evaluation.md
- Habr GigaAM-v3: https://habr.com/ru/companies/sberdevices/articles/973160/ ; GigaAM issue #18: https://github.com/salute-developers/GigaAM/issues/18
- Vikhrmodels Russian ASR Leaderboard: https://huggingface.co/spaces/Vikhrmodels/Russian_ASR_Leaderboard ; данные: https://huggingface.co/datasets/Vikhrmodels/russian-asr-leaderboard
- HF Open ASR Leaderboard: https://huggingface.co/spaces/hf-audio/open_asr_leaderboard ; отчёт: https://arxiv.org/html/2510.06961v4
- Qwen3-ASR Technical Report: https://arxiv.org/html/2601.21337v1
- Voxtral Realtime: https://arxiv.org/html/2602.11298v1
- Canary-1B-v2 & Parakeet-TDT-0.6B-v3: https://arxiv.org/abs/2509.14128
- GigaAM Multilingual: https://arxiv.org/abs/2607.10371 ; GigaAM (InterSpeech 2025): https://arxiv.org/abs/2506.01192
- Omnilingual ASR: https://arxiv.org/abs/2511.09690
- ru-speech-bench: https://github.com/meshoklv/ru-speech-bench ; статья: https://dev.to/meshoklv/benchmarking-russian-speech-recognition-on-cpu-t-one-gigaam-v3-and-cloud-models-3cgo
- Vosk models: https://alphacephei.com/vosk/models
