# Пилот на 12 фрагментах русской IT-речи

Калибровочный набор для проверки стенда и чтения гипотез глазами. **Референсов нет**: это не бенчмарк, WER по нему не считается. Аудио и результаты лежат в `experiments/corpus/local/kubernetes-dev/` и исключены из Git (`experiments/corpus/local/.gitignore` содержит `*`).

## Материал

Два доклада с русской IT-конференции, по 6 фрагментов длиной ровно 20.0 с, шаг 300 с:

| Префикс | Источник | Название |
|---|---|---|
| `avito-*` | https://www.youtube.com/watch?v=Zbe8k0cQpYo | «Настройка kubernetes: tips and tricks», Михаил Прокопчук (Avito) |
| `deckhouse-*` | https://www.youtube.com/watch?v=HheMaSV_5T0 | «Как устроена разработка Kubernetes платформы Deckhouse», HighLoad Channel |

Смещения: 300, 600, 900, 1200, 1500, 1800 с от начала записи. Формат всех файлов проверен `ffprobe`: `pcm_s16le`, 16 кГц, mono, 20.0 с, 640 078 байт.

## Воспроизведение нарезки

```bash
mkdir -p experiments/corpus/local/kubernetes-dev/audio
for start in 300 600 900 1200 1500 1800; do
  ffmpeg -nostdin -v error -ss "$start" -t 20 \
    -i "/path/to/Zbe8k0cQpYo.webm" \
    -ac 1 -ar 16000 -c:a pcm_s16le \
    "experiments/corpus/local/kubernetes-dev/audio/avito-$start.wav"
  ffmpeg -nostdin -v error -ss "$start" -t 20 \
    -i "/path/to/HheMaSV_5T0.webm" \
    -ac 1 -ar 16000 -c:a pcm_s16le \
    "experiments/corpus/local/kubernetes-dev/audio/deckhouse-$start.wav"
done
```

Хэши `audio_sha256` каждого фрагмента записаны в `gigaam-run.json`, `whisper-run.json`, `nemotron-run.json` и в `annotation-template.jsonl`; при пересборке PCM должен совпасть.

Проверено на `avito-300`: нарезка той же командой из локального `kubernetes-Zbe8k0cQpYo.webm` даёт тот же звук (смещение 0, средняя разница 0 на выборке), но sha256 файла отличается. Заголовок WAV идентичен, различаются низкие байты четырёх последних сэмплов (разница в 1 LSB, около 0.25 мс на конце). Поэтому сравнивать надо PCM, а не хэш контейнера; записанные `audio_sha256` относятся к исходным файлам. Нарезка с `-ss` после `-i` даёт другой файл (`b3ddabb6…`).

## Права

Обе записи — публичные доклады на YouTube под стандартной лицензией YouTube. Права на переиспользование **не подтверждены**; материал используется как локальная калибровка и в Git не попадает. Для бенчмарка нужен источник с подтверждёнными правами — см. [`research/wave3/R1-ru-corpora.md`](../../research/wave3/R1-ru-corpora.md).

## Прогоны

| Система | Артефакт | Движок | Гипотезы | Метаданные |
|---|---|---|---|---|
| GigaAM v3 `e2e_rnnt` | пакет `gigaam` 0.1.0, модель `v3_e2e_rnnt` (ONNX; источник весов в метаданных не записан, есть `model_sha256`) | onnxruntime, CPU, 4 потока | `gigaam-hypotheses.tsv` | `gigaam-run.json`, `gigaam-pilot.json` |
| Parakeet TDT 0.6b v3 | FluidAudio `parakeet-tdt-0.6b-v3` | CoreML | `parakeet-hypotheses.tsv` | `parakeet-run.json`, `parakeet-probe.swift` |
| Whisper large-v3-turbo | `ggml-large-v3-turbo.bin` | transcribe.cpp 0.4.1, CPU, 4 потока | `whisper-hypotheses.tsv` | `whisper-run.json`, `whisper-batch.jsonl` |
| Nemotron 3.5 multilingual 0.6b | `FluidInference/…-CoreML`, `multilingual/2240ms` | FluidAudio v0.17.7, ANE | `nemotron-hypotheses.tsv` | `nemotron-run.json`, `nemotron-run.md` |

Сводка по времени — в [`docs/validation.md`](../../docs/validation.md#пилот-на-12-фрагментах-что-уже-измерено). Прогоны делались в разное время и на разных движках: сравнивать RTF между строками можно только как порядок величины.

## Триаж

`pilot-side-by-side.tsv` — три системы рядом по каждому сегменту. Для четырёх систем и очереди ручной проверки:

```bash
python3 experiments/pilot/review.py \
  --systems gigaam=experiments/corpus/local/kubernetes-dev/gigaam-hypotheses.tsv \
  --systems parakeet=experiments/corpus/local/kubernetes-dev/parakeet-hypotheses.tsv \
  --systems whisper=experiments/corpus/local/kubernetes-dev/whisper-hypotheses.tsv \
  --systems nemotron=experiments/corpus/local/kubernetes-dev/nemotron-hypotheses.tsv \
  --out-dir experiments/corpus/local/kubernetes-dev/pilot-review-4
```

Наблюдённый результат: 96 сайтов расхождений, из них 24 — у всех четырёх систем; попарный WER 0.14–0.19. Это расстояние между системами, а не точность.

## Чего здесь нет

- Человеческого эталона и разметки терминов/команд.
- Потокового прогона с микрофона: Nemotron получал один 20-секундный буфер, поэтому его задержка первого партиала (0.032–0.119 с) не равна задержке реального стриминга.
- Спикеров, шума и регистров: два диктора-лектора, чистый звук записи конференции.
- Независимости от домена: оба доклада — Kubernetes-инфраструктура.
