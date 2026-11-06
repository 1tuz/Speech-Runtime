# Speech Runtime

Speech Runtime — проект локального общего сервиса для речевых моделей на macOS. Независимые приложения смогут явно выбирать модель и операцию, используя общий каталог и, где возможно, загруженные веса. Логика приложений и голосовых ассистентов остаётся у клиентов.

**Статус:** исследование и подготовка экспериментов; runtime не реализован как продукт, но [прототип управляющего сервиса](runtime/README.md) проверен сквозными измерениями. Есть экспериментальный [ASR evaluation harness](experiments/asr_eval/README.md) и первое сравнение моделей на лицензированном наборе русских IT-встреч ([Veche-bench](experiments/pilot/ITERATION2.md), CC BY 4.0): 10 прогонов, WER/CER, терминные метрики, задержки стриминга, диаризация. Референсы там — сценарный текст, а не результат прослушивания, поэтому числа не объявлены бенчмарком; терминные и командные метрики по-прежнему требуют ручной калибровки.

**Что нового (2026-11-04, прототип runtime):** демон на Swift + воркер на Rust (wavo) прошли сквозной путь: батч 20.1 с — ASR 76 мс (RTF 0.004), стриминг при подаче в реальном времени — 133 мс от последнего голосового кадра до финала при ASR 99–123 мс, два стрима рядом с батчем дают 133 и 101 мс. Порог < 1 с выполнен. Загрузка модели 84–105 мс на ANE, демон 36 МБ RSS, воркер 46 МБ RSS. Найдены и исправлены четыре дефекта протокола и метрики, которые до этого делали замеры недействительными: [runtime/README](runtime/README.md).

**Что нового (2026-11-04, модели):** Parakeet Ultra проверен на двух движках (FluidAudio CoreML и wavo GGUF: WER 10.42 % и 10.87 % на `dev`), на M4 ANE быстрее GPU в 1.7 раза; свип скользящего окна 0.5–11 с показал, что первый текст равен `chunk + right`, а задержка < 1 с возможна только при чанке 0.5 с ценой WER 35 %; `hypothesisChunkSeconds` в FluidAudio 0.17.7 не используется. Генеративный LoRA-корректор гипотез отклонён (WER 10.42 → 14.24 %). Пик памяти: ANE 40–58 МБ, GPU 770 МБ, CoreML 102 МБ. Разборы: [стриминг](experiments/pilot/STREAMING.md), [адаптация](experiments/pilot/ADAPTATION.md), [решения](docs/decisions.md).

## Установка

Прототип ставится одним скриптом (Apple Silicon, Xcode 26+, Rust-инструментарий репозитория):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/1tuz/Speech-Runtime/main/install.sh)
```

Скрипт собирает `sr-worker` (Rust) и демон `speech-runtime` (Swift), упаковывает меню-приложение
`Speech Runtime.app` и кладёт CLI `sr` и `sr-model-prepare` в `~/.local/bin`. Веса моделей
(gigaam, parakeet-ultra в GGUF) загружаются в кэш Hugging Face отдельно, по мере выбора
конфигурации; для backend `ane` их нужно подготовить под Neural Engine — `sr-model-prepare`
или `wavo pull gigaam-v3 parakeet-ultra`.

Приложения подключаются к локальному сервису по HTTP: OpenAI-совместимый вход
`POST /v1/audio/transcriptions` для батч-транскрипции и полный API (`GET /v1/models`,
`POST /v1/streams` + `/audio` + `/end`, `DELETE /v1/streams`, `/v1/models/load`) для сессий
с последовательной подачей и финалами. Токен — `~/Library/Application Support/SpeechRuntime/token`,
порт по умолчанию 8477. Контракт и примеры запросов — [docs](docs/architecture.md) и
[runtime/README](runtime/README.md).

Проверка из терминала:

```bash
sr health
sr models
sr transcribe file.wav --model gigaam --priority batch
```

## Документация

- [Продукт и требования](docs/vision.md)
- [Архитектура и альтернативы](docs/architecture.md)
- [Итерации и критерии готовности](docs/roadmap.md)
- [Проверки, метрики и риски](docs/validation.md)
- [Решения и гипотезы](docs/decisions.md)
- [Инструкции агентам](AGENTS.md)
- [Исследования и их ограничения](research/README.md); [исторический итог](docs/report.md)
- [Эксперименты и раннеры](experiments/runners/README.md); [отчёты итерации 2](experiments/pilot/README.md)

Документы используют статусы **CONFIRMED** — проверенный факт с указанной областью, **PROPOSED** — рабочая гипотеза или требование, **EXPERIMENT_REQUIRED** — требуется проверка, **REJECTED** — решение явно отклонено в указанном контексте. Требование не означает реализованную возможность.
