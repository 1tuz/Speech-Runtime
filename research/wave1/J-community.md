# J — Практический опыт сообщества: русская IT‑диктовка, стриминг, диаризация, ANE/MLX, «Ollama для речи»

Дата сбора: 2026‑10‑25. Классификация утверждений:
- **[измерение]** — есть числа и описанная методика (или воспроизводимый репорт с конфигом);
- **[отчёт]** — баг‑репорт/воспроизведение без полной методики;
- **[впечатление]** — субъективный опыт, маркетинг, обсуждение;
- **[вывод]** — моя интерпретация, не подтверждённая источником.

## Итог

1. **Единого победителя по русскому нет, но для IT‑диктовки связка «GigaAM v3 для русского + Whisper для смешанного ru/en» подтверждается независимо двумя разными командами** (открытый бенчмарк по доменам и Habr‑кейс с продовыми записями). Parakeet v3 русский формально поддерживает, но проигрывает во всех трёх доменах и не умеет code‑switching — для терминов вида `kubectl`/`Istio` это блокер.
2. **Чанкование потокового аудио стоит ~6 pp WER** (измерено: полное аудио 8.07 % против VAD‑чанков 13.86 %), а `AGC`/`VAD‑cut` — до +8 pp. Любой стриминговый режим должен либо переспрашивать модель на незакрытом окне (local agreement / re‑decode), либо явно отдавать клиенту «черновик vs финал».
3. **Реальная задержка фразы в потоковом пайплайне — 1–1.2 с** даже у специализированной CTC‑модели (emission 330 мс + порог тишины 600 мс + накладные), и она не может быть меньше emission latency модели. Маркетинговые «<400–800 мс» у dictation‑приложений не сопровождаются методикой (не указано, от какой точки считается), поэтому сравнивать их с этой цифрой нельзя. **[вывод]**
4. **Запуск двух CoreML‑моделей одновременно в одном процессе — воспроизводимый краш**: конкурентные ASR + диаризация корёжат общий E5RT/BNNS‑стейт (`EXC_BAD_ACCESS`), при том что документация библиотеки называла менеджеры потокобезопасными.
5. **MLX не потокобезопасен на уровне процесса**: thread‑local streams дают `Fatal error: There is no Stream(gpu, 0) in current thread`, а библиотека превращает ошибки MLX в `fatalError` — то есть падение демона. Кэш MLX без лимита раздувает пик до 19–23 ГБ на длинном аудио.
6. **ANE‑компиляция — главный источник «первого запуска»**: 440–560 с на large‑v3‑turbo, 105 с на энкодер Parakeet‑ultra на новом iPhone, минуты на вокодере; неудачная специализация **не кэшируется** и повторяется на каждом старте; на части чипов/ОС компиляция просто падает и модель молча уезжает на CPU.
7. **Память не возвращается**: ~50 МБ на цикл пересоздания стримингового транскрайбера (ANE), `coreaudiod` жрёт 10–12 % CPU после единственной транскрипции до выхода из приложения, VRAM в sherpa‑onnx‑websocket не освобождается после дисконнекта.
8. **Все известные локальные речевые серверы страдают одним и тем же**: нет честного управления моделями (скачивание «успешно», но неполное; дубликаты кэшей; 422 на валидный id), нет лимитов сессий (каждый WS‑коннект = свой ffmpeg), нет выбора модели в API (`model` — заглушка), спикеры теряются в дефолтном формате ответа.
9. **В нише «локальный речевой сервер» минимум четыре проекта** (OWhisper, speaches, nanosamurai, voicebox), и ни один не стал стандартом. OWhisper набрал 289 баллов на HN и умер: репозиторий удалён, домены не резолвятся, код остался только как клиентские крейты внутри другого продукта. Общий паттерн провала: продукт делается как «приложение + headless‑режим», а не как самостоятельный демон с контрактом.
10. **Спрос на «стандартный локальный сервер» сформулирован сообществом прямо** («wishing for a standard, full-service (live) transcription server for Whisper like Ollama has been for LLMs… standard vacuum here»), но бренд «Ollama for X» теперь токсичен, а смешивание локальных и облачных провайдеров в одном сервере вызывает отторжение.

## Факты

### A. Качество русской IT‑диктовки

**A1. [измерение] Ранжирование движков переворачивается между доменами.** Открытый бенчмарк `vakovalskii/stt-ru-benchmark` (https://github.com/vakovalskii/stt-ru-benchmark), прогоны 6–7 августа 2026, WER с 95 % бутстрап‑интервалами, построчные транскрипты в репозитории:

| движок | студийная начитка n=775 | аудиокниги n=296 | спонтанная речь n=20 |
|---|---:|---:|---:|
| GigaAM v3 | 4.50 % | **6.57 %** | **6.95 %** |
| Deepgram nova‑3 | 6.55 % | 8.88 % | 7.12 % |
| Whisper large‑v3‑turbo | 4.44 % | 9.89 % | 7.73 % |
| Deepgram nova‑2 | **3.97 %** | 10.22 % | 9.27 % |
| Parakeet TDT 0.6b v3 (локально, RTX 4090) | 5.56 % | 10.84 % | 9.27 % |

Источник: https://github.com/vakovalskii/stt-ru-benchmark (раздел «Результаты по доменам»). Автор отдельно фиксирует: «Реальный материал стоит вдвое дороже лабораторного».

**A2. [измерение, малая выборка] Английские вкрапления — слабое место GigaAM.** На подмножестве высказываний с латиницей в эталоне (термины вроде `observability`, `performance review`): Whisper 7.89 % (n=38), GigaAM v3 10.27 % (n=38); на Podlodka — 10.84 % против 11.33 % (n=5). Автор сам помечает: «🔴 Это наблюдение, а не результат… Отдельного бенчмарка на смешанную речь не существует ни для русского, ни для английского, хотя именно так звучит любая ИТ‑планёрка». URL тот же: https://github.com/vakovalskii/stt-ru-benchmark

**A3. [измерение] На шумных и реверберированных записях Whisper выигрывает у GigaAM на 6–19 pp.** Habr, 2026‑06‑02, продовые записи (корпоративная офлайн‑диктовка): на двух валидных шумных YouTube‑клипах 25.91 % против 32.45 %; на клипе с сильной реверберацией — 32.75 % против 51.97 % (19.2 pp). Сводка по 6 RU‑тестам (21 тыс. слов): дообученная Whisper‑turbo 9.43 %, GigaAM v3‑e2e‑rnnt 10.38 %, базовая turbo 13.25 %. https://habr.com/ru/articles/1042574/

**A4. [измерение] GigaAM на CPU в ~20 раз быстрее Whisper‑turbo, Whisper‑turbo на CPU не успевает за реальным временем.** RTF: Whisper turbo int8_float16 — 0.040 GPU / **1.02 CPU**; GigaAM v3 ONNX float — 0.033 GPU / **0.047 CPU**. RAM процесса 1.0 ГБ против 1.6 ГБ, размер модели 1.5 ГБ против 851 МБ. Вывод автора: «Whisper turbo на CPU тратит на минуту аудио чуть больше минуты… для онлайн‑диктовки модель не годится». https://habr.com/ru/articles/1042574/

**A5. [измерение] Задержка и качество push‑to‑talk по моделям (Windows/CPU + RTX 4090 Laptop).** tiny 56.2 % WER / 0.30 с; base 32.6 % / 0.45 с; small 23.4 % / 1.23 с; medium (GPU) 10.7 % / 1.75 с; large‑v3 (GPU) 8.8 % / 2.30 с; turbo (GPU) 7.9 % / 0.44 с; Vosk 13.0 % / 0.75 с; GigaAM v3‑e2e‑rnnt **3.3 % / 0.66 с** (CPU). Там же: RNNT 2.6 % против CTC 13.2 % на сложных текстах. https://habr.com/ru/articles/1002260/

**A6. [отчёт] Практические ловушки интеграции GigaAM (тот же кейс):** обязателен `float32` — на `int16` модель молча возвращает пустую строку; первые 2 вызова ONNX Runtime в 3–5 раз медленнее (нужен warmup); на английском — «пустой вывод или мусор»; пунктуация есть из коробки у `v3-e2e-rnnt`. https://habr.com/ru/articles/1002260/

**A7. [измерение] T‑one выигрывает у всех на телефонии и проигрывает на чтении.** T‑one 71.6M: колл‑центр 8.63 %, прочая телефония 6.20 %, именованные сущности 5.83 %, CommonVoice19 5.32 %; Whisper large‑v3 на тех же доменах 19.39 / 17.29 / 17.87 / 5.78; GigaAM‑RNNT‑v2 на CommonVoice19 2.68 %. Замеры out‑of‑the‑box. https://habr.com/ru/companies/tbank/articles/929850/

**A8. [отчёт производителя] Whisper галлюцинирует на телефонии:** «Whisper из коробки очень сильно галлюцинирует на наших данных и нужно придумывать разные дополнительные эвристики для снижения WER: фильтрацию VAD‑ом участков без речи, прокидывание suppress_tokens…». https://habr.com/ru/companies/tbank/articles/929850/

**A9. [факт] Parakeet‑tdt‑0.6b‑v3 действительно поддерживает ru и uk,** лицензия `cc-by-4.0`, 574K загрузок, 25 языков. https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 (metadata `language: … ru, uk`).

**A10. [отчёт] Parakeet v3 не умеет code‑switching** — при появлении одного‑двух английских слов модель «уезжает» в другой язык, принудительного выбора языка нет. Ответ мейнтейнера parakeet‑mlx: «The model inherently doesn't seem to support code switching». https://github.com/senstella/parakeet-mlx/issues/31

**A11. [впечатление] 30 дней продакшена на Parakeet v3:** нет кастомного словаря, «struggles with acronyms, company names, technical terms»; «Parakeet» распознаётся как «Parakit»; вывод — для диктовки всё равно нужен LLM‑постпроцессинг. https://www.reddit.com/r/LocalLLaMA/comments/1nf10ye/30_days_testing_parakeet_v3_vs_whisper/

**A12. [впечатление] Пользователь MacWhisper сменил приложение только ради GigaAM:** «its out‑of‑the‑box accuracy is phenomenal. It captures punctuation and linguistic nuances flawlessly, completely eliminating the need for a two‑step "ASR + LLM post‑processing" workflow». https://www.reddit.com/r/MacWhisper/comments/1xeyj11/feature_request_add_gigaam_local_model_native/

**A13. [отчёт] Parakeet TDT v3 в CoreML вообще не выдаёт цифры цифрами** — токены `0..9` лежат в отбрасываемой «спец‑полосе» `0..273`, декодер режет их как служебные; числа всегда словами. https://github.com/soniqo/speech-swift/issues/462

**A14. [отчёт] Parakeet v3 через MLX по умолчанию отдаёт текст без пунктуации и заглавных** — контекст‑промпт `<|startofcontext|><|pnc|><|itn|>` не подаётся; после уточнения выяснилось, что и референс NeMo без прайминга не пунктуирует, то есть это свойство модели, а не баг обёртки. https://github.com/senstella/parakeet-mlx/issues/62

**A15. [измерение] RU‑файн‑тюн Whisper‑turbo ломает слова пробелом:** 286 разрывов в 247 из 775 высказываний (32 %), WER 8.80 % → 5.01 % после oracle‑починки; у остальных движков 2–9 случаев на сплит. https://github.com/vakovalskii/stt-ru-benchmark

**A16. [факт] GigaAM v3 существует и живой:** `ai-sage/GigaAM-v3`, MIT, создан 2025‑11‑19, 447 379 загрузок, 193 лайка. https://huggingface.co/ai-sage/GigaAM-v3

### B. Реальные задержки потокового распознавания

**B1. [измерение производителя] Задержка фразы в потоковом пайплайне T‑one — 1–1.2 с.** Разложение: emission latency ≈ 330 мс + `silence_duration_threshold` 600 мс + накладные на преобразования и декодер; чанк 300 мс; граница чанка добавляет до одного чанка задержки. Методика: тайминги фраз размечены офлайн‑моделью, аудио подаётся с RTF=1. https://habr.com/ru/companies/tbank/articles/929850/ (раздел «Скорость модели»)

**B2. [измерение производителя] Emission latency — свойство CTC, а не реализации:** «в среднем он находится в районе 330 мс», разброс есть. https://habr.com/ru/companies/tbank/articles/929850/

**B3. [измерение] Чанкование режет качество на ~6 pp.** Один и тот же звук, один эталон: полное аудио 8.07 % WER; Silero‑VAD чанки с паузой ≥300 мс — 13.86 %; ≥500 мс — 14.47 %. Проверяли, что чанки + тишина покрывают файл с точностью <50 мс, т.е. это не артефакт покрытия. https://habr.com/ru/articles/1042574/

**B4. [измерение] Предобработка «на улучшение» ухудшает WER:** AGC −20 dBFS +6.1 pp, оконная AGC +7.3 pp, VAD‑cut +7.9 pp, VAD‑cut + AGC +8.8 pp; DSP‑фильтрация и лёгкий noisereduce — в пределах ±1 pp. https://habr.com/ru/articles/1042574/

**B5. [отчёт + ответ мейнтейнера] WhisperLiveKit: «compute time ~30–40 % of elapsed» → отставание растёт.** Мейнтейнер: «this is the expected failure mode when the machine cannot keep up»; рекомендации — меньшая модель, `--backend faster-whisper`, GPU. https://github.com/QuentinFuxa/WhisperLiveKit/issues/388

**B6. [отчёт] WhisperLiveKit зависает на ~12 минуте непрерывного стриминга** (без диаризации); мейнтейнер для длинных записей советует офлайн‑WhisperX после записи. https://github.com/QuentinFuxa/WhisperLiveKit/discussions/89

**B7. [измерение с оговоркой автора] Первый preview ≈0.498 с, commit‑финал ≈0.783 с** — но это время получения пакета клиентом, а не end‑to‑end p95: «**не** p95 задержка от начала слова до UI». https://github.com/hrygo/SpeechRail/issues/372

**B8. [измерение] Длинная сессия: 2220.529 с аудио обработано за 2222.563 с (RTF ≈ 1.0).** Пик физической памяти: медиана 11.895/12.639 GiB на процесс, ASR‑воркер 5.491/6.148 GiB; 164 сегмента, 2050 preview. https://github.com/hrygo/SpeechRail/issues/336

**B9. [впечатление/маркетинг с числами] Dictation‑приложение на MLX: <500 мс без LLM‑постобработки, p50 ≈800 мс с ней** (Parakeet 0.6B + Llama 3B, M1/M2/M4); при N=20 few‑shot примеров латентность LLM растёт до 1.5–3 с. https://www.reddit.com/r/LocalLLaMA/comments/1qr8vbn/shockingly_fast_local_speechtotext_llm_cleanup_on/

**B10. [впечатление] «60 минут аудио < 30 секунд» (≈120×RT) на Parakeet v3 через FluidAudio,** с оговоркой «after the local model has been loaded the first time». https://www.reddit.com/r/LocalLLaMA/comments/1s3x30d/macparakeet_free_opensource_wisprflow_alternative/

**B11. [измерение] Пропускная способность Parakeet v3 в батче:** медиана 0.088 с на 11 с аудио = ×126 RT на одной RTX 4090, 627M параметров, 2.3 ГБ VRAM. https://github.com/vakovalskii/stt-ru-benchmark

### C. Диаризация: overlap, число спикеров, русский

**C1. [отчёт] Русский подкаст с 3 спикерами стабильно детектируется как 2.** Запрос на `minSpeakers/maxSpeakers/numSpeakers` в FluidAudio: «A Russian podcast with 3 speakers is consistently detected as only 2 speakers, despite clear voice differences». https://github.com/FluidInference/FluidAudio/issues/217

**C2. [измерение] Пик памяти диаризации растёт линейно с длиной записи:** +515 МБ на 636 с (один голос); 17 мин, два голоса — +607 МБ при 4/8 воркерах, +403 МБ при 2/4, +339 МБ при 1/2 (но 8.7 с против 5.2 с). Причина — неограниченный `AsyncStream` между сегментером и эмбеддером (нет backpressure). https://github.com/argmaxinc/argmax-oss-swift/issues/551

**C3. [измерение] Деградация диаризации на iOS 27 beta:** 14‑минутный клип AMI на 4 спикера — 383 сегмента, потеряно ~240 с речи (на macOS 26.5 — ~12 с), **все сегменты получают `speakerId = 0`**; бит‑в‑бит одинаково при `.cpuAndNeuralEngine`, `.cpuAndGPU`, `.cpuOnly`. https://github.com/argmaxinc/argmax-oss-swift/issues/488

**C4. [измерение] Лимит спикеров в streaming Sortformer не «находит» спикеров, а обрезает каналы:** 30‑секундная последовательность трёх спикеров даёт ID 0,1,2 по умолчанию и только 0,1 при кэпе 2. Перекрытие двух спикеров активирует оба канала, но текущее представление вывода в WLK не может выразить одновременную речь. https://github.com/QuentinFuxa/WhisperLiveKit/issues/410

**C5. [отчёт + причина] «Диаризация не работает» в WhisperLiveKit оказалась форматом ответа:** `/v1/audio/transcriptions` по умолчанию `response_format=json` и возвращает только склеенный `text`; метки спикеров есть лишь в `diarized_json`. https://github.com/QuentinFuxa/WhisperLiveKit/discussions/460

**C6. [впечатление] «Very hard time with overlapping speakers and the number of speakers detected»** — при этом общий вывод: локальный STT «solved» для встреч, но «95 % accuracy feels impossible» именно на таймкодах + спикерах + одновременной речи. https://www.reddit.com/r/LocalLLaMA/comments/1nf10ye/30_days_testing_parakeet_v3_vs_whisper/

**C7. [отчёт] Экстрактор спикер‑эмбеддингов дорогой:** 40‑минутный файл, 10 прогонов — RAM растёт до 6 ГБ, до 24 спикер‑id; «computemapping cost 500M for 10s wav, so need to limit the length». https://github.com/k2-fsa/sherpa-onnx/issues/974

**C8. [факт из документации] Штатная диаризация whisper.cpp — экспериментальный tinydiarize** только с моделью `small.en-tdrz` (английский). https://github.com/ggml-org/whisper.cpp#L744-L757

**C9. [отчёт] Результаты диаризации расходятся между пайплайнами:** одинаковый клип, pyannote/speaker-diarization‑3.0 и k2‑fsa дают разные разбиения; тред ушёл в сверку эмбеддингов. https://github.com/k2-fsa/sherpa-onnx/issues/1708

**C10. [измерение] DER диаризации на 15 медицинских диалогах (PriMock57, ~2.4 ч), автоматическое число спикеров, допуск ±250 мс.** Батч: Pyannote Precision‑3 2.891 % (18.9 с/запись), **Nemotron 3 4.803 % (0.688 с/запись)**, Pyannote Community‑1 6.620 %, Sortformer v1 6.778 %, Sortformer v2.1 7.974 %, VibeVoice‑ASR 8.233 % (123 с). Стриминг: Pyannote live 3.959 %, Nemotron 4.971 %, Sortformer v2.1 6.958 %, VibeVoice 1.5B/7B — 17.2/18.0 %. Автор отдельно оговаривает, что стриминговые пресеты гонялись реплеем готового файла и «эти оценки не устанавливают живую латентность». https://www.reddit.com/r/speechtech/comments/1wq1fio/i_compared_diarization_models_on_15_clinical/

**C11. [впечатление] Что ломается в диаризации в проде — сформулировано сообществом:** «speaker labels drift after 20 minutes… the summary says the wrong person promised something. That is worse than a few missed filler words»; список для проверки: число спикеров, свапы, позднее подключение, overlap, похожие голоса, короткие вставки, скорость, выравнивание с текстом, live vs async. https://www.reddit.com/r/speechtech/comments/1u5g9hm/diarization_should_be_benchmarked_separately_from/

**C12. [ответ мейнтейнера] VAD — не главный источник задержки:** TEN VAD протестировали адаптером, «alpha results were not good enough to justify replacing Silero, and VAD is not the main latency bottleneck in WhisperLiveKit». https://github.com/QuentinFuxa/WhisperLiveKit/discussions/280

### D. Несколько речевых моделей в одном процессе: память и конфликты

**D1. [отчёт] Конкурентные CoreML‑модели в одном процессе — краш.** `SlidingWindowAsrManager` (Parakeet TDT v3) + `DiarizerManager` на одном аудио: `EXC_BAD_ACCESS` (byte‑write permission fault) из‑за повреждения общего E5RT/BNNS‑стейта; воспроизводится на M1–M4 и macOS 14.6.1/15.7.4/26.3.1/26.5.0. Отдельно отмечено, что `API.md` называл менеджеры потокобезопасными. https://github.com/FluidInference/FluidAudio/issues/661

**D2. [отчёт] MLX: eval из разных потоков фатален.** С core ≥0.31.2 `Stream.gpu`/`Stream.cpu` — process‑global `static let`, а `CommandEncoder` стал thread‑local: `Fatal error: There is no Stream(gpu, 0) in current thread`. https://github.com/ml-explore/mlx-swift/issues/457

**D3. [отчёт] MLX: deadlock/краш на `description` при активном eval**, и «mlx-swift's default error handler turns *any* MLX error into `fatalError`/SIGTRAP» — то есть любая ошибка инференса убивает хост‑процесс. https://github.com/ml-explore/mlx-swift/issues/428

**D4. [измерение] Кэш MLX без лимита: 19–23 ГБ на длинном аудио.** MOSS‑Transcribe‑Diarize‑0.9B‑MLX‑INT8, M4 Pro 24 ГБ: 12 мин — ~19 ГБ, 31 мин — 18.9 ГБ, 43 мин — ~20 ГБ, 68 мин — 22.9 ГБ. С `cacheLimit = 2 GB`: 5.2 / 7.0 / 8.6 ГБ, и **быстрее** (31 мин: 3.3 → 2.0 мин; 43 мин: 7.6 → 4.3 мин). После фикса — 6.3–6.6 ГБ на 20‑минутной записи. https://github.com/soniqo/speech-swift/issues/519

**D5. [измерение] Утечка на пересоздании стримингового транскрайбера (ANE):** +52 и +48 МБ за цикл 120 с (по 8 циклов, база 422 МБ → 840/808 МБ), под плотной речью 75–165 МБ за цикл; 33 цикла — 33 корректных `stopStreamTranscription()`/выхода из realtime‑цикла, т.е. Swift‑уровень чист. Уничтожение инстанса `WhisperKit` возвращает лишь 59/65/165 МБ. На `cpuAndGPU` — колебания ±300 МБ вокруг ~3.5 ГБ без монотонного тренда. 90‑минутный прогон: пик 4 069 МБ, ОС вернула ~1.5 ГБ, стабилизация 2.6–3.0 ГБ; в другом прогоне — `swift_abortAllocationFailure` при `MALLOC` 4.9 ГБ. Гипотеза автора: память ANE‑контекста/IOSurface выделяется вне процесса и не освобождается. https://github.com/argmaxinc/argmax-oss-swift/issues/517

**D6. [отчёт/измерение] После одной транскрипции `coreaudiod` держит 10–12 % CPU** до выхода приложения; `kill` демона не помогает; в трассировке фигурируют BNNS‑файлы из `com.apple.e5rt.e5bundlecache`. https://github.com/argmaxinc/argmax-oss-swift/issues/393

**D7. [измерение] ASR‑only потребитель тянет TTS‑подсистему:** бандл `FluidAudio_FluidAudio.bundle` 1.0 МБ целиком из LuxTTS‑ресурсов; исходники TTS 2.4 МБ против ASR 1.3 МБ, Diarizer 728 КБ, VAD 60 КБ. https://github.com/FluidInference/FluidAudio/issues/990

**D8. [отчёт] «Каждое голосовое приложение грузит 3–5 моделей последовательно с ручным прогрессом»** — прямая мотивация для `ModelSet`/`ModelLoader` в speech‑swift. https://github.com/soniqo/speech-swift/issues/123

**D9. [ответ мейнтейнера] Масштабирование «воркерами» = копии моделей в памяти:** «Extra workers load extra model copies and should be added only when the device and memory budget support them». https://github.com/QuentinFuxa/WhisperLiveKit/discussions/62

**D10. [отчёт] MLX на 16 ГБ: нужен wired‑limit и бюджет Metal.** Предложение `setWiredLimit(maxRecommendedWorkingSetSize * 0.9)` и `MetalBudget.canFit(modelBytes:)`; замер: pre‑allocated KV cache оказался **0.8x** от concat из‑за copy‑on‑write, т.е. «очевидная» оптимизация не сработала. https://github.com/soniqo/speech-swift/issues/157

**D11. [отчёт] Гонки данных в аудио‑слое WhisperKit** (thread sanitizer): `WhisperKit.progress`, `AudioProcessor.audioEnergy/audioSamples`. https://github.com/argmaxinc/argmax-oss-swift/issues/331 , https://github.com/argmaxinc/argmax-oss-swift/issues/442

### E. CoreML / ANE: компиляция, первый запуск, зависания, тихие фолбэки

**E1. [измерение] Компиляция large‑v3‑turbo: 440 с на M4 Pro (48 ГБ) и 560 с на M2 Pro (32 ГБ)** на macOS 15.3.1; повторные загрузки 3–5 с; на M2/14.6.1 — 18.99 с. https://github.com/argmaxinc/argmax-oss-swift/issues/309

**E2. [отчёт] «Зависает навсегда» + ANE‑ошибки загрузки:** `ANE model load has failed for on-device compiled macho. Must re-compile the E5 bundle… MILCompilerForANE error… Couldn't communicate with a helper application`; пользователь: «I'm pretty sure it's stuck infinitely»; другой: ~10 минут на `large-v3-v20240930_turbo_632MB` на M1 Pro. https://github.com/argmaxinc/argmax-oss-swift/issues/268

**E3. [измерение] Компиляция энкодера зависит от чипа и ОС:** iPhone 16 Pro Max — 12 163.72 мс, iPhone 18 Pro Max — **104 816.09 мс** (iOS 27.0.1, одинаковый код и модель); v3 на первом запуске 181 507 мс, затем 53 884 мс; после переустановки — снова. Обход: `encoderComputeUnits: .cpuAndGPU` грузится ~1 с «with identical WER», но теряется background execution. https://github.com/FluidInference/FluidAudio/issues/1022

**E4. [измерение] ANE‑компиляция может просто падать, и CoreML молча уходит на CPU/GPU.** Nemotron3Diarizer, M3 Max: `ANECCompile() FAILED`; RTFx: fast32 ane 122.3× / all 99.8×, low 18.5× / 19.0×, fast128 233.7× / 190.4×. https://github.com/FluidInference/FluidAudio/issues/951

**E5. [отчёт] Неудачная ANE‑специализация не кэшируется** → «multi‑second stall on every app launch»; один и тот же артефакт компилируется на macOS 27.2 и падает на iOS 27.2 (A16). https://github.com/soniqo/speech-swift/issues/503

**E6. [отчёт] whisper.cpp на M4 + macOS 26.4 beta: ANE inference fails, фолбэк на Metal, «~2‑3× slower than expected ANE performance».** https://github.com/ggml-org/whisper.cpp/issues/3702

**E7. [отчёт] ANECompilerService жрёт ~100 % ядра и ~1 ГБ RSS минутами,** процесс блокируется синхронно в `MLModel.init` → `MLE5ProgramLibrary.prepareAndReturnError`. https://github.com/soniqo/speech-swift/issues/464

**E8. [отчёт] Предкомпилированный `.mlmodelc` может сегфолтить на ANE (M5 Max)** — `bnns::GraphCompile` → `E5RT::OnDeviceAOTCompilerInterface::Compile`; `ComputeUnit.ALL` работает, но на CPU/GPU. https://github.com/soniqo/speech-swift/issues/313

### F. MLX: память и многопоточность (дополнение к D)

**F1. [отчёт] Память MPS не освобождается в цикле чанков:** после `del model` + `gc.collect()` память не возвращается до выхода процесса, вплоть до OOM; вопрос «Would running each transcription in a subprocess be the only workaround?»; ответ — `mx.clear_cache()`. https://github.com/senstella/parakeet-mlx/issues/34

**F2. [отчёт] Дубликаты скачивания модели и неопределённый кэш:** `uvx parakeet-mlx` перекачивает ~6 ГБ при каждом запуске, `HF_HUB_CACHE` не помогает; добавлен `--cache-dir`. https://github.com/senstella/parakeet-mlx/issues/37

**F3. [отчёт] Ошибки Metal command buffer жёстко роняют хост‑приложение** (вендоренная mlx без июньского фикса). https://github.com/ml-explore/mlx-swift/issues/458

**F4. [отчёт] Галлюцинации и деградация на неречевых сигналах в parakeet‑mlx**, автор сообщает о ~25 % снижении галлюцинаций своей переработкой кода (без публичного репозитория на момент треда). https://github.com/senstella/parakeet-mlx/issues/46

### G. Недостатки локальных серверов (то, на что жалуются пользователи)

**G1. [отчёт] «Скачивание успешно», но модель неполная:** `POST /v1/models/{id}` отвечает «downloaded», а в снапшоте нет `encoder-model.onnx.data`; при `HF_HUB_OFFLINE=1` модель не грузится. https://github.com/speaches-ai/speaches/issues/657

**G2. [отчёт] 422 на валидный id из `/v1/registry`** при вызове `/v1/models/{model_id}` (валидация ModelId). https://github.com/speaches-ai/speaches/issues/655

**G3. [отчёт] 500 на аудио нулевой длительности** — `ZeroDivisionError` в debug‑f‑строке RTF, вычисляется даже при выключенном логе. https://github.com/speaches-ai/speaches/issues/667

**G4. [факт + впечатление] speaches фактически встал:** последний коммит в ветке по умолчанию — 2026‑04‑18 (GitHub API), 149 открытых issues, 3736 звёзд; в issue #659 «Is this repository dead now?… no commits since April 2026», в #663 — «After the realization that this project is no longer maintained, I took couple of hours with AI and created voicebox». https://github.com/speaches-ai/speaches/issues/659 , https://github.com/speaches-ai/speaches/issues/663

**G5. [код] Политика выгрузки моделей в speaches:** `stt_model_ttl` по умолчанию 300 с, `-1` — никогда, `0` — сразу; VAD — `-1`; есть `DELETE /api/ps/{model_id}`. https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/config.py#L51-L56 , https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/misc.py#L72-L95

**G6. [отчёт, security] WhisperLiveKit: нет лимита на WS‑сессии, каждый коннект поднимает свой ffmpeg** (CVSS 8.7, CWE‑770). https://github.com/QuentinFuxa/WhisperLiveKit/issues/455

**G7. [отчёт, security] Транскрипт уходит по неаутентифицированному `ws://localhost:8765`** в translation‑сайдкар (CVSS 8.3). https://github.com/QuentinFuxa/WhisperLiveKit/issues/456

**G8. [отчёт] sherpa‑onnx online‑websocket‑server не освобождает VRAM после дисконнекта** — «impossible to run the server under heavy load… Eventually the server hits OOM and restarts». https://github.com/k2-fsa/sherpa-onnx/issues/2631

**G9. [отчёт] В Realtime API поле `model` было статической заглушкой:** «`model` is hardcoded on `session.created` and ignored on `session.update`»; пользователи просили выбирать вариант (`qwen3-0.6b` vs `qwen3-0.6b-coreml`, 4‑bit/8‑bit). https://github.com/soniqo/speech-swift/issues/274

**G10. [отчёт] sherpa‑onnx T‑one + endpointing:** с `--enable-endpoint=True` качество заметно падает («choppy», обрезает фразы), с `False` — «perfect»; рекомендаций по правилам для T‑one нет. https://github.com/k2-fsa/sherpa-onnx/issues/3644

**G11. [отчёт] T‑one на ARM64 (Orange Pi 5) возвращает только пробелы** при рабочем x86_64 — платформенная хрупкость. https://github.com/k2-fsa/sherpa-onnx/issues/2879

**G12. [отчёт] Модель может «не найтись» по имени/метаданным:** GigaAM v3 в sherpa‑onnx падал с `'vocab_size' does not exist in the metadata`, пользователь ушёл на raw onnxruntime; мейнтейнер отвечал «следуйте документации». https://github.com/k2-fsa/sherpa-onnx/issues/3619

**G13. [впечатление] Пользователи не понимают, где лежат модели и как их удалять** («Where locate .pth models or how to delete the models?»). https://github.com/QuentinFuxa/WhisperLiveKit/discussions/187

**G14. [отчёт] Concurrency‑лимиты невнятны:** `--concurrent-worker-count` «sounds like it is just about how many workers are doing the same transcription»; сотня файлов = «zero progress after several minutes». https://github.com/argmaxinc/argmax-oss-swift/issues/354

### H. Попытки «Ollama для речи» и почему они глохнут

**H1. [факт] OWhisper (Hyprnote) — самая заметная попытка:** Show HN 2025‑08‑14, **289 баллов, 75 комментариев**; CLI `owhisper pull <model>` / `owhisper run`, локальные whisper.cpp (ggml) и Moonshine (ONNX) в квантованных вариантах, плюс `owhisper serve` с **Deepgram‑совместимым** API; лицензия/код внутри репозитория Hyprnote. https://news.ycombinator.com/item?id=44901853

**H2. [факт] OWhisper мёртв как продукт (проверено 2026‑10‑25):** `https://github.com/fastrepl/owhisper` → 404; `docs.hyprnote.com` и `owhisper.hyprnote.com/download/...` не отвечают (curl code 000); Homebrew‑формула `owhisper` всё ещё `version 0.0.3` и указывает на мёртвый домен; репозиторий Hyprnote переименован в `fastrepl/anarlog`, внутри остались крейты `owhisper-client`/`owhisper-interface` (коммиты до 2026‑09‑30), но в README продукта OWhisper не упоминается. https://github.com/fastrepl/homebrew-fastrepl/blob/main/Formula/owhisper.rb , https://github.com/fastrepl/anarlog/tree/main/crates/owhisper-interface

**H3. [факт] OWhisper выбрал Deepgram‑совместимый контракт, а не собственный:** `provider/model/language/keywords/num_speakers/min_speakers/max_speakers/sample_rate/channels/encoding` как query‑параметры, ответы в формах Deepgram. https://github.com/fastrepl/anarlog/blob/main/crates/owhisper-interface/src/openapi.rs

**H4. [впечатление] Спрос сформулирован прямо:** «I was left wishing for a standard, full‑service (live) transcription server for Whisper like Ollama has been for LLMs… there seems to be a standard vacuum here». Там же — ответ автора, что ценность не в whisper.cpp, а в «другие модели + прокси + Deepgram‑совместимый сервер». https://news.ycombinator.com/item?id=44901853

**H5. [впечатление] Бренд «Ollama for X» стал токсичным** («They've become a commercial operation…», «it's got a pretty bad reputation in the community at this point»). https://news.ycombinator.com/item?id=44901853

**H6. [впечатление] Смешивание облачных провайдеров в «локальном» сервере ломает доверие:** «It seems to use https://api.deepgram.com … and apparently needs an API key, so it's not actually local». https://news.ycombinator.com/item?id=44901853

**H7. [впечатление] Многоязычная модель ≠ многоязычный сервер:** «even if I use a multilingual model like whisper-cpp-large-turbo-q8, the application seems to assume I am speaking English». https://news.ycombinator.com/item?id=44901853

**H8. [факт] nanosamurai («Ollama for Voice Models (STT)» на HN, 2026‑09‑21) — не рантайм, а платформа:** Docker Compose, образы из ghcr, PostgreSQL, Grafana/Prometheus/Tempo/Loki, Windows‑first Electron, мультитенантность; 9 звёзд у репозитория, 4 балла у поста. https://github.com/nanosamurai/nanosamurai , https://news.ycombinator.com/item?id=49787614

**H9. [факт] voicebox (agjs) — ещё одна переделка после смерти speaches:** «Self‑hosted, OpenAI‑compatible speech server: faster-whisper STT and Piper/Kokoro TTS», 35 звёзд, последний пуш 2026‑08‑15. https://github.com/agjs/voicebox

**H10. [факт] Живые/мёртвые на 2026‑10‑25:** WhisperLiveKit — 11 174 звезды, пуш 2026‑10‑23, 18 открытых issues (живой, https://github.com/QuentinFuxa/WhisperLiveKit); speaches — 3736 звёзд, 149 issues, коммитов нет с апреля (стоячий, https://github.com/speaches-ai/speaches); OWhisper — мёртв (H2); speech‑swift — 1248 звёзд, пуш 2026‑10‑25 (живой, но это библиотека + сервер, а не каталог моделей, https://github.com/soniqo/speech-swift).

## Ограничения и риски

1. **Бенчмарки русского собраны на чужих корпусах.** У stt-ru-benchmark n=20 для спонтанной речи (сам автор: «наблюдение, а не доказанный факт»), нет телефонии, нет far‑field, длинные записи — склейка. Habr‑кейс — 5 клипов + 8 YouTube‑фрагментов с эталоном из автосубтитров (автор сам показал, что битый эталон сдвигает вывод на 5–19 pp).
2. **Ни одного публичного замера русской IT‑диктовки с code‑switching нет.** Подмножество «латиница» в stt-ru-benchmark — n=38 и n=5, интервалы не считались. Это ровно наш основной сценарий (`kubectl`, `Istio`, `CI/CD`), и он не измерен.
3. **Все замеры латентности сделаны на разном железе и в разной семантике** (push‑to‑talk после отпускания клавиши vs непрерывный стриминг vs «время получения пакета клиентом»). Сравнивать их между собой нельзя; SpeechRail прямо предупреждает об этом в своём же issue.
4. **Часть чисел — маркетинг приложений** (Onit, MacParakeet): нет методики, нет корпуса, нет условий прогрева. Помечено как [впечатление].
5. **ANE/MLX‑поведение привязано к версии ОС и чипу.** Один и тот же артефакт падает на iOS 27.2 и работает на macOS 27.2; 105 с против 12 с на разных iPhone. Любой «средний» показатель компиляции бессмысленен без матрицы «чип × ОС».
6. **Многие репозитории — однокомандные проекты с одним мейнтейнером** (parakeet‑mlx, speech‑swift, SpeechRail, gotranscribesrv — 1 звезда). Их issues — ценный опыт, но не гарантия, что баг воспроизведётся у нас.
7. **GitHub‑issues как источник смещены:** пишут те, у кого сломалось; успешные кейсы почти не видны. Обратная сторона: часть «багов» закрыта без подтверждения причины (FluidAudio #661 закрыт с формулировкой «traces to concurrent Core ML calls in our own integration»).
8. **Reddit отдаётся только через браузер** (JS‑челлендж), поэтому глубина тредов ограничена; цитаты приведены дословно, но голосов/рейтинга у части постов мало.

## Выводы для Runtime

**Что взять:**

1. **Каталог моделей должен быть «операционным», а не «списком ссылок».** Подтверждённые сообществом требования: проверка полноты снапшота (speaches #657), отсутствие дубликатов кэша и предсказуемый путь (parakeet‑mlx #37, WLK #187), ретраи/чек‑суммы/прогресс (speech‑swift #521–#523), офлайн‑режим (`HF_HUB_OFFLINE`), идемпотентная установка/удаление, версия и вариант (`-int8`, `-fp16`, `-coreml`) как часть идентификатора.
2. **Явный выбор модели и варианта клиентом — обязательное поле, а не заглушка.** Урок speech‑swift #274: `model` в контракте должен реально диспетчеризовать движок/вариант. Плюс `engine` (coreml/mlx/onnx/whispercpp) как отдельная ось, чтобы клиент мог выбрать «качество» vs «ANE/энергия».
3. **Стриминг: два уровня вывода (interim/final) с переспросом незакрытого окна**, а не «резать VAD‑чанками и отдавать как финал»: чанкование измеренно стоит ~6 pp WER (B3), а emission latency CTC — 330 мс (B2), т.е. «финал» по определению отстаёт.
4. **Политика жизненного цикла моделей: TTL + явный unload + «закреплённые» модели.** Референс — speaches (`stt_model_ttl=300`, `DELETE /api/ps/{id}`), но с поправкой на ANE: выгружать веса можно, **компилированный артефакт и решение «ANE не работает на этом чипе» — нельзя** (E5: неудачная специализация не кэшируется → стойка на каждом старте).
5. **Изоляция движков по потокам/очередям, а не «потокобезопасные менеджеры».** D1 (CoreML‑краш при конкурентных ASR+диаризации) и D2/D3 (MLX thread‑local streams, fatalError на любой ошибке) означают: один последовательный исполнитель на движок, никакого параллельного eval в MLX, и, желательно, **отдельный процесс на MLX‑движок**, чтобы падение не убивало демон.
6. **Бюджеты памяти на модель и на сессию, с backpressure.** Ориентиры: MLX‑кэш ограничивать `min(2–4 ГБ, 25 % RAM)` (D4), wired‑limit ~0.9 от recommended (D10), очереди сегментов — ограниченные (C2), стриминговые буферы — не «накапливать всё аудио» (WhisperKit #517, FluidAudio #256).
7. **Диаризация — отдельная операция с явными параметрами `num_speakers/min/max`** и с честным поведением: либо список спикеров в ответе всегда (не только в спец‑формате, C5), либо явная ошибка «не поддержано». Overlap: либо представлять одновременно двух спикеров, либо документировать, что не представляем (C4).
8. **Дефолты под русскую IT‑диктовку:** GigaAM v3 как default для русского (MIT, пунктуация, CPU‑RTF 0.047), Whisper‑turbo/файн‑тюн как «смешанный ru+en и шум», T‑one — для телефонии/8 кГц, Parakeet v3 — только как быстрый батч‑движок (A1–A4, A7, A10). Роутинг по языку — **на клиенте** (runtime не выбирает по смыслу), но runtime обязан отдавать метаданные «языки, домен, пунктуация, ITN, скорость, лицензия».
9. **Никакого облака в локальном пути.** H6: пользователи читают наличие API‑ключей как «это не локально». Локальный демон должен стартовать и работать без сети и без ключей; облачные адаптеры — отдельный, отключаемый модуль (и это же снимает G7‑класс проблем).
10. **Безопасность по умолчанию:** bind на loopback, токен на сессию, лимит одновременных сессий и воркеров (G6, G8), никаких неаутентифицированных сайдкаров.
11. **Контракт — «де‑факто стандарт + расширения», а не изобретение.** OWhisper выбрал Deepgram, speech‑swift — OpenAI‑совместимые `/v1/realtime` + `/v1/audio/transcriptions`; оба сделали это, чтобы клиенты уже существовали. Разумно: OpenAI/Deepgram‑совместимый транспорт + собственный блок `engine/model/variant/diarization` в метаданных.

**Чего не делать:**

- Не называть продукт «Ollama для речи» (H5) и не строить позиционирование на чужом бренде.
- Не делать «сервер» как headless‑режим приложения: у OWhisper код жил внутри репозитория продукта, документация — на домене продукта, и всё умерло вместе с продуктом (H2). Отдельный репозиторий, отдельный релизный цикл, стабильный контракт.
- Не обещать «низкую задержку» без указания семантики (B5, B7): писать «p50 от конца слова до финала» и мерить.
- Не включать «умную» предобработку (AGC/VAD‑cut) по умолчанию (B4).
- Не масштабировать воркерами‑процессами «в лоб» (D9) — это множитель памяти на модель.

## Неизвестные / что измерить

1. **WER русской IT‑диктовки со code‑switching** (ru + EN‑термины: `kubectl`, `PostgreSQL`, `Istio`, `CI/CD`) — публичных замеров нет; нужен свой корпус 300–500 фраз, 2–3 диктора, WER по «терминам» отдельно + CER.
2. **Стриминговая латентность GigaAM/Parakeet/Whisper на Apple Silicon** — все найденные числа либо x86/GPU, либо push‑to‑talk. Нужны p50/p95 «конец слова → финальный токен» на M1–M5 для чанков 0.5/1/2 с.
3. **Стоимость сосуществования моделей:** сколько реально памяти занимает «GigaAM + Silero VAD + эмбеддер спикеров» в одном процессе на 16 ГБ и на 24 ГБ; сколько сессий выдерживает один экземпляр до деградации p95.
4. **Матрица ANE‑компиляции** (чип × ОС × модель): время первой компиляции, доля падений `ANECCompile`, кэшируется ли результат между перезапусками демона (не приложения) на macOS.
5. **DER русской диаризации** на своих материалах (2/3/4 спикера, overlap) — публичных DER по русским корпусам я не нашёл; нужен хотя бы внутренний замер + проверка «3 спикера → 2» (C1).
6. **Поведение при длинных сессиях:** воспроизводится ли зависание WLK на ~12 мин (B6) в нашем стеке; растёт ли память на цикле «сессия закрыта/открыта» (D5) на CoreML и на MLX.
7. **Влияние простоя на батарею/CPU:** единственный найденный факт — `coreaudiod` 10–12 % после транскрипции (D6). Нужен замер «idle с загруженной моделью» vs «idle с выгруженной» на ноутбуке.
8. **Совместимость CoreML‑конкурентности:** закрыт ли реально #661 (краш ASR+диаризация) в текущей версии FluidAudio или это «закрыли, потому что интегратор обошёл»; воспроизвести на M‑серии.
9. **VAD/endpointing как источник ошибок:** sherpa‑onnx #3644 (endpointing ломает T‑one) — нужно проверить, не воспроизводится ли то же с нашими правилами на GigaAM/Whisper.
10. **Правовая сторона каталога:** лицензии моделей различаются (GigaAM MIT, Parakeet cc‑by‑4.0, часть весов только на HF под своими условиями) — нужен машинно‑читаемый `license` в манифесте и запрет «молчаливого» скачивания не‑permissive весов (это стыкуется с задачей K, здесь только фиксирую как риск).

## Источники

**Бенчмарки и статьи**
- https://github.com/vakovalskii/stt-ru-benchmark — открытый per‑domain WER по русскому, 6–7 авг 2026
- https://habr.com/ru/articles/1042574/ — Whisper vs GigaAM в проде, три ловушки бенчмарка (2026‑06‑02)
- https://habr.com/ru/articles/1002260/ — WER 33 % → 3.3 % на CPU: GigaAM/Whisper/Vosk + латентности (2026‑02‑21)
- https://habr.com/ru/companies/tbank/articles/929850/ — T‑one: архитектура, WER, latency‑методика (2025‑07‑22)
- https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 — языки, лицензия
- https://huggingface.co/ai-sage/GigaAM-v3 — MIT, 447K загрузок

**GitHub issues / discussions**
- FluidAudio: https://github.com/FluidInference/FluidAudio/issues/661 (краш ASR+диаризация), /1022 (ANE‑компиляция 105 с), /951 (ANECCompile FAILED), /990 (TTS в ASR‑only), /217 (русский подкаст 3→2), /49 (запросы моделей), /256 (память на длинном аудио), /787 (token‑dedup на русском стриминге)
- argmax (WhisperKit/SpeakerKit): https://github.com/argmaxinc/argmax-oss-swift/issues/309 (440–560 с компиляции), /268 (ANE load failed, «stuck infinitely»), /517 (~50 МБ/цикл), /393 (coreaudiod 12 %), /551 (память диаризации), /488 (iOS 27 degenerate), /206 (лишние слова в гипотезах на русском, «requires an algorithmic improvement to the Eager Streaming Mode»), /260 (недетерминизм по железу), /331, /442 (гонки), /354 (concurrency)
- speech-swift: https://github.com/soniqo/speech-swift/issues/519 (MLX 19–23 ГБ), /503 (ANE compile + нет кэша фейла), /462 (цифры Parakeet), /464 (ANECompilerService 100 % CPU), /313 (сегфолт .mlmodelc на M5), /274 (`model` — заглушка), /157 (wired/Metal budget), /123 (ModelSet/ModelLoader), /521–523 (загрузка моделей)
- parakeet-mlx: https://github.com/senstella/parakeet-mlx/issues/34 (MPS‑память), /37 (кэш/дубликаты), /46 (галлюцинации), /62 (нет пунктуации), /31 (нет code‑switching)
- mlx-swift: https://github.com/ml-explore/mlx-swift/issues/457 (thread‑local streams), /428 (evalLock deadlock, fatalError), /458 (Metal abort)
- WhisperLiveKit: https://github.com/QuentinFuxa/WhisperLiveKit/issues/455 (нет лимита сессий), /456 (неаутентифицированный сайдкар), /410 (max_speakers/Sortformer), /388 (compute time vs elapsed); discussions /62 (production readiness, копии моделей), /89 (зависание на 12 мин), /460 (спикеры теряются в json), /187 (где лежат модели), /280 (TEN VAD vs Silero)
- speaches: https://github.com/speaches-ai/speaches/issues/657 (неполное скачивание), /655 (422), /667 (500 на нулевом аудио), /659 («dead repo?»), /663 (форк voicebox), /622 (статус проекта); код TTL: /blob/993994f7.../src/speaches/config.py#L51-L56
- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx/issues/2631 (VRAM не освобождается), /974 (6 ГБ эмбеддинги), /3644 (endpointing ломает T‑one), /2879 (T‑one на ARM64), /3619 (GigaAM v3 метаданные), /1708 (расхождение с pyannote), /2435 (нет streaming ru — исторический)
- whisper.cpp: https://github.com/ggml-org/whisper.cpp/issues/3702 (ANE на M4/macOS 26.4), README tinydiarize
- SpeechRail: https://github.com/hrygo/SpeechRail/issues/336 (RTF≈1.0, 12 ГиБ), /372 (preview/final без p95)

**HN / Reddit**
- https://news.ycombinator.com/item?id=44901853 — Show HN: OWhisper, 289 баллов
- https://github.com/fastrepl/anarlog/tree/main/crates/owhisper-interface — остатки контракта OWhisper
- https://github.com/fastrepl/homebrew-fastrepl/blob/main/Formula/owhisper.rb — мёртвая формула
- https://www.reddit.com/r/LocalLLaMA/comments/1nf10ye/ — 30 дней Parakeet v3 vs Whisper
- https://www.reddit.com/r/LocalLLaMA/comments/1qr8vbn/ — p50 ~800 мс с LLM‑cleanup
- https://www.reddit.com/r/LocalLLaMA/comments/1s3x30d/ — MacParakeet (60 мин < 30 с)
- https://www.reddit.com/r/MacWhisper/comments/1xeyj11/ — запрос GigaAM, пунктуация из коробки
- https://www.reddit.com/r/RuProgrammers/comments/1wxgk01/ — софт на GigaAM с диаризацией и MCP
- https://www.reddit.com/r/speechtech/comments/1wq1fio/ — DER диаризации на 15 клинических диалогах (batch + streaming)
- https://www.reddit.com/r/speechtech/comments/1u5g9hm/ — «диаризацию мерить отдельно от ASR», дрейф меток после 20 минут
- https://github.com/nanosamurai/nanosamurai , https://github.com/agjs/voicebox — прочие «speech‑платформы»
