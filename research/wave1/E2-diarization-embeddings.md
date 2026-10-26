# E2. Диаризация, голосовые эмбеддинги и идентификация участников между встречами

Дата исследования: 2026-10-25. Проверялись реальный код локальных клонов и официальные карточки моделей.
Обозначения: **[П]** — проверено по коду/официальному бенчмарку с источником; **[П-3P]** — измерение третьей стороны
(репозиторий, не вендор модели); **[М]** — маркетинговое/вендорское утверждение; **[I]** — мой вывод (инференс).

---

## Итог

1. **Для «Ollama для речи» диаризация распадается на два независимых слоя**: (а) «кто когда говорил» внутри сессии и
   (б) «кто это из известных людей» между сессиями. Ни один открытый движок не решает (б) сам — все продакшн-решения
   строят (б) поверх отдельной модели эмбеддингов + собственного хранилища центроидов (pyannoteAI voiceprint API,
   FluidAudio `SpeakerManager`, sherpa-onnx `SpeakerEmbeddingManager`, speaches `known_speaker_*`).
2. **End-to-end модели (Sortformer, Nemotron 3 Diarization, LS-EEND) выдают слоты, упорядоченные по появлению
   говорящего, а не устойчивые личности**. Слот `speaker_0` в следующей встрече — другой человек. Значит диаризатор
   E2E и модель эмбеддингов — **две разные модели в каталоге**, и это надо закладывать в API с первого дня.
3. **Лимиты спикеров жёсткие и зашиты в веса**: Sortformer — 4 слота, Nemotron 3 Diarization — 8, LS-EEND — 4/7/10 в
   зависимости от варианта, pyannote/DiariZen/3D-Speaker — без верхнего лимита (кластеризация). Это первое, что
   должно валидироваться в запросе.
4. **Лучшее качество офлайн на AMI SDM — у DiariZen (13.9 %) и pyannote community-1 (19.9 %); лучший DIHARD III
   в потоковом/офлайн-стилевом режиме — у NVIDIA Nemotron 3 Diarization** (12.73 % при буфере 30.4 с и 13.18 % при
   1.04 с против 14.5 % у DiariZen и 20.2 % у community-1). Потоковые пайплайны «сегментация+эмбеддинги+онлайн-
   кластеризация» (FluidAudio `DiarizerManager`) деградируют катастрофически: 38–53 % DER на AMI SDM — их нельзя
   ставить дефолтом [П-3P].
5. **Лицензии — главный минный заряд**: Sortformer v1 — `cc-by-nc-4.0` (некоммерческая), DiariZen — `cc-by-nc-4.0`,
   Nemotron 3 Diarization — `openmdw-1.1` (карточка разрешает commercial use), community-1 — `cc-by-4.0`,
   pyannote 3.1/segmentation-3.0/embedding — MIT, WeSpeaker ResNet34 — `cc-by-4.0`, TitaNet-L — `cc-by-4.0`,
   ECAPA (SpeechBrain) — `apache-2.0`, 3D-Speaker код — Apache-2.0. Runtime обязан хранить лицензию как поле модели.
6. **Одна модель эмбеддингов на всё** работает там, где диаризация каскадная: pyannote community-1 использует
   WeSpeaker ResNet34 (256-d) и для кластеризации, и для последующей идентификации (FluidAudio `embedSpans`).
   Для E2E-диаризаторов эмбеддинги надо брать отдельно — и **пространства нельзя смешивать**: 256-d WeSpeaker,
   192-d CAM++/ERes2Net/ECAPA/TitaNet несопоставимы.
7. **Русского публичного DER-бенчмарка не существует** (в отличие от ASR: `stt-ru-benchmark`). Все модели обучены на
   английском/китайском; проверок на русской речи с IT-лексикой не найдено ни в одном репозитории. Это
   must-measure, а не «известная величина».
8. **Порог косинуса — не константа**: в проверенном коде встречаются 0.65 и 0.8 как *косинусное расстояние*
   (FluidAudio), 0.5 как *косинусная близость* (sherpa-onnx), 0.6 как евклидов отсечка AHC (community-1).
   Один и тот же «порог» в разных системах означает разное; в контракте API нужно фиксировать семантику явно.
9. **Anti-pattern, который нельзя повторять**: speaches сопоставляет кластеры с известными спикерами по argmax
   косинуса **без порога и без reject** — любой незнакомец принудительно получает чужое имя. Нужен порог +
   margin + явный `unknown`.
10. **Механику «общие веса, разные сессии» уже можно скопировать**: speaches `SelfDisposingModel` (refcount + TTL
    выгрузки), FluidAudio (неизменяемые CoreML-бандлы + мутабельное состояние сессии в объекте диаризатора),
    gotranscribesrv (один актор на всё — наоборот, антипример сериализации сессий).

---

## Факты

### 1. NVIDIA Sortformer: v1 (офлайн), v2 / v2.1 (стриминг)

**v1 — офлайн, 4 слота, некоммерческая лицензия.** Карточка `nvidia/diar_sortformer_4spk-v1`: `license:
cc-by-nc-4.0`, архитектура `SortformerOffline`, заявленные DER при post-processing [П, https://huggingface.co/nvidia/diar_sortformer_4spk-v1]:

| Условие | DER | collar |
|---|---|---|
| DIHARD III eval, подмножество 1–4 спикера | 14.76 % | 0.0 s |
| CALLHOME part2, 2 / 3 / 4 спикера | 5.85 / 8.46 / 12.59 % | 0.25 s |
| CH109 (2 спикера, 109 сессий) | 6.86 % | 0.25 s |

**v2 — стриминговый, 4 слота, `cc-by-4.0`** (проверено через HF API: `license: cc-by-4.0`, sha `84edd514b8ef`).
**v2.1** — та же архитектура, лицензия `nvidia-open-model-license` («other» в API). Модельная карточка v2.1 содержит
model-index с DER при input-buffer latency 1.04 s, overlap **учитывается**, collar 0 [П,
https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1]:

| Датасет | DER |
|---|---|
| DIHARD III full / 1–4 spk / 5–9 spk | 20.21 / 15.09 / 41.42 % |
| CALLHOME part2 (full / 2 / 3 / 4 / 5 / 6 spk) | 11.19 / 6.65 / 11.25 / 13.35 / 22.12 / 24.51 % |
| AMI IHM / AMI SDM | 16.67 / 20.57 % |
| AliMeeting near / far | 12.60 / 15.60 % |
| NOTSOFAR1 SC full / ≤4 spk / ≥5 spk | 28.75 / 17.26 / 36.76 % |
| CHAES (109 сессий, 2 spk) | 5.09 % |

Параметров 117M (бейдж в карточке). **Ключевое:** 4 фиксированных слота, «нет persistent speaker embeddings» —
прямая цитата из документации порта FluidAudio [П, `Documentation/Diarization/Sortformer.md`]:
`4 speaker maximum — cannot handle 5+ speakers`, `Does not remember speakers across recordings`.

**Стриминговое состояние** (порт NeMo): speaker cache 188 кадров + FIFO 40 + правая контекстная зона; выход —
`sigmoid`-вероятности `[T', 4]`, шаг 80 мс; латентность `(chunkLen + rightContext) × 8 × 10 ms`, дефолт 1.04 s,
high-context 30.4 s [П, `Documentation/Diarization/Sortformer.md`]. Состояние мутабельно и живёт в объекте
диаризатора (`SortformerStreamingState`: `spkcache`, `fifo`, `meanSilenceEmbedding`) — то есть **веса общие, состояние
сессионное** [П, `Sources/FluidAudio/Diarizer/Sortformer/`].

Перенос на Apple Silicon существует и измерен: единый fused-граф на окно 30.72 s даёт 2884× RTFx только на модель
(10.65 ms, fp16, M5 Pro), но качество офлайн-варианта на длинных встречах проваливается — AMI SDM 56.7 % DER против
26.4 % у стримингового high-context, «весь разрыв — speaker confusion» [П-3P,
`Documentation/Benchmarks.md:930-963`].

### 2. NVIDIA Nemotron 3 Diarization — существует, это преемник Sortformer

Название точное: **`nvidia/Nemotron-3-Diarization`**, релиз 23.09.2026, `license: openmdw-1.1`, 99 226 504 параметра
(F32, safetensors), архитектура `Nemotron3DiarizationForAudioFrameClassification`, есть GGUF q8_0 (архитектура
`sortformer`, ~99.3M) [П, https://huggingface.co/api/models/nvidia/Nemotron-3-Diarization].

Заявлено в карточке [П, https://huggingface.co/nvidia/Nemotron-3-Diarization]:
- **до 8 спикеров**, каналы упорядочены по первому появлению (arrival-ordering), выход 10 мс;
- один чекпойнт на стриминг и офлайн; input-buffer latency 30.4 / 1.04 / 0.64 / 0.32 s (минимум технически 80 мс,
  рекомендованный минимум 0.32 s);
- «ready for commercial or non-commercial use» (формулировка карточки; сам текст OpenMDW-1.1 я не читал — см. §5);
- AOSC (speaker cache) + FIFO, как в Streaming Sortformer.

Цифры из блога NVIDIA (не из model-index; протокол указан: overlap учитывается, DIHARD/AMI/AliMeeting/NOTSOFAR —
collar 0, CALLHOME-P2 — 0.25 s) [П/М, https://huggingface.co/blog/nvidia/nemotron-diarization]:
- DIHARD III full: **12.73 %** DER при 30.4 s буфере, **13.18 %** при 1.04 s (baseline v2.1: 19.09 / 19.60 %);
- CALLHOME-Part2 full: **9.10 %** (было 10.32 %), но на подмножестве 2 спикера **хуже** baseline: 5.98 % против 5.68 %;
- VoiceArena Diarization-Bench (139 англ. диалогов, ~22 ч, collar 0): **14.72 %**, 1-е место среди 12 систем;
- throughput: 15 113× RTFx при batch 32 и 30.4 s (RTX PRO 5000, BF16, `torch.compile`), 865× при 1.04 s.

Порт FluidAudio: 5 профилей инференса (low / fast / fast32 / fast128 / offline) + 2 split-w8a8, размер бандла
190 MB (fp16) или 95 MB (split-w8a8), заявлено «closed-loop output matches the NeMo reference at 99.995 % frame
agreement» на реальном аудио [П-3P, `Documentation/Diarization/Nemotron3.md`]. **Внимание:** документация FluidAudio говорит «gated until NVIDIA's public
release», но на 2026-10-25 HF API отдаёт `gated: false` и для `nvidia/Nemotron-3-Diarization`, и для
`FluidInference/nemotron-3-diarization-coreml` — документация устарела.

### 3. pyannote.audio 3.1 / 4.0 / community-1 / precision-2

`community-1` — новый дефолтный пайплайн pyannote.audio 4.0. Официальная таблица DER (полностью автоматическая
обработка, **без collar и без исключения overlap**) [П, https://github.com/pyannote/pyannote-audio#benchmark и
https://huggingface.co/pyannote/speaker-diarization-community-1]:

| Бенчмарк | legacy 3.1 | community-1 | precision-2 |
|---|---|---|---|
| AISHELL-4 | 12.2 | 11.7 | 11.4 |
| AliMeeting (ch1) | 24.5 | 20.3 | 15.2 |
| AMI IHM | 18.8 | 17.0 | 12.9 |
| AMI SDM | 22.7 | 19.9 | 15.6 |
| AVA-AVD | 49.7 | 44.6 | 37.1 |
| CALLHOME part2 | 28.5 | 26.7 | 16.6 |
| DIHARD 3 full | 21.4 | 20.2 | 14.7 |
| Ego4D dev | 51.2 | 46.8 | 39.0 |
| MSDWild | 25.4 | 22.8 | 17.3 |
| RAMC | 22.2 | 20.8 | 10.5 |
| REPERE phase2 | 7.9 | 8.9 | 7.4 |
| VoxConverse v0.3 | 11.2 | 11.2 | 8.5 |

Скорость (self-hosted, H100): community-1 — 31 s на час AMI IHM, precision-2 — 14 s на час [П, тот же README].

**precision-2 — облачный/коммерческий**: в README код `Pipeline.from_pretrained("pyannote/speaker-diarization-precision-2",
token="PYANNOTEAI_API_KEY")` с комментарием «runs on pyannoteAI servers»; публичных весов нет. README упоминает
«in its self-hosted version» — то есть self-host возможен как коммерческая поставка, не как открытые веса [П].
Для локального runtime precision-2 неприменим.

Лицензии (проверено через HF API): `pyannote/speaker-diarization-community-1` → **cc-by-4.0**, gated (нужен токен и
принятие условий); `pyannote/speaker-diarization-3.1` → **mit**, gated; `pyannote/segmentation-3.0` → **mit**, gated;
`pyannote/embedding` → **mit**, gated; `pyannote/wespeaker-voxceleb-resnet34-LM` → **cc-by-4.0**, не gated.
Код pyannote.audio — MIT. То есть у community-1 весовая лицензия строже кодовой: **CC-BY требует атрибуции в
продукте**.

Состав community-1 (из цитат карточки): powerset-сегментация (Plaquet & Bredin, Interspeech 2023) + эмбеддинги
**WeSpeaker** + кластеризация **VBx** (Landini et al.). Порт подтверждает: 7 локальных powerset-классов, 589 кадров
на 10 s окно, 256-d L2-нормированные эмбеддинги, PLDA + VBx с AHC-варм-стартом [П-3P,
`Documentation/Diarization/GettingStarted.md`]. Выход дополнительно содержит **exclusive diarization**
(`output.exclusive_speaker_diarization`) — специально для стыковки с таймстемпами ASR [П, карточка community-1].

### 4. WeSpeaker, 3D-Speaker / CAM++ / ERes2Net, NeMo TitaNet, SpeechBrain ECAPA

| Модель | Параметры | Размерность | EER VoxCeleb1-O | Лицензия весов | Форматы |
|---|---|---|---|---|---|
| WeSpeaker ResNet34 (LM + AS-Norm) | ~6.3M (ResNet34) | 256 | **0.723 %** [П, README WeSpeaker] | cc-by-4.0 | PyTorch, ONNX, CoreML (FluidAudio/SpeakerKit), MLX |
| CAM++ (3D-Speaker/FunASR) | 7.2M | 192 | **0.65 %** [П, README 3D-Speaker] | не подтверждено (ModelScope `iic/...`) | PyTorch, ONNX (sherpa), CoreML (FluidAudio, beta) |
| ERes2Net-base | 6.61M | 192 | 0.84 % | — | PyTorch, ONNX |
| ERes2NetV2 | 17.8M | 192 | 0.61 % | — | PyTorch, ONNX |
| ERes2Net-large | 22.46M | 192 | **0.52 %** | — | PyTorch, ONNX |
| ECAPA-TDNN (SpeechBrain) | 20.8M | 192 | **0.80 %** [П, https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb] | apache-2.0 | PyTorch, ONNX-конверсии сообщества |
| TitaNet-Large (NeMo) | 23M | 192 [П, `emb_sizes: 192` в `conf/titanet-large.yaml`] | **0.66 %** | cc-by-4.0 | `.nemo`, ONNX (sherpa `nemo` extractor) |

Важно про TitaNet: DER в его карточке (1.73 % AMI MixHeadset, 6.73 % NIST SRE 2000) получены при
**oracle VAD и известном числе спикеров** — с полноценным DER они несравнимы [П, карточка модели].

CAM++ на Apple Silicon: CoreML-конверсия FluidAudio, EER 0.48 % на AISHELL-1 (чистая мандаринская речь — легче, чем
CN-Celeb), средний косинус same-speaker 0.805 / different-speaker 0.256 на 20 спикерах, 6000+6000 пар; расхождение
CoreML↔torch по косинусу 0.9997–0.99999 [П-3P, `Documentation/Benchmarks.md:649-676`]. Это, по сути, готовая
калибровка порога для CAM++: **same ≈ 0.80, diff ≈ 0.26 → порог ≈ 0.5–0.55 по косинусу**.

### 5. DiariZen (BUT Speech@FIT)

Код MIT, **веса CC BY-NC 4.0** — прямо заявлено, что причина: RAMC, MSDWild, DIHARD-3 запрещают коммерческое
использование, а конфликт NC/SA решён в пользу NC [П, https://github.com/BUTSpeechFIT/DiariZen#license].

DER **без collar**, без доменной адаптации, одинаковые гиперпараметры кластеризации [П, README]:

| Датасет | pyannote 3.1 | DiariZen-Base-s80 | DiariZen-Large-s80 | Large-s80-v2 |
|---|---|---|---|---|
| AMI-SDM | 22.4 | 15.8 | 14.0 | **13.9** |
| AISHELL-4 | 12.2 | 10.7 | 9.8 | 10.1 |
| AliMeeting far | 24.4 | 14.1 | 12.5 | **10.8** |
| NOTSOFAR-1 | — | 20.3 | 17.9 | **16.7** |
| MSDWild | 25.3 | 17.4 | 15.6 | 15.8 |
| DIHARD3 full | 21.7 | 15.9 | 14.5 | 14.5 |
| RAMC | 22.2 | 11.4 | 11.0 | 11.0 |
| VoxConverse | 11.3 | 9.7 | 9.2 | **9.1** |

Архитектура: EEND на **WavLM-Large** + Conformer, затем структурный прунинг 80 %: 316.6M → **63.3M** параметров,
17.8G → **3.8G MAC** на секунду аудио; v1 рассчитан на ≤3 одновременных спикера, v2 — до 4 [П, карточка
https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md]. Лимита на число спикеров нет.

### 6. 3D-Speaker: диаризационный пайплайн и «китайские» данные

Официальные DER (источник — README 3D-Speaker; протокол в README не детализирован) [П,
https://github.com/modelscope/3D-Speaker]:

| Тест | 3D-Speaker | pyannote.audio | DiariZen_WavLM |
|---|---|---|---|
| AISHELL-4 | **10.30** | 12.2 | 11.7 |
| AliMeeting | 19.73 | 24.4 | **17.6** |
| AMI_SDM | 21.76 | 22.4 | **15.4** |
| VoxConverse | 11.75 | **11.3** | 28.39 |
| Meeting-CN_ZH-1 (внутр.) | **18.91** | 22.37 | 32.66 |
| Meeting-CN_ZH-2 (внутр.) | **12.78** | 17.86 | 18.00 |

Пайплайн: overlap detection (опц.) + VAD + сегментация + эмбеддинги + кластеризация; код Apache-2.0; модели на
ModelScope (лицензии отдельных весов не проверял — см. §5). Показательно, что DiariZen на китайских встречах
деградирует до 32.66 % — **доменный сдвиг реален даже внутри «meetings»**, что прямо относится к русской речи [I].

### 7. FluidAudio: самый полный открытый набор диаризаторов под Apple Silicon (Apache-2.0)

Пять независимых движков в одном Swift-пакете (четыре «классических» + Nemotron 3 Diarization, добавленный в
сентябре 2026), sha `1b0ed50d686873128a4129b3510ce3914c849bab`
[П, `Documentation/Diarization/GettingStarted.md`, `Nemotron3.md`]:

| Движок | Макс. спикеров | Латентность | Профиль |
|---|---|---|---|
| Sortformer (NVIDIA) | 4 | обновление 480 мс, tentative 560 мс | шум, overlap, стабильные личности; пропускает тихую речь |
| Nemotron 3 Diarization | 8 | 0.32 / 0.64 / 1.04 / 2.88 / 10.56 / 30.4 с (пресеты) | преемник Sortformer, arrival-ordering, 10 мс выход |
| LS-EEND (Westlake) | 10 | 100 мс, tentative ~900 мс | лучший на шумных/overlap, но ложные срабатывания |
| DiarizerManager (pyannote 3.1-подобный онлайн) | нет лимита | по чанкам | плохо на шуме/overlap, самый тяжёлый |
| Offline VBx (community-1) | нет лимита | батч | лучшее качество офлайн |

Измеренные значения (M5 Pro, AMI SDM 16 встреч, **collar 0.25, ignoreOverlap=true**) [П-3P,
`Documentation/Benchmarks.md`]:

| Система | Avg DER | RTFx |
|---|---|---|
| Offline VBx (community-1) | **10.62 %** | 323× |
| LS-EEND (AMI variant) | 20.7 % | 74.5× |
| Sortformer streaming high-context | 26.4 % | 835× |
| Sortformer offline (30.72 s окна) | 56.7 % | 1418× |
| Streaming pyannote 3s/1s overlap | 53.3 % | 24.6× |
| Streaming pyannote 5s/2s overlap | 55.9 % | — |
| Streaming pyannote 10s/0s (лучший потоковый) | 38.2 % | ~96–223× |
| Streaming pyannote 5s/0s | 39.0 % | — |

VoxConverse (232 клипа, collar 0.25): 15.07 % DER / 122× при stepRatio 0.2; 13.89 % / 64.75× при stepRatio 0.1
[П-3P]. Оговорка авторов: baseline PyTorch даёт ~11 %, потеря — из-за fp16 для ANE.

**Что именно важно для Runtime (код, не README):**
- `Speaker` — `Codable`-структура с `currentEmbedding`, `rawEmbeddings: [RawEmbedding]`, `duration`, `updateCount`,
  `isPermanent` [П, `Sources/FluidAudio/Diarizer/Clustering/SpeakerTypes.swift:7-25`] — готовый формат записи
  реестра, но **дисковой персистентности нет**: `speakerDatabase` — in-memory словарь
  [П, `.../SpeakerManager.swift:16`].
- Пороги: `speakerThreshold` (макс. косинусное **расстояние** для отнесения к спикеру, дефолт 0.65),
  `embeddingThreshold` (0.45 — только близкие матчи обновляют профиль), `minSpeechDuration` 1.0 s,
  `minEmbeddingUpdateDuration` 2.0 s [П, `Documentation/Diarization/SpeakerManager.md`].
- Присвоение: линейный поиск по базе, `cosineDistance(embedding, speaker.currentEmbedding) <= speakerThreshold`
  [П, `.../SpeakerManager.swift:205-208`]; `cosineDistance` возвращает 1−cos, диапазон 0..2
  [П, `.../SpeakerOperations.swift:63-...`].
- Обновление центроида — усреднение накопленных эмбеддингов (`averageEmbeddings`), а не EMA
  [П, `.../SpeakerOperations.swift:465-474`].
- `findMergeablePairs` — пары спикеров ближе порога, для ручного/полуавтоматического слияния
  [П, `SpeakerManager.md`].
- `initializeKnownSpeakers(_:mode:preserveIfPermanent:)` — режимы `.reset/.merge/.overwrite/.skip` и флаг
  «постоянного» спикера: готовая модель для клиентского реестра [П, `SpeakerManager.md`].
- **Enrollment «праймингом»**: `enrollSpeaker(withAudio:named:)` прогоняет клип через полный пайплайн, заполняет
  speaker cache и назначает имя лучшему по числу кадров слоту; слоты ограничены 4/8
  [П, `Sources/FluidAudio/Diarizer/Sortformer/SortformerDiarizer.swift:259-400`].
- **Эмбеддинги для произвольных интервалов**: `embedSpans(audioSource:spans:) -> [[Float]]`, 256-d, без
  сегментации/кластеризации, с явным указанием «используйте, когда реплики уже известны, например из другого
  диаризатора» [П, `.../Offline/Core/OfflineDiarizerManager.swift:516-536`]. Это ровно тот примитив, который нужен
  для идентификации между встречами.
- Ограничение числа спикеров: `numSpeakers` / `minSpeakers` / `maxSpeakers` (numSpeakers перекрывает остальные)
  [П, `.../Offline/Core/OfflineDiarizerTypes.swift:144-158`].
- Семантика порога менялась: с issue #801 `clusteringThreshold` — евклидова отсечка на дендрограмме AHC,
  «больше = агрессивнее слияние», конверсия старых значений `sqrt(2 − 2·old)` [П-3P, `Benchmarks.md:735-736`].

### 8. sherpa-onnx: только офлайн-диаризация

- Отдельного онлайн-диаризатора нет: единственный бинарь — `sherpa-onnx-offline-speaker-diarization`
  [П, `sherpa-onnx/csrc/`].
- Пайплайн жёстко каскадный: pyannote-сегментация обязательна (`Create` возвращает ошибку «Please specify a speaker
  segmentation model»), затем эмбеддинги, затем `FastClustering`
  [П, `sherpa-onnx/csrc/offline-speaker-diarization-impl.cc`].
- `FastClusteringConfig`: `num_clusters` (если >0, порог игнорируется; авторы «strongly recommend» задавать число
  спикеров) + `threshold` (дефолт 0.5, «меньше → больше кластеров») + `compute_confidence`
  [П, `sherpa-onnx/csrc/fast-clustering-config.h:16-27`].
- Постобработка: `min_duration_on = 0.3 s`, `min_duration_off = 0.5 s`
  [П, `sherpa-onnx/csrc/offline-speaker-diarization.h:25-30`].
- Поддерживаемые семейства эмбеддингов: `wespeaker`, `3d-speaker`
  [П, `sherpa-onnx/csrc/speaker-embedding-extractor-model.cc:107-113`] и `nemo` (отдельный загрузчик
  `speaker-embedding-extractor-nemo-model.cc:114-121`).
- `SpeakerEmbeddingManager` — референсная реализация реестра между сессиями: `Add(name, embedding | list)`
  (усредняет список), `Search(p, threshold)` (лучший по косинусу ≥ порога), `GetBestMatches(p, threshold, n)`,
  `Verify(name, p, threshold)`, `Remove`, `GetEmbedding` [П, `sherpa-onnx/csrc/speaker-embedding-manager.h:20-100`].

### 9. WhisperLiveKit и SpeechRail: стриминговая интеграция ASR+диаризация

WhisperLiveKit (Apache-2.0) содержит два бэкенда диаризации: `diart_backend.py` (pyannote `segmentation-3.0` +
`pyannote/embedding`, block 1.5 s) и `sortformer_backend.py`, где по умолчанию
`nvidia/diar_streaming_sortformer_4spk-v2`, а streaming-параметры выставляются вручную: `chunk_len=10`,
`subsampling_factor=10`, `chunk_right_context=0`, `chunk_left_context=10`, `spkcache_len=188`, `fifo_len=188`,
`spkcache_update_period=144` [П, `whisperlivekit/diarization/sortformer_backend.py:55-127`]. Модель грузится через
NeMo `SortformerEncLabelModel` — то есть требует PyTorch/CUDA-стек, на Apple Silicon только CPU
(`torch.device("cuda" if available else "cpu")`, строка 117-119). Отдельный класс
`SortformerDiarizationOnline` (строка 151) держит потоковое состояние.

`sherpa-onnx`/WhisperLiveKit вместе показывают: связка «диаризация ↔ слово ASR» реализуется как отдельный слой
сопоставления по времени, а не как часть модели (в FluidAudio — `consumeWordTimings()` + выбор слота с максимумом
активности на интервале слова, [П-3P, `Nemotron3.md`]).

SpeechRail (MIT) в этом срезе не содержит собственной диаризации — только ASR-инфраструктуру; для E2 её код не дал
фактов по теме.

### 10. Готовые реализации «идентификация между встречами» и их дефекты

- **speaches** (MIT, sha `993994f7984bf3fe9655b267448328cf66fccb42`): эндпоинт `POST /v1/audio/diarization` с
  `known_speaker_names[]` + `known_speaker_references[]` (data-URL аудио). Алгоритм: посчитать эмбеддинг каждого
  эталонного клипа (`Inference(pipeline._embedding, window="whole")`), усреднить эмбеддинги по каждому
  диаризованному спикеру, затем **argmax косинуса без порога** (`best_sim = -2.0`, `if sim > best_sim`)
  [П, `src/speaches/routers/diarization.py:44-104`]. Следствие: незнакомец всегда получает чьё-то имя — нет reject.
  Поддерживаемая модель диаризации ровно одна: `pyannote/speaker-diarization-community-1`
  [П, `src/speaches/executors/pyannote_diarization.py:17`].
- **speaches, жизненный цикл моделей**: `SelfDisposingModel` с refcount (`_increment_ref`/`_decrement_ref`) и
  `threading.Timer(self.ttl, self.unload)` — при TTL=0 выгрузка сразу после последнего использования
  [П, `src/speaches/executors/shared/base_model_manager.py:37-100`]. Это готовый паттерн «минимальные накладные
  расходы в простое».
- **FluidAudio**: enrollment праймингом (Sortformer) + `SpeakerManager` с известными спикерами, `isPermanent`,
  `findMergeablePairs`, `.merge/.overwrite/.skip` — ближе всех к полноценному реестру, но без персистентности и без
  версионирования модели эмбеддингов.
- **pyannoteAI**: voiceprint API — enrollment + identify (одно-к-одному и одно-ко-многим), identification «всегда
  возвращает диаризацию вместе с личностями»; позиционируется как soft-signal, **не** как биометрическая
  аутентификация [П/М, https://huggingface.co/blog/pyannote/speaker-diarization-recognition-and-identification].
- **gotranscribesrv** (GPL-3.0): один `actor EngineManager`, всё инференс-состояние внутри, все движки грузятся
  **эагерно на старте** (`AsrModels.downloadAndLoad`, `SortformerModels.loadFromHuggingFace` в одном методе)
  [П, `sidecar-audio/Sources/Server/EngineManager.swift:12,86-116`]. Антипример: сессии сериализуются, простой
  стоит полной памяти всех моделей.

### 11. Русский язык: что реально проверено

- Публичного DER-бенчмарка для русской диаризации не найдено. `stt-ru-benchmark` (vakovalskii) покрывает только
  WER ASR, не диаризацию [П, https://github.com/vakovalskii/stt-ru-benchmark].
- **GigaAM (Sber)** — ASR/эмоции/эмбеддинги; в `README_ru.md` (14 172 байта) **ноль упоминаний** «диаризац»
  (проверено `grep -ic` → 0) [П, https://github.com/salute-developers/GigaAM/blob/main/README_ru.md].
- Все проверенные диаризаторы обучены на англоязычных/китайских корпусах: Sortformer/v2.1 — Fisher, LibriSpeech,
  AMI, ICSI, VoxConverse, AISHELL-4, DIHARD-3, NOTSOFAR1, AliMeeting, DiPCo; community-1/DiariZen — те же + RAMC,
  MSDWILD; Nemotron 3 — «21 язык» в симулированных миксах, но бенчмарки только английские
  [П, карточки моделей].
- Единственная проверка «неанглийской» идентификации в изученном коде — CAM++ на AISHELL-1 (мандарин)
  [П-3P, FluidAudio `Benchmarks.md`].
- Пользовательские впечатления (Habr, обсуждения): pyannote «путает спикеров между звонками», `SPEAKER_00`
  назначается произвольно — то есть боль ровно та, которую должен закрыть реестр
  [неофиц., https://habr.com/ru/articles/1020632/].

### 12. Приватность: голосовые эмбеддинги — биометрия

- BIPA (740 ILCS 14/1) прямо перечисляет **voiceprint** как «biometric identifier» наравне с отпечатком и
  геометрией лица [П, https://www.ilga.gov/Legislation/ILCS/Articles?ActID=3004].
- Карточка Nemotron 3 Diarization в разделе privacy: «Personal data used to create this model? Yes», категория —
  «Voice recordings», consent — «Yes», но «Is data compliant with data subject requests for data correction or
  removal…? No, not possible with externally-sourced data» [П,
  https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/privacy.md].
- Практический вывод: хранение центроидов голоса = хранение биометрического шаблона; в ЕС это special category
  data по GDPR (Art. 4(14) определяет биометрические данные, Art. 9 ограничивает обработку для уникальной
  идентификации) — точную формулировку статьи в этом ресёрче я по первоисточнику не сверял, см. §5.

---

## Ограничения и риски

1. **Протоколы DER несравнимы между источниками.** pyannote/DiariZen публикуют collar=0 и scoring overlap;
   FluidAudio — collar=0.25 и `ignoreOverlap=true`; NVIDIA — collar=0 с overlap, но с post-processing YAML.
   Разница в 5–10 абсолютных пунктов достигается сменой протокола. Любая матрица без колонки «протокол»
   бесполезна.
2. **Неточность «DER» у TitaNet** (oracle VAD + известное число спикеров) — типичная ловушка при сравнении
   эмбеддинг-моделей по DER. Эмбеддинги надо сравнивать по EER/minDCF на trial-парах, а не по DER.
3. **Лицензионная несовместимость.** Sortformer v1 и DiariZen — NC. Порт FluidAudio `diar-streaming-sortformer-coreml`
   заявлен как `cc-by-4.0`, тогда как upstream v2.1 — NVIDIA Open Model License: переупаковка не может ослабить
   лицензию оригинала [I]. Плюс `speaker-diarization-coreml` — `other` (наследует CC-BY-4.0 community-1).
   Runtime, который «просто скачивает бандл с HF», может нарушить лицензию, не заметив.
4. **Gated-модели.** community-1, 3.1, segmentation-3.0, pyannote/embedding требуют HF-токена и принятия условий —
   автоматическая установка «по требованию» без интерактивного логина невозможна. Это ломает UX «как Ollama».
5. **4/8 слотов — не «мягкое» ограничение.** На встрече с 9 участниками Sortformer сольёт голоса, Nemotron
   упрётся в 8; DER на подмножестве 5–9 спикеров DIHARD III у v2.1 = 41.42 %. Runtime обязан валидировать
   `max_speakers` и возвращать ошибку/деградировать осознанно.
6. **Стриминговый онлайн-кластеризатор — источник основных ошибок.** FluidAudio измеряет 38–53 % DER на AMI SDM,
   с 7–11 детектированными спикерами вместо 4 [П-3P]. Ставить его дефолтом для встреч нельзя.
7. **Состояние диаризатора не потокобезопасно.** «`Nemotron3Diarizer` is not thread-safe: own it from one actor or
   task» [П-3P, `Nemotron3.md`]; FluidAudio `SpeakerManager` — `mutating`-структура. Значит: один объект состояния
   на сессию, никакого шаринга состояния между клиентами.
8. **ANE — общий ресурс.** Несколько сессий одновременно конкурируют за Neural Engine; в FluidAudio отмечены
   деградации при параллельных прогонах (исчерпание IOSurface-буферов CoreML при последовательных прогонах многих
   встреч в одном процессе, `Benchmarks.md:882`). Нужен планировщик, а не «просто параллельные запросы».
9. **Дрейф центроидов.** Обновление усреднением (FluidAudio) без ограничения вклада даёт «уплывание» профиля к
   последним записям; ограничение — `embeddingThreshold=0.45` + `minEmbeddingUpdateDuration=2.0 s`. Для реестра
   нужны явные политики (см. §4) и возможность откатить.
10. **Русская речь не измерена вообще.** Все пороги (0.45/0.5/0.65/0.8) калибровались на английском/китайском.
    Для русской речи с англоязычными IT-терминами (Kubernetes, kubectl, Istio) распределение скорее сдвинется
    из-за кодовых переключений внутри реплик — но это гипотеза, требующая замера [I].

---

## Выводы для Runtime

### 4.1. Модельный слой: что брать

| Роль в каталоге | Кандидат | Почему | Лицензия |
|---|---|---|---|
| `diarization.batch.meeting` (дефолт, офлайн) | pyannote community-1 (или CoreML-порт) / DiariZen Large-s80-v2 | лучшее качество на AMI/DIHARD; без лимита спикеров | CC-BY-4.0 (DiariZen — NC → только для некоммерческого профиля) |
| `diarization.streaming.low_latency` | Nemotron 3 Diarization (`fast`/`fast32`) | 1.04 s буфер, до 8 спикеров, лучший потоковый DER | openmdw-1.1 (карточка: commercial OK) |
| `diarization.streaming.4spk` | Sortformer v2.1 | зрелый, стабильные личности, дешёвый | NVIDIA OML (v2 — CC-BY-4.0) |
| `diarization.streaming.many_spk` | LS-EEND (dihard3/ami вариант) | до 10 спикеров, но шумные FA | MIT (CoreML-порт) |
| `embedding.speaker.default` | WeSpeaker ResNet34 (256-d) | совместим с community-1 (та же модель в пайплайне), 0.723 % EER | CC-BY-4.0 |
| `embedding.speaker.multilingual` | CAM++ 192-d / ERes2NetV2 | 0.65/0.61 % EER, дешёвые, есть ONNX+CoreML | требует проверки |
| `embedding.speaker.alt` | TitaNet-L 192-d / ECAPA 192-d | совместимы с NeMo- и SpeechBrain-пайплайнами | CC-BY-4.0 / Apache-2.0 |

**Не брать:** precision-2 (облако), Sortformer v1 (NC + офлайн-качество на длинных встречах 56.7 % DER),
онлайн-кластеризатор FluidAudio как дефолт для встреч.

### 4.2. Одна модель эмбеддингов или две?

- Если диаризация каскадная (pyannote/DiariZen/3D-Speaker/sherpa) — **одна и та же** модель даёт эмбеддинги и для
  кластеризации, и для реестра: у community-1 это WeSpeaker 256-d, интерфейс `embedSpans` уже есть. Плюс: центроид
  кластера сразу пригоден для сравнения с реестром.
- Если диаризация E2E (Sortformer/Nemotron/LS-EEND) — эмбеддингов нет вообще, **обязательна вторая модель**.
  Правильный путь: взять таймлайн от E2E-модели и прогнать `embed` по интервалам реплик (ровно сценарий
  `embedSpans`: «use when the speaker turns are already known, e.g. from another diarizer's output»).
- **Контракт:** у записи реестра обязательны `embedding_model_id`, `dim`, `normalization` (L2), `revision`.
  Сравнение между разными `embedding_model_id` — ошибка `EMBEDDING_SPACE_MISMATCH`, а не «попробуем косинус».

### 4.3. Дизайн speaker-registry: разделение runtime / клиент

Принцип: **runtime — «арифметика и модели», клиент — «личности и политика»**. Runtime не должен знать имён
(«Runtime не ведёт диалог и не решает по смыслу»), но обязан дать примитивы, чтобы клиенты не изобретали свою
математику (как speaches, где получился argmax без порога).

**В runtime (сервис):**
1. `POST /v1/embeddings/speaker` — `{audio | (session_id, spans[]), model}` → `{embeddings[][], dim, model_id,
   revision}`. Безтиповая операция, полностью детерминированная.
2. `POST /v1/diarize` — `{audio, model, num_speakers|min|max, exclusive: bool}` → таймлайн
   `[{start, end, speaker_index}]` + (если движок каскадный) `speaker_centroids` + `speaker_confidences`.
   `speaker_index` — всегда относительный (`S0..Sn`), никогда не имя.
3. `POST /v1/speaker/match` — **stateless**: `{query_embedding | spans, candidates: [{id, centroid, model_id}],
   threshold, margin, return_top_k}` → `[{id, score, decision: match|unknown}]`. Runtime считает косинус, порог и
   margin, но **кандидатов передаёт клиент**. Так runtime не хранит биометрию и остаётся multitenant-нейтральным.
4. `POST /v1/speaker/assign` — присвоение кластеров сессии к кандидатам: на вход таймлайн + центроиды сессии +
   кандидаты, на выход `speaker_index → id|unknown`. Внутри — жадное назначение по косинусу с порогом **и**
   проверкой margin (top1 − top2 ≥ δ), опционально венгерское назначение, если кандидатов ≥ кластеров
   (в FluidAudio уже есть `HungarianAssignment.swift` как референс).
5. **Опциональное хранилище реестра** — namespaced, embeddings-only, без имён: `PUT/GET/DELETE
   /v1/registries/{ns}/speakers/{id}` c `{centroid, model_id, revision, samples_count, updated_at, ttl}`.
   Ключевое: namespace изолирован, шифрование на диске, TTL, каскадное удаление. Если политика клиента запрещает
   хранение — он использует stateless `/match`.

**У клиента (приложение):**
- Отображение `id → имя/роль/фото`, привязка к календарю/CRM/HRIS.
- UI согласия и юридическая база (BIPA/GDPR), retention policy, экспорт/удаление.
- Решение «сохранить нового спикера в реестр» и слияние дубликатов (`findMergeablePairs`-подобный отчёт — от
  runtime как рекомендация, решение — у клиента).
- Пороги по умолчанию и их переопределение (см. 4.4) — потому что порог зависит от домена клиента.

**Почему так:** (а) биометрия не покидает приложение, если клиент не включил хранилище; (б) runtime остаётся
без «смысла»; (в) один и тот же сервис обслуживает Swift-приложение и Rust-бэкенд без общей модели данных
личностей; (г) при смене модели эмбеддингов клиент может держать два пространства и мигрировать постепенно.

### 4.4. Пороги и калибровка (обязательные правила)

- Хранить **семантику**, а не число: `threshold_kind: "cosine_similarity" | "cosine_distance" | "euclidean"`.
  Иначе 0.65 из FluidAudio (расстояние) и 0.5 из sherpa (близость) перепутаются местами.
- Дефолты по умолчанию (стартовые, не «истина»): WeSpeaker 256-d — similarity 0.55–0.6; CAM++ 192-d — 0.5–0.55
  (обосновано измеренными same 0.805 / diff 0.256); ECAPA/TitaNet — 0.5–0.6; обязательный `margin` 0.03–0.05.
- **Reject обязателен**: `decision: "unknown"` — нормальный ответ. Принудительное присвоение (как в speaches)
  даёт ложные личности, что хуже «Спикер 3».
- Обновление центроида: только при `score ≤ embeddingThreshold` и длительности ≥ 2 s; вклад нового образца
  ограничить (EMA с α ≤ 0.2) или хранить все образцы и пересчитывать среднее; хранить `revision` и позволять
  откат. Пересчёт при смене модели — офлайн-миграция, не «на лету».
- Калибровка на домене: собирать score-распределения (same/diff) и публиковать EER по реестру — иначе порог
  «на глаз».

### 4.5. Сессии и совместное использование весов

- **Веса — read-only, шарятся**: CoreML-бандл/ONNX компилируется один раз, refcount + TTL-выгрузка (паттерн
  `SelfDisposingModel` из speaches; в FluidAudio кэш бандлов лежит в
  `~/Library/Application Support/FluidAudio/Models/...`).
- **Состояние — всегда per-session**: `SortformerStreamingState` (spkcache/fifo/silence), `SpeakerManager`,
  VBx-статистики, очередь чанков. Один объект состояния — один владелец (actor/task).
- **Enrollment праймингом — сессионный и ограниченный**: 4 слота Sortformer / 8 Nemotron; если прайминг
  исчерпал слоты, новые участники не появятся. Поэтому: прайминг — оптимизация для «всех знаем заранее»,
  реестр — для «знаем часть».
- **Планировщик по вычислительным юнитам**: ANE/GPU/CPU — общий ресурс; сериализовать инференс на юнит,
  а не на весь сервис, чтобы ASR-сессия не блокировала диаризацию (в отличие от `actor EngineManager`
  в gotranscribesrv).
- **Идле-стоимость**: TTL-выгрузка моделей по умолчанию (например 5–15 мин), выгрузка состояния сессии по
  завершении, «нулевая» память при простое — только HTTP-сервер и каталог.

### 4.6. Связка с ASR и VAD (границы ответственности)

- Диаризация не приписывает слова: runtime возвращает таймлайн, клиент (или отдельная операция `align`)
  сопоставляет слова по времени. Для overlap — правило «слот с максимумом активности на интервале слова»
  (реализовано в FluidAudio для Nemotron 3).
- `exclusive_diarization` из community-1 — хороший дефолт для транскриптов (один спикер на слово), тогда как
  обычный таймлайн нужен для аналитики overlap. Отдавать оба.

---

## Неизвестные / что измерить

1. **Русский DER.** Нет ни одного публичного замера. Нужен собственный набор: 3–5 встреч 4–8 человек, 40–60 мин,
   русская речь + англ. IT-термины, эталон RTTM вручную. Сравнить community-1 (CoreML), DiariZen, Nemotron 3,
   Sortformer v2.1. Метрики: DER (collar 0.25 и 0), miss/FA/confusion, SCA.
2. **EER идентификации на русском.** Собрать trial-пары (same/diff) из своих записей для WeSpeaker-256, CAM++-192,
   ECAPA-192, TitaNet-192; получить пороги и EER; проверить деградацию при кодовых переключениях.
3. **Влияние канала** (микрофон ноутбука vs гарнитура vs дальнее поле) на те же пары — ожидаемо главный фактор
   дрейфа центроидов.
4. **Скорость загрузки моделей** на Apple Silicon: время холодного старта CoreML (компиляция), peak RSS для
   Sortformer/Nemotron/community-1-порта, стоимость TTL-выгрузки/повторной загрузки. В документации есть только
   косвенные цифры (13 MB на окно при `batchSize`, ~400 MB пик при 32 окнах).
5. **Параллельные сессии**: сколько одновременных диаризаций выдерживает ANE без деградации латентности;
   реально ли шарятся веса CoreML между процессами (или только между сессиями одного процесса) — не проверял.
6. **Лицензия OpenMDW-1.1** — текст не прочитан; нужно юридически подтвердить, что коммерческое использование
   разрешено и какие есть ограничения (карточка утверждает «ready for commercial»).
7. **Лицензии весов CAM++/ERes2Net на ModelScope** — не подтверждены; без этого нельзя включать в каталог для
   коммерческого профиля.
8. **Точность Nemotron 3 в CoreML-порте на реальных русских встречах** — parity с NeMo заявлен 99.995 % на
   реальном аудио, но язык/домен не раскрыт.
9. **Стабильность слотов Sortformer между чанками при длинных паузах** — «extremely stable identities» заявлено,
   но количественной метрики (например, доля корректных переименований после тишины > 30 s) нет.
10. **Смешение моделей эмбеддингов**: насколько падает EER, если центроид собран на CAM++, а запрос — на WeSpeaker
    (ожидаемо полная несовместимость; нужно подтвердить и зафиксировать как ошибку API).

---

## Источники

**Модели и официальные бенчмарки**
- nvidia/diar_sortformer_4spk-v1 (CC-BY-NC-4.0): https://huggingface.co/nvidia/diar_sortformer_4spk-v1
- nvidia/diar_streaming_sortformer_4spk-v2 (CC-BY-4.0): https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2
- nvidia/diar_streaming_sortformer_4spk-v2.1 (NVIDIA Open Model License): https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1
- nvidia/Nemotron-3-Diarization (openmdw-1.1): https://huggingface.co/nvidia/Nemotron-3-Diarization
  - privacy.md: https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/privacy.md
  - diarization_evaluation.md (протокол оценки): https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/diarization_evaluation.md
- Блог NVIDIA «Know Who Spoke When» (DER/RTFx): https://huggingface.co/blog/nvidia/nemotron-diarization
- NeMo checkpoints / результаты диаризации: https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/results.html
- pyannote.audio README (таблица DER 3.1 / community-1 / precision-2, скорость): https://github.com/pyannote/pyannote-audio
- pyannote/speaker-diarization-community-1 (CC-BY-4.0, exclusive diarization): https://huggingface.co/pyannote/speaker-diarization-community-1
- pyannote blog «Diarization vs Recognition vs Identification» (23.10.2026): https://huggingface.co/blog/pyannote/speaker-diarization-recognition-and-identification
- DiariZen (MIT код, CC BY-NC 4.0 веса, DER-таблица): https://github.com/BUTSpeechFIT/DiariZen
- BUT-FIT/diarizen-wavlm-large-s80-md (прунинг 316.6M→63.3M): https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md
- 3D-Speaker (EER и DER таблицы, Apache-2.0): https://github.com/modelscope/3D-Speaker
- WeSpeaker (0.723 % EER ResNet34, diarization recipe): https://github.com/wenet-e2e/wespeaker
- pyannote/wespeaker-voxceleb-resnet34-LM (CC-BY-4.0): https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM
- speechbrain/spkrec-ecapa-voxceleb (0.80 % EER, Apache-2.0): https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- nvidia/speakerverification_en_titanet_large (0.66 % EER, oracle-VAD DER): https://huggingface.co/nvidia/speakerverification_en_titanet_large
- titanet-large конфиг (`emb_sizes: 192`): https://github.com/NVIDIA/NeMo/blob/main/examples/speaker_tasks/recognition/conf/titanet-large.yaml

**Код (локальные клоны, permalinks по sha)**
- FluidAudio @ `1b0ed50d686873128a4129b3510ce3914c849bab`:
  - `Documentation/Diarization/GettingStarted.md` (сравнение движков, лимиты, латентность, VBx-пайплайн)
  - `Documentation/Diarization/Sortformer.md`, `Nemotron3.md`, `LS-EEND.md`, `SpeakerManager.md`, `BenchmarkAMISubset.md`
  - `Documentation/Benchmarks.md` (offline 10.62 % AMI SDM; VoxConverse 15.07/13.89 %; CAM++ EER; Sortformer offline 56.7 %)
  - `Sources/FluidAudio/Diarizer/Clustering/SpeakerTypes.swift#L7-L25` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Clustering/SpeakerTypes.swift#L7-L25
  - `Sources/FluidAudio/Diarizer/Clustering/SpeakerManager.swift#L16,L136,L205-L208` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Clustering/SpeakerManager.swift#L205-L208
  - `Sources/FluidAudio/Diarizer/Clustering/SpeakerOperations.swift#L63,L465-L474` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Clustering/SpeakerOperations.swift#L63
  - `Sources/FluidAudio/Diarizer/Sortformer/SortformerDiarizer.swift#L259-L400` (enrollment праймингом) — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Sortformer/SortformerDiarizer.swift#L259-L400
  - `Sources/FluidAudio/Diarizer/Offline/Core/OfflineDiarizerManager.swift#L516-L536` (`embedSpans`, 256-d) — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Offline/Core/OfflineDiarizerManager.swift#L516-L536
  - `Sources/FluidAudio/Diarizer/Offline/Core/OfflineDiarizerTypes.swift#L144-L158` (num/min/max speakers) — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Offline/Core/OfflineDiarizerTypes.swift#L144-L158
- sherpa-onnx @ `532f1b5a8a207bf53b116b747207c1506696c843`:
  - `sherpa-onnx/csrc/offline-speaker-diarization-impl.cc` (только pyannote-сегментация) — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-speaker-diarization-impl.cc
  - `sherpa-onnx/csrc/fast-clustering-config.h#L16-L27` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/fast-clustering-config.h#L16-L27
  - `sherpa-onnx/csrc/speaker-embedding-manager.h#L20-L100` — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/speaker-embedding-manager.h#L20-L100
  - `sherpa-onnx/csrc/offline-speaker-diarization.h#L25-L30` (min_duration_on/off) — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-speaker-diarization.h#L25-L30
  - `sherpa-onnx/csrc/speaker-embedding-extractor-model.cc#L107-L113` (wespeaker/3d-speaker) — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/speaker-embedding-extractor-model.cc#L107-L113
- speaches @ `993994f7984bf3fe9655b267448328cf66fccb42`:
  - `src/speaches/routers/diarization.py#L44-L104` (argmax без порога) — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/diarization.py#L44-L104
  - `src/speaches/executors/shared/base_model_manager.py#L37-L100` (SelfDisposingModel + TTL) — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/shared/base_model_manager.py#L37-L100
  - `src/speaches/executors/pyannote_diarization.py#L17` (единственная модель диаризации) — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/pyannote_diarization.py#L17
- WhisperLiveKit @ `eb92c115ee7abc1e161b0b2159da31785b0f5566`: `whisperlivekit/diarization/sortformer_backend.py#L55-L127` — https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/diarization/sortformer_backend.py#L55-L127
- speech-swift @ `cbad3be29480825707890490f446b19d7288be2d`:
  - `Sources/SpeechVAD/Community1Configuration.swift#L17-L42` (256-d, AHC-отсечка 0.6) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechVAD/Community1Configuration.swift#L17-L42
  - `Sources/DiarizationBenchmark/DiarizationBench.swift#L23` (движки: community1-coreml, pyannote-mlx, sortformer, nemotron3) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/DiarizationBenchmark/DiarizationBench.swift#L23
- gotranscribesrv @ `a5f4289078553ba66f83510c62146043bed91a7a`: `sidecar-audio/Sources/Server/EngineManager.swift#L12,L86-L116` (actor + эагерная загрузка) — https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/sidecar-audio/Sources/Server/EngineManager.swift#L12

**Прочее**
- Argmax SpeakerKit (pyannote community-1 на Apple Silicon, MIT-код, CC-BY-4.0 веса): https://github.com/argmaxinc/argmax-oss-swift
- `argmaxinc/speakerkit-coreml` (pyannote-v3 сегментер/эмбеддер + pyannote-v4 кластеризатор): https://huggingface.co/argmaxinc/speakerkit-coreml
- GigaAM (русский ASR, без диаризации): https://github.com/salute-developers/GigaAM/blob/main/README_ru.md
- stt-ru-benchmark (WER, не DER): https://github.com/vakovalskii/stt-ru-benchmark
- BIPA 740 ILCS 14 (voiceprint как биометрический идентификатор): https://www.ilga.gov/Legislation/ILCS/Articles?ActID=3004
- Habr, пользовательский опыт «диаризация путает спикеров между звонками»: https://habr.com/ru/articles/1020632/
