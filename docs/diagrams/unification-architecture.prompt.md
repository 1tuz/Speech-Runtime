# Speech Runtime · unification-architecture — контракт перерисовки

Диаграмма: `docs/diagrams/unification-architecture.html` (self-contained HTML + inline SVG, kami-токены).
PNG-экспорт отсутствует: по условиям задачи HTML с inline SVG достаточно, отдельный `.svg`/`.png` не требуется.

## Must preserve

- Три полосы: приложения слева, контракт + демон + воркер в центре, модели справа. Чтение слева направо.
- Две пунктирные границы адаптеров с mono-подписями КЛИЕНТСКИЕ АДАПТЕРЫ / ВЫЧИСЛИТЕЛЬНЫЕ АДАПТЕРЫ.
- Фокусные узлы (ink-blue stroke + tint): «Единый вход речевых задач» (контракт) и «Демон Swift» (роутер). Максимум два фокальных.
- Три клиентских узла: Диктовка, Встречи, Обработка записей. Два модельных узла: gigaam, parakeet-ultra.
- Воркер «Rust + wavo»: очереди interactive/batch, earshot VAD, batch-splitting.
- Ключевые рёбра: apps → contract (OPENAI: `POST /v1/audio/transcriptions`; STREAMS: `/v1/streams` + `/audio` + `/end`, `DELETE`); router → worker (ЗАДАЧИ, ink-blue основной путь); worker → router (ФИНАЛЫ, финалы после эндпоинтера, interim_results: false); worker → gigaam (загрузка подключаемой модели).
- Список возможностей контракта в узле: models · capabilities, batch · sessions · end, finals · cancel.
- Подпись-критерий внизу SVG: новая модель подключается адаптером к воркеру, новое приложение адаптером к контракту.

## Suggested additions

- Честные capabilities из `GET /v1/models` (batch_transcription, streaming_sessions, interim_results: false, word_timestamps, diarization: false) — теперь отдаются API; можно показать сокращённо в узле контракта.
- `sr-model-prepare` как шаг подготовки GGUF под ANE рядом с модельными узлами (за границей вычислительных адаптеров).
- Idle-unload и надзор воркера уже упомянуты в узле демона; можно вынести отдельной подписью при росте деталей.

## Visual direction

- Держать плотность 4/10: не добавлять узлов сверх текущих девяти; новые факты уходят в подписи или sister-диаграммы.
- Фокальные узлы — только контракт и демон; воркер и модели остаются нейтральными (near-black stroke, ivory fill).
- Ортогональные линии, без пересечения текста; метки рёбер на подложках пергамента.
- Токены: пергамент #f5f4ed, ink-blue #1B365D, tint #EEF2F7, near-black #141413, olive #504e49, stone #6b6a64. Без градиентов, теней, скриптов, внешних ресурсов.
- Русские подписи; mono-якоря заглавными (LEGEND, WORKER, МОДЕЛЬ, ПРИЛОЖЕНИЕ).

## Sister boundaries

- `docs/diagrams/model-session-lifecycle.html` — жизненный цикл сессии и события `/v1/streams/events`; протокольные тайминги не входят в эту диаграмму.
- `docs/diagrams/runtime-architecture.html` — внутренности демона и воркера; детали памяти и супервизии остаются там.
- `docs/diagrams/system-context.html`, `docs/diagrams/architecture.html`, `docs/diagrams/plan.html` — системный контекст и план; границы продукта не дублировать здесь.
