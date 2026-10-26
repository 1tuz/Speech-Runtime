# G. Управление моделями: Ollama, LM Studio, речевые серверы и дизайн Model Registry

Дата: 2026-10-25. Метод: чтение исходного кода локальных клонов (`/tmp/srr/repos`, `/tmp/srr/extra/ollama`, SHA зафиксированы в разделе «Источники») + официальная документация (LM Studio, Hugging Face). Всё, что не подтверждено кодом/официальным документом, помечено `[INFERENCE]`.

Фиксация версий: Ollama `d44c2581` (2026-10-24), speech-swift `cbad3be2` (2026-10-25), SpeechRail `cdccc740` (2026-10-25), speaches `993994f7` (2026-04-17), FluidAudio `1b0ed50d`, WhisperKit `9f99b5cb`, WhisperLiveKit `eb92c115`, sherpa-onnx `532f1b5a`, gotranscribesrv `a5f42890`.

---

## Итог

1. **Ollama — единственный из изученных проектов с полным жизненным циклом моделей**: content-addressed блобы (`sha256`), манифесты с media-type-слоями, pull с докачкой по частям, планировщик с `refCount` + TTL (`keep_alive`) + вытеснением по памяти, движки как подпроцессы с health-пингом. Это ближайший архитектурный референс, и его устройство надо копировать, а не изобретать.
2. **Ollama уже решил «одна модель → несколько артефактов под разные движки»**: в манифесте есть `mediaType: application/vnd.ollama.manifest.list.v2+json` и поле `runner` (`mlx` / `llamacpp` / `ggml`); при pull скачивается **только выбранный child-манифест**, а выбор по умолчанию зависит от платформы (`darwin/arm64` → сначала `mlx`). Это готовый шаблон для «CoreML/MLX/ONNX-артефакты одной логической модели».
3. **LM Studio** даёт лучшую UX-модель резидентности: JIT-загрузка по первому запросу, `Idle TTL` (по умолчанию 60 мин, переопределяется полем `ttl` в запросе), `Auto-Evict` (при включении — не более 1 JIT-модели в памяти), `lms load --ttl/--estimate-only`, `lms ps`. Исходники закрыты — воспроизводим только поведение, не реализацию. `Auto-Evict=1` для мультиклиентного speech-рантайма неприемлем.
4. **HF Hub cache** — эталон разделения «контент vs имя»: `blobs/<hash>` + `snapshots/<commit>/…` (симлинки) + `refs/<branch>` + `trees/<commit>.json` (кэш листинга) + `.no_exist`. Дедупликация между ревизиями бесплатна. Дополнительно в актуальной версии — общий `blobs/<prefix>/<xet_hash>` с манифестами `.refs` для дедупликации **между репозиториями**.
5. **speech-swift** — лучшая реализация *загрузки* для Swift: один запрос `tree?recursive=true&expand=true` вместо HEAD-фан-аута, `sha256` из LFS `oid`, staging в отдельном `.incomplete` **вне** дерева модели (чтобы CoreML-бандл не содержал мусора), проверка полноты шардов по `model.safetensors.index.json`, отказ использовать неполный кэш при офлайне. Но резидентность — `ModelCache` (actor), который **никогда не выгружает** модель: ни refcount, ни TTL, ни бюджета.
6. **SpeechRail** — единственный проект с настоящим *манифестом артефактов*: `ArtifactFile{path,size,sha256}`, `SourceLocation{provider,repository,revision(40 hex)}`, `QuantizationSpec{bits | dtype}` (взаимоисключающие), жёсткая валидация обязательных файлов по семейству, `RuntimeLock` с `package==version --hash=sha256:…` и пином wheel движка, схема `schema_version=2`. Но загрузчика моделей там нет — артефакты готовит оператор; управление ресурсами сделано на уровне *классов работ* (`ResourceGovernor`), а не моделей.
7. **speaches** — ровно наш паттерн резидентности, но in-process: `SelfDisposingModel` с `ref_count` + `threading.Timer` TTL (stt/tts = 300 c, vad = -1), `BaseModelManager` на `OrderedDict`. Просто, читаемо, без бюджета памяти и без ревизий (идентичность = HF repo id).
8. **sherpa-onnx менеджера моделей не имеет вообще**: модель — это набор путей в C-структуре (`SherpaOnnxOfflineModelConfig`) + `provider` + `num_threads`; HTTP-клиента в библиотеке нет. Значит реестр обязан материализовать «набор файлов под конкретный engine config», а не «директорию модели».
9. **Вывод**: строим собственный реестр (манифест + CAS + `refs` + материализованные `builds`), резидентность — по Ollama/speaches (`refCount` + TTL + бюджет памяти), очереди — по SpeechRail (классы работ, realtime-резерв), изоляция падений — подпроцессы по Ollama. Ни один из проектов не покрывает набор целиком.
10. **Четыре обязательных отличия от Ollama для speech**: (a) не вытеснять занятый инстанс (у Ollama `findRunnerToUnload` берёт занятый раннер с минимальной длительностью и ждёт — для длинных стримов это неприемлемо); (b) несколько артефактов на модель с выбором по платформе и по требуемой операции; (c) дисковые lease-файлы для восстановления после падения **демона** (у Ollama вся бухгалтерия раннеров только в памяти); (d) переиспользование загруженных весов между клиентами как явный контракт (`refCount` по `(artifact, load_options)`), а не побочный эффект.

---

## Факты

### 1. Ollama: content-addressed хранилище и манифесты

Раскладка: `<OLLAMA_MODELS>/blobs/sha256-<hex>` + `<OLLAMA_MODELS>/manifests-v2/<host>/<namespace>/<model>/<tag>` (легаси-дерево `manifests/` поддерживается для отката). Каталог моделей задаётся `OLLAMA_MODELS` (https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/envconfig/config.go#L111-L120).

- Слои/блобы: `type Layer{MediaType, Digest, Size, From, Name}` и константы media-type (`application/vnd.ollama.image.model|projector|adapter|embed|template|system|license|params|messages|tensor|json|draft`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/layer.go#L12-L37
- `NewLayer` пишет поток во временный файл в каталоге блобов, параллельно считает `sha256`, затем `os.Rename` в `blobs/sha256-<hex>` + `chmod 0644`; если блоб уже есть — статус «using existing layer» (дедупликация по контенту) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/layer.go#L39-L88
- `BlobsPath`: валидация digest регуляркой `^sha256[:-][0-9a-fA-F]{64}$`, замена `:`→`-` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/paths.go#L35-L41 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/paths.go#L400-L418
- `touchLayer` (`os.Chtimes`) обновляет mtime блоба при использовании — примитив для LRU/GC — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/layer.go#L161-L164
- Манифест: `{schemaVersion, mediaType, config: Layer, layers: []Layer, runner, format, manifests: []Manifest}` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L43-L58
- Манифест-лист (несколько вариантов одной модели): `MediaTypeManifestList = "application/vnd.ollama.manifest.list.v2+json"`, раннеры `mlx` / `ggml` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L33-L36
- Выбор child-манифеста по платформе: `runtime.GOOS=="darwin" && GOARCH=="arm64"` → `[mlx, llamacpp, ggml]`, иначе `[llamacpp, ggml, mlx]`; при явном `runner` — только он — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L854-L871
- `SelectManifestReferenceForRunner` / `selectManifestReferenceWithPreferences`: линейный перебор предпочтений × children, ошибка `ErrNoCompatibleManifest` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L774-L852
- Метаданные движка выводятся из формата весов: `safetensors → (mlx, safetensors)`, `gguf|ggml → (ggml, gguf)` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L885-L900

### 2. Ollama: pull, докачка, проверка digest, GC

- `PullModel`: сначала строит `deleteMap` из блобов *текущего* манифеста (чтобы удалить осиротевшие после апдейта), затем `pulling manifest`, при манифест-листе — `pullSelectedManifest` (качается только выбранный child), затем по-слойный `downloadBlob`, затем статус `verifying sha256 digest` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/images.go#L1167-L1300
- `pullSelectedManifest` скачивает child-манифест как блоб, проверяет его digest, парсит JSON, запрещает вложенные списки — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/images.go#L1406-L1454
- `verifyBlob`: полное чтение файла, `GetSHA256Digest(f)` сравнивается с digest; при расхождении файл удаляется (`os.Remove`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/images.go#L1844-L1862
- **Важная деталь**: `downloadBlob` при существующем файле возвращает `cacheHit=true` без проверки содержимого (`os.Stat` → сразу `pulling … Completed=Size`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L510-L536 ; в `PullModel` такие блобы исключаются из проверки (`skipVerify[digest] = cacheHit`, причём логика склеивает результаты по одному digest для config+layer) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/images.go#L1245-L1292
- Докачка: 16 частей, размер части 100 MB…1000 MB (`numDownloadParts`, `minDownloadPartSize`, `maxDownloadPartSize`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L100-L105
- Состояние частей — JSON-файлы `<blob>-partial-<n>` с `{N, Offset, Size, Completed}`; `Prepare` глобит их и продолжает с места (`StartsAt = Offset + Completed`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L66-L105 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L131-L190
- Данные пишутся в `<blob>-partial` (sparse, `Truncate(Total)`), после успеха — `os.Rename(file.Name(), b.Name)` (атомарная публикация) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L219-L230 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L318-L330
- Части качаются `Range: bytes=start-end`, прогресс коммитится после каждого чанка; `context.Canceled` и `UnexpectedEOF` считаются возобновляемыми — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L367-L415
- Stall-детектор: 30 с без прогресса → `errPartStalled` → retry с квадратичным бэкоффом (`maxRetries = 6`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L43-L46 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L206-L260
- Один download на digest на процесс: `blobDownloadManager.LoadOrStore(digest, …)` + `references`/`CancelFunc` — параллельные клиенты делят одну загрузку; последний ушедший отменяет — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L536-L549 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L484-L500
- GC: `RemoveNamed` → кандидаты из манифеста → `RemoveUnreferencedBlobs`; последний строит множество `retained` из **всех** именованных манифестов (`retainedBlobDigestsLocked`) и удаляет только не вошедшие — mark-and-sweep — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L259-L275 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L602-L640 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L436-L460
- Атомарная запись: манифест сохраняется как блоб (`sha256` от JSON), затем `linkManifest(name, digest)`; запись — `CreateTemp` в каталоге назначения → `chmod` → `Rename` (на Windows fallback `Remove`+`Rename`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L1287-L1330 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L1331-L1390 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L1139-L1166
- Откат при даунгрейде: `WriteManifestListPreserveLegacy` + `WriteLegacyAnchor` дописывают digest'ы v2-манифестов в легаси-манифест как слои, чтобы старый GC не удалил их — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L1168-L1206
- `PullModel` **не** вызывается автоматически при инференсе: авто-pull есть только в `create` (`parseFromModel` для базовой модели) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/model.go#L27-L45

### 3. Ollama: планировщик, keep_alive, лимиты, вытеснение, refcount

- Структура: один канал `pendingReqCh`, `loaded map[string]*runnerRef`, `activeLoading` (загрузка строго по одной за раз), `expiredCh`, `unloadedCh` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L63-L87
- Ключ инстанса: `ModelPath` для GGUF, иначе digest выбранного манифеста (`schedulerModelKey`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L146-L170
- Каналы буферизуются `OLLAMA_MAX_QUEUE` (по умолчанию 512); при переполнении `getRunner` делает неблокирующий `select` и отдаёт `ErrMaxQueue` («server busy, please try again. maximum pending requests exceeded») — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L125-L142 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L240-L253
- Лимиты: `OLLAMA_NUM_PARALLEL` (1), `OLLAMA_MAX_LOADED_MODELS` (0 = авто), `OLLAMA_MAX_QUEUE` (512) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/envconfig/config.go#L274-L282 ; авто-значение `MAX_LOADED_MODELS` = `3 * число GPU` (`defaultModelsPerGPU = 3`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L118-L121 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L325-L345
- `keep_alive`: `OLLAMA_KEEP_ALIVE` по умолчанию 5 минут, отрицательное → «бесконечно» (`MaxInt64`); per-request `keep_alive` (строка `"5m"` или число секунд, отрицательное → бесконечно) переопределяет `sessionDuration` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/envconfig/config.go#L126-L144 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/api/types.go#L1277-L1300 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L555-L560
- Refcount: `useLoadedRunner` делает `refCount++`, останавливает `expireTimer`, запоминает `sessionDuration` и запускает горутину `<-pending.ctx.Done() → finishedReqCh` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L518-L537
- На завершении запроса: `refCount--`; при 0 — если `sessionDuration <= 0` немедленная выгрузка, иначе `time.AfterFunc(sessionDuration)` с `expiresAt = now+duration`; при новом запросе таймер `Reset` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L410-L455
- Вытеснение: `findRunnerToUnload` сортирует `ByDurationAndName` (по `sessionDuration`), берёт первый с `refCount==0`; если все заняты — **берёт занятый с минимальной длительностью** и ждёт `unloadedCh` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1714-L1745 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L378-L405
- Учёт VRAM: `updateFreeSpace` суммирует `VRAMByGPU` по загруженным раннерам и вычитает из «свободной» памяти, выбирая минимум из предсказанной и репортируемой — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1335-L1375 ; после выгрузки — `waitForVRAMRecovery` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1485
- OOM-ретрай: при падении загрузки после spawn и наличии резидентных моделей — `evictAllAndWait`, затем один повтор; флаг `oomRetryAttempted` защищает от бесконечного цикла — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L360-L372 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1640
- Переиспользование vs перезагрузка: `needsReload` возвращает true при отличии опций раннера (`reflect.DeepEqual(optsExisting, optsNew)`), смене адаптеров/проекторов, смене `contextShift` **или если `runner.llama.Ping(ctx) != nil`** — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1423-L1470
- Параллелизм — свойство *загрузки*, не запроса: `numParallel` фиксируется при spawn (принудительно 1 для не-completion моделей и для списка «небезопасных» архитектур), а эффективный контекст = `ctx * numParallel` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L539-L552 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L847-L849
- `/api/ps` отдаёт `{name, model, size, digest, details, expires_at, size_vram, context_length, runner}` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/api/types.go#L945-L955 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L2793-L2830
- «Запрос-только-выгрузка»: пустой `prompt` (или пустой `messages`) + `keep_alive: 0` → `expireRunner`, ответ `done_reason: "unload"` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L477-L491 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L3076-L3090

### 4. Ollama: движки как подпроцессы и восстановление после падения

- `llama-server` запускается через `exec.Command`, порт эфемерный; `startProcess` вешает горутину-рипер `cmd.Wait()` → `doneErr` + `close(done)` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/llm/llama_server.go#L1045-L1073
- Готовность и здоровье — HTTP `GET /health` с состояниями `ok` / `loading model` / `no slot available` / `error`, плюс проверка `cmd.ProcessState` («process no longer running») — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/llm/llama_server.go#L1238-L1295
- `Ping` = `getServerStatus` == Ready; используется и в `needsReload`, и в `WaitUntilRunning` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/llm/llama_server.go#L1319-L1325
- MLX-раннер устроен так же: подпроцесс + localhost-порт, `NewClient` не стартует процесс до `Load()`, `WaitUntilRunning` крутит `Ping` каждые 100 мс до `OLLAMA_LOAD_TIMEOUT`, `Close` → `Process.Kill()` и ожидание `<-done` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/mlxrunner/client.go#L36-L160
- `OLLAMA_LOAD_TIMEOUT` по умолчанию 5 минут (stall-детект загрузки) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/envconfig/config.go#L146-L164
- Осиротевшие раннеры: `processCompleted` сравнивает `pid` события и текущего раннера в `loaded`; при несовпадении («orphaned runner») выгружает пришедший, но не трогает загруженный — защита от двойной выгрузки при отменённых запросах — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L484-L500
- Отмена по разрыву соединения: контекст gin-запроса передаётся в `scheduleRunnerForModel`; отмена контекста завершает `pending.ctx.Done()` → декремент `refCount`; уже стоящие в очереди отменённые запросы пропускаются в `processPending` (`if pending.ctx.Err() != nil { continue }`) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L257-L295 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L280-L284

### 5. Ollama уже обслуживает речь

- `POST /v1/audio/speech` (TTS, стрим `wav`/`pcm`, `speechSampleRate = 24000`, лимит 4096 символов) — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/speech.go#L33-L70
- `POST /v1/audio/transcriptions` обслуживается `ChatHandler` через `TranscriptionMiddleware` (multipart до 25 MB → audio уходит в чат-модель) — то есть ASR у Ollama = мультимодальный чат, а не выделенный потоковый ASR-пайплайн — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L2425-L2426 , https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/middleware/openai.go#L1352-L1392
- Есть capability `speech` для моделей — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/types/model/capability.go#L15

### 6. LM Studio (закрытый исходник; только официальная документация)

- JIT-загрузка включена по умолчанию: первый запрос к модели загружает её. `Idle TTL` — по умолчанию **60 минут**, задаётся полем `ttl` (секунды) в запросе; таймер сбрасывается каждым запросом; «idle» = не выполняет работу — https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict
- `Auto-Evict` (вкл. по умолчанию): перед JIT-загрузкой новой модели выгружаются ранее JIT-загруженные; при включённом — **не более 1** JIT-модели в памяти одновременно; модели, загруженные вручную, не затрагиваются — там же
- `lms load <key> [--ttl N] [--gpu 0-1|off|max] [--context-length N] [--identifier NAME] [--estimate-only]`; модели, загруженные через `lms`, TTL по умолчанию **не имеют** (висят до `lms unload`) — https://lmstudio.ai/docs/cli/local-models/load
- `--estimate-only` печатает «Estimated GPU Memory / Estimated Total Memory» и учитывает context length, flash attention, vision-режим — там же
- `lms ps` — модели в памяти; `lms ls [--json|--detailed|--llm|--embedding]` — модели на диске с размером; `lms unload [--all]`; `lms get <name>@<quant>` — загрузка с выбором квантизации, `--mlx/--gguf` фильтры; всё работает с `--host` для удалённого инстанса — https://lmstudio.ai/docs/cli , https://lmstudio.ai/docs/cli/local-models/get , https://lmstudio.ai/docs/cli/local-models/ls
- Native REST API v1: `/api/v1/models`, `/api/v1/models/load`, `/api/v1/models/unload`, `/api/v1/models/download`, `/api/v1/models/download/status`; есть «Model load streaming events» — https://lmstudio.ai/docs/developer/rest
- `lms` — MIT, но это клиент к демону LM Studio (https://github.com/lmstudio-ai/lms); формат каталога моделей и внутренняя бухгалтерия памяти в открытых исходниках не раскрыты (в документации — только «LM Studio models directory», задаваемый в UI).

### 7. Hugging Face Hub cache

- Раскладка: `<CACHE_DIR>/{models,datasets,spaces,kernels}`, внутри репозитория — `blobs/`, `snapshots/`, `refs/`, `trees/`, `.no_exist/`; по умолчанию `~/.cache/huggingface/hub`, переопределяется `HF_HOME` / `HF_HUB_CACHE` — https://huggingface.co/docs/huggingface_hub/guides/manage-cache
- `blobs/<hash>` — реальные файлы; `snapshots/<commit>/<path>` — симлинки на блобы (одна и та же ревизия файла не качается дважды); `refs/main` — файл с commit-id; `trees/<commit>.json` — кэш листинга репозитория (один сетевой вызов на коммит вместо запроса на файл) — там же
- `.no_exist/<commit>/<path>` — пустые файлы-маркеры «этого файла на Hub нет», экономят по одному HTTP-запросу на опциональный файл — там же
- `CACHEDIR.TAG` в корне кэша — стандарт Cache Directory Tagging (исключение из бэкапов) — там же
- Общие блобы между репозиториями (Xet): `<CACHE_DIR>/blobs/<prefix>/<xet_hash>` + `<xet_hash>.refs` с перечнем репозиториев; `hf cache prune` удаляет неиспользуемые; при отсутствии симлинков (Windows) кэш деградирует до копий в `snapshots/` — там же
- Пиннинг ревизии: `resolve_revision()` возвращает `ResolvedRevision` (`str`-подкласс: строка = запрошенная, `.resolved` = commit); все последующие `hf_hub_download` идут по commit-hash, что гарантирует «все файлы из одного коммита» и отсутствие сети при повторной загрузке; маппинг ревизии пишется в `refs/` и работает офлайн — https://huggingface.co/docs/huggingface_hub/guides/manage-cache#pin-a-revision-advanced
- `snapshot_download` при недоступном Hub и неполном снапшоте бросает `IncompleteSnapshotError` вместо возврата частичной папки — там же

### 8. speech-swift (soniqo): загрузка — сильная, резидентность — отсутствует

- Кэш: `~/Library/Caches/<cacheDirName>/models/<org>/<name>` (через `HubApi.localRepoLocation`), с откатом на легаси-плоский путь `…/<org>_<name>`, если он уже наполнен; проверка легаси мемоизирована — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceDownloader.swift#L57-L95
- Полнота: `weightsExist` требует хотя бы один файл из `{safetensors, mlmodelc, mlpackage}` **и** наличие всех шардов, перечисленных в `model.safetensors.index.json` (`weight_map`) — комментарий прямо фиксирует наблюдавшийся баг «half-loaded model runs with random weights» — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceDownloader.swift#L96-L140
- Отбор файлов: globs `config.json`, `*.safetensors`, index, плюс `additionalFiles`; при наличии index — «сужение» через `applyingShardIndex` (репозиторий может публиковать те же тензоры дважды) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceDownloader.swift#L147-L225
- Офлайн-деградация: при сетевой ошибке загрузка не падает, если `cachedBundleSatisfies(globs:)` подтверждает полный кэш; «404 и checksum — это ответ, а не сбой сети» — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceDownloader.swift#L200-L250
- Один листинг на репозиторий: `GET /api/models/<id>/tree/<rev>?recursive=true&expand=true`, пагинация по `Link`-заголовку с жёстким лимитом страниц; `sha256` берётся из `lfs.oid` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceRepoManifest.swift#L40-L130 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceRepoManifest.swift#L155-L165
- Верификация: сначала размер, затем `sha256` файла; при несовпадении файл **удаляется** (иначе «bad file» принимается навсегда по размеру) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceTransfer.swift#L104-L124
- Staging: единый скрытый каталог `.incomplete` в корне кэша модели, имя = FNV-1a от repo-path + плоское имя (стабильно между процессами, в отличие от `hashValue`); комментарий объясняет, почему нельзя писать временные файлы внутрь `*.mlmodelc` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioCommon/HuggingFaceTransfer.swift#L325-L360
- Резидентность: `ModelCache` — actor, коалесцирующий параллельные загрузки по ключу `(ObjectIdentifier(type), identity)`, хранит `Task<Box>` **навсегда** (удаляет только при ошибке) → нет выгрузки, нет TTL, нет refcount — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelCache.swift#L1-L33
- Исполнение: `InferenceGate` — ограниченная FIFO-полоса (`maximumPending` = 32 по умолчанию), ожидающие отменяемы, при переполнении `AdmissionError.queueFull`; `ModelRuntime.withModel` грузит модель лениво под тем же permit'ом, `unload()` идёт через ту же полосу, `deinit` выгружает — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L3-L30 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L55-L90 , https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelRuntime.swift#L1-L43
- Реестр вариантов: `MODEL_REGISTRY: [ModelVariant]` с полями `{name, engine, modelId, aliases, kind, descriptor}`, `kind` ∈ `asr|tts|s2s|enhance|music|vad|diarize|speaker|separate|sr|turn`; разные квантования/движки одной модели — **отдельные строки** (например `qwen3-asr-0.6b-mlx-int4`, `qwen3-asr-1.7b-mlx-int8`, `qwen3-asr-coreml`) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/ModelRegistry.swift#L30-L120
- Дескриптор исполнения: `ModelDescriptor{id, modelID, backend: mlx|coreML|hybrid, inputSampleRate, streaming: buffered|incremental}` — то есть «операция» и «потоковость» — свойства артефакта, а не догадка по имени репозитория — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelDescriptor.swift#L1-L24
- Сервер держит `ModelCache` в состоянии и грузит по `modelId` (`cache.load(key: modelId)`) — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L541-L560

### 9. SpeechRail (hrygo): манифест артефактов, lock-файл, governor

- `ArtifactFile{path, size, sha256}` — путь валидируется как относительный, хеш — 64 hex — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L137-L155
- `QuantizationSpec{bits | dtype, group_size, format}`: `bits` и `dtype` взаимоисключающие; «unquantized» обязан объявить `dtype` (иначе UI не может сказать точность) — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L157-L183
- `SourceLocation{provider, repository, revision(40 hex)}` — источник обязателен, ревизия — полный git-SHA — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L185-L198
- `ModelArtifact{key, model_id, revision, family, variant, quantization, files[], sources[]}` + валидаторы: уникальность путей, обязательные `config.json` + `model.safetensors`, токенизатор (`tokenizer.json` либо `vocab.json`+`merges.txt`+`tokenizer_config.json`), для TTS — codec-конфиг и codec-веса, обязательное совпадение одной из `sources.revision` с `revision` артефакта — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L200-L263
- Привязка «профиль → артефакт» явная, без вывода из имени: `REQUIRED_SPEC_BINDINGS[(tier, role)] = artifact_key`, `tier` ∈ `fast|quality|reference`, `role` ∈ `asr|tts_base|tts_custom_voice|voice_design|alignment` — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L42-L56
- `RuntimeLock`: `{id, python, asr_requirements[], tts_requirements[], ffmpeg_artifact, file_hashes{}, engine_wheel}`; требования — строго `package==version --hash=sha256:…`, wheel движка пиннится по sha256/patch/build-inputs — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L275-L355
- `ModelCatalog{schema_version=2, artifacts[], specs[]}` с проверкой дублей ключей и полноты матрицы биндингов — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_catalog.py#L371-L410
- Реальный каталог: пофайловые `size`+`sha256` для каждого артефакта, `provider: modelscope`, ревизии 40-hex (пример: `aligner-bf16`, `model.safetensors` 1 835 539 240 байт) — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/assets/model-catalog.json#L1-L80
- Внешние корни моделей: `model_locations.json` (`{schema_version, bindings{key: abs_path}}`), путь обязан быть абсолютным, не симлинком, не внутри `app-home/models|diarization`, не содержать `.staging` — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/model_locations.py#L24-L100
- Staging никогда не может стать активной моделью: `_require_directory`/`_optional_directory` бросают `SelectionError`, если в пути есть `.staging`; отсутствие опционального артефакта **деградирует capability**, а не ломает выбор — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/config/selection.py#L148-L170
- Governor: классы работ `REALTIME_ASR|REALTIME_TTS|BATCH_ASR|BATCH_TTS|DIARIZATION`, отдельные ограниченные очереди, realtime-резерв, `resource_key`-полосы для TTS (одинаковый ключ сериализуется), карантин полосы при неподтверждённой очистке бэкенда — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L20-L60 , https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L104-L300
- Лимиты governor: `{total_capacity, realtime_reserved_capacity, max_pending_per_class, batch_aging_seconds}` с инвариантами `total>=2`, `1<=reserved<total` — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/domain/resource_limits.py#L9-L26
- Изоляция движков — подпроцессы с length-prefixed фреймами, ограниченный stderr-ринг (16 KiB), `_TERMINATE_GRACE_SECONDS = 2.0` — https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/worker_process.py#L1-L35

### 10. speaches: refcount + TTL, in-process

- `SelfDisposingModel`: `ref_count`, `RLock`, `expire_timer`; `unload()` отказывается при `ref_count>0`; `__enter__` грузит (если нужно) и инкрементит, `__exit__` декрементит; при 0 — `ttl>0` → `threading.Timer(ttl)`, `ttl==0` → немедленно, `ttl<0` → никогда — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/shared/base_model_manager.py#L37-L110
- `BaseModelManager`: `OrderedDict[str, SelfDisposingModel]` под `threading.Lock`, `load_model` идемпотентен, `unload_model` явный, колбэк `_handle_model_unloaded` удаляет из словаря — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/shared/base_model_manager.py#L112-L146
- TTL по типу задачи: `stt_model_ttl=300`, `tts_model_ttl=300`, `vad_model_ttl=-1` (не выгружать) — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/config.py#L51-L70
- `preload_models`: список id, качается последовательно на старте, при ошибке приложение завершается — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/config.py#L139-L145
- Хранилище — HF-кэш как есть: `HF_HUB_CACHE`, `models--<org>--<name>` (разбор имени), `snapshots/**` glob, удаление — `shutil.rmtree` каталога репозитория (ревизии не различаются) — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/hf_utils.py#L117-L196
- API моделей: `GET /v1/models` (локальные), `GET /v1/registry` (удалённые), `POST /v1/models/{id}` → 200 «downloaded» / 201 «already exists», `DELETE /v1/models/{id}`; gated-репозиторий → 401 с подсказкой про `HF_TOKEN` — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/models.py#L28-L110
- Абстракция реестра: `ModelRegistry.list_local_models/list_remote_models/get_model_files/download_model_files_if_not_exist` (наличие = «файлы находятся») — https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/model_registry.py#L8-L24

### 11. sherpa-onnx: реестра нет, модель = набор путей

- Модель задаётся C-структурой: `SherpaOnnxOfflineModelConfig{transducer{...}, paraformer{...}, whisper{...}, tokens, num_threads, debug, provider, model_type, modeling_unit, bpe_vocab, …}` — то есть пути к файлам + провайдер исполнения — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/c-api/c-api.h#L1074-L1099
- HTTP-клиента в библиотеке нет (`curl` не подключается нигде в `sherpa-onnx/`), ссылки на `huggingface.co` встречаются только в комментариях/тестовых данных — https://github.com/k2-fsa/sherpa-onnx/blob/532f1b5a8a207bf53b116b747207c1506696c843/sherpa-onnx/csrc/offline-ctc-model.cc#L80
- Следствие: каталог/загрузка/версии — целиком ответственность встраивающего приложения.

### 12. FluidAudio: ревизионный маркер, зеркала, содержимое-хеши

- `baseURL` переопределяется (`REGISTRY_URL` / `MODEL_REGISTRY_URL`), `repoOverrides` (longest-prefix) и `revisionOverrides` позволяют переехать на зеркало, не теряя пинов — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/ModelRegistry.swift#L32-L99
- Кэш: `~/Library/Application Support/FluidAudio/Models/…` — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/Download/ModelHub.swift#L58-L60
- Маркер `.fluidaudio-revision`: пиннинг ревизии требует точного совпадения маркера; кэши `main` без маркера считаются валидными (обратная совместимость) — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/Download/ModelCache.swift#L14-L33
- `prepareForDownload`: при смене ревизии каталог репозитория **удаляется целиком**, затем пишется маркер (то есть откат к предыдущей ревизии = повторное скачивание) — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/Download/ModelCache.swift#L212-L228
- Проверка содержимого: размер как предварительное условие + потоковый хеш (LFS `sha256` или git-blob `sha1` с заголовком `blob <size>\0`), чтение по 1 MiB — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/Download/ModelCache.swift#L133-L175
- `.partial`-файлы сохраняются при транзиентной сетевой ошибке, чтобы докачка по `Range` работала — https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Shared/Download/ModelHub.swift#L118-L130

### 13. WhisperKit, WhisperLiveKit, gotranscribesrv

- WhisperKit: `download(variant:downloadBase:from:)` → `hubApi.getFilenames(matching:)` для дизамбигуации варианта → `hubApi.snapshot(matching:)`; дефолтный репозиторий `argmaxinc/whisperkit-coreml` — https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/WhisperKit/Core/WhisperKit.swift#L253-L310
- WhisperKit `localRepoLocation = downloadBase/<type>/<repo.id>` — плоская папка на репозиторий, без `blobs/`+`snapshots/`, значит дедупликации файлов между репозиториями нет — https://github.com/argmaxinc/WhisperKit/blob/9f99b5cb6a907730543823ad271b82532782a899/Sources/ArgmaxCore/External/Hub/HubApi.swift#L333-L335
- WhisperLiveKit: CLI `wlk` с таблицей бэкендов (`BACKENDS`), картами «размер → HF-репозиторий» отдельно для faster-whisper и mlx-whisper (`FASTER_WHISPER_REPOS`, `MLX_WHISPER_REPOS`), display-каталогом `MODEL_CATALOG` (примерные `params`/`disk`) — https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/cli.py#L54-L245
- WhisperLiveKit обнаружение локальных моделей — сканирование HF-кэша (`huggingface_hub.scan_cache_dir`) плюс `~/.cache/whisper/*.pt` — https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/cli.py#L247-L270
- gotranscribesrv: собственного реестра нет; `LLMModel{id, kind, runtime, repo, status, preload, notes}` приходит из LLM-сайдкара через `GET /v1/models`, а действия `status|download|load|unload` проксируются как `POST /models/<id>/<action>` — https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/sidecar/llm.go#L25-L50 , https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/sidecar/llm.go#L222-L266

---

## Ограничения и риски

1. **Ollama не проверяет кэш-хиты.** `downloadBlob` возвращает `cacheHit=true` по одному `os.Stat`, и `PullModel` исключает такие блобы из цикла верификации. Значит битый/усечённый блоб, попавший на диск (например, обрыв ФС, ручное вмешательство, баг частичной записи), будет принят навсегда. Для speech-моделей весом 1–3 ГБ это реальный риск «тихо испорченной модели». Копировать эту оптимизацию без отдельной команды `verify` нельзя.
2. **Вытеснение занятого раннера.** `findRunnerToUnload` при отсутствии простаивающих берёт раннер с минимальным `sessionDuration` — даже если `refCount > 0` — и ждёт выгрузки. Для LLM-чата это приемлемо (запрос короткий), для потокового ASR/диаризации — нет: это означает «дождаться конца чужой 20-минутной сессии», либо, при принудительной выгрузке, обрыв чужого стрима.
3. **Вся бухгалтерия раннеров у Ollama в памяти.** Нет lease-файлов на диске: после падения и рестарта демона подпроцессы-сироты (llama-server/mlx-runner) остаются жить и держать память. Код защищает только от двойной выгрузки внутри одного процесса (сравнение `pid`).
4. **LM Studio — закрытый исходник.** Проверяемого кода нет: только документация. Формат каталога моделей, алгоритм `Auto-Evict`, «Model load streaming events» и оценка памяти не верифицируемы. Значения «60 минут TTL» и «не более 1 JIT-модели» — из документации, не из кода.
5. **speaches не различает ревизии.** Идентичность модели = HF repo id; удаление — `rmtree` всего каталога репозитория (все ревизии). TTL — глобальная настройка по типу задачи, бюджета памяти нет: две модели с TTL=-1 могут уронить процесс по OOM.
6. **speech-swift не выгружает ничего.** `ModelCache` держит `Task` с загруженной моделью до конца жизни процесса; `InferenceGate` сериализует доступ, но не освобождает память. Для сервера, обслуживающего несколько моделей, это гарантированный рост RSS до OOM.
7. **SpeechRail не имеет загрузчика.** Каталог/`RuntimeLock` описывают *что должно лежать*, но скачивание, верификация и материализация — вне репозитория (артефакты готовит оператор; внешние корни объявляются в `model_locations.json`). Значит оттуда берём схему данных, а не механику.
8. **FluidAudio теряет возможность отката.** `prepareForDownload` при смене ревизии удаляет каталог репозитория: две ревизии рядом не живут, откат = повторная загрузка (при недоступном Hub — невозможен).
9. **HF-кэш зависит от симлинков.** На ФС без симлинков кэш деградирует до копий (документировано), а «общие блобы» (Xet) требуют симлинк-раскладки и отключаются `HF_HUB_DISABLE_XET=1`. Планировать дедупликацию «через HF-кэш» как гарантию нельзя.
10. **Идентичность модели у всех разная.** Ollama: `(manifest digest, runner)`; speaches/WhisperKit: HF repo id; SpeechRail: `artifact.key` + `revision`; sherpa-onnx: набор путей. Ни одна схема не покрывает «одна логическая модель × N движков × M ревизий» одновременно — это придётся вводить самим, и это главный источник несовместимости при интеграции.
11. **`num_parallel` у Ollama — свойство загрузки**, а не запроса: разные клиенты с разными `num_ctx` заставят раннер перезагружаться (`needsReload` сравнивает опции). Для мультиклиента это thrash; у нас ключ инстанса должен включать набор load-опций, а не только артефакт.
12. **Оценки памяти — предсказания.** Ollama считает VRAM по `VRAMByGPU` и берёт минимум из предсказанного и репортируемого; LM Studio даёт `--estimate-only`. Ни то, ни другое не гарантирует фактический RSS/compressed memory на macOS при mmap и ANE-компиляции CoreML.

---

## Выводы для Runtime

### 4.1. Границы и принципы

1. **Runtime владеет тремя вещами**: каталогом артефактов на диске (CAS), резидентностью (какие веса сейчас в памяти и кто их держит) и очередями/бюджетом. Всё остальное — движки через адаптеры.
2. **Клиент явно указывает `model_id` (+ опционально `artifact_id`) и операцию.** Никакого выбора «по смыслу», никакого авто-pull на инференсе (Ollama тоже так делает: авто-pull есть только в `create`). Установка — отдельная явная операция, опционально `install_on_demand: true` в конфиге рантайма для доверенных клиентов.
3. **Два независимых автомата состояний**: *установка* (диск, персистентно) и *инстанс* (память, эфемерно). Смешивать их — источник багов «модель есть на диске, но её нельзя загрузить» (ср. `weightsExist` vs `cachedBundleSatisfies` у speech-swift).
4. **Иммутабельность**: артефакт и его файлы после публикации не меняются. Обновление = новый артефакт + переключение указателя. Откат = обратное переключение (у FluidAudio это невозможно — см. риск 8).
5. **Идентичность инстанса** = `(artifact_id, load_options_hash)`. Один артефакт может иметь несколько инстансов с разными опциями (например, `num_ctx`, `provider`, `quant`-оверрайды), каждый учитывается в бюджете памяти.

### 4.2. Схема манифеста

Один файл на **логическую модель-ревизию**; внутри — массив артефактов по движкам/квантованиям. Обязательные поля выбраны так, чтобы покрыть требования из задачи (id, версия, формат, движок, операции, хеши файлов, источник, лицензия, размер, требования) и повторить проверенные практики: `ArtifactFile`/`SourceLocation`/`QuantizationSpec` из SpeechRail, `runner`/`format`/`manifests[]` из Ollama, `streaming`/`backend` из speech-swift.

> Пример ниже **иллюстративный**: значения `repo`, `revision`, `sha256`, `size` условные и не описывают конкретный опубликованный репозиторий. Реальные значения берутся из `tree?expand=true` источника при сборке каталога (см. `fetchManifest` у speech-swift и `assets/model-catalog.json` у SpeechRail как образцы заполнения).

```json
{
  "schema_version": 1,
  "model_id": "qwen3-asr-0.6b",
  "display_name": "Qwen3-ASR 0.6B",
  "family": "qwen3_asr",
  "revision": "2026-10-21T00:00:00Z+9f2c1a",   
  "revision_semver": "1.2.0",
  "operations": ["asr.batch", "asr.stream"],
  "languages": ["ru", "en"],
  "license": {
    "spdx": "Apache-2.0",
    "file": "LICENSE",
    "gated": false,
    "requires_acceptance": false
  },
  "requirements": {
    "os": "macos",
    "arch": ["arm64"],
    "min_os_version": "14.0",
    "min_unified_memory_bytes": 8589934592,
    "disk_bytes": 1288490188
  },
  "default_artifact": "qwen3-asr-0.6b.mlx-4bit",
  "artifacts": [
    {
      "artifact_id": "qwen3-asr-0.6b.mlx-4bit",
      "engine": "mlx",
      "engine_abi": 1,
      "format": "safetensors",
      "quantization": { "scheme": "mlx", "bits": 4, "group_size": 64 },
      "operations": ["asr.batch", "asr.stream"],
      "streaming": "incremental",
      "concurrency": { "mode": "exclusive" },
      "platform": { "os": "macos", "arch": ["arm64"], "min_os_version": "14.0" },
      "footprint": {
        "weights_bytes": 419430400,
        "peak_resident_bytes": 943718400,
        "load_time_ms_p50": null
      },
      "files": [
        { "path": "config.json",          "size": 1204,      "sha256": "…64hex…" },
        { "path": "tokenizer.json",       "size": 11400000,  "sha256": "…64hex…" },
        { "path": "model.safetensors",    "size": 419430400, "sha256": "…64hex…" }
      ],
      "entry": {
        "config": "config.json",
        "weights": ["model.safetensors"],
        "tokenizer": "tokenizer.json"
      },
      "sources": [
        {
          "provider": "hf",
          "repo": "mlx-community/Qwen3-ASR-0.6B-4bit",
          "revision": "9f2c1a0b7d4e5f60718293a4b5c6d7e8f9012345",
          "paths": ["config.json", "*.safetensors", "tokenizer*"],
          "auth": "optional"
        }
      ]
    },
    {
      "artifact_id": "qwen3-asr-0.6b.coreml-int8",
      "engine": "coreml",
      "engine_abi": 1,
      "format": "mlmodelc",
      "quantization": { "scheme": "int8", "bits": 8, "group_size": null },
      "operations": ["asr.batch"],
      "streaming": "buffered",
      "concurrency": { "mode": "slots", "slots": 2 },
      "platform": { "os": "macos", "arch": ["arm64"], "min_os_version": "14.0" },
      "footprint": { "weights_bytes": 314572800, "peak_resident_bytes": 524288000 },
      "files": [
        { "path": "Encoder.mlmodelc/coremldata.bin", "size": 123456789, "sha256": "…64hex…" },
        { "path": "Decoder.mlmodelc/coremldata.bin", "size": 98765432,  "sha256": "…64hex…" },
        { "path": "config.json",                     "size": 1204,      "sha256": "…64hex…" },
        { "path": "tokenizer.json",                  "size": 11400000,  "sha256": "…64hex…" }
      ],
      "entry": { "config": "config.json", "bundles": ["Encoder.mlmodelc", "Decoder.mlmodelc"] },
      "sources": [
        {
          "provider": "hf",
          "repo": "FluidInference/qwen3-asr-coreml",
          "revision": "0123456789abcdef0123456789abcdef01234567",
          "paths": ["**"]
        }
      ]
    }
  ]
}
```

Пояснения к решениям в схеме:

| Поле | Зачем | Откуда образец |
|---|---|---|
| `schema_version` + строгая валидация | манифест — контракт между версиями демона; неизвестная версия = отказ, а не «попробуем» | SpeechRail `ModelCatalog.schema_version=2`; Ollama `Manifest.SchemaVersion` |
| `revision` (монотонная, с временем) + `revision_semver` | апдейт/откат и «какая версия сейчас активна» | SpeechRail `revision`; Ollama tag/digest |
| `operations[]` на модели и на артефакте | CoreML-вариант может не уметь стрим — клиент не должен узнать это из 500 | speech-swift `ModelDescriptor.streaming`, `kind` |
| `engine` + `engine_abi` | манифест, собранный под адаптер v2, не должен грузиться адаптером v1 | Ollama `runner` + `format`; FluidAudio `.fluidaudio-revision` |
| `quantization` с взаимоисключающими `bits` / `dtype` | «какая это точность» — обязательный атрибут артефакта, а не догадка по имени | SpeechRail `QuantizationSpec` |
| `files[].sha256` по каждому файлу | CAS-ключ и проверка целостности; для CoreML — пофайлово, т.к. бандл = каталог | SpeechRail `ArtifactFile`; HF `lfs.oid`; Ollama digest |
| `sources[]` c 40-hex ревизией и `paths` | пиннинг + возможность зеркала/нескольких провайдеров (HF, ModelScope, свой) | SpeechRail `SourceLocation`; FluidAudio `repoOverrides`/`revisionOverrides` |
| `footprint.peak_resident_bytes` | бюджет памяти и допуск до загрузки (admission) до того, как OOM случится | Ollama `VRAMByGPU`/`updateFreeSpace`; LM Studio `--estimate-only` |
| `concurrency{mode, slots}` | «одна сессия» vs «N слотов» — свойство движка, фиксируется при загрузке | Ollama `numParallel` (launch-time); speech-swift `InferenceGate` (exclusive) |
| `license` | блокировка установки до акцепта, атрибуция при редистрибуции | требование задачи; практика FluidInference NOTICE |

### 4.3. Раскладка на диске

```
$SR_HOME  (default ~/Library/Application Support/SpeechRuntime, override SR_HOME)
├── blobs/sha256/<xx>/<64hex>              # CAS, immutable, 0444; <xx> = первые 2 hex (как Xet-префикс)
├── trees/<64hex>.json                     # снимок листинга источника для (repo, revision)
├── manifests/<model_id>/<revision>.json   # опубликованные манифесты (immutable копии)
├── builds/<model_id>/<revision>/<artifact_id>/   # материализованный вид для движка
│      config.json -> ../../../../../blobs/...     # symlink или APFS clone
│      model.safetensors -> ...
│      Encoder.mlmodelc/                           # каталог: symlink на бандл в blobs
├── state/
│   ├── models/<model_id>.json             # указатель: active_revision, previous_revision, artifact states
│   ├── installs/<op_id>.json              # журнал незавершённых операций (для resume/recovery)
│   └── leases/<instance_key>.json         # runtime-lease: pid, port, artifact_id, started_at, engine
├── staging/<op_id>/<file_id>.part         # частичные загрузки (+ .part.json с {offset,size,etag})
├── locks/{registry.lock, model-<id>.lock} # flock: единственный писатель
├── cache/                                 # производное, можно удалить: coreml-compiled/, gguf-meta/, tokenizers/
└── logs/
```

Обоснование:

- `blobs` + `builds` = «контент» и «вид для движка» (аналог `blobs` + `snapshots` в HF-кэше, https://huggingface.co/docs/huggingface_hub/guides/manage-cache). Это даёт дедупликацию **между артефактами**: `config.json` и `tokenizer.json` у MLX- и CoreML-варианта совпадают по sha256 → лежат один раз. У Ollama это работает так же (общий config-слой между вариантами), у WhisperKit — нет.
- `builds/...` заполняется **symlink'ами** (портируемо) либо `clonefile(2)` на APFS (`[INFERENCE]`: предпочтительнее для движков, которые делают `realpath`/отказываются от симлинков; COW-копия не занимает места). Проверить на CoreML — см. «что измерить».
- `state/models/<model_id>.json` — единственная точка, которую читают клиенты «что установлено»; пишется только через `temp+rename` (Ollama `writeManifestBlob`/`replaceManifestPath`, https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/manifest/manifest.go#L1287-L1330).
- `state/installs/<op_id>.json` — журнал: `{op_id, model_id, revision, phase, files[{path, expected_sha256, bytes_done, part_path}], started_at, attempt}`. Нужен, чтобы после падения демона либо продолжить, либо убрать staging. Ollama обходится глобом `-partial-*` (https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/download.go#L131-L190) — работает, но не даёт «какая операция была какая»; для UI и для отмены журнал удобнее.
- `cache/` отделён от `blobs`, потому что CoreML-компиляция (`mlmodelc`) — производная и её нельзя считать частью верифицированного артефакта.

### 4.4. Автомат состояний

**A. Установка (персистентно, per `model_id` + `revision`)**

```
                    ┌──────────── resolve ok ────────────┐
                    │                                    v
 not_installed ─► resolving ─► downloading ─► verifying ─► materializing ─► installed
       ^              │             │  ^            │             │            │
       │              │ fail        │  │ resume     │ mismatch    │ fail       │ update available
       │              v             v  │            v             v            v
       └────────── failed ◄──── partial ┘         corrupt      failed      update_pending
                      │                                                         │
                      │ retry                                                   v
                      └──────────────────────────────────────────────────► updating (новый revision
                                                                            ставится рядом; active
                                                                            не меняется до публикации)
 installed ─► removing ─► not_installed
 installed ─► verify (ручная/фоновая) ─► installed | corrupt
```

| Состояние | Инвариант | Что можно делать |
|---|---|---|
| `not_installed` | нет `state/models/<id>.json` | install |
| `resolving` | есть журнал, нет байт | отменить; упасть → `failed` |
| `downloading` | есть `staging/<op>/…part` + журнал | отменить (staging сохраняется), резюмировать, читать прогресс |
| `verifying` | все файлы скачаны, идёт sha256 | только ждать/отменить (файлы ещё не в CAS) |
| `materializing` | блобы опубликованы, строится `builds/` | ждать; повтор идемпотентен |
| `installed` | `active_revision` указывает на этот revision, все `files` есть в CAS | load, remove, update |
| `update_pending` | установлена старая, доступна новая | load (старой), update, pin (запретить update) |
| `corrupt` | проверка `verify` не прошла | только remove/reinstall; load запрещён |
| `removing` | идёт удаление указателя | load запрещён, новые запросы → 404 |

Правила: `partial` (оборванная загрузка) — не отдельное состояние, а `downloading` с `bytes_done>0` и живым журналом; `failed` хранит последнюю ошибку и `attempts`, UI показывает «retry».

**B. Инстанс (в памяти, per `(artifact_id, load_options_hash)`)**

```
 cold ──load()──► loading ──ok──► warm ──idle TTL / evict──► draining ──refCount==0──► unloading ──► cold
   ▲                 │                │  ▲                                                  │
   │                 │ fail           │  └──── acquire (refCount++) ◄── request ───────────┘
   │                 v                │
   └── cooldown ── failed ◄───────────┘  (engine_crashed / load_error; артефакт → quarantine при повторах)
```

| Состояние | `refCount` | Допустимые переходы |
|---|---|---|
| `cold` | 0 | → `loading` (single-flight по ключу) |
| `loading` | 0..N (ждущие привязаны к загрузке) | → `warm` (всем ждущим выдан permit), → `failed` |
| `warm` | ≥0 | → `draining` (TTL/evict/manual), → `failed` (health check) |
| `draining` | ≥0, новые запросы получают `409 model_unloading` или ждут | → `unloading` при `refCount==0` |
| `unloading` | 0 | → `cold` |
| `failed` | 0 | → `cold` после cooldown; повторные падения → `quarantine` артефакта |

Отображение на изученное: `refCount`+`expireTimer`+`sessionDuration` — Ollama (https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L410-L455); `ref_count`+`threading.Timer` — speaches (https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/shared/base_model_manager.py#L37-L110); карантин полосы после неподтверждённой очистки — SpeechRail (https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/src/speechrail/runtime/resource_governor.py#L259-L280).

### 4.5. Lazy load, refcount, безопасная выгрузка

1. **Single-flight загрузки**: ключ `(artifact_id, load_options_hash)`; параллельные запросы получают один и тот же `Task`/future (speech-swift `ModelCache` — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/ModelCache.swift#L18-L32). Ошибка загрузки не кэшируется — иначе один сбой навсегда ломает модель (там же).
2. **Refcount на время всей операции, включая стрим** (не только на «получить модель»): permit освобождается после последнего байта/токена (speech-swift: «Release at the end of the entire inference operation, including streaming» — https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/SpeechRuntime/InferenceGate.swift#L92-L117).
3. **Выгрузка только при `refCount==0`** и только по одной из причин: `manual`, `ttl_expired`, `memory_pressure`, `update_cutover`, `idle_shutdown`. Причина пишется в лог и в `/instances` — иначе «модель исчезла» не диагностируется.
4. **Не вытеснять занятый инстанс.** При нехватке бюджета: (a) выгрузить `ttl_expired`; (b) выгрузить LRU-простаивающий; (c) подождать до `admission_timeout` (по умолчанию 30 с), затем `429/507` с `Retry-After`. Это осознанное отличие от Ollama (`findRunnerToUnload` берёт занятый раннер).
5. **TTL как политика, а не единственный механизм**: `keep_alive` на уровне запроса (Ollama: `sessionDuration` из `keep_alive`, 0 = выгрузить сразу после, отрицательное = навсегда — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/api/types.go#L1277-L1300), плюс дефолт на артефакт (`idle_ttl_seconds`), плюс глобальный дефолт.
6. **«Только загрузка» и «только выгрузка» — отдельные операции**, не трюк с пустым prompt (у Ollama это `prompt:""`+`keep_alive:0` → `done_reason:"unload"`, https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/routes.go#L477-L491): `POST /v1/instances/{artifact}/load`, `POST /v1/instances/{artifact}/unload`.
7. **Смена load-опций = новый инстанс.** Не перезагружать существующий под запрос другого клиента (у Ollama это `needsReload` → полная перезагрузка, https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L1423-L1470). Политика: `max_instances_per_artifact` (по умолчанию 2) + учёт в бюджете; при переполнении — `409 conflicting_load_options`.

### 4.6. Лимиты памяти, очереди, параллелизм

- **Бюджет**: `mem_budget = min(0.7 * physical_memory, physical_memory - 4 GiB)`, переопределяется в конфиге; учёт = `Σ peak_resident_bytes` по `warm`+`loading` инстансам. Заявка на загрузку — резервация `footprint.peak_resident_bytes` **до** старта (иначе два параллельных load'а пройдут проверку и вместе уронят процесс).
- **Очереди — по классам работ**, как в SpeechRail: `realtime_asr`, `batch_asr`, `diarization`, `embeddings`, `tts` (если появится). Отдельные границы (`max_pending_per_class`) и realtime-резерв, чтобы батч не занимал единственный слот интерактивного стрима. Батч FIFO с aging (`batch_aging_seconds`).
- **Параллелизм внутри инстанса** определяется `concurrency` в манифесте: `exclusive` (один запрос; остальные в очереди — speech-swift `InferenceGate`), `slots:N` (движок сам умеет N одновременных сессий; фиксируется при загрузке, как `OLLAMA_NUM_PARALLEL` — https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L539-L552), `shared_readonly` (эмбеддинги/диаризация: модель read-only, запросы параллельны).
- **Ограничение очереди с немедленным отказом** (а не бесконечным ожиданием): при переполнении — `429 queue_full` (Ollama: `ErrMaxQueue` вместо блокировки, https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/server/sched.go#L240-L253).
- **Отмена**: клиент отключился → отмена контекста запроса → (a) если запрос в очереди — вынуть и вернуть permit; (b) если выполняется — послать `cancel(request_id)` воркеру; если движок не умеет прерывать — пометить инстанс `tainted` и перезапустить воркер после завершения текущей операции. Освобождение permit — в `defer`/`deinit` (двойная защита от «повисшего» refcount).
- **Простые idle-накладные**: демон не держит потоков на модель; всё — event loop + один таймер на инстанс. При `idle_shutdown_seconds` и отсутствии warm-инстансов и активных клиентов — корректный выход (снимает leases), запуск по требованию через launchd socket activation `[INFERENCE]`.

### 4.7. Crash recovery: воркеры vs in-process

| Вариант | Плюсы | Минусы | Решение |
|---|---|---|---|
| Подпроцесс на инстанс (Ollama: `llama-server`, `mlx-runner`) | падение движка не убивает демон; можно убить зависший инференс; изоляция памяти; разные ABI/зависимости | +1 IPC-хоп, +latency на стрим, дублирование токенизаторов/буферов | **default** для MLX и ONNX-движков |
| In-process (speaches, speech-swift) | минимальная latency, нет IPC, проще отладка | падение = падение демона; нельзя прервать нативный вызов; утечка = утечка демона | только для маленьких моделей (VAD, speaker-embedding), флагом |

Механика (проверенная у Ollama + добавка для падения демона):

1. Lease-файл `state/leases/<instance_key>.json` пишется **до** spawn: `{instance_key, artifact_id, pid, port, engine, started_at, boot_id, argv_hash}`.
2. Воркер отдаёт `GET /health` → `{status: ok|loading|error}` (Ollama: `ok`/`loading model`/`no slot available`/`error`, https://github.com/ollama/ollama/blob/d44c2581904a98430e348d89cc2af1529b1808a0/llm/llama_server.go#L1238-L1295). Демон пингует раз в `health_interval` (5 с) и при неудаче: инстанс → `failed`, все держатели получают `engine_crashed` (retryable), lease удаляется, следующий запрос грузит заново (Ollama: `Ping` внутри `needsReload`).
3. **Старт демона**: скан `state/leases/*`; для каждого — если `boot_id` совпадает с текущим загрузочным и pid жив и `argv_hash` совпадает → `SIGKILL` (сирота от прошлого демона), иначе удалить lease. Это закрывает дыру Ollama (риск 3).
4. **Crash-loop breaker**: N падений одного артефакта за M минут → `quarantine` артефакта: `503 artifact_quarantined` до `POST /v1/registry/models/{id}/reset` (по образцу SpeechRail `quarantine_*_lane`).
5. **Не доверять `pid` без токена**: в argv воркера передаётся случайный `--lease-token`, чтобы не убить чужой процесс с совпавшим pid.

### 4.8. Атомарная установка, обновление, откат

Последовательность `install` (каждый шаг идемпотентен, повтор после падения безопасен):

1. `resolving`: получить манифест из каталога (встроенного/обновляемого) или из источника; зафиксировать `revision` источника (40-hex) и снять `trees/<sha>.json` (один листинг на репозиторий — как `fetchManifest` у speech-swift).
2. `downloading`: файлы в `staging/<op_id>/<file_id>.part`; при ≥32 MiB — нарезка на части с `Range` и состояние в `.part.json` (Ollama: 16 частей, 100–1000 MB). Дедупликация: если `blobs/sha256/<xx>/<hex>` уже есть — файл не качается (проверка по манифесту, а не по ответу сервера).
3. `verifying`: полный sha256 каждого файла; несовпадение → удалить part, `failed` (Ollama `verifyBlob`, speech-swift `verifyDownloaded`, FluidAudio `hasMatchingContent`).
4. Публикация в CAS: `rename(part → blobs/sha256/<xx>/<hex>)` (в пределах одной ФС, атомарно), `chmod 0444`, `fsync` файла и каталога.
5. `materializing`: `builds/<model>/<rev>/<artifact>/` — symlink/clone на блобы; для CoreML — компиляция в `cache/coreml-compiled/<sha>/…` и symlink бандла.
6. Публикация указателя: `manifests/<model>/<rev>.json` (immutable) → `state/models/<id>.json` через `temp + rename` **последним** шагом. До этого шага модель «не установлена» для читателей — это и есть точка атомарности.
7. GC: `state/models` → множество достижимых digest'ов; удалить неиспользуемые блобы старше `blob_grace_period` (например 24 ч) и не входящие в активные `installs`. Mark-and-sweep как у Ollama (`retainedBlobDigestsLocked` + `RemoveUnreferencedBlobs`), но с grace-периодом — иначе гонка «GC удалил блоб, который только что скачал другой клиент» (у Ollama от неё защищает `manifestStoreMu` + пересчёт под тем же мьютексом; у нас — grace + flock).

`update`: полностью поставить новый revision рядом (шаги 1–6), не трогая `active_revision`; в конце — атомарно переписать указатель и записать `previous_revision`. Старый `builds/` не удаляется до успешного «прогрева» нового (первый успешный `load`) либо до `retain_revisions` (по умолчанию 1 предыдущая).

`rollback`: переключить `active_revision` на `previous_revision` (один `rename`), предварительно проверив `engine_abi` и наличие всех блобов (иначе — `409 rollback_unavailable: blobs missing`). Никаких копирований данных.

`remove`: удалить `state/models/<id>.json` → GC. Если инстанс `warm` — сначала `unload` (или `409 in_use` с `refCount`, если клиент не разрешил force).

### 4.9. Адаптер движка (контракт)

```swift
protocol EngineAdapter {                    // один адаптер = один engine id ("mlx", "coreml", "onnx")
    var engineID: String { get }
    var abiVersion: Int { get }
    func probe() -> EngineAvailability      // загрузилась ли библиотека/рантайм (Ollama: mlx.CheckRuntime)
    func plan(artifact: BuildRef, options: LoadOptions) throws -> LaunchPlan   // argv/env/ports/files
    func load(_ plan: LaunchPlan, progress: @escaping (Double) -> Void) async throws -> InstanceHandle
    func run(_ h: InstanceHandle, request: OperationRequest, sink: OutputSink) async throws
    func cancel(_ h: InstanceHandle, requestID: String) async throws
    func health(_ h: InstanceHandle) async -> Health
    func unload(_ h: InstanceHandle) async
}
```

Прообраз — `llm.LlamaServer` у Ollama с двумя реализациями (`llama-server` и `mlx-runner`) за одним интерфейсом (`Ping`, `Completion`, `Close`), плюс `sherpa-onnx`, где «план» — это структура путей + `provider` + `num_threads`. Важно: **`LaunchPlan` — единственное место, где адаптер знает про раскладку файлов**; реестр не знает, что такое `Encoder.mlmodelc`.

### 4.10. API (стабильная поверхность)

| Метод | Путь | Назначение | Ошибки |
|---|---|---|---|
| GET | `/v1/registry/models?op=asr.stream&lang=ru` | каталог: установлено/доступно/размер/лицензия | — |
| GET | `/v1/registry/models/{id}` | манифест + состояние установки + артефакты | 404 |
| POST | `/v1/registry/models/{id}/install` | SSE-стрим прогресса; `?revision=&artifact=` | 409 already_installing, 402 license_required |
| POST | `/v1/registry/installs/{op}/cancel` | отмена с сохранением staging | 404 |
| DELETE | `/v1/registry/models/{id}` | удалить (с `?force=true` при warm) | 409 in_use |
| POST | `/v1/registry/models/{id}/verify` | пересчёт sha256 | 200/409 corrupt |
| GET | `/v1/instances` | аналог `/api/ps`: artifact, state, refCount, expiresAt, residentBytes, engine, pid | — |
| POST | `/v1/instances/{artifact}/load` | preload c `keepAlive`, `options` | 507 budget, 429 queue_full |
| POST | `/v1/instances/{artifact}/unload` | выгрузка (drain) | 409 busy |
| POST | `/v1/asr/transcriptions` | батч-ASR (`model`, `artifact?`, `language`) | 404 not_installed, 503 quarantined |
| WS | `/v1/asr/stream` | стрим-ASR (сессии, `session.update` как в speech-swift Realtime) | — |
| POST | `/v1/vad`, `/v1/diarize`, `/v1/embeddings/speaker` | остальные операции | — |
| GET | `/v1/health`, `/v1/metrics` | готовность, счётчики очередей/памяти | — |

Коды ошибок фиксированы и машиночитаемы (`error.code`): `model_not_installed`, `model_loading`, `model_unloading`, `artifact_incompatible` (engine_abi/платформа), `conflicting_load_options`, `budget_exceeded`, `queue_full`, `artifact_quarantined`, `engine_crashed`, `checksum_mismatch`, `license_required`.

### 4.11. Что взять / не брать

**Взять:**
- Ollama: CAS-блобы с `sha256`-именем и `rename`-публикацией; манифест-лист с `runner`; pull с 16 частями и JSON-состоянием; mark-and-sweep GC; `refCount`+`expireTimer`+`sessionDuration`; `needsReload` через `Ping`; движки-подпроцессы с `/health`; атомарная запись указателей `temp+rename`.
- HF: `blobs`/`snapshots`/`refs`/`trees`/`.no_exist`; пиннинг ревизии одним `resolve_revision`; `CACHEDIR.TAG`; `IncompleteSnapshotError` вместо частичного снапшота.
- speech-swift: один tree-запрос; `sha256` из `lfs.oid`; staging вне дерева модели; проверка шардов по index; отказ от неполного кэша при офлайне; `ModelDescriptor.streaming`; `InferenceGate` как «полоса на модель».
- SpeechRail: `ArtifactFile`/`SourceLocation`/`QuantizationSpec`; явные биндинги tier×role; `RuntimeLock` с `--hash`; запрет `.staging` как активной модели; governor с классами работ и realtime-резервом; карантин полосы.
- speaches: минимальная и понятная механика `ref_count` + TTL, включая `ttl==0`/`ttl<0`.
- FluidAudio: `.fluidaudio-revision`-маркер как дешёвая проверка «кэш от нужной ревизии»; `repoOverrides`/`revisionOverrides` для зеркал; проверка контента по sha256/sha1 потоково.

**Не брать:**
- Вытеснение занятого инстанса (Ollama) — ждать/отказывать.
- `cacheHit` без верификации (Ollama) — верификация при установке обязательна, при загрузке — быстрый stat + опциональный `verify`.
- Бухгалтерию только в памяти (Ollama) — дисковые leases.
- `Auto-Evict` с лимитом «1 модель» (LM Studio) — мультиклиентный сервис так не работает.
- TTL как единственную политику (speaches, LM Studio) — нужен ещё бюджет памяти и ручное управление.
- Удаление каталога при смене ревизии (FluidAudio) — держим N ревизий.
- Отсутствие реестра (sherpa-onnx, WhisperKit, gotranscribesrv) — реестр обязателен.
- Плоскую папку на репозиторий без `blobs` (WhisperKit) — теряется дедупликация `config.json`/`tokenizer.json`.

---

## Неизвестные / что измерить

1. **Реентерабельность движков.** Неизвестно, можно ли в одном процессе MLX/Swift вести два одновременных декодирования (и то же для CoreML `MLModel` с `MLPredictionOptions`). От этого зависит `concurrency.mode` в манифестах. Измерение: два параллельных стрима на одном инстансе, замер interleaving и RSS.
2. **Фактический footprint vs `peak_resident_bytes`.** Заявленный размер весов ≠ RSS на macOS (mmap, compressed memory, ANE-копии). Измерение: `phys_footprint` до/во время/после загрузки для MLX-4bit, CoreML-int8, ONNX-int8.
3. **Время холодной загрузки и стоимость CoreML-компиляции.** Первый запуск `mlmodelc` может компилироваться десятки секунд; нужно решить, кэшировать ли результат в `cache/coreml-compiled` и считать ли это частью `install`. Замер: cold load p50/p95 по артефактам.
4. **Симлинки vs `clonefile` для CoreML-бандлов.** Не проверено, принимает ли CoreML `.mlmodelc`, внутри которого симлинки на блобы, и не ломает ли это code-signing/валидацию. Измерение: загрузить бандл, собранный (а) симлинками, (б) `clonefile`, (в) копией.
5. **Стоимость полной верификации.** sha256 1–3 ГБ: сколько секунд, и допустимо ли это на каждом старте. Измерение на реальном диске (APFS, NVMe).
6. **Латентность отмены по движкам.** Умеет ли MLX-раннер прерывать генерацию по `cancel`, и что происходит с CoreML-инференсом, который нельзя прервать. Измерение: время от `cancel` до освобождения permit.
7. **Цена подпроцесса для маленьких моделей.** Для VAD/speaker-эмбеддингов IPC может быть дороже самого инференса. Измерение: p50 latency in-process vs subprocess для 10-секундного аудио.
8. **Дедупликация между артефактами на практике.** Ожидание: `config.json`+`tokenizer.json` общие у MLX/CoreML-вариантов; вес — нет. Точный процент экономии неизвестен, пока нет реальных манифестов.
9. **Поведение LM Studio при `ttl` и `Auto-Evict` в реальности** (не по докам): остаётся ли модель при активном стриме, как ведёт себя `--estimate-only` на Apple Silicon. Проверяется только эмпирически на установленном приложении — кода нет.
10. **Наличие манифест-листов `runner` у реальных моделей в registry.ollama.ai.** Код поддерживает список, но не проверено, для скольких публичных моделей он реально используется (влияет на оценку «готовности паттерна»).
11. **Лицензионные метаданные в манифестах.** Собственный `license.spdx` надо заполнять по артефакту, а не по семейству (см. расхождения Sortformer v1/v2.1 в `K-licenses-maintenance.md`). Пока не проверено по каждому кандидату каталога — это задача волны лицензий.
12. **Готовность движков к «одна модель — несколько файлов разных форматов».** Например, ONNX-модели sherpa-onnx требуют ещё `tokens.txt`/`lexicon`/`bpe.model` — точный набор для каждого кандидата надо вычитывать из его конфигурации, а не угадывать (в манифесте это `entry`/`files`).

---

## Источники

**Код (локальные клоны, SHA зафиксированы)**

| Проект | SHA | Что читалось |
|---|---|---|
| ollama/ollama | `d44c2581904a98430e348d89cc2af1529b1808a0` | `manifest/{manifest,layer,paths}.go`, `server/{sched,images,download,routes,model,speech}.go`, `llm/llama_server.go`, `mlxrunner/client.go`, `envconfig/config.go`, `api/types.go`, `middleware/openai.go`, `types/model/capability.go` |
| soniqo/speech-swift | `cbad3be29480825707890490f446b19d7288be2d` | `Sources/SpeechRuntime/{ModelCache,ModelRuntime,ModelDescriptor,InferenceGate}.swift`, `Sources/AudioCommon/{HuggingFaceDownloader,HuggingFaceTransfer,HuggingFaceRepoManifest,ModelLoader}.swift`, `Sources/AudioServer/{ModelRegistry,AudioServer}.swift` |
| hrygo/SpeechRail | `cdccc74084bc94f8c996b5b71a386455640ad1cb` | `src/speechrail/config/{model_catalog,model_locations,selection}.py`, `src/speechrail/assets/{model-catalog,runtime-lock}.json`, `src/speechrail/runtime/{resource_governor,worker_process,registry,limits}.py`, `src/speechrail/domain/resource_limits.py` |
| speaches-ai/speaches | `993994f7984bf3fe9655b267448328cf66fccb42` | `src/speaches/{config,hf_utils,model_registry}.py`, `src/speaches/executors/shared/base_model_manager.py`, `src/speaches/routers/models.py` |
| FluidInference/FluidAudio | `1b0ed50d686873128a4129b3510ce3914c849bab` | `Sources/FluidAudio/ModelRegistry.swift`, `Sources/FluidAudio/Shared/Download/{ModelCache,ModelHub}.swift` |
| argmaxinc/WhisperKit | `9f99b5cb6a907730543823ad271b82532782a899` | `Sources/WhisperKit/Core/{WhisperKit,Configurations}.swift`, `Sources/ArgmaxCore/External/Hub/HubApi.swift` |
| QuentinFuxa/WhisperLiveKit | `eb92c115ee7abc1e161b0b2159da31785b0f5566` | `whisperlivekit/cli.py` |
| k2-fsa/sherpa-onnx | `532f1b5a8a207bf53b116b747207c1506696c843` | `sherpa-onnx/c-api/c-api.h`, `sherpa-onnx/csrc/offline-ctc-model.cc` |
| sagostin/gotranscribesrv | `a5f4289078553ba66f83510c62146043bed91a7a` | `internal/sidecar/llm.go`, `internal/config/config.go` |

**Документация (web)**

- LM Studio, Idle TTL и Auto-Evict: https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict
- LM Studio, `lms load` / `lms unload` (`--ttl`, `--estimate-only`): https://lmstudio.ai/docs/cli/local-models/load
- LM Studio, `lms` (обзор команд): https://lmstudio.ai/docs/cli
- LM Studio, `lms get`: https://lmstudio.ai/docs/cli/local-models/get
- LM Studio, `lms ls`: https://lmstudio.ai/docs/cli/local-models/ls
- LM Studio, native REST API v1 (models/load/unload/download): https://lmstudio.ai/docs/developer/rest
- LM Studio CLI (исходники клиента, MIT): https://github.com/lmstudio-ai/lms
- Hugging Face, Understand caching (blobs/snapshots/refs/trees/.no_exist, shared blobs, пиннинг ревизии): https://huggingface.co/docs/huggingface_hub/guides/manage-cache

**Связанные материалы проекта**

- `research/wave1/K-licenses-maintenance.md` — лицензии артефактов и моделей (для заполнения `license.spdx` в манифесте).
