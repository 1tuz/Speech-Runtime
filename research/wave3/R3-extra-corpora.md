# R3 — дополнительные корпуса (FLEURS, CS-YODAS, Podlodka, ToneWebinars)

Дата получения (retrieval date): **2026-10-28**. Источники фактов: HF API (`/api/datasets/<id>`, `/tree/...`, `/resolve/<rev>/...`), HF datasets-server (`/size`), README/скрипт загрузки внутри ревизий, arXiv HTML `2606.11514v1`. Всё, что не пришло в HTTP-ответе, помечено `[INFERENCE]`. Локальные файлы: `experiments/corpus/local/extra/`, контрольные суммы — `experiments/corpus/local/extra/SHA256SUMS.txt`.

Аудио нигде не скачивалось: только текстовые метаданные и `rus.jsonl`.

## Реестр

| Источник | Ревизия | Лицензия | Что реально скачано | Проверенные факты | Открытые вопросы |
|---|---|---|---|---|---|
| `google/fleurs`, конфиг `ru_ru` | `70bb2e84b976b7e960aa89f1c648e09c59f894dd` (lastModified 2026-05-15) | `cc-by-4.0` (frontmatter карточки; в README: «All datasets are licensed under the Creative Commons license (CC-BY)») | `data/ru_ru/test.tsv` (735 258 B) и `data/ru_ru/dev.tsv` (316 328 B). Паркет `parquet-data/ru_ru/test-00000-of-00001.parquet` (564 710 562 B) **не** скачивался — в нём аудио | test = **775** примеров (см. §1), dev = 356, train = 2562; 2.50 ч теста, 344 диктора | нет транскриптов для `ru_ru` в виде «одна строка = один сегмент» с таймкодами — FLEURS посегментный, без границ; для WER нужен внешний нормализатор |
| `byan/cs-yodas`, файл `rus.jsonl` | `e51028041b403f63c99ac91a4af040e72d0cad0e` (lastModified 2026-06-10) | **CC-BY-NC-4.0** по README и `cs_yodas.py` (`_LICENSE`); в HF API поля `license` **нет**, тег `license:*` отсутствует. arXiv-версия статьи заявляет `License: CC BY 4.0` — расхождение, см. §2.6 | `README.md` (1 091 B), `rus.jsonl` (3 947 866 B), `cs_yodas.py` (3 211 B). `rus.tar` (2 045 777 920 B) **не** скачивался | 1081 запись (совпадает с таблицей в README: rus = 1,081); 954 видео; целевых сегментов ≈ 1.03 ч (контекстных окон 17.8 ч, окон `id` 8.42 ч — см. §2.5); транскрипты — **автоматические черновики**, см. §2.4 | аудио не проверено; нет сплитов; NC-контур блокирует коммерческое использование |
| `bond005/podlodka_speech` | `4a9161485a70ea849ccaa54a72faa9bfc0a60627` (lastModified 2024-04-05) | **не объявлена**: `cardData.license = null`, тегов `license:*` нет, README состоит из одного frontmatter без текста | ничего | 67 / 20 / 20 эпизодов (train/validation/test) по frontmatter; `data/test-...parquet` и `data/validation-...parquet` имеют **один и тот же** LFS-oid `53ebea8e77b6470dbafaa2c8…` (15588245 B каждый) | лицензия отсутствует → использовать нельзя без запроса к автору; test и validation — байт-в-байт один файл, заявленные 20 «тестовых» эпизодов дублируют валидацию |
| `Vikhrmodels/ToneWebinars` | `a9bda19fb4bf501a744dc0a9c1c7d239b4c43ea4` (lastModified 2025-06-17) | `apache-2.0` (frontmatter + тег) | ничего | 286 787 train / 21 587 validation, `dataset_size` 755.8 ГБ; в карточке прямо указано, что это перепаковка `ZeroAgency/shkolkovo-bobr.video-webinars-audio` | текст — из upstream whisper+VAD (§3), но сама карточка способ получения текста не описывает; правовая цепочка спорная (§3) |
| `lab260/ToneWebinars_Balalaika` | `3a3eb3e26f6674d2984461baee55520a32ad6988` (lastModified 2026-06-25) | `apache-2.0` (frontmatter + тег) | ничего | 248.9 ч после фильтрации из >2200 ч; `skolkovo_webinars_balalaika.parquet` 51 832 458 B + `…tar.gz` 13 887 445 505 B; транскрипты — ROVER-слияние 5 ASR | нет заявленной человеческой проверки текста; см. §3 |

## 1. FLEURS `ru_ru` — счёт теста

Скачаны только TSV-транскрипты (`data/ru_ru/`), без аудио. Файлы без строки заголовка, 7 колонок, разделитель — табуляция.

| Колонка | Пример | Смысл |
|---|---|---|
| 1 | `1799` | id диктора (в test — 344 уникальных, 1–3 реплики на диктора) |
| 2 | `13632175593719991398.wav` | имя файла в `data/ru_ru/audio/test/` |
| 3 | `Помимо соревнований в среду, Карпендо …` | `transcription` (с пунктуацией и регистром) |
| 4 | `помимо соревнований в среду карпендо …` | `raw_transcription` (нормализованный) |
| 5 | `п о м и м о \| с о р е в н о в а н и й \| …` | посимвольное выравнивание, `\|` — граница слова |
| 6 | `113280` | `num_samples` (16 кГц) |
| 7 | `MALE` | пол |

Наблюдённые числа (посчитаны по скачанному файлу, не взяты из карточки):

- строк в `ru_ru-test.tsv` — **775**, все 775 файлов уникальны, пустых транскриптов 0;
- `wc -l` даёт 775, потому что заголовка нет — строка 1 уже является данными;
- сумма `num_samples` = 143 901 120 → **2.499 ч** при 16 кГц, средняя реплика 11.6 с;
- пол: 362 MALE / 413 FEMALE;
- `ru_ru-dev.tsv` — 356 строк, 1.08 ч (тот же подсчёт по `num_samples`).

Итого по конфигу `ru_ru`: измерено test 2.50 ч + dev 1.08 ч = 3.58 ч; train (2562 строки) не скачивался, `[INFERENCE]` при средней длине теста/дева ≈ 11.4 с он даёт ≈ 8.1 ч, то есть весь `ru_ru` ≈ **11.7 ч** — сходится с оценкой «≈12 ч» из `R1-ru-corpora.md`.

Перекрёстные проверки того же числа 775: собственный подсчёт строк TSV (источник — скачанный файл), frontmatter README (`config_name: ru_ru` → `test: num_examples: 775`) и `datasets-server.huggingface.co/size?dataset=google/fleurs&config=ru_ru` → `test rows= 775, bytes= 564710562` (последние два считаются по паркету, независимо от TSV). Все три совпали.

Транскрипты лежат в `experiments/corpus/local/extra/fleurs/ru_ru-test.tsv`, sha256 `cd54f261…676e6` (полностью — в `SHA256SUMS.txt`). Аудио для прогона: либо `parquet-data/ru_ru/test-00000-of-00001.parquet` (564.7 МБ, 775 строк, аудио внутри), либо 775 wav из `data/ru_ru/audio/test/`.

## 2. CS-YODAS `rus`

### 2.1 Что скачано

`README.md`, `rus.jsonl`, `cs_yodas.py` в `experiments/corpus/local/extra/cs-yodas/`. `rus.tar` (2 045 777 920 B ≈ 1.9 ГиБ) не скачивался; `fra.tar.aa`/`fra.tar.ab` (32.2 + 28.2 ГБ) тоже. Общий объём репозитория по API — 68 762 513 372 B.

sha256 `rus.jsonl` = `f29381d741c611e54e5ad06d7645f4a9fa837508ea8784006d4f600d70be006a` — совпадает с LFS-oid из `/tree/main`, то есть локальная копия бит-в-бит соответствует ревизии `e5102804…`.

### 2.2 Схема `rus.jsonl`

1081 строка = 1081 JSON-объект (пустых строк нет), 16 полей, набор полей одинаков во всех записях.

| Поле | Тип | Пример / диапазон | Комментарий |
|---|---|---|---|
| `id` | str | `-1WwmC-g-FY_000229065_000257392_rus_asr_000001260_000001572` | `<videoId>_<start_ms>_<end_ms>_<lang>_asr_<tok_start>_<tok_end>`; суффикс `_asr_` присутствует во всех 1081 |
| `text` | str | `украинском языке и на английском языке` | целевая реплика; подстрока `context_text` в 1080 из 1081 случаев |
| `language` | str | `rus` (1081/1081) | матричный язык |
| `languages` | list[str] | `["Russian","English"]` (1000), `["Russian","Numerals"]` (46), `["Russian","Numbers"]` (6), `["Russian","Cyrillic"]` (4), `["Russian","Ukrainian"]` (3), `["Russian","Russian (slang/colloquial)"]` (2) | «второй язык» — не всегда язык: цифры, кириллица, сленг |
| `consistent` | int | `1` (1081/1081) | нулевая вариативность |
| `Q1` | str | `Yes` (1081/1081) | вопрос 1 из §2.4 статьи: «транскрипт верен?» |
| `Q2` | str | `Yes` (1081/1081) | «есть ли `<lang1>`?» |
| `Q3` | str | `Yes` (1081/1081) | «`<lang1>` — матричный?» |
| `Q4` | str | `Yes` (1081/1081) | «есть ли `<lang2>`?» |
| `Q5` | str | `No` (1081/1081) | «все ли слова `<lang2>` — имена собственные?» → `No` = есть настоящий код-свитч |
| `Comments` | str | 578 уникальных значений; топ: `Good CS example, with jargon CS related to IT` (269) | непусто во всех 1081 |
| `context_text` | str | ASR-текст окна ~60 с вокруг реплики | без пунктуации, нижний регистр |
| `context_text_list` | list[list] | `[["серого а такой цвет впервые мы видим", 0.0, 3.53], …]` | `[текст, начало_с, конец_с]`, таймкоды **относительно контекстного wav**; 4–46 элементов на запись |
| `kaldi_uttid` | str | `-1WwmC-g-FY_rus_000212855_000274244` | id контекстного окна (~61 с) |
| `all_code_switched_texts` | list[str] | `["украинском языке и на английском языке"]` | |
| `wav_path` | str | `iter1_context/rus/data/format.1/<video>_rus_<start>_<end>.wav` | путь внутри `rus.tar`; `format.N` — номер пачки, не формат аудио |

### 2.3 Три записи (длинные поля усечены)

```json
{"id": "-1WwmC-g-FY_000229065_000257392_rus_asr_000001260_000001572", "text": "украинском языке и на английском языке", "language": "rus", "languages": ["Russian", "English"], "consistent": 1, "Q1": "Yes", "Q2": "Yes", "Q3": "Yes", "Q4": "Yes", "Q5": "No", "Comments": "The transcript mentions both Ukrainian and English languages, but there is no actual code-switching in the speech. The transcription is correct, but the speech itself is monolingual Russian.", "context_text": "серого а такой цвет впервые мы видим если честно мне он очень нравится неординарный такой …", "context_text_list": [["серого а такой цвет впервые мы видим", 0.0, 3.53], ["если честно мне он очень нравится", 3.71, 6.58], ["неординарный такой интересный причем", 7.73, 10.83], …], "kaldi_uttid": "-1WwmC-g-FY_rus_000212855_000274244", "all_code_switched_texts": ["украинском языке и на английском языке"], "wav_path": "iter1_context/rus/data/format.1/-1WwmC-g-FY_rus_000212855_000274244.wav"}

{"id": "P2LbGpFbvuU_000130623_000157003_rus_asr_000000000_000000512", "text": "где написано next а давайте быстренько", "language": "rus", "languages": ["Russian", "English"], "consistent": 1, "Q1": "Yes", "Q2": "Yes", "Q3": "Yes", "Q4": "Yes", "Q5": "No", "Comments": "Good CS example, with jargon CS related to IT or software interfaces", "context_text": "проходит довольно таки обычную историю вот за всего каких-то какой-то час можно пройти вот …", "context_text_list": [["проходит довольно таки обычную историю", 0.0, 1.93], ["вот за всего каких-то какой-то час можно", 2.03, 8.02], ["пройти вот этот чел от лин левой", 8.05, 12.04], …], "kaldi_uttid": "P2LbGpFbvuU_rus_000104843_000157003", "all_code_switched_texts": ["где написано next а давайте быстренько"], "wav_path": "iter1_context/rus/data/format.8/P2LbGpFbvuU_rus_000104843_000157003.wav"}

{"id": "w1_3ewNZ6O4_001792296_001820923_rus_asr_000000912_000001554", "text": "control x контроль и получаем два слоя", "language": "rus", "languages": ["Russian", "English"], "consistent": 1, "Q1": "Yes", "Q2": "Yes", "Q3": "Yes", "Q4": "Yes", "Q5": "No", "Comments": "Good CS example, with jargon CS related to IT or design", "context_text": "растрировать данный текст как бы это не ужасно звучало для верстки растрировать текст ну в …", "context_text_list": [["растрировать данный текст как бы это не", 0.0, 3.19], ["ужасно звучало для верстки растрировать", 3.19, 5.6], ["текст ну в данном случае будет играть", 5.6, 7.44], …], "kaldi_uttid": "w1_3ewNZ6O4_rus_001771673_001835769", "all_code_switched_texts": ["control x контроль и получаем два слоя"], "wav_path": "iter1_context/rus/data/format.16/w1_3ewNZ6O4_rus_001771673_001835769.wav"}
```

### 2.4 Транскрипты — автоматические черновики, не выверенный текст

Это главное ограничение корпуса, и оно подтверждается четырьмя независимыми признаками:

1. Статья (§2.1) прямо пишет, что исходник — «cleaned YODAS from the OWSM v4 project … with speech segments **paired to automatic transcripts**».
2. Статья (§2.3) перечисляет «transcription errors» как один из трёх главных источников ложного код-свитча: «noisy or imperfect transcripts can falsely mix languages».
3. Поле `id` содержит маркер `_asr_` во всех 1081 записях, а `text` в 1080/1081 случаев является дословной подстрокой `context_text` — то есть это вырезка из ASR-выхода, а не отдельная ручная транскрипция.
4. Поля `Q1–Q5` не несут информации: во всех 1081 записях `Q1..Q4 = Yes`, `Q5 = No`, `consistent = 1`. При 1081 записи и выборке из 100 сегментов на язык для ручной валидации (§2.4 статьи) нулевая вариативность означает, что посегментные человеческие метки в файл не попали. Более того, запись №0 содержит `Q4 = Yes` («второй язык присутствует») при `Comments` = «there is no actual code-switching … monolingual Russian» — внутреннее противоречие.

Вывод для наших замеров: **текст CS-YODAS нельзя брать как эталон для WER без ручной проверки**. Его корректная роль — (а) источник спонтанной русской речи с англицизмами/IT-жаргоном, (б) черновик-гипотеза для сравнения моделей между собой (CER между моделями на одном и том же шуме), но не абсолютная метрика.

### 2.5 Геометрия и объём

- 954 уникальных videoId на 1081 запись (в среднем 1.13 реплики на видео).
- Три разные временные сущности, их легко перепутать:
  - `kaldi_uttid` — контекстное окно (тот самый wav из `rus.tar`): min 21.7 с, медиана 59.6 с, max 131.5 с. Таймкоды `context_text_list` отсчитываются **от начала этого wav** — проверено на всех 1081 записях: `kaldi_dur − max(ts)` = 0.00 с (разброс ±0.01 с).
  - `id` — окно ~30 с внутри контекста (min 6.58 / медиана 28.55 / max 29.99 с; сумма по 1081 = 8.42 ч). Это **не** длительность реплики: окно целиком содержит целевой сегмент во всех 1079 записях, где сегмент однозначно найден, но его границы шире границ реплики на медиану 5.9 с (p90 11.5 с, max 13.9 с).
  - целевой сегмент — элемент `context_text_list`, чей текст совпадает с `text`. Найден точным совпадением в 1079 из 1081 (в остальных двух `text` — подстрока, а не целый элемент). Его длительность: min 0.56 / медиана 2.87 / max 24.47 с, сумма по 1079 сегментам ≈ **1.03 ч**.
- Практический вывод: резать аудио надо по паре `(kaldi_uttid, элемент context_text_list с текстом = text)`, а не по `id`. Наивная интерпретация «8.42 ч кода-свитча» завышает объём в 8 раз: это длина окон `id`, а собственно реплики — ≈ 1 ч.
- `rus.tar` = 2 045 777 920 B при суммарной длине контекстных окон 17.77 ч. `[INFERENCE]` 17.77 ч × 3600 × 16 000 Гц × 2 байта = 2.047 ГБ — совпадение с размером архива до 0.05 %, то есть аудио почти наверняка **16 кГц, 16-бит PCM, моно**; вариант float32 дал бы 4.09 ГБ, вдвое больше. Формат не проверен (архив не скачивался), но иных разумных объяснений размера нет.

### 2.6 Лицензионный контур: CC BY-NC 4.0

CS-YODAS идёт по **CC-BY-NC-4.0** (README: «## License / CC-BY-NC-4.0»; `cs_yodas.py`: `_LICENSE = "CC-BY-NC-4.0"`). В HF API метаданных лицензии нет вовсе — ни `cardData.license`, ни тега `license:*`, поэтому автоматические лицензионные сканеры увидят репозиторий как «без лицензии».

Отдельно: arXiv-версия статьи (`arXiv:2606.11514v1`, 2026-06-09) несёт шапку `License: CC BY 4.0`, а §2.1 говорит «by implication, CS-YODAS is under the Creative Commons license» — то есть лицензия выведена из лицензии YODAS, а не установлена авторами явно. **Действуем по более строгой: NC.** Практические следствия:

- корпус годится для внутренних исследований и сравнения моделей;
- его нельзя включать в коммерческий продукт, в дообучение коммерческой модели и в перепубликацию;
- аудио — вырезки из YouTube, права на исходные видео принадлежат авторам каналов; NC-лицензия этого не отменяет `[INFERENCE]`;
- хранить локально можно, но при передаче наружу — только вместе с указанием NC и ссылкой на статью.

## 3. Лицензии и происхождение: три репозитория по одной строке

| Репозиторий | Заявлено | Что реально в API/карточке | Происхождение текста |
|---|---|---|---|
| `bond005/podlodka_speech` (`4a916148…`) | ничего не заявлено | `cardData.license = null`, тегов `license:*` нет, README — один frontmatter без прозы; поля: `audio`, `transcription`, `episode`, `title` | не описано; test и validation — один и тот же LFS-объект `53ebea8e77b6…` |
| `Vikhrmodels/ToneWebinars` (`a9bda19f…`) | `apache-2.0` | подтверждено: frontmatter + тег `license:apache-2.0`; `gated=false` | карточка: перепаковка `ZeroAgency/shkolkovo-bobr.video-webinars-audio` (`c35640ea…`, MIT), а upstream пишет дословно: «text transcription made with **whisper and VAD**» и «The copyright for course materials belongs to **Shkolkovo.online**» → текст машинный, права на контент у третьей стороны |
| `lab260/ToneWebinars_Balalaika` (`3a3eb3e2…`) | `apache-2.0` («same as original ToneWebinars») | подтверждено: frontmatter + тег; 248.9 ч после фильтрации из >2200 ч | карточка: «Revised transcriptions: **Crowd-sourced with multiple ASRs, fused via ROVER**» (T-one, GigaAMv3-rnnt/ctc/ctc-lm, vosk) + пунктуация RuPunct + ударения RuAccent + IPA; человеческой выверки не заявлено |

Расхождения, которые надо держать в голове:

- `ToneWebinars` переобъявляет MIT-исходник как Apache-2.0. `[INFERENCE]` Производный набор не может расширять права: лицензия на текст/аудио остаётся MIT + права Shkolkovo на материалы курсов.
- `Balalaika` называет ROVER-слияние ASR «crowd-sourced» — это не краудсорсинг людей, а ансамбль машин; в карточке рядом стоят ссылки на GigaAM v2 и v3, что тоже неточно.
- `podlodka_speech` без лицензии = юридически «все права сохранены»; в реестр его можно вносить только как «не использовать».

## 4. Команды (точные, как выполнялись)

Все команды запускались из корня репозитория; префикс `rtk` — требование окружения.

```bash
# метаданные и деревья
rtk curl -s "https://huggingface.co/api/datasets/byan/cs-yodas"
rtk curl -s "https://huggingface.co/api/datasets/google/fleurs?full=true"
rtk curl -s "https://huggingface.co/api/datasets/google/fleurs/tree/main/parquet-data/ru_ru"
rtk curl -s "https://huggingface.co/api/datasets/google/fleurs/tree/main/data/ru_ru"
rtk curl -s "https://huggingface.co/api/datasets/byan/cs-yodas/tree/main"
rtk curl -s "https://huggingface.co/api/datasets/bond005/podlodka_speech/tree/main/data"
rtk curl -s "https://huggingface.co/api/datasets/lab260/ToneWebinars_Balalaika/tree/main"
rtk curl -s "https://huggingface.co/api/datasets/ZeroAgency/shkolkovo-bobr.video-webinars-audio?full=true"
rtk curl -s "https://datasets-server.huggingface.co/size?dataset=google%2Ffleurs&config=ru_ru"

# скачивание (закреплено на ревизии)
rtk mkdir -p experiments/corpus/local/extra/fleurs experiments/corpus/local/extra/cs-yodas
REV=70bb2e84b976b7e960aa89f1c648e09c59f894dd
rtk curl -sSL --fail -o experiments/corpus/local/extra/fleurs/ru_ru-test.tsv \
  "https://huggingface.co/datasets/google/fleurs/resolve/$REV/data/ru_ru/test.tsv"
rtk curl -sSL --fail -o experiments/corpus/local/extra/fleurs/ru_ru-dev.tsv \
  "https://huggingface.co/datasets/google/fleurs/resolve/$REV/data/ru_ru/dev.tsv"
REV=e51028041b403f63c99ac91a4af040e72d0cad0e
rtk curl -sSL --fail -o experiments/corpus/local/extra/cs-yodas/README.md \
  "https://huggingface.co/datasets/byan/cs-yodas/resolve/$REV/README.md"
rtk curl -sSL --fail -o experiments/corpus/local/extra/cs-yodas/rus.jsonl \
  "https://huggingface.co/datasets/byan/cs-yodas/resolve/$REV/rus.jsonl"
rtk curl -sSL --fail -o experiments/corpus/local/extra/cs-yodas/cs_yodas.py \
  "https://huggingface.co/datasets/byan/cs-yodas/resolve/$REV/cs_yodas.py"

# подсчёты и суммы
rtk wc -l experiments/corpus/local/extra/fleurs/ru_ru-test.tsv
rtk shasum -a 256 experiments/corpus/local/extra/fleurs/ru_ru-test.tsv \
  experiments/corpus/local/extra/fleurs/ru_ru-dev.tsv \
  experiments/corpus/local/extra/cs-yodas/rus.jsonl \
  experiments/corpus/local/extra/cs-yodas/README.md \
  experiments/corpus/local/extra/cs-yodas/cs_yodas.py \
  > experiments/corpus/local/extra/SHA256SUMS.txt
# проверка: из каталога extra (пути в файле относительные)
cd experiments/corpus/local/extra && rtk shasum -a 256 -c SHA256SUMS.txt   # все 5 — OK

# статья CS-YODAS
rtk curl -sS --max-time 60 "https://arxiv.org/abs/2606.11514"
rtk curl -sS --max-time 60 "https://arxiv.org/html/2606.11514v1"
```

`https://lrec.elra.info/lrec2026-main-456` (ссылка из README CS-YODAS) дважды отдал таймаут на 271 КБ страницы; текст статьи взят с arXiv, DOI из README — `10.63317/3fir5kp98qyt`, страницы 5776+ в `2026.lrec-1.456` (по данным поисковой выдачи, PDF не скачивался).

## 5. Открытые вопросы

- **Аудио CS-YODAS не проверено.** `rus.tar` (1.9 ГиБ) не скачивался: внутри — контекстные окна `kaldi_uttid` (это следует из `wav_path`), но фактический формат/каналы/частота не подтверждены; размер архива указывает на 16 кГц/16-бит/моно (см. §2.5).
- **Нет сплитов и нет официального теста** у CS-YODAS: 1081 запись — один общий набор, разбиение придётся делать самим (например, по videoId, чтобы не течь речью между train/test — 954 видео на 1081 реплику).
- **Человеческие метки `Q1–Q5` непригодны** (нулевая вариативность, противоречие в записи №0). Нужна собственная проверка 30–50 реплик на слух, чтобы оценить долю реального код-свитча.
- **Лицензионный конфликт CS-YODAS** (NC в репозитории vs CC BY 4.0 в arXiv) стоит зафиксировать письменно до любого использования вне исследования.
- **FLEURS `ru_ru` без сегментных таймкодов**: 775 файлов целиком, средняя длина 11.6 с. Для WER нужна нормализация (числа, регистр, пунктуация) — согласовать с `experiments/asr_eval/terms.json`.
- **`podlodka_speech`** — лицензия отсутствует, test дублирует validation; к использованию не рекомендуется без ответа автора.
- **ToneWebinars** (755.8 ГБ, 286 787 записей) и **Balalaika** (248.9 ч, 13.9 ГБ архив) не скачивались; перед скачиванием нужно решить вопрос прав на контент Shkolkovo и подтвердить, что `text` в ToneWebinars — это whisper-выход без последующей правки.
