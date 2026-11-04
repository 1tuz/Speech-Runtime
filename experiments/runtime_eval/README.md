# Runtime pipeline evaluation

Измеряет **полный** путь `audio → VAD/эндпоинтинг → GigaAM` и сравнивает его с
батч-базлайном без VAD на тех же аудио. Клиентская часть — только стандартная
библиотека Python 3; нормализация и WER/CER переиспользуются из
[`experiments/asr_eval`](../asr_eval) (`norm-v2`, `asr-eval-v2`), новый выравниватель
не пишется.

Демон этими скриптами **не запускается** и модели не загружаются: `run`/`sweep`
предполагают уже работающий демон (см. `runtime/README.md`) и обращаются к нему
только по HTTP. Дефолт — `http://127.0.0.1:8477`, токен читается из
`~/Library/Application Support/SpeechRuntime/token`; переопределяется
`SPEECH_RUNTIME_URL` / `SPEECH_RUNTIME_TOKEN` или флагами `--url` / `--token`.

## Состав

| Файл | Назначение |
|---|---|
| `runtime_client.py` | stdlib HTTP-клиент контракта + CLI (`health`, `models`, `load`, `unload`, `transcribe`, `stream`) |
| `pipeline_eval.py` | `make-audio` / `run` / `sweep` — сборка окна, прогон, скоринг, свип |
| `tests/test_pipeline_eval.py` | офлайн-тесты чистых частей: `python3 -m unittest`, без демона и моделей |

Все команды выполняются из `experiments/`:

```bash
python3 -m unittest runtime_eval.tests.test_pipeline_eval

# 1) окно из 12 подряд идущих dev-высказываний записи 2-one-to-one,
#    тишина 700 мс между ними, 200 мс в начале и 1200 мс в конце
python3 -m runtime_eval.pipeline_eval make-audio \
    --recording 2-one-to-one --start 0 --count 12 --gap-ms 700 \
    --out /tmp/window.wav
# вариант с расписанием пауз по стыкам (N-1 значений или одно для всех):
python3 -m runtime_eval.pipeline_eval make-audio \
    --recording 2-one-to-one --start 0 --count 4 --gaps 300,1200,300 \
    --out /tmp/window.wav

# 2) полный прогон через демон: стрим + два батч-базлайна, артефакт в JSON
python3 -m runtime_eval.runtime_client load gigaam
python3 -m runtime_eval.pipeline_eval run \
    --wav /tmp/window.wav --model gigaam --realtime 8 \
    --config min_silence_ms=700 \
    --json /tmp/run.json

# 3) свип по порогам: markdown-таблица + JSON
python3 -m runtime_eval.pipeline_eval sweep \
    --wav /tmp/window.wav --model gigaam --realtime 8 \
    --config min_silence_ms=500,700,900 --config enter_threshold=0.2,0.35 \
    --json /tmp/sweep.json --markdown /tmp/sweep.md

# разовый стрим одного файла с печатью финалов (как sr stream, но из Python)
python3 -m runtime_eval.runtime_client stream /tmp/window.wav \
    --model gigaam --realtime 8 --config min_silence_ms=700
```

Ориентир по времени: 60 с аудио при `--realtime 8` занимает ≈8 с на подачу
(плюс инференс; GigaAM v3 Q8_0 ANE ≈ 0.01 RTF).

## `runtime_client.py`

`RuntimeClient(base_url, token)` с методами `health()`, `models()`,
`load(model, backend=None)`, `unload(model)`, `open_stream(model, config) -> stream_id`,
`send_audio(stream_id, samples, sample_format='f32le')`, `end_stream(stream_id)`,
`events(stream_id, since, wait=0.0)`, `cancel_stream(stream_id)`,
`transcribe_wav(wav_bytes, model, language=None)`. Помощники: `read_wav`,
`encode_pcm`, `drain_finals`, `stream_file` (подача файла в темпе `realtime`×,
сбор финалов с клиентским `e2e_ms`), `parse_config` / `coerce_value`.

`POST /v1/streams/end` блокируется, пока воркер не подтвердит хвостовую
высказывание; `stream_file` после него дорабатывает события до `ended` и делает
`cancel_stream` для очистки сессии. Событие `error` превращается в исключение —
молчаливых потерь текста нет. Формат `final`-данных фиксирован контрактом:
`{utterance, text, asr_ms, queue_ms, audio_ms, start_ms, end_ms, last_voice_ms,
detected_ms}` (все `*_ms`, кроме текста, — миллисекунды; аудио-позиции —
миллисекунды аудиопотока, `asr_ms`/`queue_ms` — wall-clock). Клиент добавляет
`e2e_ms` — wall-время от момента подачи аудиопозиции `last_voice_ms` до прихода
финала.

## `pipeline_eval.py`

### `make-audio`

Склеивает N подряд идущих dev-высказываний одной записи
(`experiments/corpus/local/veche-prepared/simple`, порядок манифеста) с паузами
тишины: `--gap-ms` плоский или `--gaps a,b,c` по стыкам (ровно N−1 значений либо
одно; другой список — ошибка входа). `--lead-ms`/`--trail-ms` — тишина до/после.
Рядом с WAV пишет `<имя>.jsonl`: `{index, id, reference, start_ms, end_ms,
duration_ms, gap_before_ms}` — смещения в аудиопотоке, поэтому любой финал можно
сопоставить референсу по времени. Корпус — 16 кГц моно 16-bit; иное отвергается.

### `run`

1. Стримит WAV через демон при `--realtime` (типично 8), собирает `final`.
2. Склеивает тексты финалов по порядку и считает WER/CER склеенного текста против
   склеенных референсов (`norm-v2` + `asr-eval-v2`, корпусный уровень).
3. Те же аудио батчем (`POST /v1/audio/transcriptions`) — базлайн без VAD.
4. Каждая референсная высказывание отдельно батчем — верхняя граница, изолирующая
   вред от склейки/разрезания (VAD-урон = разница между 2 и 4).
5. Границы: число финалов против числа референсов + по-словное выравнивание
   (`align.align`) склеенного гипотезиса к склеенному референсу; удаления,
   попадающие в первое/последнее слово референса, и вставки у стыков считаются
   граничными. Примеры слов сохраняются в артефакт.

Каждый прогон пишет JSON-артефакт: точная команда (`argv`), конфиг, модель,
SHA-256 WAV, UTC-времена начала/конца, машина (`platform`), все измеренные числа
(латентность `asr_ms`/`queue_ms`/`e2e_ms` mean/p50, длительности финалов mean/p50,
счётчики ошибок, тексты). Числа не выдумываются: чего не измерили — `null`.

### `sweep`

`--config key=v1,v2` для любого числа ключей → декартово произведение; типы
значений выводятся (`700` → int, `0.5` → float, `ru` → str). Каждый узел — полный
прогон `run`; батч-базлайны измеряются один раз и выносятся в `baseline` /
`baseline_per_utterance` (они — свойство аудио, а не конфига). Выход: JSON +
markdown-таблица (`config | WER | CER | finals | refs | utt mean/p50 | asr | queue |
e2e | boundary del/ins`) и базлайны под таблицей.

## Что измеряется, а что нет

Измеряется: сквозное качество (WER/CER склеенного текста), урон границ от
эндпоинтинга, латентность по стадиям (`queue_ms` — очередь, `asr_ms` — инференс,
`e2e_ms` — от последнего голосового кадра до финала на стороне клиента),
чувствительность к `min_silence_ms` / порогам.

Ограничения:

- Скоринг устроен как «один длинный текст против одного длинного текста»:
  разбиение финалов не обязано совпадать с референсными высказываниями, поэтому
  WER не делится на пер-высказочные, а диагностика границ — по-словная.
- Вставки атрибутируются стыку только по соседству с границей референса в
  выравнивании; без таймстемпов часть вставок останется «неатрибутированными»
  (см. `insertions_total`).
- Корпус veche — непроверенные транскрипты неизвестной лицензии
  (см. [`../asr_eval/README.md`](../asr_eval/README.md)): это девелоперский
  полигон для механики и латентности, не подтверждённый бенчмарк качества.
- Паузы между высказываниями синтетические (чистая тишина); реальные совпадения
  и перекрытия дикторов модель не видит.
- `e2e_ms` включает клиентскую подачу и опрос событий; при `--realtime 8`
  доставка быстрее реального времени, но это всё ещё не продовая метрика
  продукта (ср. `runtime/README.md`).
- Два батч-базлайна требуют загрузленной модели; `--no-baselines` пропускает их.
