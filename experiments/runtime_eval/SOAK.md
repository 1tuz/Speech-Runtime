# Soak and failure-scenario harness

Длительные и аварийные сценарии против **уже работающего** демона: один
долгий стрим с выборкой памяти, параллельные клиенты с батчем, `kill -9`
воркера, отмена стрима, две модели одновременно, деградация памяти.
Только стандартная библиотека Python 3; демон этими командами **не
запускается** (кроме явного `--spawn` на нестандартном порту, см. ниже).

Файлы:

| Файл | Назначение |
|---|---|
| `soak.py` | сценарии + чистые помощники (перцентили, разбор RSS, нарезка WAV) |
| `tests/test_soak.py` | офлайн-тесты чистых частей, без демона и моделей |
| `SOAK.md` | этот файл |

Все команды выполняются из `experiments/`:

```bash
python3 -m py_compile runtime_eval/soak.py
python3 -m unittest runtime_eval.tests.test_soak
```

## Подключение

Демон должен быть запущен заранее (см. `runtime/README.md`). По умолчанию
`http://127.0.0.1:8477`, токен — `~/Library/Application Support/SpeechRuntime/token`;
переопределяется `SPEECH_RUNTIME_URL` / `SPEECH_RUNTIME_TOKEN` или флагами
`--url` / `--token-file`. Модель по умолчанию `gigaam` (`--model`).

## Сценарии

WAV — 16 kHz mono PCM. `--speed N` подаёт аудио в N× темпе реального времени.

### `long-session`

Один стрим длительностью `--minutes N` (WAV зацикливается до нужной длины),
подача со скоростью `--speed`. На каждый финал собираются стадии задержки
(`asr_ms`, `queue_ms`, `e2e_ms`), каждые 30 с — выборка RSS и
`phys_footprint` демона и воркера.

```bash
python3 -m runtime_eval.soak long-session --wav /tmp/window.wav \
    --minutes 10 --speed 1 --json /tmp/soak-long.json
```

Проверяется: модель загружена, стрим завершился `ended`, финалы есть, ошибок
нет, рост RSS каждого из процессов в пределах `--rss-growth-limit-mib` (по умолчанию 512 МиБ).
При менее чем двух измерениях RSS соответствующая проверка не проходит. Порог 512 МиБ
не доказывает отсутствие утечки; артефакт содержит выборки для анализа динамики.
При `--speed 16` длительность аудио не равна реальному времени работы.

### `multiclient`

`--clients` параллельных стримов; с `--batch` сценарий работает в две фазы по
**одному и тому же** аудио и **одному и тому же** числу клиентов:

1. **baseline-фаза** — те же интерактивные стримы без батча;
2. **фаза измерения** — те же стримы снова, параллельно крутится цикл
   батч-транскрипций того же WAV.

Отчёт: интерактивные p50/p95 по стадиям (`asr_ms`, `queue_ms`, `e2e_ms`) для
обеих фаз, wall time батча, `end_reason_counts`.

Проверки:

- `finals_complete` — каждый интерактивный клиент завершился без ошибки и дал
  **не меньше** финалов, чем в baseline-фазе; дефицит — FAIL с числами
  («client 1: finals 1 < baseline 2»).
- `no_starvation` — p95 стадий `queue_ms` и `e2e_ms` фазы измерения против
  **тех же стадий** baseline-фазы. `asr_ms` не используется: 60 с ожидания в
  очереди при быстром ASR — это ровно то голодание, которое старый критерий
  (interactive p95 `asr_ms` против батч p50 `asr_ms`) пропускал.

Допуски `no_starvation` (на каждую стадию отдельно): провал, если
`p95 > max(5 × baseline_p95, baseline_p95 + 2000 мс)`. Фактор 5 и абсолютный
запас 2000 мс сохранены от прежнего критерия: p95 шумный на малых выборках, а
2000 мс покрывает разовую фоновую нагрузку. Обе стадии должны иметь измеренный
p95; иначе статус UNMEASURED, но никогда PASS.

```bash
python3 -m runtime_eval.soak multiclient --wav /tmp/window.wav \
    --clients 4 --speed 1 --batch --json /tmp/soak-multi.json
```

#### Статусы проверок

Каждая проверка в `assertions` несёт `status`:

| Статус | Смысл |
|---|---|
| `PASS` | проверка прошла |
| `FAIL` | проверка провалилась |
| `UNMEASURED` | данных нет (нет baseline, пустые стадии, мало семплов) — это **не** PASS |
| `BLOCKED` | предусловие не выполнено, проверка не могла запуститься |

Поле `pass` сохранено (UNMEASURED/BLOCKED пишут `false`), так что старые
скрипты, читающие только `pass`, продолжают работать. Код выхода 0 — только
если все проверки PASS. Отсутствие измерения не даёт PASS: нет данных — нет
«пройдено».

#### `end_reason_counts`

Для каждого финала, если событие несёт `end_reason` (`silence`,
`max_utterance`, `client_end`), финал считается по соответствующей причине;
финалы без поля или с неизвестным значением считаются как `unknown` (не
угадываются). Старые прогоны без `end_reason` дают `unknown` для всех финалов
и **не** проваливают сценарий.

### `crash`

`kill -9` воркера посреди стрима. Проверяется: демон жив (`/health` отвечает),
клиент получил явную ошибку/`ended` (не молчаливую потерю), `health.worker.restarts
>= 1` (до/после), модель перезагружена и новый стрим работает.

```bash
python3 -m runtime_eval.soak crash --wav /tmp/window.wav \
    --worker-pid $(python3 -m runtime_eval.runtime_client health | ...) --speed 1
```

`--worker-pid` по умолчанию берётся из `health().worker.pid`, флаг нужен только
если надо убить конкретный процесс.

### `cancel`

Отмена стрима в середине подачи. Проверяется: после `cancel_stream` не
пришло ни одного `final`, `health.streams` вернулся к базовому уровню.

```bash
python3 -m runtime_eval.soak cancel --wav /tmp/window.wav
```

### `two-models`

Загружает `gigaam` и `parakeet-ultra` (имена из `runtime/models.json`),
стримит оба параллельно. Отчёт: per-model `asr_ms` p50/p95, число финалов,
список упавших. Обе модели выгружаются в конце.

```bash
python3 -m runtime_eval.soak two-models --wav /tmp/window.wav
```

### `slow-client`

Клиент подаёт весь поток, но **не опрашивает события** до конца аудио: проверяется, что
демон не блокируется и не теряет события молча. Во время подачи харнесс трогает только
`/health` (второе соединение) и меряет его задержку; события сливаются потом с курсора 0.

Проверки: `health_responsive` (p95 `/health` < 500 мс при непрочитанном буфере),
`session_visible` (`session_buffers ≥ 1` виден из `/health`), `no_error_events`,
`no_silent_loss` (число `final` равно числу событий `utterance`), `stream_ended`,
`streams_back_to_baseline`. Бюджет непрочитанных событий — явный: cap буфера 20 000
событий и TTL 300 с после `ended`; при вытеснении растёт `droppedCount`, а не тишина.

```bash
python3 -m runtime_eval.soak slow-client --wav /tmp/window.wav --speed 4
```

### `memory`

RSS/`phys_footprint` в точках baseline → loaded → N стримов (`--clients`,
по умолчанию 2) → все завершились → unloaded. Проверяется: `health.streams`
не растёт после завершения стримов, выгрузка проходит, RSS воркера
вернулся в пределы лимита.

```bash
python3 -m runtime_eval.soak memory --wav /tmp/window.wav --clients 2
```

## `--spawn`: изолированный демон на нестандартном порту

Любой сценарий может сам поднять отдельный демон, но только с явными
`--spawn BINARY`, `--url` с **нестандартным** портом (не 8477), `--token-file`
и `--config`. Процесс терминируется (затем убивается по таймауту) в конце
сценария. Попытка задать дефолтный порт — ошибка argparse ещё до старта.

```bash
python3 -m runtime_eval.soak crash --wav /tmp/window.wav \
    --url http://127.0.0.1:8478 \
    --spawn ../runtime/daemon/.build/debug/speech-runtime \
    --config ../runtime/models.json \
    --token-file /tmp/soak-token
```

## Артефакты

Каждый прогон пишет JSON (по умолчанию `/tmp/soak-<scenario>.json`, путь —
`--json`): `argv`, время начала/конца, машина, результаты сценария,
перцентили и список `assertions` (имя / `pass` / `detail`). Код выхода 0 —
только если все проверки прошли.

## Ограничения по измерениям

- **ANE-память.** Учёт памяти ANE (включая `neural_peak` и разделяемые
  буферы ANE-компилятора) **не измеряется**: `phys_footprint` не покрывает
  резервы ANE, а инструменты вроде `powermetrics` требуют `sudo`, которого
  здесь нет и не будет. Всё, что за пределами `phys_footprint`, за рамками
  этих сценариев.
- RSS берётся через `ps -o rss=`, `phys_footprint` — через `footprint -p PID`
  (если инструмент доступен); при отсутствии данных поле `null`, и проверки
  роста сознательно не падают (нет данных — нет основания для провала).
- `no_starvation` при отсутствии baseline-фазы или измеренных p95 `queue_ms`/
  `e2e_ms` получает статус UNMEASURED (нет данных — нет «пройдено»), но
  никогда PASS. `asr_ms` критерием голодания не является.
- `crash` убивает воркер-процесс демона, к которому подключён сценарий;
  запускайте его на изолированном демоне (`--spawn`), не против shared-инстанса.

## Что делает интегратор

```bash
cd experiments/
python3 -m py_compile runtime_eval/soak.py
python3 -m unittest runtime_eval.tests.test_soak        # 53 теста, без демона

# против работающего демона (по одному, не параллельно):
python3 -m runtime_eval.soak cancel     --wav /tmp/window.wav
python3 -m runtime_eval.soak two-models --wav /tmp/window.wav
python3 -m runtime_eval.soak memory     --wav /tmp/window.wav --clients 2
python3 -m runtime_eval.soak multiclient --wav /tmp/window.wav --clients 4 --batch
python3 -m runtime_eval.soak long-session --wav /tmp/window.wav --minutes 10
python3 -m runtime_eval.soak crash     --wav /tmp/window.wav    # воркер будет перезапущен
```

Каждая команда пишет артефакт в `/tmp/soak-<scenario>.json` и возвращает 0
только при полном прохождении проверок.
