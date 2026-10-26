# I. Адаптация ASR к русскому IT-языку: контекстный биасинг, пост-коррекция, дообучение

Дата: 2026-10-25. Платформа анализа: Apple Silicon macOS (архитектурные выводы — без привязки к железу).
Проверялись реальные исходники в `/tmp/srr/repos/*` (sha зафиксированы) и публичные источники (URL). Всё, что не подтверждено кодом/документом/бенчмарком, помечено `[INFERENCE]`.

---

## Итог

1. **Ни у одной из поставляемых русских моделей нет «настоящего» (акустического) hotword-биасинга «из коробки», кроме трансдьюсеров в sherpa-onnx.** Официальная документация sherpa-onnx прямо ограничивает hotwords трансдьюсерами и `modified_beam_search`; CTC (GigaAM-CTC, T-one, Vosk, Whisper, Paraformer, SenseVoice) hotwords не поддерживают вообще (`k2-fsa.github.io/sherpa/onnx/hotwords/`). Vosk умеет менять словарь в рантайме, но только для «динамических» моделей, а его большие русские модели требуют пересборки графа (`alphacephei.com/vosk/adaptation`). Декод-тайм биасинг реализован и в FluidAudio (`setCustomVocabulary`) для Nemotron multilingual, который поддерживает `ru`, но сам мультиязычный чекпойнт не опубликован на HF и требует локальной конвертации.
2. **Единственный работающий сегодня в sherpa-onnx путь для русских hotwords — GigaAM RNNT** (`sherpa-onnx-nemo-transducer-giga-am-v3-russian-2025-12-16`, charwise) либо её punctuation-вариант с BPE (1025 токенов, `▁`), где нужен `--modeling-unit=bpe --bpe-vocab=...`.
3. **Критичная деталь, которую никто не документирует: в charwise-словаре GigaAM пробел есть как символ, но недостижим из hotwords.** `tokens.txt` содержит `" "` → `id 0` (парсер распознаёт строку «только id» как пробел: `symbol-table.cc:109-131`), далее `1..32` буквы и `33 = <blk>`. Кодировщик hotwords (`cjkchar`) склеивает символы слов без пробела (`utils.cc:146-149`), а пробел в файле hotwords — разделитель, а не токен. Поэтому многословные фразы («кубернетес кластер») в AC-автомате не совпадут с потоком токенов модели (там между словами эмитится `id 0`) и скор будет отменён. Однословные термины работают, многословные — нет `[INFERENCE, проверено по tokens.txt + utils.cc + symbol-table.cc]`.
4. **`hotwords_score` в sherpa-onnx — это бонус на КАЖДЫЙ токен** с отменой при провале пути. На charwise-моделях термин из 15 букв получает ~15×score; дефолт 1.5 почти наверняка перебалансирует декодер `[INFERENCE]`. Для charwise нужен score порядка 0.1–0.5 и обязательный замер precision/recall.
5. **Prompt-биасинг (Whisper `initial_prompt`, faster-whisper `hotwords`, Qwen3-ASR system prompt, Voxtral `context_bias`) — самое дешёвое и переносимое решение, но самое слабое:** жёсткий лимит 223 токена промпта (Whisper режет `n_text_ctx//2-1`), мейнтейнер Whisper прямо пишет, что модель может проигнорировать промпт, а на длинном аудио влияние затухает после ~90 с.
6. **Лучший инженерный компромисс для русского IT-жаргона: двухслойка** — (а) биасинг на этапе декодирования, где он есть (transducer-hotwords или CTC-WS), (б) пост-коррекция по персональному словарю с CTC/строковым подтверждением. Подтверждённые цифры второго слоя: vocab precision 99.3%, F-score 91.7%, dict recall 99.3% на earnings22-kws (FluidAudio, внутренний бенчмарк), и F-score 79.8–91.7% в независимой работе TurboBias для CTC-WS.
7. **CTC-WS и GPU-PB — единственные методы с опубликованными измерениями одновременно по F-score, WER и скорости:** GPU-PB даёт +8–10 abs F-score в greedy и +17–23 abs в beam при потере 2–5% RTFx; CTC-WS лучше в greedy (F 79.8 vs 64.8 у GPU-PB), но в ~2 раза медленнее и требует полного аудио (не стриминг).
8. **Apple уже реализует ровно ту архитектуру, которую мы проектируем:** `AnalysisContext.contextualStrings` (≤100 фраз, 1–2 слова) + `SFSpeechLanguageModel`/`SFCustomLanguageModelData`, где пользовательский словарь компилируется в LM с X-SAMPA-произношениями и весами фраз. Это готовый образец API «словарь → скомпилированный артефакт».
9. **Дообучение реалистично только для GigaAM (официальный `train_utils/`, 2–3 эпохи, lr 2e-5…8e-5) и T-one (`finetune_example.ipynb`)**, но риск катастрофического забывания измерен и велик: полный fine-tune Whisper-base на уйгурском полностью убивает китайский (WER → N/A), последовательный LoRA/SeqFT оставляет >30% WER на старом языке. LoRA+ортогональные градиенты снижают забывание.
10. **ITN нельзя запихивать в модель:** GigaAM-e2e (пунктуация+нормализация зашиты) стоит ~+3 pp WER против сырых v3 (12.0/11.2 vs 9.1/8.3 в среднем). Правильнее — сырой ASR + внешний ITN (NeMo `ru` WFST или собственные правила), как отдельный, отключаемый слой.

---

## Факты

### A. sherpa-onnx: hotwords через Aho–Corasick (проверено по коду @ `532f1b5`, `532f1b5a8a207bf53b116b747207c1506696c843`)

**A1. Структура автомата.** `ContextState` содержит `token_score`, `node_score`, `output_score`, `level`, `ac_threshold`, `is_end`, `phrase`, `next`, `fail`, `output` — классический Aho–Corasick с output-дугами и порогом на терминал.
`context-graph.h:22-47` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/context-graph.h#L22-L47

**A2. Шаг автомата и отмена скора.** `ForwardOneStep` при промахе уходит по `fail` и возвращает дельту `node->node_score - state->node_score` (т.е. **вычитает ранее начисленный бонус за несовпавший префикс**); в non-strict режиме (`strict_mode=false`) при выходе на терминал сбрасывает состояние в root и начисляет `output_score`.
`context-graph.cc:69-107` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/context-graph.cc#L69-L107

**A3. Fail/output-дуги строятся BFS-обходом**, `output_score` накапливается по цепочке суффиксов (`FillFailOutput`).
`context-graph.cc:131-168` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/context-graph.cc#L131-L168

**A4. Кодирование hotwords зависит от modeling unit** и поддерживает 4 режима: `cjkchar` (посимвольно, `SplitUtf8`), `bpe` (через `Ssentencepiece` + `bpe.vocab`), `bbpe` (байтовый BPE, таблица встроена), `cjkchar+bpe`. Неизвестный unit → `SHERPA_ONNX_EXIT(-1)`.
`utils.cc:104-206` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/utils.cc#L104-L206
Там же видно, что в ветке `cjkchar` символы слов выводятся через пробел-разделитель, а пробелы внутри фразы теряются (см. Итог п.3). Символ `" "` в tokens.txt существует: `ReadTokens` трактует строку, состоящую только из id, как пробел (`sym = " "`).
`symbol-table.cc:109-131` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/symbol-table.cc#L109-L131

**A5. Формат файла hotwords**: одна фраза на строку; суффиксы `:3.5` (персональный бонус), `#0.6` (порог срабатывания), `@phrase` (исходная строка); OOV-токен → предупреждение и возврат `false`.
`utils.cc:21-102` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/utils.cc#L21-L102

**A6. Ограничение декодера проверяется в рантайме:** при `hotwords_file` и методе ≠ `modified_beam_search` — ошибка и отказ старта.
`online-recognizer.cc:149-157` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-recognizer.cc#L149-L157

**A7. Per-stream hotwords — готовый примитив для API runtime:** у стрима есть `hotwords_buf`/`hotwords_buf_size` (in-memory вместо файла), и отдельные фабрики `CreateStream(recognizer, hotwords)` для online и offline.
`c-api.h:380-400`, `c-api.h:522-537`, `c-api.h:1330-1345` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/c-api/c-api.h#L380-L400 , `#L522-L537`, `#L1330-L1345`
Отдельно: `CreateStream(const std::string &hotwords)` в C++ API объединяет дефолтные hotwords распознавателя и пер-стримовые, склеивая списки и скоры (`offline-recognizer-transducer-impl.h:163-201`), поэтому словарь приложения и словарь сессии не конфликтуют, а складываются.

**A8. Где именно начисляется бонус:** в online-декодере `ForwardOneStep(context_state, new_token, false)` и `context_score` прибавляется к скору гипотезы на каждом непустом токене; в NeMo-офлайн-декодере то же плюс `Finalize()` перед выбором лучшей гипотезы.
`online-transducer-modified-beam-search-decoder.cc:194-199` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/online-transducer-modified-beam-search-decoder.cc#L194-L199
`offline-transducer-modified-beam-search-nemo-decoder.cc:350-357` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-transducer-modified-beam-search-nemo-decoder.cc#L350-L357

**A9. Какие модели реально читают `ContextGraph`:** только `online/offline recognizer transducer`, `*-transducer-nemo`, `keyword-spotter-transducer` (+ RKNN/QNN-варианты). Ни `offline-recognizer-ctc-impl.h`, ни Whisper, ни Paraformer, ни SenseVoice в этот список не входят (grep по `GetContextGraph|ContextGraph`).

**A10. У LLM-подобных ASR hotwords сделаны иначе — через промпт:**
- Qwen3-ASR: список hotwords кладётся в system-сегмент chat-template, пер-стрим через `SetOption("hotwords", "a,b,c")`; код считает, сколько токенов добавили hotwords, и пишет предупреждение при `hotword_tokens >= 48` **или** если под аудио остаётся меньше 32 аудио-токенов (`tight`), с советами «сократить hotwords / аудио / поднять `max_total_len`».
  `offline-recognizer-qwen3-asr-impl.cc:49-58`, `:817-830`, `:849-871` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-recognizer-qwen3-asr-impl.cc#L49-L58
- FunASR-Nano: hotwords + language + itn собираются в user-prompt, есть диагностика «уменьшите число hotwords, промпт не влезает в max_total_len».
  `offline-recognizer-funasr-nano-impl.cc:94-193` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-recognizer-funasr-nano-impl.cc#L94-L193

**A11. ITN в sherpa-onnx — это FST-нормализатор поверх текста** (`kaldifst::TextNormalizer`, список `itn_list_`), подключается параметрами `--rule-fsts`/`--rule-fars`, т.е. правилами, а не моделью.
`offline-recognizer-impl.h:57-59` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-recognizer-impl.h#L57-L59
`offline-recognizer.cc:62-126` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-recognizer.cc#L62-L126

**A12. Официальная позиция проекта (док):** «Only transducer models support hotwords… All other models don't support hotwords», «you have to change the decoding method to `modified_beam_search`», бонус применяется на уровне токенов, при неполном совпадении скоринг отменяется, переобучение не требуется.
https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html

**A13. Реально существующие русские модели в sherpa-onnx (проверено по HF API, 2026-10-25):**
- `csukuangfj/sherpa-onnx-nemo-transducer-giga-am-v3-russian-2025-12-16` (charwise, `tokens.txt` = 34 строки: `" "`+32 буквы+`<blk>`)
- `csukuangfj/sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16` (BPE 1025 токенов, `▁`)
- `csukuangfj/sherpa-onnx-nemo-transducer-giga-am-v2-russian-2025-04-19`, `…-giga-am-russian-2024-10-24`
- `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08` (CTC, `model_type=t-one`)
- `csukuangfj/sherpa-onnx-streaming-zipformer-small-ru-vosk-2025-08-16`
Файлы моделей: `tokens.txt` получен напрямую из HF (`/raw/main/tokens.txt`); CHANGELOG подтверждает поддержку T-one/Vosk/GigaAM: `CHANGELOG.md:802-804`, `:828`, `:1389-1390`.

### B. NeMo: GPU-PB, word boosting, CTC-WS

**B1. GPU-PB** — GPU-ускоренное phrase-boosting дерево (Aho–Corasick + логарифмический бонус по глубине `c0*β+ln(depth)`, backoff-скор = вычитание накопленного), применяется в shallow fusion на этапе декодирования для CTC, RNN-T/TDT и AED, поддерживает greedy и beam, работает с CUDA graphs; до 20K фраз.
https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/asr_customization/word_boosting.html
https://arxiv.org/abs/2508.07014

**B2. Измеренный эффект GPU-PB (TurboBias, Table II, 108M FastConformer, 24k ч англ., BPE-1024, RTX A6000):**
- CTC greedy: CSTalks F 35.0 → 64.8, WER 13.7 → 11.9; RNN-T greedy: F 42.5 → 70.4, WER 12.8 → 10.7
- CTC beam: F 35.0 → 83.2, WER 13.8 → 10.2; RNN-T beam: F 44.2 → 82.9, WER 12.8 → 9.6
- MultiMed (991 фраза): RNN-T beam WER 13.6 → 12.9; на Earnings21 эффект по WER почти нулевой (CTC greedy 15.6 → 15.6, RNN-T beam 14.3 → 14.2) — т.е. выигрыш сильно зависит от плотности терминов в речи
- Скорость: −2…5% RTFx; при росте списка до 20K фраз — ~5% замедления, F 81.1 → 78.4
https://arxiv.org/html/2508.07014v2 (Table II, §IV-A, §IV-C)

**B3. CTC-WS** (arXiv 2406.07096): CTC-word-spotter по CTC-логпробам + замена greedy-фрагментов на найденные кандидаты в пределах интервалов; для RNN-T нужна гибридная Transducer-CTC модель (общий энкодер + два выхода).
https://arxiv.org/abs/2406.07096
Практическая деталь из доков NeMo: в файле контекста можно задавать несколько написаний через `_` (`gpu_gpu_g p u`, `nvidia_n video`) — это дешёвый «псевдо-фонетический» вариант, не требующий G2P.
https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/asr_customization/word_boosting.html

**B4. CTC-WS vs GPU-PB (TurboBias, Table III, CSTalks):** CTC greedy CTC-WS F 79.8 (P90/R72), WER 10.9, RTFx 906 против GPU-PB F 64.8, WER 11.9, RTFx 1991; RNN-T greedy CTC-WS F 80.0, WER 10.1, RTFx 639. Вывод авторов: CTC-WS точнее в greedy, но ~в 2 раза медленнее (детектор на CPU) и неприменим в стриминге.
https://arxiv.org/html/2508.07014v2 (§IV-B)

**B5. Параметры NeMo, которые придётся повторять в своём API:** `context_score` (рекомендуется 1.0), `depth_scaling` (2.0 для CTC/RNN-T/TDT, 1.0 для Canary), `boosting_tree_alpha` (вес при shallow fusion), `beam_threshold`, `ctc_ali_token_weight` (защита от ложных срабатываний), плюс утилита `compute_key_words_fscore.py` для F-score по манифесту.
https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/asr_customization/word_boosting.html

**B6. Flashlight word boosting** (KenLM + лексикон): файл «слово\tскор», рекомендация ±20…100, работает **только в lexicon-режиме**, т.е. требует словарь произношений — это отдельный класс решений (n-gram LM + лексикон), а не hotword-граф.
Там же (раздел Flashlight-based Word Boosting).

### C. FluidAudio: два разных механизма, оба пригодны как референс

**C1. CTC-WS для Parakeet (Swift/CoreML).** Реализация DP перенесена из NeMo `ctc_word_spotter.py` и работает на **blank-expanded** последовательности `[B,t1,B,t2,…,tN,B]`, накапливая логпробы бланка на stay/within-token путях (`CtcDPAlgorithm.swift:1-30`).
https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/SlidingWindow/CustomVocabulary/WordSpotting/CtcDPAlgorithm.swift#L1-L39

**C2. Два способа получить CTC-голову:** (а) встроенная проекция 512→1025 в гибридной TDT-CTC-110M (+1 MB, второй проход энкодера не нужен), (б) отдельный CTC-энкодер 97.5 MB (работает с любым TDT). Заявлено: dict recall 99.4% в обоих, RTFx 70.29× vs 25.98×, память ~67 vs ~130 MB.
`Documentation/ASR/CustomVocabulary.md:18-27, 176-186` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/CustomVocabulary.md#L18-L27

**C3. Измеренный бенчмарк earnings22-kws (771 файл, TDT v2 + CTC-110M):** WER 15.00%, dict recall 1299/1308 = 99.3%, vocab precision 99.3% (TP=1068, FP=8), vocab recall 85.2%, F-score 91.7%, RTFx 63.36×.
`Documentation/Benchmarks.md:215-232` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Benchmarks.md#L215-L232

**C4. Пороговая политика (готовый набор констант для нашего словаря):** `minSimilarity` 0.50 (≤10 терминов) / 0.55 (10–100) / 0.60 (>100), `cbw`=4.5 для всех размеров; защита от коротких слов (≤4 символов → 0.80), от стоп-слов в спане (0.85), от рассогласования длин (<0.75 → строже); термины короче `minTermLength=3` отбрасываются.
`ContextBiasingConstants.swift:79-140, 178-255` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/SlidingWindow/CustomVocabulary/ContextBiasingConstants.swift#L79-L140

**C5. Измеренная цена ложных срабатываний:** на 8 коротких брендовых дистракторах (не произносились) — 12 FP по умолчанию, 0 при опциональных ограничителях ценой recall; отключение «акустического rescue» снижает FP с ~94 до ~19 и повышает biasing recall 0.92 → 0.97.
`Documentation/ASR/CustomVocabulary.md:600-658` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/CustomVocabulary.md#L600-L658

**C6. Стриминг ограничен:** в streaming-режиме многословные compound-термины почти не работают, кросс-чанковые совпадения не детектируются, rescoring деградирует (нужна полная матрица CTC-логпробов).
`Documentation/ASR/CustomVocabulary.md:686-712` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/CustomVocabulary.md#L686-L712

**C7. Второй механизм — decode-time shallow fusion для Nemotron multilingual RNNT (без CTC-головы):** на каждом шаге жадного декода токены, продолжающие частично совпавший термин, получают плоский бонус к логпробу; матчинг идёт по NFC-скалярам **текста пьес**, а не по фиксированной сегментации; trie по piece-формам, стоимость шага не зависит от размера словаря; термины <3 символов отбрасываются; слово-старт анкоринг (`▁`) защищает от «ran» → «CRAN»; состояние переживает границы чанков (в отличие от CTC-пути).
`NemotronVocabularyBias.swift:1-113` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/Streaming/Nemotron/NemotronVocabularyBias.swift#L1-L113
**Измерено (комментарий в коде, рига из 8 редких терминов + 4 фонетических соседа + 2 нейтрала):** boost 3.0 → recall 2/8, 4.5 → 5/8, 6.0 → 4/8 с артефактами («build today» → «build to day»); соседи и нейтралы не затронуты ни при каком весе. Отсюда `defaultBoost=4.5`, `maxBoost=6.0`.
`NemotronVocabularyBias.swift:83-98` (там же).

**C8. Языковая применимость:** Nemotron multilingual — ~40 языков, включая `ru`, vocab 13,087+1, latency-тиры 560/1120/2240 мс; WER на FLEURS en 8.72%, и это единственный путь FluidAudio, где hotwords работают для не-английского (т.к. матчинг в пространстве piece-текста, а не в английском BPE CTC-модели).
`Documentation/ASR/NemotronMultilingual.md:1-25, 86-135` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/ASR/NemotronMultilingual.md#L86-L135
Важно: сам multilingual-чекпойнт **не выложен на HF**, конвертация локальная (`conversion_scripts/convert_nemotron_multilingual.py`, нужен Linux+CUDA) — `NemotronMultilingual.md:26-30`.

**C9. Русский в Parakeet-линейке:** FLEURS ru = 7.2% WER (v3), ultra даёт −1.3 pp на ru, redux — +2.57 pp хуже; всё это read-speech, не IT-жаргон.
`Documentation/Benchmarks.md:34` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Documentation/Benchmarks.md#L34
`Documentation/ASR/ParakeetUltra.md:40-48`, `ParakeetRedux.md:80-95` — те же пути в том же коммите.

### D. Prompt-биасинг: Whisper, Qwen3-ASR, Voxtral

**D1. Whisper: лимит промпта — 223 токена, а не «224».** `prompt_tokens[-(self.n_ctx // 2 - 1):]` при `n_ctx=448` → 223; промпт ставится после `<|startofprev|>` перед SOT-последовательностью.
https://github.com/openai/whisper/blob/main/whisper/decoding.py (`SequenceRanker`/`DecodingOptions._get_initial_tokens`, строки ~600-604)

**D2. WhisperKit режет промпт вдвое жёстче:** `maxPromptLen = (Constants.maxTokenContext / 2) - 1`, где `maxTokenContext = Int(448/2) = 224` → **111 токенов**; пустой после трима промпт не ставится, т.к. «голый `<|startofprev|>` смещает модель к раннему завершению сегмента».
`TextDecoder.swift:196-201` — https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/TextDecoder.swift#L196-L201
`Models.swift:1340` — https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Models.swift#L1340
Публичный API — только `DecodingOptions.promptTokens: [Int]?` (токены, не текст): https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/Configurations.swift#L153-L180

**D3. faster-whisper `hotwords` = тот же промпт, только текст:** `tokenizer.encode(" " + hotwords.strip())`, обрезка до `max_length//2-1`, вставляется после `sot_prev` при условии «нет prefix».
https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py (`WhisperModel.get_prompt`, ~1569-1602)
Именно этот параметр пробрасывает speaches (OpenAI-совместимый сервер): `hotwords=request.hotwords`, `initial_prompt=request.prompt`.
`executors/whisper.py:165-170` — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/whisper.py#L165-L170
`routers/stt.py:67,130,140` — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/stt.py#L130-L161
Для Parakeet-исполнителя speaches честно пишет TODO «issue warnings when client specifies unsupported parameters like `prompt`, `temperature`, `hotwords`» — т.е. в де-факто стандартном OpenAI-совместимом API поле `hotwords` уже существует, но часто игнорируется.
`executors/parakeet.py:131` — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/parakeet.py#L131

**D4. Ограничения prompt-подхода (первоисточник, мейнтейнер Whisper):** промпт «условливает» модель на текст предыдущих ~30 с; можно подсказать жаргон, но «the model unfortunately may ignore the prompt in which case you may need to resort to fine-tuning».
https://github.com/openai/whisper/discussions/117#discussioncomment-3727051
**Затухание на длинном аудио:** в `transcribe()` `initial_prompt` конкатенируется с транскриптом предыдущего окна и обрезается до последних 223 токенов, поэтому подсказка исчезает; практическое наблюдение — имена перестают помогать после ~90 с.
https://github.com/openai/whisper/discussions/117 (комментарии radurevutchi/MattFisher)

**D5. Qwen3-ASR: контекст как system-prompt, и модель этому специально обучена.** В отчёте: SFT-стадия включает «context biasing data», модель учится использовать контекстные токены system-промпта; при этом модель принудительно «ASR-only» — не следует инструкциям в промпте (защита от prompt injection).
https://arxiv.org/abs/2601.21337 (§ про SFT/контекст)
Карточка модели: `prompt="Vocabulary: Quilter, apostle, gospel."` либо system-сообщение с тем же текстом; поддерживается 30 языков + диалекты, среди них `ru`.
https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf (разделы Context/hotwords, Chat template)
Бенчмарк: mean WER 6.31 (0.6B) / 5.59 (1.7B) на HF Open ASR Leaderboard (26.06.2026), per-language ru не публикуется.

**D6. Voxtral: `context_bias` — массив строк в API.** Схема: `context_bias: array of string`, `items.pattern: ^[^,\s]+$` (элемент без пробелов и запятых), default `[]`; `language` — `^\w{2}$`.
https://raw.githubusercontent.com/mistralai/platform-docs-public/main/openapi.yaml (строки 14055-14061 и 14130-14136)
Публичные материалы Mistral обещают «up to 100 words or phrases» и «free-form text» для Voxtral Transcribe 2/Voxtral 3 (страницы `docs.mistral.ai/studio/audio/speech_to_text*` на момент проверки не отдавались автоматическому чтению — цифра «100» взята из поисковой выдержки официальных страниц, не подтверждена прямым чтением).
Независимая оценка prompt-подхода к speech-LLM: context biasing на Whisper режет biased-WER до −88% относительных и почти не задевает остальные слова, а speech-LLM «generalize less well to non-read speech» и чувствительны к числу дистракторов и порядку слов в промпте.
https://arxiv.org/abs/2608.05759

**D7. Whisper-специфичный риск: галлюцинации.** ~1% транскрипций содержат целые выдуманные фразы/предложения, 38% из них — с явным вредом; галлюцинации коррелируют с длинными невокальными участками.
https://arxiv.org/abs/2402.08021
Практический вывод для runtime: любой prompt-биасинг обязан валидировать выход (термин, которого не было в аудио, но он был в промпте, — типовой сценарий «эха»/додумывания) `[INFERENCE]`.

**D8. Измеренный эффект prompt-биасинга как отдельного метода:** supervised fine-tune Whisper под «контекстную инструкцию» на 670 ч Common Voice даёт +45.6% к распознаванию редких слов и +60.8% к словам, не виденным при обучении, и переносится на незнакомые языки; т.е. чистый prompt без дообучения — это baseline, который авторы улучшают обучением.
https://arxiv.org/abs/2502.11572

### E. Apple: два уровня биасинга (эталон архитектуры)

**E1. `SFSpeechRecognitionRequest.contextualStrings`:** «improves the likelihood of those phrases being recognized», фразы — 1–2 слова, «Limit the total number of phrases to no more than 100».
https://developer.apple.com/documentation/speech/sfspeechrecognitionrequest/contextualstrings

**E2. `AnalysisContext.contextualStrings` (SpeechAnalyzer/DictationTranscriber, iOS/macOS 26):** словарь `[ContextualStringsTag: [String]]`, лимит 100 фраз суммарно по всем тегам, теги позволяют менять часть словаря; прямо сказано: «the system may not estimate their pronunciation correctly» → нужна кастомная LM для произношений.
https://developer.apple.com/documentation/speech/analysiscontext/contextualstrings

**E3. Компилируемая кастомная LM:** `SFCustomLanguageModelData` (macOS 14+/iOS 17+) — вставка терминов с `CustomPronunciation` (X-SAMPA, `supportedPhonemes(locale:)`), фраз с весами (`PhraseCount`), шаблонных генераторов; экспорт в файл, затем `SFSpeechLanguageModel.prepareCustomLanguageModel(for:configuration:)`; подключается к `DictationTranscriber` через `customizedLanguage(modelConfiguration:)`.
https://developer.apple.com/documentation/speech/sfcustomlanguagemodeldata
https://developer.apple.com/documentation/speech/sfspeechlanguagemodel
https://developer.apple.com/documentation/speech/dictationtranscriber

**E4. Следствие для нас:** Apple разделяет (1) «горячий» список фраз (≤100, дёшево, слабо) и (2) «скомпилированный» артефакт LM с произношениями и весами (дорого, сильно, версионируемо). Это ровно та модель, которую должен повторять Speech Runtime: `context_phrases` vs `dictionary → compiled artifact`.

### F. Пост-коррекция, фонетика, ITN

**F1. Строковый матчинг + акустическое подтверждение — рабочая база (FluidAudio):** Levenshtein-подобная similarity `1 - edit/maxLen`, BK-дерево для V>100 (экспериментально, O(W log V) вместо O(V·W)), плюс обязательное подтверждение CTC-скором и структурные защиты (короткие слова, стоп-слова, отношение длин).
`ContextBiasingConstants.swift:79-140, 394-413` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ASR/Parakeet/SlidingWindow/CustomVocabulary/ContextBiasingConstants.swift#L394-L413

**F2. «Псевдо-фонетика» без G2P:** NeMo позволяет в файле контекста задать альтернативные написания термина (`gpu_gpu_g p u`, `nvidia_n video`), т.е. ручные фонетические варианты вместо автоматической транскрипции.
https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/asr_customization/word_boosting.html

**F3. ITN для русского существует:** `nemo_text_processing` содержит локаль `ru` (WFST/Pynini), API `inverse_normalize.inverse_normalize`. Ограничение: Pynini/openfst — «Pip installation on MacOS and Windows are not supported… use conda-forge to install Pynini on MacOS», т.е. на Apple Silicon это conda-зависимость, не нативный Swift/Rust-код.
https://api.github.com/repos/NVIDIA/NeMo-text-processing/contents/nemo_text_processing/inverse_text_normalization (есть `ru`)
https://raw.githubusercontent.com/NVIDIA/NeMo-text-processing/main/README.md (раздел Installation)

**F4. Цена «ITN внутри модели»:** GigaAM-e2e (пунктуация + нормализация) хуже сырых моделей: средний WER v3 CTC 9.1 → e2e CTC 12.0, v3 RNNT 8.3 → e2e RNNT 11.2; при этом F1 запятых ~83–84 против 50 при обучении на «пунктуация-только» метках.
`evaluation.md:6-20, 51-57` — https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/evaluation.md#L6-L20

**F5. LLM-коррекция: работает, но не бесплатна.** Нулевой/few-shot ChatGPT по N-best даёт выигрыш на transducer и AED архитектурах (без нейтрализации риска галлюцинаций), https://arxiv.org/abs/2307.04172 ; компактная seq2seq-модель коррекции (в 15× меньше LLM) достигает 1.5/3.3% WER на LibriSpeech test-clean/other и превосходит LLM, при этом авторы прямо называют проблемы LLM-коррекции — latency и hallucination, https://arxiv.org/abs/2405.15216 .

### G. Дообучение: что реально доступно и чего стоит

**G1. GigaAM — официальный рецепт (появился 2026/04):** `train_utils/README.md` + `train_utils/train.py`.
- CTC: `--model_name v3_e2e_ctc --max_epochs 3 --batch_size 64 --lr 8e-5 --activation_checkpointing`
- RNNT: `--model_name v3_rnnt --max_epochs 2 --batch_size 8 --accumulate_grad_batches 2 --rnnt_subbatch_size 2 --lr 2e-5`
- есть `--freeze_encoder_epochs N` (поэтапная разморозка), SpecAugment по умолчанию, `--raw_text` для charwise-нормализации, загрузка чекпойнта через `gigaam.load_model(path.ckpt)`
https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/train_utils/README.md (строки 24-70, 108-190)
Лицензия GigaAM — MIT (`LICENSE:1-3`), модели: 220–600M параметров, предобучение до 700k ч (v3), ASR 4k ч.
https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/README.md (таблица линеек)

**G2. T-one — 71M CTC, Apache 2.0, официальный finetune:** `poetry install -E finetune`, `examples/finetune_example.ipynb`; алфавит — русские буквы + пробел + blank, стриминг по 300 мс, greedy или beam с KenLM.
https://github.com/voicekit-team/T-one (README: разделы «Model fine-tuning», «ASR pipeline overview», «License»)
WER (их собственные замеры): Named entities 5.83 у T-one против 9.55/9.81 у GigaAM v2 RNNT/CTC и 17.87 у Whisper large-v3 (тот же README, таблица).

**G3. Vosk (Kaldi) — три уровня адаптации без обучения:** правка словаря в рантайме (только «динамические» модели), пересборка `Gr.fst` из текста (opengrm), пересборка графа с новым лексиконом; акустический fine-tune — «possible with about 1 hour of data».
https://alphacephei.com/vosk/adaptation

**G4. Whisper LoRA и забывание:**
- LoRA-Whisper: 8 языков, +18.5% (multilingual) и +23.0% (language expansion) относительно baseline; LoRA вставляется в q/k/v/o/fc1/fc2.
  https://arxiv.org/abs/2406.06619
- Забывание измерено на реальных числах: fine-tune Whisper-base на уйгурском (250 ч) → китайский (Aishell-1) перестаёт распознаваться вовсе (`N/A`); Whisper-large — 8.82 → 30.87 WER (full-ft), 24.31 (LoRA), 28.45 (AdaLoRA); последовательное обучение (SeqFT/SeqLoRA) на тибетском оставляет китайский WER >30%; O-LoRA/O-AdaLoRA держат ~12.35/22.82 при 3.47%/1.36% обучаемых параметров; LoRA rank 32, AdaLoRA r=12→8, lr 1e-3/1e-4.
  https://arxiv.org/html/2408.10680v1 (§3.4-3.5, Table 2-3)
- Полный fine-tune на малых данных часто хуже LoRA («full-model fine-tuning yielded the poorest performance… dataset was significantly smaller than 680k hours»), там же.

**G5. Синтетика через TTS для коррекции — измеренный подход:** «cascaded TTS and ASR» для генерации синтетических корпусов ошибок; ключ — разнообразие распределения ошибок, а не объём.
https://arxiv.org/abs/2405.15216
Отдельно: NeMo/GigaAM используют LLM для разметки/нормализации обучающих меток (GigaChat Max Audio), т.е. «синтетические метки» уже стандартная практика.
https://github.com/salute-developers/GigaAM/blob/7447938d791c4f3e643386ee22c33777004293a5/evaluation.md#L49

### H. Сводная таблица: методы × модели × где реализовано × эффект

Только то, для чего есть код/документ/бенчмарк. «н/д» = публичных цифр не найдено (не выдумываю).

| Метод | Модели, где применим | Где реализовано (проверено) | Ожидаемый эффект (источник) |
|---|---|---|---|
| Aho–Corasick shallow fusion по токенам декодера («hotwords») | Трансдьюсеры: Zipformer, NeMo RNNT/TDT (в т.ч. **GigaAM RNNT**), только `modified_beam_search` | sherpa-onnx (`context-graph.cc`, `utils.cc`), C/C++/Python/Swift API, per-stream `hotwords` | Механизм описан (бонус на токен, отмена при провале пути, `:score`/`#threshold` на фразу). Публичных WER/F-score для sherpa-onnx нет — **н/д** |
| GPU-PB (boosting tree + depth scaling) | CTC, RNN-T/TDT, AED (Canary) | NeMo toolkit (`boosting_tree.*`, `build_gpu_boosting_tree.py`) | F +8–10 abs (greedy) / +17–23 abs (beam); WER −1.5…−3.2 pp; −2…5% RTFx; до 20K фраз (arXiv 2508.07014, Table II) |
| CTC-WS (word spotter + замена фрагментов) | CTC и гибридные Transducer-CTC | NeMo (`eval_greedy_decoding_with_context_biasing.py`), FluidAudio (Swift/CoreML) | CSTalks: F 35.0 → 79.8 (CTC greedy), WER 13.7 → 10.9; RTFx 906 (vs 2232 базовая) (arXiv 2508.07014, Table III). FluidAudio earnings22: precision 99.3%, recall 85.2%, F 91.7%, dict recall 99.3% (Benchmarks.md) |
| Decode-time trie biasing в greedy RNN-T | Nemotron multilingual 0.6B (~40 языков, вкл. ru) | FluidAudio (`NemotronVocabularyBias.swift`) | recall 2/8→5/8 (boost 3.0→4.5), деградация при 6.0; переносится через границы чанков (комментарии в коде) |
| Prompt/system-prompt биасинг | Whisper (223 ток. в openai/whisper, 111 в WhisperKit), faster-whisper `hotwords`, Qwen3-ASR, FunASR-Nano, Voxtral `context_bias` | openai/whisper, WhisperKit `promptTokens`, faster-whisper `get_prompt`, sherpa-onnx (`qwen3-asr`, `funasr-nano`), Mistral API | Whisper: модель может игнорировать промпт, затухание на длинном аудио (discussion #117). Speech-LLM: чувствительны к порядку/дистракторам, biased-WER до −88% отн. у context-biasing методов (arXiv 2608.05759); Qwen3-ASR обучен на context-biasing данных (arXiv 2601.21337) — цифр ru нет |
| Apple contextualStrings | Системный on-device диктант (SFSpeechRecognizer, DictationTranscriber) | Apple Speech framework | Заявлено «improves likelihood», лимит 100 фраз по 1–2 слова; цифр нет (Apple docs) |
| Кастомная LM с произношениями (X-SAMPA) | Apple DictationTranscriber | `SFCustomLanguageModelData` → `SFSpeechLanguageModel` → `customizedLanguage` | Позволяет задать произношение термина и вес фразы; публичных WER нет (Apple docs) |
| Пост-коррекция: строка/Levenshtein + CTC-подтверждение | Любой ASR с CTC-головой (Parakeet TDT+CTC, гибрид) | FluidAudio (`VocabularyRescorer`, BK-tree) | earnings22 F 91.7% при precision 99.3%; цена — FP на коротких терминах (12 FP → 0 при опциональных ограничителях, recall ниже) |
| ITN (FST) | Любая модель, текст на выходе | sherpa-onnx `--rule-fsts` (kaldifst), NeMo-text-processing `ru` | Снимает числа/даты/аббревиатуры с ASR; на macOS Pynini требует conda (README). Цифр для ru нет |
| ITN «внутри модели» (e2e) | GigaAM v3 e2e CTC/RNNT | GigaAM | Стоит +2.9/+2.9 pp среднего WER (9.1→12.0, 8.3→11.2) (evaluation.md) |
| Дообучение CTC/RNNT | GigaAM v1–v3 (MIT), T-one (Apache 2.0), Vosk/Kaldi | `GigaAM/train_utils`, `T-one/examples/finetune_example.ipynb`, kaldi-скрипты Vosk | Рецепты с LR/эпохами есть; Vosk: «about 1 hour of data» для акустической адаптации; забывание: LoRA/AdaLoRA против full-ft (arXiv 2408.10680) |
| LoRA-адаптер как артефакт | Whisper | HF PEFT (LoRA rank 32 на q/k/v/o/fc1/fc2) | +18.5/+23.0% к multilingual/language-expansion; 3.47% обучаемых параметров (arXiv 2406.06619, 2408.10680) |

---

## Ограничения и риски

1. **Charwise-русские модели плохо совместимы с hotwords-файлом sherpa-onnx**: пробел-токен недостижим → многословные термины не скорить; длинные однословные термины получают бонус ×длина. Нужен либо BPE-чекпойнт (GigaAM v3 punct, 1025 BPE, но `bpe.vocab` придётся извлечь из `v3_e2e_rnnt_tokenizer.model` через `scripts/export_bpe_vocab.py`), либо собственный скорер вместо файла hotwords.
2. **Все сильные цифры — не на русском.** CTC-WS/GPU-PB измерены на английских доменных наборах (CSTalks/Earnings21/MultiMed/earnings22); T-one/GigaAM — на русском, но без биасинга. Перенос «+20 abs F-score» на русский IT-жаргон не обоснован.
3. **Prompt-биасинг системно ненадёжен**: лимит токенов, игнорирование промпта, затухание, чувствительность к порядку слов; плюс общий риск галлюцинаций (1% фраз у Whisper, 38% из них вредные) и «эха» промпта в выходе `[INFERENCE для эха]`.
4. **Ложные срабатывания — главный практический риск**: FluidAudio документирует ~94 FP на 90 клипов при включённом «акустическом rescue» и 12 FP на 8 коротких дистракторах по умолчанию. Для русского IT-жаргона (короткие аббревиатуры: «Ист»/Istio, «куб»/kube) риск выше, чем для брендов.
5. **LLM-подобные ASR (Qwen3-ASR, Voxtral, FunASR-Nano) добавляют латентность и недетерминизм**, а их контекст-биасинг — свойство обученной модели, не гарантированный механизм; при этом они же требуют контроля «инъекции инструкций» (Qwen3-ASR специально обучен их не выполнять).
6. **Дообучение русской модели на IT-домене упирается в данные**: 250–670 ч — это порядок цифр из публикаций для языковой/инструкционной адаптации, а не сотни размеченных IT-диалогов; при малом объёме full-ft ухудшает модель (2408.10680).
7. **ITN на Apple Silicon**: WFST-ITN (NeMo ru) — Python+Pynini, официально без pip на macOS; нативный путь — либо свои правила (regex/FST), либо модель с зашитым ITN (ценой WER).
8. **Юридические/лицензионные ограничения**: GigaAM — MIT, T-one — Apache 2.0 (проверено), но веса GigaAM v3 в sherpa-экспорте распространяются третьим лицом (`csukuangfj/*`) — проверить условия конкретного репозитория перед поставкой; Nemotron-multilingual вообще не опубликован как HF-чекпойнт.
9. **`hotwords_score`/`cbw` не универсальны**: 1.5 (sherpa, per-token) ≠ 4.5 (FluidAudio cbw) ≠ 1.0+`depth_scaling` (NeMo) — переносить числа между движками нельзя, только калибровать заново.
10. **`promptTokens` в WhisperKit — токены, а не текст**: клиент не может передать «список слов» без токенизатора нужной модели; значит токенизация словаря — обязанность runtime.

---

## Выводы для Runtime

### 1. Что встроить в runtime (API-уровень)

- **Параметр `bias` на запрос**, не глобальная настройка: `bias: {phrases: [{text, weight?, aliases?, min_similarity?}], profile_id?}`. Прецеденты: sherpa-onnx `CreateStream(hotwords)` + `hotwords_buf` (per-stream), FluidAudio `CustomVocabularyTerm` (text/weight/aliases/tokenIds/ctcTokenIds/minSimilarity), Apple `contextualStrings` (per-`AnalysisContext`).
- **Компиляция словаря в артефакт модели-специфичного формата** (наш аналог `SFSpeechLanguageModel`): для AC-движка — токенизованный автомат + скоры; для prompt-движка — строка промпта с бюджетом токенов; для CTC-WS — список терминов с CTC-токенами. Артефакт кэшируется по `(model_id, dictionary_version)` и версионируется.
- **Единая семантика веса**: наружу — нормированный `weight ∈ [0,1]` («сила подсказки»), внутри — маппинг на `hotwords_score` / `cbw` / `boost` / `context_score` с калибровкой на бенчмарке. Иначе клиенты будут вставлять «4.5» туда, где ждут «1.5».
- **Диагностика вместо тишины**: sherpa-onnx/Qwen3 логируют «не влезло в контекст», «OOV-токен пропущен», «добавили N токенов». Runtime обязан возвращать это структурно: `bias.applied_terms`, `bias.rejected_terms[{term, reason}]`, `bias.prompt_tokens`, `bias.dropped`.
- **Никогда не «додумывать» термин**: если термин был только в словаре и не подтверждён акустикой/строкой выше порога — не вставлять (см. галлюцинации Whisper). Для prompt-движков — пост-фильтр «термин не появляется, если его не слышно» `[INFERENCE]`.

### 2. Что оставить приложению

- Источник терминов и их приоритеты (UI, интеграция с IDE/трекером, извлечение из открытых файлов/тикетов).
- Сбор пользовательских исправлений и решение «применить/не применить» замену (runtime может лишь предложить `correction_candidates` с score).
- Политика приватности: словарь — это персональные данные (имена коллег, названия внутренних сервисов); runtime хранит его локально, с явным TTL и scope по приложению.
- ITN/форматирование под конкретный UI (клавиатурный ввод vs субтитры vs документ) — правило «как писать `kubectl` и `CI/CD`» продуктовое, не акустическое.

### 3. Рекомендуемая архитектура биасинга по типам движков

| Движок | Основной механизм | Fallback | Чего не делать |
|---|---|---|---|
| Трансдьюсер (GigaAM RNNT, Nemotron) | AC-hotword граф на токенах декодера (sherpa-механика) или decode-time trie bias (FluidAudio-механика) | пост-коррекция | не включать AC на charwise без пере-калибровки score |
| Гибрид CTC+RNN-T / CTC | CTC-WS (spotter + замена по интервалам) | пост-коррекция по словарю | не ждать стриминга: CTC-WS требует полного аудио |
| LLM-ASR (Qwen3-ASR, Voxtral, FunASR) | контекст в system/user промпте с бюджетом токенов | пост-коррекция | не передавать свободный текст инструкций (prompt injection) |
| Whisper | промпт (223 ток. / 111 в WhisperKit) + `condition_on_previous_text` под контроль | пост-коррекция | не рассчитывать на промпт дольше ~1–2 окон |
| Apple системный | `contextualStrings` (≤100) + кастомная LM | — | не пытаться обойти лимит 100 |

### 4. Персональный словарь → hotwords (накопление исправлений)

Проверенных публикаций «правки пользователя автоматически превращаются в hotwords с измеримым приростом» не найдено — это наш дизайн `[INFERENCE]`, опирающийся на существующие примитивы:
1. хранить пары `(распознано, исправлено, контекст, timestamp)`;
2. повышать вес термина при повторяемости (частота + разнообразие контекстов), не при единичной правке;
3. для термина строить «варианты ошибок» из реальных наблюдённых ошибок (как NeMo `_g p u`), а не из G2P — G2P/фонетику подключать только если замер (см. «Неизвестные» п.9) покажет прирост над строками;
4. ограничивать активный набор: ≤100 терминов для Apple-подобных путей, ≤200–1000 для AC-графа (FluidAudio тестировал 100–230; NeMo GPU-PB — до 20K с деградацией F с 81.1 до 78.4);
5. пере-калибровать пороги по мере роста словаря (FluidAudio: 0.50/0.55/0.60 в зависимости от размера).

### 5. Дообучение: что предлагать как «адаптер модели», а что запретить

- **Предлагать**: (а) LoRA/AdaLoRA-адаптер как отдельный артефакт с версией и метриками (прецедент: LoRA-Whisper 3.47% параметров, +18.5%/+23.0%; O-LoRA против забывания); (б) официальный fine-tune рецепт GigaAM (2–3 эпохи, lr 2e-5…8e-5, `--freeze_encoder_epochs`) и T-one (notebook) — как «рецепт», исполняемый вне runtime (Python/torch), с импортом результата как новой версии модели.
- **Запретить/не поощрять**: full fine-tune на пользовательских данных (<десятков часов) — измеренное забывание и ухудшение; fine-tune внутри runtime-процесса на Mac (нет бюджета/риска порчи базовой модели).
- **Синтетика через TTS** — только для генерации «ошибок» для корректора, с контролем разнообразия голосов; конкретных цифр для русского IT-домена нет → в «измерить».

---

## Неизвестные / что измерить

1. **Нет ни одной публичной цифры по hotword-биасингу для русского IT-жаргона.** Нужен собственный набор: 300–600 фраз реальной русской речи с терминами (Kubernetes, PostgreSQL, Istio, CI/CD, kubectl, Helm, деплой, под, неймспейс, репликасет, ингресс) + список терминов + разметка «термин произнесён/нет».
2. **Неизвестно, где ломается AC-hotword на charwise GigaAM**: однословные термины (нужный score), многословные (гипотеза о пробел-токене) — проверить экспериментом на `sherpa-onnx-nemo-transducer-giga-am-v3-russian`, метрики precision/recall/F по терминам + WER на не-терминах.
3. **Неизвестна цена prompt-биасинга у Qwen3-ASR-0.6B на русском**: сколько терминов влезает, растёт ли WER на остальном тексте, есть ли «эхо» словаря. (В отчёте Qwen3-ASR только заявлено обучение на context-biasing данных.)
4. **Voxtral `context_bias`**: лимит «100 фраз» не подтверждён прямым чтением официальной страницы (только поисковая выдержка); схема API подтверждена. Отдельно неизвестно, поддерживается ли `ru` в self-hosted Voxtral и как ведёт себя биасинг на русском.
5. **Не найдено** публичных данных о FluidAudio vocabulary boosting для русского: CTC-энкодер `parakeet-ctc-110m` англоязычный, значит для русского нужен свой CTC (GigaAM-CTC/T-one) + перенос DP-алгоритма; неизвестно, сохранится ли качество споттера на кириллице.
6. **Не измерено**: влияние `hotwords_score` на WER не-терминов для русских моделей; влияние размера словаря (100/500/2000) на латентность в sherpa-onnx (замер есть только для NeMo GPU-PB и FluidAudio ≤230).
7. **Не подтверждено**: существует ли официальный рецепт fine-tune Parakeet в NeMo-доках с рекомендованным объёмом данных (найденные материалы — сторонние туториалы; AWS-блог не открылся автоматически). Нужен замер: сколько часов русской IT-речи надо для −X pp WER на терминах, и как деградирует общий русский WER.
8. **Не проверено эмпирически**: работает ли `bpe`+`bpe.vocab` (из `v3_e2e_rnnt_tokenizer.model`) с `sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian` и насколько многословные термины скорить лучше, чем charwise-вариант.
9. **Не найдено** исследований «фонетический матчинг для русского ASR-посткоррекции» с цифрами; нужен замер: Levenshtein vs русский Metaphone/`espeak-ng` IPA vs комбинация — на том же наборе терминов.
10. **Не измерено**: сколько пользовательских правок нужно, чтобы автопополнение словаря не деградировало precision (нет публикаций).

---

## Источники

**Код (локальные клоны, sha зафиксированы)**
- sherpa-onnx @ `532f1b5a8a207bf53b116b747207c1506696c843` — `sherpa-onnx/csrc/context-graph.{h,cc}`, `utils.cc`, `online-recognizer.{h,cc}`, `offline-recognizer-transducer-impl.h`, `online/offline-transducer-modified-beam-search*.cc`, `offline-recognizer-qwen3-asr-impl.cc`, `offline-recognizer-funasr-nano-impl.cc`, `offline-recognizer-impl.h`, `offline-model-config.cc`, `c-api/c-api.h`, `CHANGELOG.md`, `scripts/nemo/GigaAM/*`
- FluidAudio @ `1b0ed50d686873128a4129b3510ce3914c849bab` — `Sources/FluidAudio/ASR/Parakeet/SlidingWindow/CustomVocabulary/**`, `.../Streaming/Nemotron/NemotronVocabularyBias.swift`, `Documentation/ASR/CustomVocabulary.md`, `Documentation/ASR/NemotronMultilingual.md`, `Documentation/ASR/{ParakeetUltra,ParakeetRedux}.md`, `Documentation/Benchmarks.md`
- WhisperKit @ `9f99b5cb6a907730543823ad271b82532782a899` — `Sources/WhisperKit/Core/{TextDecoder,Models,Configurations}.swift`
- speaches @ `993994f7984bf3fe9655b267448328cf66fccb42` — `src/speaches/executors/{whisper,parakeet}.py`, `src/speaches/routers/stt.py`
- GigaAM @ `7447938d791c4f3e643386ee22c33777004293a5` — `README.md`, `evaluation.md`, `train_utils/README.md`, `gigaam/decoding.py`, `LICENSE`

**Документация и статьи**
- sherpa-onnx hotwords: https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html
- NeMo Word Boosting (GPU-PB, Flashlight, CTC-WS): https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/asr_customization/word_boosting.html
- TurboBias (GPU-PB): https://arxiv.org/abs/2508.07014 , https://arxiv.org/html/2508.07014v2
- CTC-WS: https://arxiv.org/abs/2406.07096
- Whisper prompt semantics (мейнтейнер): https://github.com/openai/whisper/discussions/117
- Whisper prompt truncation (код): https://github.com/openai/whisper/blob/main/whisper/decoding.py
- faster-whisper `hotwords`: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- Whisper hallucinations: https://arxiv.org/abs/2402.08021
- Whisper rare-word biasing via fine-tune: https://arxiv.org/abs/2502.11572
- Context biasing vs Speech-LLM (2026): https://arxiv.org/abs/2608.05759
- Whisper contextual biasing без fine-tune: https://arxiv.org/abs/2410.18363
- Qwen3-ASR (карточка, ru + context): https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf
- Qwen3-ASR Technical Report: https://arxiv.org/abs/2601.21337
- Mistral OpenAPI (`context_bias`): https://raw.githubusercontent.com/mistralai/platform-docs-public/main/openapi.yaml
- Apple: https://developer.apple.com/documentation/speech/sfspeechrecognitionrequest/contextualstrings , https://developer.apple.com/documentation/speech/analysiscontext/contextualstrings , https://developer.apple.com/documentation/speech/sfcustomlanguagemodeldata , https://developer.apple.com/documentation/speech/sfspeechlanguagemodel , https://developer.apple.com/documentation/speech/dictationtranscriber
- NeMo-text-processing (ITN, локаль ru): https://github.com/NVIDIA/NeMo-text-processing (README; каталог `inverse_text_normalization`)
- LLM-коррекция: https://arxiv.org/abs/2307.04172 , https://arxiv.org/abs/2405.15216
- LoRA-Whisper: https://arxiv.org/abs/2406.06619 ; забывание и O-LoRA: https://arxiv.org/html/2408.10680v1
- Vosk adaptation: https://alphacephei.com/vosk/adaptation
- T-one: https://github.com/voicekit-team/T-one
- Модели sherpa-onnx для ru (проверено HF API): `csukuangfj/sherpa-onnx-nemo-transducer-giga-am-v3-russian-2025-12-16`, `…-punct-giga-am-v3-russian-2025-12-16`, `…-giga-am-v2-russian-2025-04-19`, `csukuangfj/sherpa-onnx-streaming-t-one-russian-2025-09-08`, `csukuangfj/sherpa-onnx-streaming-zipformer-small-ru-vosk-2025-08-16`
