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

`--clients` параллельных стримов; с `--batch` параллельно крутится цикл
батч-транскрипций того же WAV. Отчёт: интерактивные p50/p95 по стадиям,
wall time батча, проверка голодания (interactive p95 `asr_ms` против батч
p50 `asr_ms`), возврат `health.streams` к базовому уровню.

```bash
python3 -m runtime_eval.soak multiclient --wav /tmp/window.wav \
    --clients 4 --speed 1 --batch --json /tmp/soak-multi.json
```

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
- `no_starvation` не объявляется при пустых измерениях (нет данных — нет
  голодания).
- `crash` убивает воркер-процесс демона, к которому подключён сценарий;
  запускайте его на изолированном демоне (`--spawn`), не против shared-инстанса.

## Что делает интегратор

```bash
cd experiments/
python3 -m py_compile runtime_eval/soak.py
python3 -m unittest runtime_eval.tests.test_soak        # 29 тестов, без демона

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
