# ASR-LoRA: минимальный воспроизводимый сбой

Дата: 2026-10-29. Окружение: `experiments/.venv` (Python 3.12.13), Apple M5 Pro,
macOS 27.0.

## Что пробовали

Обучение LoRA декодера `Qwen3-ASR-0.6B` (MLX 8-bit) на 24 реальных парах
аудио-текст из dev Veche-bench, `mlx-tune` 0.6.0, 20 шагов, r=8,
targets `q_proj/k_proj/v_proj/o_proj`, языковой префикс `ru`.

## Сбой 1 (блокирующий)

```
File "mlx_audio/stt/models/qwen3_asr/qwen3_asr.py", line 354, in __call__
    feature_lens_np = np.array(feature_lens)
ValueError: [eval] Attempting to eval an array during function transformations
like compile or vmap is not allowed.
```

Версии: `mlx-tune==0.6.0`, `mlx-audio==0.6.3`, `mlx==0.32.4`.

Причина: `Qwen3ASRAudioTower.__call__` переводит `mx.array` (длины признаков) в
`numpy` и дальше строит по ним управляющий поток (нарезку на чанки
`self.n_window * 2`). Во время `mx.grad` значения — трассировочные, конвертация
запрещена. Это не настройка и не флаг: без переписывания функции на полностью
`mx`-операции обучение через `mlx-tune` невозможно.

Минимальный повтор (без данных и моделей):

```python
import mlx.core as mx, numpy as np
from mlx_audio.stt.models.qwen3_asr.qwen3_asr import Qwen3ASRAudioTower  # нужен конфиг
f = lambda m: mx.sum(m + Qwen3ASRAudioTower(...)(mx.zeros((1, 128, 100))))
mx.grad(f)(mx.zeros(()))
```

## Сбой 2 (в эмбеддингах, обойдён)

```
File "mlx_embeddings/models/qwen3.py", line 30, in last_token_pool
    if left_padding:
ValueError: [eval] Attempting to run an array during function transformations ...
```

Тот же класс проблемы: `if` по `mx.array`. Обойдён локальным патчем
`experiments/runners/patches/last_token_pool.py.txt` (эквивалентная запись через
`mx.where`). После патча эмбеддинг-LoRA обучается и воспроизводится после
перезагрузки адаптера.

## Решение

По условию задачи при выявленной несовместимости не писать собственный trainer:
ветка ASR-LoRA остановлена, зафиксирован сбой и версии. Доступные без неё ветки
(базовые модели, словарь, контекст, эмбеддинги) доведены до чисел.

Отдельно проверено, что адаптер другой реализации (`AleksanderObuchowski/…-lora-decoder-only`
и подобные) нельзя считать переносимым: формат зависит от реализации, и без
обучения в этой среде переносимость не проверяется.
