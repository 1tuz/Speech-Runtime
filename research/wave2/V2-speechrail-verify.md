# V2 — Проверка архитектурных преимуществ SpeechRail

Клон: `github.com/hrygo/SpeechRail` @ `cdccc74084bc94f8c996b5b71a386455640ad1cb` (`cdccc74`, 2026-10-25).
Проверяемые отчёты: `wave1/C-speechrail.md`, `wave1/G-model-management.md`, `wave1/H-api-contract.md`;
расхождение с `wave1/K-licenses-maintenance.md` по истории git.
Дата проверки: 2026-10-26. Метод: чтение кода клона + команды `git`; ничего не запускалось (сервис требует macOS 26 + Python 3.14 + MLX-веса, в песочнице недоступно).
`[INFERENCE]` — мои выводы, не из отчётов первой волны.

Базовый URL всех permalink'ов ниже:
`https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/`

---

## Итог

1. **Расхождение C vs K снято: оба правы, но в разных метриках.** Всего коммитов — **1483**
   (`git rev-list --count HEAD`), за 30 дней — **524** (`--since=30.days` — совпадает с K до единицы).
   «3 автора» у K — это три *разных email* в поле author: `aaronwong1989@gmail.com` (1082),
   `aaronwong1989@live.cn` (388), `dependabot[bot]` (13). Два первых email — один человек
   (в `Co-authored-by` он же фигурирует как `黄飞虹` и `Aaron Wong`). **Bus factor = 1 человек + боты**;
   утверждение C «один автор» верно по сути, утверждение K «3 автора» верно буквально, но вводит в
   заблуждение. C ошибается в другом: тега `v0.1.0` нет, первый тег — `v1.2.0` (2026-09-02).

2. **Манифест артефактов — подтверждён целиком** и это действительно лучшая часть проекта:
   `ArtifactFile`, `SourceLocation`, `QuantizationSpec`, `ModelArtifact`, `RuntimeLock`,
   `ModelCatalog{schema_version=2}` с жёсткими валидаторами (`extra="forbid"`, `frozen=True`).

3. **staging→verify→atomic publish — подтверждён** (скачивание в `models/.staging/<op>/<key>` →
   пофайловая сверка `size`+`sha256` при записи → `Path.replace()` в `models/<key>` →
   атомарная запись реестра через `mkstemp`+`fsync`+`rename`). «Rollback-журнал» из C — **неточность**:
   это **один транзакционный журнал текущего перехода** (`state/profile-transaction.json`), а не история
   откатов; плюс `config/selection.previous.json`. Второй, отдельный механизм откатов —
   `_rollback_publication` внутри `_prepare_models_impl` (память, не персистентный журнал).

4. **Трёхступенчатое вытеснение — подтверждено, цифры C точны:** `min_uptime=60 s`,
   `warm_standby=60 s`, `idle(cold)=300 s`; отдельный TTL для design-лейна (тоже 60/300).
   **Но:** «trim памяти» на деле — fire-and-forget кадр `trim_memory`, который в воркере делает только
   `_clear_metal_cache()`; веса не выгружаются, подтверждения нет, исключения глушатся.
   `WARM_STANDBY` — состояние без доказательства освобождения памяти.

5. **ResourceGovernor — подтверждён:** `total_capacity=4`, `realtime_reserved_capacity=1`,
   `max_pending_per_class=8`, `batch_aging_seconds=30`; бюджет `max(4 GiB, RAM/2)` при минимуме 8 GiB
   физической памяти. Два независимых механизма: слотовый (governor) и байтовый (`model_budget`,
   по *объявленным* `ComponentFootprint`). Инвариант `1 ≤ reserved < total` проверяется.

6. **batch XOR streaming (`backend_busy`) — подтверждено, но формулировка C неточна:**
   `AsrModeGate` даёт `AsrModeBusy` с `busy_reason = asr_mode_conflict`; `backend_busy` — общий
   HTTP-код (503/429) для шести разных причин из `BusyReason`. Смешивать их нельзя.

7. **«Выбора модели в запросе нет» — подтверждено и сильнее, чем в C.** Поле `model=` только
   валидируется; алиасы захардкожены в `_ASR_MODEL_ALIASES` → литерал `speechrail/qwen3-asr-1.7b`
   и **не сверяются** с активным артефактом. Т.е. `model="whisper-1"` принимается даже когда активен
   артефакт 0.6B. Поле декоративно; выбор модели — только офлайн-транзакция `profile apply`.

8. **Partial-контракт `revision` + `stable_prefix_codepoints` — подтверждён**, включая семантику
   «replace, never concatenate», монотонную ревизию и отдельный `delta` только для доказуемого префикса.
   `sample_span` — в 24 кГц wire-сэмплах (не в сэмплах ядра 16 кГц) — это надо переносить вместе с
   единицей измерения.

9. **Воркеры-подпроцессы и IPC — подтверждено:** length-prefixed `>I`/`>II` фреймы, JSON-заголовок +
   опциональный raw binary, `MAX_FRAME_BYTES = 128 MiB`, `PROTOCOL_VERSION = 1`, **раздельные**
   read/write-локи, кольцевой stderr-буфер 16 KiB, `_TERMINATE_GRACE_SECONDS = 2.0`, allowlist-окружение
   с `HF_HUB_OFFLINE=1`. Swift-диаризатор — подтверждён: отдельный CoreML-бинарь с 8-байтовым префиксом
   (`headerSize:UInt32 BE` + `payloadSize:UInt32 BE`), по процессу на сессию. **Уточнение:** в
   `MODEL_FILE_SHA256` **10** файлов, не 11, как в C.

10. **Отчёт G в ключевом пункте ошибается:** «загрузчика моделей там нет — артефакты готовит оператор»
    — **неверно**. Есть полноценный загрузчик: `ModelScopeDownloader` инжектится в CLI
    (`speechrail model prepare`), качает стримом, сверяет хэши, публикует атомарно, ведёт реестр
    подготовок. Правильная формулировка: загрузчик есть, но он **single-selection** (готовит ровно один
    набор `(asr_spec, tts_spec)`), без удаления/GC и без понятия «модель × ревизия».

---

## Проверенные утверждения

| # | Утверждение | Источник | Вердикт | Доказательство |
|---|---|---|---|---|
| 1 | 1483 коммита, один автор + 13 dependabot | C §9 | ПОДТВЕРЖДЕНО | `git rev-list --count HEAD` → 1483; `git log --format=%ae \| sort \| uniq -c` → 1082 `aaronwong1989@gmail.com`, 388 `…@live.cn`, 13 `dependabot[bot]` |
| 2 | 524 коммита / 3 автора | K §6, §таблица | ПОДТВЕРЖДЕНО (метрика 30 дней) | `git rev-list --count --since=30.days HEAD` → **524**; 3 = 2 email одного человека + бот |
| 3 | Bus factor 1 (боты и co-author не в счёт) | C §9 / K §6 (противоречие) | ПОДТВЕРЖДЕНО для C | 98 `Co-authored-by`-трейлеров: 85× `黄飞虹 <…@gmail.com>`, 5× dependabot, 4× `…@live.cn`, 2× `Aaron Wong <…@gmail.com>`, 1× `github-actions[bot]`, 1× `the model 4.8 (1M context)` — все не-боты = тот же человек |
| 4 | 73 тега, v5.0.0 (2026-10-24), первый тег v0.1.0 (2026-08-31) | C §9 | ЧАСТИЧНО | `git tag \| wc -l` → 73 ✔; v5.0.0 2026-10-24 ✔; **v0.1.0 не существует**, первый `v1.2.0` 2026-09-02 (`git for-each-ref --sort=creatordate refs/tags \| head -3`) |
| 5 | 312 тестовых файлов / ~115 316 строк против 71 096 в `src/` | C §9 | ПОДТВЕРЖДЕНО | `git ls-files 'tests/**/*.py' \| wc -l` → 312; `xargs wc -l` → 115316; `src/**/*.py` → 71096 |
| 6 | `ArtifactFile{path,size,sha256}` с валидацией | C §3, G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L137-L155](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L137-L155) |
| 7 | `QuantizationSpec`: `bits` и `dtype` взаимоисключающие; unquantized обязан объявить `dtype` | G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L157-L183](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L157-L183) |
| 8 | `SourceLocation{provider,repository,revision(40 hex)}` | C §3, G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L185-L198](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L185-L198) |
| 9 | `ModelArtifact` требует `config.json`+`model.safetensors`+токенизатор, TTS — codec, и совпадения одной `sources.revision` с `revision` | G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L200-L263](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L200-L263) |
| 10 | `RuntimeLock`: python 3.14.x, `package==version --hash=sha256:…`, `file_hashes`, пин wheel движка | G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L311-L349](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L311-L349) |
| 11 | `ModelCatalog{schema_version=2, artifacts[], specs[]}` + запрет дублей и полнота матрицы биндингов | G §9 | ПОДТВЕРЖДЕНО | [model_catalog.py#L371-L410](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L371-L410) |
| 12 | Каталог = 12 артефактов, `schema_version: 2`, `provider: modelscope` | C §3 | ПОДТВЕРЖДЕНО | `assets/model-catalog.json` (python: `len(d['artifacts'])` → 12, `d['schema_version']` → 2) |
| 13 | Скачивание в `.staging` → проверка хэшей → атомарная публикация | C §5 | ПОДТВЕРЖДЕНО | [model_store.py#L1663-L1666](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1663-L1666), [L1740-L1755](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1740-L1755) |
| 14 | Пофайловая сверка `size`+`sha256` во время записи | C §5 | ПОДТВЕРЖДЕНО | [model_store.py#L1388-L1461](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1388-L1461) |
| 15 | Атомарная запись реестра: `mkstemp`+`fchmod 0600`+`fsync`+`replace`+dir-`fsync` | — (моя проверка) | ПОДТВЕРЖДЕНО | [model_store.py#L370-L400](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L370-L400) |
| 16 | Откат публикации: удалить опубликованное, вернуть бэкапы из `.releases/<op>/<key>` | C §5 | ПОДТВЕРЖДЕНО | [model_store.py#L1534-L1560](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1534-L1560) |
| 17 | «журнал откатов» смены профиля | C §1 | ЧАСТИЧНО | Журнал — **одна** запись текущего перехода `state/profile-transaction.json` + `config/selection.previous.json`; истории откатов нет: [profile_store.py#L75-L80](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_store.py#L75-L80) |
| 18 | Машина состояний `PREPARING→VERIFIED→STOPPING→SWITCHING→STARTING→SMOKING→COMMITTED\|ROLLING_BACK→ROLLED_BACK\|NOT_READY` | C §7 | ПОДТВЕРЖДЕНО | [profile_store.py#L19-L31](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_store.py#L19-L31); `allowed_transition` — [L68-L69](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_store.py#L68-L69) |
| 19 | Транзакция `profile apply`: stop → switch → start → smoke → commit, один откат без retry-петель | C §1, §7 | ПОДТВЕРЖДЕНО | [profile_switch.py#L139-L200](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_switch.py#L139-L200), откат [L97-L136](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_switch.py#L97-L136) |
| 20 | Три состояния воркера `active → warm_standby → cold_evicted` + `reclamation_failed` | C §5 | ПОДТВЕРЖДЕНО | [worker_lease.py#L18-L22](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_lease.py#L18-L22) |
| 21 | Тайминги 60 s (min-uptime) / 60 s (standby) / 300 s (cold) | C §5 | ПОДТВЕРЖДЕНО | [config/__init__.py#L116-L123](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/__init__.py#L116-L123); применение — [services.py#L1005-L1013](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/services.py#L1005-L1013) |
| 22 | min-uptime — анти-thrash, не сбрасывает idle-часы | C §5 | ПОДТВЕРЖДЕНО (по коду и комментарию) | [worker_lease.py#L303-L311](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_lease.py#L303-L311) |
| 23 | standby «trim памяти» реально освобождает память | C §5 | ОПРОВЕРГНУТО | `trim_memory` шлёт кадр и глушит исключения ([qwen3_shared.py#L406-L413](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/qwen3_shared.py#L406-L413)); воркер делает только `_clear_metal_cache()` ([qwen3_worker.py#L1002-L1006](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/qwen3_worker.py#L1002-L1006)) |
| 24 | Ошибка освобождения → `reclamation_failed` + изоляция лейна | C §5 | ПОДТВЕРЖДЕНО | [worker_lease.py#L221-L262](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_lease.py#L221-L262); `quarantine_tts_lane` — [resource_governor.py#L259-L276](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L259-L276) |
| 25 | `budget_for_hardware = max(4 GiB, total//2)`, минимум 8 GiB | C §5 | ПОДТВЕРЖДЕНО | [model_budget.py#L173-L182](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/model_budget.py#L173-L182), константы [L15-L16](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/model_budget.py#L15-L16) |
| 26 | Перекрытие тяжёлых осей только если сумма объявленных пиков ≤ бюджета | C §5 | ПОДТВЕРЖДЕНО | [model_budget.py#L199-L231](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/model_budget.py#L199-L231) |
| 27 | Governor: `total=4`, `realtime_reserved=1`, `max_pending_per_class=8`, `aging=30 s` | C §5 | ПОДТВЕРЖДЕНО | [config/__init__.py#L131-L134](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/__init__.py#L131-L134); инварианты [resource_limits.py#L9-L26](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/domain/resource_limits.py#L9-L26) |
| 28 | Классы работ `REALTIME_ASR/REALTIME_TTS/BATCH_ASR/BATCH_TTS/DIARIZATION` | C §5, G §9 | ПОДТВЕРЖДЕНО | [resource_governor.py#L20-L51](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L20-L51) |
| 29 | batch XOR streaming на общем ASR-воркере | C §5 | ПОДТВЕРЖДЕНО | [asr_mode.py#L55-L97](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/asr_mode.py#L55-L97) |
| 30 | Конфликт режима отдаётся как `backend_busy` | C §5, §6 | ЧАСТИЧНО | Внутренний код — `asr_mode_conflict` ([busy.py#L9-L17](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/busy.py#L9-L17)); `backend_busy` — внешний код для 6 причин, отдаётся 503/429 с заголовками `SpeechRail-Busy-Reason`/`-Retry-Hint` ([audio.py#L125-L143](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/http/routes/audio.py#L125-L143)) |
| 31 | `AdmissionQueue` фиксированной ёмкости, отказ атомарный, дедлайн `asyncio.timeout` | C §5 | ПОДТВЕРЖДЕНО | [admission.py#L16-L40](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/admission.py#L16-L40); ёмкость `settings.max_queue_size` — [services.py#L837](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/services.py#L837) |
| 32 | `model=` не выбирает модель; проверяется как alias, иначе `400 model_not_found` | C §7 | ПОДТВЕРЖДЕНО (сильнее, чем в C) | [audio.py#L753-L757](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/http/routes/audio.py#L753-L757); алиасы захардкожены [openai_realtime.py#L34-L47](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/compatibility/openai_realtime.py#L34-L47) |
| 33 | Все ASR-алиасы сходятся в одну строку `speechrail/qwen3-asr-1.7b` | C §7 | ПОДТВЕРЖДЕНО | [openai_realtime.py#L34-L42](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/compatibility/openai_realtime.py#L34-L42) |
| 34 | Алиас сверяется с *сконфигурированным* артефактом | C §7 | ОПРОВЕРГНУТО | `canonical_asr_model` возвращает литерал из словаря и не сравнивается с `registered` ([openai_realtime.py#L434-L438](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/compatibility/openai_realtime.py#L434-L438)) |
| 35 | Partial: `revision` + `stable_prefix_codepoints` + `sample_span`, семантика replace | H §5.3 | ПОДТВЕРЖДЕНО | [openai_realtime.py#L574-L598](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/compatibility/openai_realtime.py#L574-L598); контракт [contracts/realtime-openai.md#L250-L262](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/realtime-openai.md#L250-L262) |
| 36 | Версия realtime-контракта 7.1.0, путь остаётся `/v1/realtime`, fail-closed без legacy/`/v2` | H §7 | ПОДТВЕРЖДЕНО | [contracts/realtime-openai.md#L1-L8](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/realtime-openai.md#L1-L8) |
| 37 | Перегрузка — `429` + `queue_full`/`backend_busy` + `SpeechRail-Busy-Reason`/`-Retry-Hint` | H §3 | ПОДТВЕРЖДЕНО | [audio.py#L1075-L1120](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/http/routes/audio.py#L1075-L1120) |
| 38 | Length-prefixed IPC, JSON-заголовок + raw binary, `MAX_FRAME_BYTES=128 MiB`, `PROTOCOL_VERSION=1` | C §5, G §9 | ПОДТВЕРЖДЕНО | [worker_protocol.py#L10-L30](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_protocol.py#L10-L30) |
| 39 | Раздельные read/write-локи; `exchange()` берёт оба | C §5 | ПОДТВЕРЖДЕНО | [worker_process.py#L130-L145](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L130-L145), [L222-L245](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L222-L245) |
| 40 | stderr-ринг 16 KiB, `_TERMINATE_GRACE_SECONDS = 2.0` | C §5, G §9 | ПОДТВЕРЖДЕНО | [worker_process.py#L31-L33](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L31-L33) |
| 41 | Инвариант `HF_HUB_OFFLINE` + allowlist окружения | C §10 | ПОДТВЕРЖДЕНО | [worker_process.py#L106-L127](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L106-L127) |
| 42 | Барьер завершения: `commit` + `request_receipt` → `speechrail.input_audio_buffer.committed{accepted_samples}`, лимит 16 незавершённых | C §10 | ПОДТВЕРЖДЕНО | [realtime_asr.py#L1116-L1133](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/realtime_asr.py#L1116-L1133); `INPUT_BARRIER_LIMIT = 16` — [realtime_openai.py#L37](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/http/routes/realtime_openai.py#L37), [L259-L262](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/http/routes/realtime_openai.py#L259-L262) |
| 43 | Swift-диаризатор — отдельный бинарь, 8-байтовый префикс + header + payload по stdin/stdout | C §9 | ПОДТВЕРЖДЕНО | [main.swift#L24-L40](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/native/diarization/Sources/SpeechRailDiarizationWorker/main.swift#L24-L40), [PacketCodec.swift#L33-L45](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/native/diarization/Sources/SpeechRailDiarizationProtocol/PacketCodec.swift#L33-L45) |
| 44 | `MODEL_REVISION`/`FLUIDAUDIO_COMMIT` + per-file sha256 для 11 файлов `.mlmodelc` | C §9 | ЧАСТИЧНО | `MODEL_REVISION`, `FLUIDAUDIO_COMMIT`, `MODEL_BUNDLE_NAME`, `MODEL_DTYPE`, `MODEL_FILE_SHA256` — [coreml.py#L30-L55](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/diarization/coreml.py#L30-L55); **файлов 10, не 11** (AST: `len(MODEL_FILE_SHA256.keys)` → 10) |
| 45 | FluidAudio запинен по ревизии в `Package.swift` | C §9 | ПОДТВЕРЖДЕНО | [native/diarization/Package.swift#L14-L19](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/native/diarization/Package.swift#L14-L19) — `revision: "5c19d5e1…"` |
| 46 | По процессу Swift-диаризатора на активную сессию, не в списке вытесняемых | C §5, §9 | ПОДТВЕРЖДЕНО | комментарий и `evictable`-список: [services.py#L983-L993](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/services.py#L983-L993) |
| 47 | Голосовых эмбеддингов как API нет | C §9 | ПОДТВЕРЖДЕНО | `grep -c embedding contracts/openapi.yaml` → 0; `grep -rn embedding src/speechrail/http/routes/` → пусто |
| 48 | Команды удаления/GC моделей нет | C §5 | ПОДТВЕРЖДЕНО | CLI-парсеры: только `model prepare`, `model prepare-diarization` ([cli.py#L217-L232](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/cli.py#L217-L232)) |
| 49 | launchd: `RunAtLoad=true`, `KeepAlive{SuccessfulExit:false}`, `ThrottleInterval=10`, `ProcessType=Interactive` | C §2 | ПОДТВЕРЖДЕНО | [launchd.py#L71-L90](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/launchd.py#L71-L90), [plist.example#L1-L27](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/deploy/macos/com.speechrail.plist.example#L1-L27) |
| 50 | `ServerInstanceLock` — flock по `(uid, port)` в TMPDIR | C §2 | ПОДТВЕРЖДЕНО | [server_lock.py#L30-L45](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/server_lock.py#L30-L45), использование [cli.py#L89-L93](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/cli.py#L89-L93) |
| 51 | Coverage-gate 80 %, strict mypy, ruff, `requires-python >=3.14,<3.15` | C §9 | ПОДТВЕРЖДЕНО | `pyproject.toml`: `fail_under = 80`, `strict = true`, `target-version = "py314"`, `requires-python` |
| 52 | «Загрузчика моделей нет — артефакты готовит оператор» | G §7 | ОПРОВЕРГНУТО | `prepare_selected_models` + `ModelScopeDownloader` инжектится из CLI: [cli.py#L790-L800](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/cli.py#L790-L800); реализация [model_store.py#L1584-L1785](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1584-L1785) |
| 53 | «One service, one ASGI worker… Contention returns `backend_busy` by design» | C §6 | ПОДТВЕРЖДЕНО | `README.md`, раздел Boundaries (стр. 145–146 в клоне) |
| 54 | `realtime_max_sessions=3` (1–8), `MAX_SESSIONS=8`, `SESSION_QUEUE_MAXSIZE=64` | C §6 | ПОДТВЕРЖДЕНО | [config/__init__.py#L129](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/__init__.py#L129); [qwen3_shared.py#L23-L24](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/qwen3_shared.py#L23-L24) |

Сводка вердиктов (54 строки): **ПОДТВЕРЖДЕНО — 47**, **ЧАСТИЧНО — 4** (#4, #17, #30, #44),
**ОПРОВЕРГНУТО — 3** (#23, #34, #52). Отдельно: в #2 верна цифра K, но неверна её интерпретация;
в #44 неверно число файлов (10, а не 11).

---

## Что заимствовать

### 1. Манифест артефакта + runtime-lock (`ArtifactFile` / `SourceLocation` / `QuantizationSpec` / `RuntimeLock`)

**Суть.** Манифест — это контракт, а не метаданные. Один `ModelArtifact` описывает ровно одну
(модель, ревизия, квантование, семейство) и несёт *пофайловые* `size`+`sha256`, обязательный
`SourceLocation` с 40-hex ревизией и `QuantizationSpec` с взаимоисключающими `bits`/`dtype`.
`RuntimeLock` отдельно пинит окружение исполнения: Python 3.14.x, `package==version --hash=sha256:…`,
хэши файлов и wheel движка с `patch_sha256`/`build_inputs_sha256`.
[model_catalog.py#L137-L341](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L137-L341)

**Инварианты (зашиты в валидаторы, не в документацию).**
- `extra="forbid"` + `frozen=True` на всех моделях → неизвестное поле = отказ, а не игнор.
- `revision` и `sha256` валидируются по длине/алфавиту; `path` — только относительный.
- `bits` XOR `dtype`; unquantized обязан объявить `dtype` (иначе «какая это точность» невыразимо).
- Хотя бы одна `sources[].revision` совпадает с `revision` артефакта → нельзя опубликовать манифест
  без доказуемого источника.
- Обязательные файлы по семейству (`config.json`+`model.safetensors`+токенизатор; для TTS — codec).
- `schema_version != 2` → `ValueError` на загрузке, без «попробуем».

**Дефекты реализации.**
- Требование `config.json` + `model.safetensors` **невыразимо для ONNX и CoreML**: одиночный
  `.onnx` и каталог `.mlmodelc` (см. `MODEL_FILE_SHA256` для диаризации) в эту схему не ложатся.
  Для нашего мультидвижкового каталога нужен `files[]` без обязательного safetensors + флаг
  «бандл = каталог».
- `RuntimeLock.python` захардкожен на regex `3\.14\.\d+` — lock непереносим на другой Python.
- Нет поля лицензии и нет поля «какие операции поддерживает артефакт» (asr/vad/diarize/embed) —
  G это уже отметил; в SpeechRail операции выводятся из `family`/`variant`, что для мультидвижка мало.
- Нет `capabilities`/`requirements` (например «нужен Metal», «нужен 16 kHz»).

**Тесты.** Есть и плотные: `tests/test_model_catalog_contract.py` (534 строки),
`tests/test_model_catalog_builder.py` (329). Покрывают валидаторы и сборку каталога.

**Применимость к многомодельному многоклиентному runtime.** Схему берём, но с тремя расширениями:
(а) `files[]` без обязательного safetensors и с `layout: file|directory`; (б) отдельный слой
«логическая модель → N артефактов по движкам» (у SpeechRail этого нет — `ModelSpecBinding`
привязывает `(tier, role)` к одному `artifact_key`, т.е. ровно один активный артефакт на роль);
(в) `license` и `operations` в артефакте. Без (б) каталог останется «одна модель на роль», и
требование «клиент явно задаёт модель» не выполнить.

---

### 2. staging → verify → atomic publish (с откатом публикации)

**Суть.** Операция подготовки модели — транзакция с одной точкой фиксации:
`models/.staging/<op_id>/<key>` → скачивание с пофайловой сверкой `size` и `sha256` **во время записи**
(`O_EXCL|O_NOFOLLOW`, режим `0600`, `fsync` на файл) → при повторной публикации существующий каталог
уезжает в `models/.releases/<op_id>/<key>` → `Path.replace()` (атомарный rename в пределах ФС) →
атомарная запись реестра `state/model-preparations.json` (`mkstemp` → `fchmod 0600` → `fsync` →
`replace` → `fsync` каталога, ошибка каталога не откатывает уже видимый реестр).
Любое исключение → `_rollback_publication`: удалить опубликованное, вернуть бэкапы.
[model_store.py#L1388-L1461](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1388-L1461),
[#L1534-L1560](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1534-L1560),
[#L1663-L1785](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L1663-L1785),
[#L370-L400](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/model_store.py#L370-L400)

**Инварианты.**
- Модель никогда не видна по финальному пути, пока не сверены все файлы: `.staging` — отдельный
  каталог, а `selection.py` отказывается трактовать `.staging` как активную модель (это проверяет G §9).
- `rename` — единственная точка фиксации; частично записанный каталог физически не может стать активным.
- Проверка `size` идёт *до* записи лишнего байта (`total + len(data) > expected_size` → отказ),
  а не после скачивания.
- Реестр пишется последним; если реестр не записался — публикация откатывается.
- `prepared_id` — детерминированный хэш `(selection_id, runtime_lock, artifacts)` → повторный
  `prepare` идемпотентен и не качает второй раз.
- Свободное место проверяется заранее (`_check_disk_space`, `_required_staging_bytes`).

**Дефекты реализации.**
- **Нет GC.** Каталог `models/.releases/<op_id>/<key>` только пополняется: `_move_existing_to_backup`
  вызывается при каждой повторной публикации, а единственный код, который вообще упоминает `.releases`
  в остальном, — валидатор пути (`_strict_prepared_path`). Команды `model remove/gc` нет.
  Итог: каждая смена ревизии модели оставляет полную копию весов навсегда. Для мультимодельного
  реестра это блокер.
- Реестр — один плоский `prepared`-словарь без refcount и без «какая ревизия сейчас нужна»;
  ключ реестра — `prepared_id`, а не `artifact_key@revision`.
- `operation_id` — `uuid4`, т.е. после падения процесса `.releases/<op>` остаётся без владельца;
  уборка только `.staging` (`finally: _remove_tree(operation_root)`), а не `.releases`.
- Транзакция держит все артефакты набора в памяти списком `published` — при N моделях накладных
  расходов нет, но и параллельных подготовок нет: один `operation_root` на вызов.

**Тесты.** Есть: `tests/test_model_store.py` — 1523 строки, 56 тестов (включая откат публикации).

**Применимость.** Механика переносится почти как есть, но её надо обернуть в CAS:
`blobs/<sha256>` (общий для всех моделей) + `manifests/<model>/<revision>.json` + `refs/`.
Тогда `.releases`-бэкапы не нужны вовсе (манифест — это ссылка, а не копия весов), а GC становится
подсчётом ссылок. Прямой перенос `.releases` в мультимодельный runtime даст квадратичный рост диска.

---

### 3. Трёхступенчатое вытеснение воркера (min-uptime → warm standby → cold evict)

**Суть.** Один фоновый монитор (`check_interval=10 s`) ведёт на каждого воркера idle-часы и
переводит его по трём ступеням: (0) `min_uptime=60 s` — только что загруженный воркер не вытесняется,
но idle-часы не сбрасываются (анти-thrash, не анти-вытеснение); (1) `warm_standby=60 s` — вызывается
`trim_memory()`/`release_cache()`, состояние `WARM_STANDBY`; (2) `cold_evict=300 s` — `worker.close()`
под удержанием admission-лока. Ошибка закрытия → `RECLAMATION_FAILED` и карантин лейна.
[worker_lease.py#L92-L121](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_lease.py#L92-L121),
[#L265-L338](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_lease.py#L265-L338),
[config/__init__.py#L116-L123](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/__init__.py#L116-L123)

**Инварианты (сильная часть — они про гонки, а не про TTL).**
- `_in_use()` учитывает и `lease.active_leases`, и `AsrModeGate.active_count` → вытеснение не
  может случиться «между» двумя запросами одного стрима.
- Решение о вытеснении принимается **под** `WorkerLeaseLock.idle()`, который держится всё время
  `close()`; новый lease либо успевает раньше и отменяет вытеснение, либо ждёт.
- Исход `close()` фиксируется **внутри** cleanup-задачи, а не в вызывающем: отмена ожидающего
  не превращается в «будто освободили».
- `reclamation_failed` — терминальное состояние: TTL и активность больше не могут его «вылечить»,
  только явный `confirm_shutdown()`/карантин.
- `force_evict()` (например по memory-pressure) уважает те же локи.
- `_last_active` берётся максимумом из трёх источников (lease, `worker.last_active`, локальные часы).

**Дефекты реализации.**
- **Standby ничего не освобождает.** `trim_memory()` у ASR — fire-and-forget кадр, у которого
  `contextlib.suppress(Exception)`, а воркер в ответ делает `_clear_metal_cache()` (только Metal-кэш,
  не веса). Значит `WARM_STANDBY` — это *заявленное*, а не измеренное состояние; в мультимодельном
  runtime на него нельзя опираться при приёме решения «влезет ли вторая модель».
- `evictor.track()` умеет добавлять воркеров, но `untrack` нет — набор вытесняемых воркеров только растёт.
- Дефолт конструктора (`idle=900`, `standby=180`, `min_uptime=0`) расходится с настройками
  (`300/60/60`) — легко получить «тихий» другой режим, если создавать эвиктор напрямую.
- `min_uptime` защищает от thrash только по времени загрузки; при двух моделях, конкурирующих за
  память, нужен ещё и приоритет (какую вытеснять), которого нет: вытеснение идёт по порядку `self._workers`.

**Тесты.** Есть: `tests/test_worker_lease.py` (740 строк, 20 тестов), включая
`test_two_stage_standby_and_eviction`, `test_lease_lock_protects_against_eviction`,
`test_evicted_worker_stays_cold_even_if_close_refreshes_last_active`,
`test_evictor_close_is_blocked_while_worker_lock_is_held`.

**Применимость.** Берём машину состояний и локов целиком; **не берём** идею «standby = освободил память».
Для многомодельного runtime нужно: (а) измерять реальный RSS/`phys_footprint` после trim и только
тогда объявлять standby; (б) политику выбора жертвы (LRU по модели, а не по порядку списка);
(в) `untrack` при выгрузке модели; (г) TTL как часть контракта клиента (`keep_alive`), а не только
глобальная настройка.

---

### 4. ResourceGovernor: realtime-резерв + классы работ + байтовый бюджет

**Суть.** Два независимых, но согласованных механизма.
(а) *Слотовый*: `GovernorLimits{total_capacity=4, realtime_reserved_capacity=1, max_pending_per_class=8,
batch_aging_seconds=30}`, инвариант `1 ≤ reserved < total`; batch может занять только нерезервированную
ёмкость, realtime не ждёт batch никогда (намеренно оставлен свободный слот вместо преэмпции);
FIFO + aging, чтобы batch не голодал. Классы: `realtime_asr`, `realtime_tts`, `batch_asr`, `batch_tts`,
`diarization` — у диаризации отдельная полоса, которая не может занять ASR/TTS.
(б) *Байтовый*: `budget_for_hardware(total) = max(4 GiB, total//2)`, порог 8 GiB физической памяти,
`ComponentFootprint` с явными `asr_bytes/tts_bytes/diarization_bytes`; перекрытие тяжёлых осей
разрешается только если сумма объявленных пиков ≤ бюджета, иначе fail-closed сериализация.
Три состояния отказа различаются: `queue_full`, `budget_exceeded` (объявленный пик не влезает) и
`lane_isolated` (владение бэкендом не подтверждено — очередь не поможет).
[resource_limits.py#L9-L26](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/domain/resource_limits.py#L9-L26),
[resource_governor.py#L104-L250](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L104-L250),
[model_budget.py#L173-L231](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/model_budget.py#L173-L231)

**Инварианты.**
- Резерв нельзя «одолжить»: `realtime_reserved_capacity` физически недоступен batch-классам.
- `reserve()` — контекстный менеджер: слот освобождается в `finally` через `join_cleanup`, т.е.
  отмена/исключение не течёт слот.
- `deadline`/`expires_at` — не «мягкий» таймаут: `TimeoutError` до допуска, а не после.
- Карантин полосы (`quarantine_tts_lane`/`_asr_lane`/`_diarization_lane`) — отдельное состояние,
  отличное от «очередь полна»; клиенту это разные коды.
- Бюджет fail-closed: неизвестный (undeclared) пик → сериализация, а не «наверное, влезет».

**Дефекты реализации.**
- Ёмкость считается **в слотах, а не в байтах**. Две модели по 3 GiB и две по 300 MiB равны для
  governor'а; память ловится только байтовым слоем, и только если пики объявлены вручную.
- `ComponentFootprint` оперирует осями `asr/tts/diarization`, а не моделями — для N моделей
  одной оси нужен другой агрегат.
- `max_pending_per_class=8` общий на класс, не на модель и не на клиента.
- Batch-aging 30 s помогает против голодания, но при физическом XOR (см. §5) batch всё равно
  ждёт освобождения режима, а не слота.
- Порог «минимум 8 GiB» — продуктовое решение, не техническая необходимость.

**Тесты.** Есть и очень плотные: `tests/test_resource_governor.py` (1221 строка),
`tests/test_model_budget.py` (169 строк).

**Применимость.** Берём оба слоя и **явно разделяем** их в API: ошибка «нет слота» ≠ «не влезает в
память» ≠ «полоса изолирована». Для многомодельного runtime: `total_capacity` заменить на
«сумма байтов загруженных моделей + per-request декларация», классы работ оставить, но добавить
измерение реального RSS вместо доверия декларациям. Резерв под realtime — обязательно.

---

### 5. Mode-gate batch XOR streaming на общем воркере

**Суть.** Один ASR-воркер не может одновременно обслуживать batch-транскрипцию и потоковую сессию:
`AsrModeGate` — синхронный финальный арбитр (`batch` эксклюзивен, `streaming` — счётчик), lease
выдаётся только владельцем, повторный `release` идемпотентен; поверх — `AsrModeScheduler` с FIFO и
aging 30 s, чтобы batch не голодал при непрерывном стриме.
[asr_mode.py#L22-L120](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/asr_mode.py#L22-L120)

**Инварианты.**
- Конфликт режима обнаруживается **до** начала работы, а не в середине.
- `lease._owner` проверяется на `release()` → чужой lease не может освободить чужой режим.
- Ошибка retryable и несёт `busy_reason`, по которому клиент выбирает стратегию ожидания.
- Эвиктор видит `mode_gate.active_count` → не вытеснит воркер посреди стрима.

**Дефекты реализации.**
- Ограничение физическое, а не логическое: причина — один MLX-контекст и один процесс. Это
  архитектурная бедность, а не дизайн-решение: правильный ответ — разные воркеры/инстансы для
  batch и streaming, а не взаимоисключение.
- `backend_busy` (внешний код) переиспользуется для 6 причин; клиент без разбора
  `SpeechRail-Busy-Reason` не может отличить «подожди 200 мс» от «подожди, пока закончится стрим».

**Тесты.** Есть: `tests/test_asr_mode.py` (254 строки).

**Применимость.** Берём **словарь причин занятости** и идею финального синхронного арбитра — они
полезны всегда. Само XOR — не берём: в мультимодельном runtime гейт должен быть per-worker
(на модель/движок), а не глобальным, иначе один клиент со стримом блокирует batch-транскрипцию
другой модели.

---

### 6. Частичные гипотезы: `revision` + `stable_prefix_codepoints` + `sample_span`

**Суть.** Partial — это **заменяемый снимок** (`speechrail.transcription.hypothesis`) с монотонной
`revision` на `utterance_id`, полным `text`, полуоткрытым `sample_span` и отдельным
`stable_prefix_codepoints` — длиной доказуемо стабильного префикса в code points. Отдельно
`…transcription.delta` несёт только доказуемый префикс как append-only; если стабильность не
доказуема — delta не отправляется вовсе (никаких угаданных префиксов).
[openai_realtime.py#L574-L598](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/compatibility/openai_realtime.py#L574-L598),
[contracts/realtime-openai.md#L250-L262](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/realtime-openai.md#L250-L262)

**Инварианты (сформулированы в контракте как обязанности клиента).**
- Заменять текст гипотезы, никогда не конкатенировать.
- Принимать только строго бо́льшую `revision`; дубли `event_id` и устаревшие ревизии игнорировать.
- Пустая гипотеза — валидная замена.
- `completed` — терминальный и авторитетный; поздний partial не двигает item.
- Клиент, уже показавший гипотезу, подавляет последующие delta.
- `sample_span` — в 24 кГц wire-сэмплах (ядро 16 кГц, ресемплер между ними), и это объявлено явно.

**Дефекты реализации.**
- `stable_prefix_codepoints` в коде вычисляется как `max(0, …)`, но фактически на одном из путей
  отправляется `0` ([realtime_asr.py#L1572](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/realtime_asr.py#L1572)) — т.е. «стабильного префикса нет»,
  и различие между «ноль» и «неизвестно» на проводе не выражено.
- `revision` — `int`, без epoch-склейки на уровне поля: epoch есть отдельным полем, и клиент
  обязан сравнивать пару. Легко ошибиться.
- Единица `sample_span` привязана к 24 кГц wire-формату — при переносе на другой движок/частоту
  контракт молча меняет смысл.

**Тесты.** Контрактные: `tests/test_app_contract.py`, `tests/realtime_wire.py`,
`tests/test_realtime_*.py`; машиночитаемая схема `contracts/realtime-events.schema.json` +
`realtime-field-matrix.json` (см. шапку контракта).

**Применимость.** Забирать целиком — это единственный контракт в выборке, который честно решает
проблему «частичная гипотеза vs append-only». В нашем API надо: (а) объявить единицу `sample_span`
в спеке протокола (сэмплы 16 кГц ядра либо wire-сэмплы — но не «как получится»);
(б) сделать `stable_prefix` явно опциональным (`null` = неизвестно, `0` = доказано пусто);
(в) добавить `model_revision` в гипотезу, т.к. у нас в одном utterance возможна смена модели между
сегментами (у SpeechRail это невозможно by design).

---

### 7. Воркеры-подпроцессы и framed IPC

**Суть.** Инференс живёт в дочерних процессах, общающихся length-prefixed кадрами:
`>I total` для JSON-only либо `>II total, json_len` + JSON + raw binary (PCM без Base64).
`PROTOCOL_VERSION = 1`, `MAX_FRAME_BYTES = 128 MiB` (обосновано `max_audio_seconds=3600` × 16 kHz PCM16).
`AsyncFramedWorkerProcess`: **раздельные** read/write-локи, `exchange()` берёт оба, stderr
дренируется в кольцо 16 KiB, `_TERMINATE_GRACE_SECONDS = 2.0`, окружение воркера — allowlist
(`PATH/TMPDIR/LANG/LC_ALL`) + `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`/`HF_DATASETS_OFFLINE=1`.
[worker_protocol.py#L10-L63](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_protocol.py#L10-L63),
[worker_process.py#L106-L145](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L106-L145),
[#L222-L245](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L222-L245)

**Инварианты.**
- Один writer и один reader на транспорт; `exchange()` атомарен по паре запрос/ответ.
- Паркованный `receive()` (realtime read loop) **не блокирует** writer — это и есть причина
  раздельных локов (в комментарии прямо описан дедлок однолоковой версии).
- Любое расхождение размера/типа кадра → `ProtocolError`, не «попробуем прочитать ещё».
- Приватный stderr воркера **никогда** не отдаётся клиенту — только низкокардинальные коды.
- Воркер не имеет доступа к сети/HF: офлайн-инвариант задаётся окружением, а не дисциплиной.

**Дефекты реализации.**
- `MAX_FRAME_BYTES = 128 MiB` — один кадр на один запрос; при мультимодельном runtime с большими
  чанками и N одновременных сессий это и потолок, и риск OOM при чтении (`_read_exact` собирает
  кадр целиком в память).
- Кольцо stderr 16 KiB обрезает vendor-трейс; для диагностики OOM этого мало.
- `trim_memory` идёт тем же каналом, но без ответа — нарушает «один writer/один reader, каждая
  пара атомарна»; в комментарии это осознанно допущено.
- Протокол операций зашит под Qwen3 (`window.decode`, `session.open`, `audio.append`, `commit`, …) —
  обобщения нет; для N движков нужен versioned op-словарь с capability-handshake.

**Тесты.** Есть: `tests/test_worker_protocol.py` (246), `tests/test_worker_process.py` (543).

**Применимость.** Формат кадра и модель локов переносятся напрямую (это лучший образец в выборке).
Для многомодельного runtime добавить: (а) `op`-словарь с версией и capability-negotiation в handshake;
(б) потоковую передачу больших payload вместо одного кадра; (в) per-session мультиплексирование
(`session_id` в заголовке) — у SpeechRail это есть для стрима, но не как общий механизм.

---

### 8. Транзакция смены модели с журналом и единственным откатом

**Суть.** Смена активного набора — офлайн-транзакция: `begin(previous, candidate)` пишет журнал
`state/profile-transaction.json`; далее строго по одному переходу
`PREPARING→VERIFIED→STOPPING→SWITCHING→STARTING→SMOKING→COMMITTED`, где каждый `mark()` валидирует
допустимость перехода (`allowed_transition`) и пишет файл целиком. Сбой → `ROLLING_BACK` →
восстановить `selection.previous.json` → `stop/start` → smoke на предыдущем наборе → `ROLLED_BACK`;
если и это не удалось — `NOT_READY` (сервис честно не готов, а не «наполовину переключён»).
`recover()` при старте: незавершённый журнал → `profile_store_busy` (не два перехода сразу).
[profile_store.py#L19-L31](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_store.py#L19-L31),
[#L276-L320](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_store.py#L276-L320),
[profile_switch.py#L97-L200](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/profile_switch.py#L97-L200)

**Инварианты.**
- Переходы конечны и проверяемы; «перескочить» стадию нельзя.
- `generation` монотонно растёт → клиент может понять, что профиль сменился.
- Ровно один откат, без retry-петель (в коде это буквально комментарий «without retry loops»).
- Smoke — часть транзакции: `COMMITTED` ставится только после успешного публичного запроса.
- Симлинки запрещены на всех путях журнала/селекта (`_safe_path`), файлы читаются с `O_NOFOLLOW`.
- `selection.previous.json` пишется **перед** откатом, а не после.

**Дефекты реализации.**
- Это журнал **одного** перехода: истории откатов нет, аудита «что и когда откатывали» нет.
- Транзакция останавливает **весь** сервис — для мультимодельного runtime неприемлемо
  (нельзя останавливать все модели, чтобы добавить одну).
- `SelectionRecord` жёстко фиксирует `asr_spec`/`tts_spec` ∈ `{fast, quality, reference}` и
  `auto` — т.е. одна модель на роль.
- `_mark_not_ready`/`_mark_rolling_back` глушат `OSError` — при сбое записи журнала состояние
  может остаться неотражённым (осознанный компромисс, но клиенту об этом не сообщается).

**Тесты.** Есть: `tests/test_profile_store.py` (401), `tests/test_profile_switch.py` (351),
`tests/test_profile_smoke.py`, `tests/test_profile_service_controller.py`.

**Применимость.** Берём схему «журнал перехода + явные стадии + smoke + один откат» как шаблон
для операций над **одной** моделью (`load`/`update`/`unload`), но не для «смены профиля сервиса».
В мультимодельном runtime аналог — транзакция над записью реестра + refcount, без остановки сервиса.

---

### 9. launchd-семантика и singleton по порту

**Суть.** Пользовательский `LaunchAgent` `com.speechrail` с `RunAtLoad=true`,
`KeepAlive={SuccessfulExit:false}` (перезапуск только после падения), `ThrottleInterval=10`,
`ProcessType=Interactive`; plist генерируется кодом (`plistlib.dumps`), а не шаблонизируется
shell-подстановкой. Синглтон — kernel `flock` по ключу `(uid, port)` в TMPDIR, файл намеренно
не удаляется (flock привязан к процессу и не «протухает» после краха).
[launchd.py#L71-L90](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/service/launchd.py#L71-L90),
[server_lock.py#L30-L45](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/server_lock.py#L30-L45),
[cli.py#L89-L93](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/cli.py#L89-L93)

**Инварианты.**
- `SuccessfulExit: false` — «чистый» выход не перезапускается: остановка оператором не воюет с launchd.
- Throttle 10 s не даёт crash-loop'у съесть CPU.
- Синглтон по порту, а не по app home → dev-checkout и managed-install не поднимают два процесса.
- `launchctl` вызывается с таймаутом 15 s, без root и без shell-интерполяции.
- stdout/stderr — в файлы (лог-конфиг uvicorn отключён, чтобы не дублировать хендлеры).

**Дефекты реализации.**
- Один агент = один сервис = один порт; для нескольких моделей это нормально, но нет
  механизма «второй агент на другой порт» (и это, вероятно, правильно).
- Логи launchd не ротируются самим сервисом (uvicorn `log_config=None` отдаёт их launchd-файлам).
- `KeepAlive.SuccessfulExit:false` означает, что падение воркера (дочернего) не поднимает сервис —
  это ответственность супервизора в процессе, и она есть, но не персистентна между рестартами.

**Тесты.** Есть: `tests/test_launchd_service.py` (280), `tests/test_server_lock.py` (41).

**Применимость.** Берём как есть. Для нашего runtime: `ProcessType=Interactive` уместен для
realtime; при batch-нагрузке стоит рассмотреть `Adaptive`/`Background` для воркеров, но это
надо измерять.

---

### 10. Swift-диаризатор как отдельный процесс с собственным пакетным протоколом

**Суть.** CoreML-диаризация вынесена в Swift-бинарь `SpeechRailDiarizationWorker`
(зависимость FluidAudio, запинена по `revision`), общение по stdin/stdout пакетами
`headerSize:UInt32 BE | payloadSize:UInt32 BE | JSON header | payload`, `protocolVersion = 1`,
`maxHeaderBytes = 64 KiB`, `maxPayloadBytes = 4 MiB`. Python-сторона не содержит модели:
`MODEL_REVISION`, `FLUIDAUDIO_COMMIT`, `MODEL_BUNDLE_NAME`, `MODEL_DTYPE` и `MODEL_FILE_SHA256`
по каждому файлу `.mlmodelc`; перед запуском проверяются и хэши, и сигнатуры `.mil`.
Процесс — **на сессию**, закрывается по finish/cancel и намеренно не входит в список вытесняемых.
[main.swift#L24-L40](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/native/diarization/Sources/SpeechRailDiarizationWorker/main.swift#L24-L40),
[PacketCodec.swift#L7-L45](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/native/diarization/Sources/SpeechRailDiarizationProtocol/PacketCodec.swift#L7-L45),
[coreml.py#L30-L75](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/backends/diarization/coreml.py#L30-L75),
[services.py#L983-L993](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/services.py#L983-L993)

**Инварианты.**
- Два независимых замка на пакет (header и payload) с явными потолками — нельзя «прочитать 4 GiB».
- Числа парсятся строго: `NSNumber` отвергается, если это `CFBoolean` или float (`objCType` `f`/`d`),
  и проверяется 2^53 — защита от JSON-ловушек.
- Хэши **всех** файлов бандла + сигнатуры входов/выходов `.mil` → подмена весов невозможна.
- Python-адаптер не импортирует CoreML: граница процесса = граница доверия.
- Один процесс на сессию = полная изоляция падений (сессия падает, сервис живёт).

**Дефекты реализации.**
- Процесс на сессию — дорого: загрузка CoreML-модели на каждый старт сессии.
- Нет пула/переиспользования; нет прогрева.
- В `MODEL_FILE_SHA256` **10** файлов (C пишет 11) — мелочь, но показывает, что цифры в отчётах
  надо перепроверять.
- Отдельный протокол (не общий `worker_protocol.py`) — второй словарь кадров в одном проекте.

**Тесты.** `native/diarization/Sources/SpeechRailDiarizationProtocolSelfTest/main.swift` (self-test
цели), `tests/test_diarization_worker.py`, `tests/test_diarization_owner.py`.

**Применимость.** Схема «тяжёлый Apple-движок в отдельном процессе с собственным пакетным
протоколом + хэши бандла» — прямая рекомендация для нашего `worker-coreml`. Но нужен пул
инстансов и прогрев, иначе latency первой сессии будет неприемлемой.

---

## Неизвестные / что измерить

1. **Реальный эффект `trim_memory`.** Код вызывает `_clear_metal_cache()`; сколько байт это
   возвращает на Qwen3-ASR 1.7B q8 — неизвестно. Нужно измерить `phys_footprint` до/после
   `trim_memory` (без этого нельзя проектировать вытеснение под многомодельность).
2. **Стоимость cold evict + повторной загрузки** (модель 1.8–3.5 GB): сколько секунд занимает
   `close()` и последующий lazy load. TTL 300 s выбраны интуитивно — подтверждения нет.
3. **Почему `_ASR_MODEL_ALIASES` не сверяются с активным артефактом** — это осознанно
   (совместимость с [OI]) или недосмотр? По коду — недосмотр: `canonical_*` возвращает литерал,
   а результат не сравнивается с `registered`. Влияние: `model=` не влияет ни на что.
4. **Поведение при двух одновременных `model prepare`** — `profile_store` защищает смену профиля
   файловым локом, а `model_store` — нет; конкурентные `prepare` могут писать в один реестр.
   Нужно проверить (в коде лока вокруг `_write_registry` не видно).
5. **Рост `models/.releases`** — оценки нет; надо измерить на 3–4 сменах ревизии одной модели.
6. **Точность `stable_prefix_codepoints`**: на одном пути всегда `0`
   ([realtime_asr.py#L1572](https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/application/realtime_asr.py#L1572)). Нужно понять, всегда ли это
   «доказуемо пусто» или частично заглушка.
7. **Не проверено (не запускалось):** реальные значения `phys_footprint`, латентности,
   поведение `launchd` на macOS 26, работоспособность `ModelScopeDownloader` — всё требует
   запуска, чего в этой среде нет.
8. **Лицензии артефактов** — в каталоге SpeechRail поля лицензии нет; MIT относится к коду,
   а не к весам (это уже отмечено в G). Для нашего реестра лицензия обязательна в манифесте.

---

## Источники

Репозиторий: `https://github.com/hrygo/SpeechRail`, коммит `cdccc74084bc94f8c996b5b71a386455640ad1cb`.
Локальный клон (read-only): `/tmp/srr/repos/SpeechRail`.

Файлы, прочитанные для проверки:
- `src/speechrail/config/model_catalog.py`, `src/speechrail/config/selection.py`, `src/speechrail/config/__init__.py`
- `src/speechrail/assets/model-catalog.json`, `src/speechrail/assets/runtime-lock.json`
- `src/speechrail/service/model_store.py`, `model_commands.py`, `profile_store.py`, `profile_switch.py`, `launchd.py`
- `src/speechrail/runtime/worker_protocol.py`, `worker_process.py`, `worker_lease.py`, `resource_governor.py`,
  `model_budget.py`, `asr_mode.py`, `busy.py`, `admission.py`, `server_lock.py`
- `src/speechrail/domain/resource_limits.py`
- `src/speechrail/http/routes/audio.py`, `realtime_openai.py`
- `src/speechrail/compatibility/openai_realtime.py`
- `src/speechrail/application/services.py`, `realtime_asr.py`, `lifecycle.py`
- `src/speechrail/backends/qwen3_shared.py`, `qwen3_native.py`, `qwen3_worker.py`
- `src/speechrail/backends/diarization/coreml.py`
- `native/diarization/Package.swift`, `Sources/SpeechRailDiarizationWorker/main.swift`,
  `Sources/SpeechRailDiarizationProtocol/PacketCodec.swift`
- `contracts/realtime-openai.md`, `contracts/openapi.yaml`, `deploy/macos/com.speechrail.plist.example`,
  `pyproject.toml`, `README.md`

Команды git (сырые результаты):
```
$ git rev-list --count HEAD                    → 1483
$ git rev-list --count --since=30.days HEAD    → 524
$ git log --format='%ae' | sort | uniq -c
   1082 aaronwong1989@gmail.com
    388 aaronwong1989@live.cn
     13 49699333+dependabot[bot]@users.noreply.github.com
$ git log --format='%ad' --date=format:'%Y-%m' | sort | uniq -c
     55 2026-08
   1033 2026-09
    395 2026-10
$ git tag | wc -l                              → 73
$ git for-each-ref --sort=creatordate --format='%(refname:short) %(creatordate:short)' refs/tags | head -3
   v1.2.0 2026-09-02
   v1.3.1 2026-09-02
   v1.4.0 2026-09-02
$ git for-each-ref ... | tail -3               → v4.2.0 2026-10-21, v4.3.0 2026-10-21, v5.0.0 2026-10-24
$ git log --format='%b' | grep -c '^Co-authored-by:'   → 98
$ git ls-files 'tests/**/*.py' | wc -l         → 312 ; xargs wc -l → 115316
$ git ls-files 'src/**/*.py' | xargs wc -l     → 71096
$ python3 -c "ast: len(MODEL_FILE_SHA256.keys)" → 10
```

Проверяемые отчёты: `research/wave1/C-speechrail.md`, `research/wave1/G-model-management.md`,
`research/wave1/H-api-contract.md`, `research/wave1/K-licenses-maintenance.md`.
