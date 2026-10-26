# H — API и транспорт локального Speech Runtime: контракт v1

Дата: 2026-10-25. Метод: чтение исходников локальных клонов (`/tmp/srr/repos`, `/tmp/srr/extra`)
+ официальные документы и спецификации. Версии зафиксированы SHA:

| Репозиторий | SHA |
|---|---|
| `soniqo/speech-swift` | `cbad3be29480825707890490f446b19d7288be2d` |
| `hrygo/SpeechRail` | `cdccc74084bc94f8c996b5b71a386455640ad1cb` |
| `k2-fsa/sherpa-onnx` | `532f1b5a8a207bf53b116b747207c1506696c843` |
| `QuentinFuxa/WhisperLiveKit` | `eb92c115ee7abc1e161b0b2159da31785b0f5566` |
| `speaches-ai/speaches` | `993994f7984bf3fe9655b267448328cf66fccb42` |
| `sagostin/gotranscribesrv` | `a5f4289078553ba66f83510c62146043bed91a7a` |
| `FluidInference/FluidAudio` | `1b0ed50d686873128a4129b3510ce3914c849bab` |
| `argmaxinc/WhisperKit` | `9f99b5cb6a907730543823ad271b82532782a899` |
| `rhasspy/wyoming` | `08a0c258df83fea6e1643bcfe67e86243f01cf31` |
| `grpc/grpc-swift-nio-transport` | `9765a256477383b9d441f20952118b75104304e8` |

Всё, что не подтверждено кодом, официальной спецификацией или зафиксированным захватом трафика,
помечено `[INFERENCE]`. Числа производительности в отчёте не приводятся, если они не взяты
из первоисточника.

---

## Итог

1. **Три оси, а не одна.** Транспорт (UDS / TCP loopback), фрейминг (HTTP, WebSocket, gRPC)
   и семантика (имена событий) независимы. Именно поэтому один runtime может одновременно
   отдавать нативный протокол, [OI]-совместимый и Deepgram-совместимый: совместимость — это
   *переименование событий и переупаковка кадров*, а не второй сервер. Полностью эту границу
   не провёл никто из изученных: у WhisperLiveKit три независимых обработчика (`/asr`,
   `/v1/listen`, REST), у speaches два (REST + realtime), у SpeechRail один, но без
   совместимых слоёв вообще. Ближе всех к переиспользованию speaches — его realtime-сессия
   ходит HTTP-клиентом в собственный `/v1/audio/transcriptions` по loopback, но переиспользует
   только инференс, а не семантику событий.

2. **Единственный честный примитив частичной гипотезы — «замена с ревизией», а не «дельта».**
   `transcript.text.delta` в [OI] append-only; Wyoming прямо пишет, что `transcript-chunk`
   *заменяет* предыдущий; Deepgram и AssemblyAI присылают полный текст сегмента/хода;
   Google отдаёт `stability` 0.0–1.0. SpeechRail нашёл правильную формулировку: append-only
   дельта допустима только для **доказуемо стабильного префикса**. Контракт обязан иметь
   `revision` + `stable_prefix_chars` и не притворяться, что частичный текст монотонен.

3. **Частичные результаты в изученных [OI]-совместимых серверах отсутствуют как класс.**
   speaches в realtime-сессии делает один HTTP-вызов `/v1/audio/transcriptions` по
   VAD-обрезанному буферу и отдаёт только `conversation.item.input_audio_transcription.completed`;
   speech-swift тоже отдаёт только `.completed`. То есть «[OI]-совместимость» сегодня означает
   «batch-точность с задержкой до конца фразы», и это надо честно писать в контракте, а не
   изображать потоковость.

4. **Аудио на проводе должно быть бинарным.** Deepgram (захвачено) и WhisperLiveKit (документировано)
   шлют сырой PCM бинарными кадрами WebSocket; base64 в JSON — это +33 % байт и лишний
   кодек-цикл на каждом кадре. Base64 остаётся только там, где его требует чужой протокол
   ([OI] Realtime, `input_audio_buffer.append` до 15 MiB на событие).

5. **Backpressure в WebSocket не существует на уровне протокола** — RFC 6455 не имеет окон.
   Значит контракт обязан сам объявить: лимит входящего аудио (`max_inflight_audio_ms`),
   право сервера отбрасывать *только* частичные гипотезы, запрет отбрасывать финалы и
   правило «финал не доставлен → закрыть сессию с 1011». gRPC/HTTP2 даёт flow control
   бесплатно — это его главное преимущество перед WS.

6. **Ошибки: `application/problem+json` (RFC 9457) на нативных путях, конверт [OI] — на
   [OI]-путях.** Смешивать нельзя: клиент, пришедший с `openai-python`, ждёт `{"error":{...}}`.
   Машинный код ошибки должен быть один на всю систему и совпадать в HTTP `type`, в WS
   `error.code` и в gRPC `details` — иначе три транспорта разъедутся.

7. **Аутентификация локального клиента — это не «доверенная машина».** Любой процесс любого
   пользователя и любая веб-страница в браузере на этой машине может открыть `127.0.0.1`.
   Отсюда: bind только на loopback, обязательный bearer-токен из файла с правами 0600 для TCP,
   права на файл сокета + `getpeereid()`/`LOCAL_PEERCRED` для UDS, отказ от токена в query.

8. **UDS — правильный транспорт, но не для Swift.** `sun_path` на macOS равен 104 байтам
   (проверено в SDK), `URLSession` не умеет адресовать unix-сокет (документированной URL-схемы
   нет; поддержка UDS в новом загрузчике заявлена, но публичного API адресации не описано),
   а `Network.framework` умеет (`NWEndpoint.unix(path:)`). Для Rust UDS бесплатен (tonic
   `connect_with_connector`), для Swift — это свой HTTP/WS-стек. Отсюда рекомендация:
   **один протокол, два адреса** — TCP loopback как основной для Swift/браузера, UDS как
   первый класс для Rust/CLI/демон-демон.

9. **gRPC bidi — лучший *второй* транспорт, а не первый.** Он единственный даёт типизированный
   контракт с кодогенерацией для Swift и Rust, мультиплексирование сессий в одном соединении,
   flow control и дедлайны. Цена — protoc в сборке, невозможность отладки через `curl`,
   отсутствие bidi в браузерах. Google STT прямо пишет, что `StreamingRecognize` доступен
   только через gRPC, не через REST.

10. **XPC и Wyoming — не транспорты runtime, а точки интеграции.** XPC привязывает к
    launchd/Mach и к Apple-платформе навсегда (Swift on Linux его не имеет); Wyoming не имеет
    аутентификации *по дизайну* и его полезно поддержать только как адаптер к Home Assistant.
    Ни один из них не должен влиять на форму основного контракта.

---

## Факты

### 1. [OI] HTTP audio API: параметры и форматы

Актуальная спецификация — `openai/openai-openapi` (`master` на 2026-10-25), `POST /audio/transcriptions`
принимает `multipart/form-data` с обязательными `file` и `model`:

- `model` — enum `whisper-1`, `gpt-transcribe`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`,
  `gpt-4o-mini-transcribe-2025-12-15`, `gpt-4o-transcribe-diarize`.
- `language` (ISO-639-1), `languages[]` (только `gpt-transcribe`), `keywords[]` (только `gpt-transcribe`),
  `prompt`, `temperature` (default 0), `include[]` = `logprobs`, `timestamp_granularities[]` =
  `word|segment` (default `segment`), `stream` (default false), `chunking_strategy`
  (`auto` | `VadConfig`), `known_speaker_names[]` / `known_speaker_references[]` (до 4, каждый
  сэмпл 2–10 с, data URL).
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L49181-L49344
- `AudioResponseFormat` = `json | text | srt | verbose_json | vtt | diarized_json` (default `json`);
  для `gpt-4o-transcribe*` поддержан только `json`, для `gpt-4o-transcribe-diarize` —
  `json | text | diarized_json`.
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L41107-L41123
- `verbose_json` требует `language`, `duration`, `text`; опционально `words[]`, `segments[]`,
  `usage`. `segments[]` содержит `id, seek, start, end, text, tokens[], temperature, avg_logprob,
  compression_ratio, no_speech_prob`.
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L49485-L49545
- `diarized_json` = `{task, duration, text, segments[], usage}`, где сегмент
  `{type:"transcript.text.segment", id, start, end, text, speaker}`; `speaker` — строка
  (`"agent"`, `"A"`, …), не число.
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L49342-L49430
- `VadConfig` = `{type:"server_vad", prefix_padding_ms:300, silence_duration_ms:200, threshold:0.5}`
  — те же имена и почти те же дефолты, что у `turn_detection` в Realtime (там `silence_duration_ms`
  по умолчанию 500).
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L79683-L79712
- Коды ответа, которые спецификация объявляет: 400 (плохие параметры/аудио), 401, 403, 413
  (слишком большой файл), 429 (rate limit), 500 (не удалось обработать), 502 (upstream),
  503 (сервис недоступен).
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L810-L872

Локальные реализации воспроизводят этот контракт с расширениями:

- speaches: `POST /v1/audio/transcriptions` с `model, language, prompt, response_format,
  temperature, timestamp_granularities[], stream` **плюс нестандартные** `hotwords` и
  `without_timestamps`; VAD прогоняется *до* транскрипции, а `timestamp_granularities[]`
  читается вручную из формы, потому что `Form(alias=...)` не работает.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/stt.py#L118-L173
- SpeechRail держит тот же путь и формат, но `model` по умолчанию — собственный артефакт,
  а перегрузка отдаётся как `429` + `queue_full` + заголовки `SpeechRail-Busy-Reason` /
  `SpeechRail-Retry-Hint`.
  https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/openapi.yaml#L197-L262
- WhisperLiveKit поднимает `[OI]`-совместимый REST рядом с двумя WebSocket-эндпоинтами и
  ограничивает тело запроса 512 МиБ (`_MAX_AUDIO_BYTES = 512 * 1024 * 1024`).
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/basic_server.py#L253

### 2. [OI] `stream=true`: это SSE, а не потоковая транскрипция

Спецификация описывает `stream` так: «the model response data will be streamed to the client
as it is generated using server-sent events», и отдельно: «Streaming is not supported for the
`whisper-1` model and will be ignored».
https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L49289-L49303

Формат события потока — `CreateTranscriptionResponseStreamEvent` = anyOf
`transcript.text.delta` | `transcript.text.done` | `transcript.text.segment`:

```
data: {"type":"transcript.text.delta","delta":" see","logprobs":[{"token":" see","logprob":-3.1281633e-7,"bytes":[32,115,101,101]}]}
...
data: {"type":"transcript.text.done","text":"I see skies of blue ...","usage":{"type":"tokens",...}}
```

- `transcript.text.delta` обязателен в части `{type, delta}`; `logprobs` — только при
  `include[]=logprobs`; `segment_id` — только для `gpt-4o-transcribe-diarize`.
- `transcript.text.done` обязателен в части `{type, text}`, несёт `languages[]` и `usage`.
- `transcript.text.segment` (только `diarized_json` + `stream=true`) — `{type, id, start, end,
  text, speaker}`.
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L78071-L78230
  и пример потока https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L1044-L1120

Важное следствие: **SSE здесь — доставка результата одного файла по частям, а не приём аудио
потоком.** Клиент не может ничего отправить обратно: отмена = разрыв соединения.

Как это реализуют локально:

- speaches отдаёт `StreamingResponse` из генератора `format_as_sse(x.model_dump_json())`,
  где `format_as_sse` — буквально `f"data: {data}\n\n"`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/text_utils.py#L25-L26
- Сам генератор (`handle_streaming_transcription_request`) сначала грузит модель и запускает
  `BatchedInferencePipeline.transcribe(...)` по всему файлу с `clip_timestamps` из VAD, и лишь
  затем по одному выдаёт `TranscriptionTextDeltaEvent` на сегмент, в конце —
  `TranscriptionTextDoneEvent`, склеенный из уже полученных сегментов.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/whisper.py#L187-L226
  То есть «поток» начинается после загрузки модели и первого декодированного сегмента; это
  сегментный стриминг, а не посимвольный.

### 3. [OI] Realtime transcription sessions

REST-часть: `POST /v1/realtime/transcription_sessions` создаёт эфемерный токен и возвращает
объект сессии. Пример ответа из спецификации:

```json
{
  "id": "sess_BBwZc7cFV3XizEyKGDCGL",
  "object": "realtime.transcription_session",
  "modalities": ["audio", "text"],
  "turn_detection": {"type": "server_vad", "threshold": 0.5,
                     "prefix_padding_ms": 300, "silence_duration_ms": 200},
  "input_audio_format": "pcm16",
  "input_audio_transcription": {"model": "gpt-4o-transcribe", "language": null, "prompt": ""},
  "client_secret": {"value": "ek_abc123", "expires_at": 1742188264}
}
```
https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L21406-L21495

Конфигурация сессии (beta-форма, плоская): `turn_detection` (только `server_vad`),
`input_audio_noise_reduction`, `input_audio_format` = `pcm16 | g711_ulaw | g711_alaw`
(**`pcm16` = 16-bit mono 24 kHz little-endian**), `input_audio_transcription` (`model`, `language`,
`prompt`), `include[]` = `item.input_audio_transcription.logprobs`.
https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L69063-L69160

GA-форма (используется новыми клиентами и SpeechRail): `{type:"transcription", audio:{input:{format,
transcription, noise_reduction, turn_detection}}, include[]}` — то есть конфиг вложен в `audio.input`.
https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L69163-L69222

События:

- `input_audio_buffer.append` — `{event_id?, type, audio}` где `audio` — base64; «maximum of 15 MiB»
  на событие; «the server will not send a confirmation response to this event».
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L60281-L60320
- `conversation.item.input_audio_transcription.delta` — `{event_id, type, item_id, content_index,
  delta, logprobs?}`; `delta` — «The text delta», без ревизий.
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L61300-L61336
- `conversation.item.input_audio_transcription.completed` / `.failed` — терминальные события,
  привязанные к `item_id`.
- `turn_detection` (GA): `server_vad` с `threshold` (0.5), `prefix_padding_ms` (300),
  `silence_duration_ms` (500), `create_response`, `interrupt_response`, `idle_timeout_ms`
  (5000–30000), либо `semantic_vad`. Отдельно оговорено: «For `gpt-realtime-whisper` transcription
  sessions, turn detection must be set to `null`; VAD is not supported».
  https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L70347-L70455

Последняя оговорка — прямой аргумент за то, что **возможности обязаны быть per-model**, а не
per-API: одна и та же ручка `turn_detection` для одной модели обязательна-пустая, для другой
обязательна-заполненная.

### 4. Что реально делают локальные [OI]-совместимые серверы

**speaches** (Python/FastAPI) — самый полный [OI]-совместимый набор:

- Роутеры: `/v1/chat/completions`, `/v1/audio/transcriptions`, `/v1/audio/translations`,
  `/v1/audio/speech`, `/v1/audio/speech/timestamps`, `/v1/audio/speech/embedding`, `/v1/models`,
  `/v1/realtime`, `/health`, `/api/ps`. WebSocket-роутер подключается **без** HTTP-зависимостей
  аутентификации, а `/health` — публичный.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/main.py#L158-L186
- Аутентификация в WS делается вручную **до** `accept()`, и токен принимается из query-параметра
  `?api_key=`, из `Authorization: Bearer` или из `X-API-Key`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/utils.py#L53-L75
  При ошибке — `close(code=1008, reason="Authentication failed")` без `accept()`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/realtime_ws.py#L78-L84
- Внутри realtime-сессии сервер **сам себе** делает HTTP-клиент на loopback
  (`base_url=f"{loopback}/v1"`) и переиспользует обычный `/v1/audio/transcriptions`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/realtime_ws.py#L86-L100
- `input_audio_buffer.append` декодирует base64 как файл, ресемплит 24 k → 16 k, кладёт в буфер
  и на каждом кадре гоняет Silero VAD; при `speech_stopped` — ротация буфера и
  `commit_and_transcribe`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer_event_router.py#L107-L145
- Транскрибер пишет WAV из VAD-обрезанного буфера и делает **один** вызов
  `transcription_client.create(response_format="text")`, после чего публикует единственное
  событие `...transcription.completed`. Никаких `delta`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer.py#L134-L172
- `commit` на буфере короче 100 мс возвращает `invalid_request_error`, а не молча игнорирует.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/input_audio_buffer_event_router.py#L33
- Сессия ограничена 30 минутами (`OPENAI_REALTIME_SESSION_DURATION_SECONDS = 30 * 60`).
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/session.py#L9
- Все входные файлы декодируются в 16 kHz mono float32 на границе HTTP; `audio/pcm` и
  `audio/raw` трактуются как s16le mono без заголовка; не декодировалось — 415/400.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/dependencies.py#L84-L123
- VAD-эндпоинт называется `/v1/audio/speech/timestamps` и возвращает
  `[{start, end}, ...]` в миллисекундах; принимает `threshold, neg_threshold,
  min_speech_duration_ms, max_speech_duration_s, min_silence_duration_ms, speech_pad_ms`.
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/vad.py#L24-L100
- Эмбеддинг спикера — `POST /v1/audio/speech/embedding`, ответ в форме [OI] embeddings
  (`object:"list"`, `data:[{object:"embedding", index:0, embedding:[...]}]`, `usage`).
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/speech_embedding.py#L27-L52
- Управление резидентностью — `/api/ps` (список загруженных), `POST /api/ps/{model_id}`
  (загрузить; **409**, если уже загружена), `DELETE /api/ps/{model_id}` (выгрузить; **409**,
  если `ref_count > 0`).
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/routers/misc.py#L32-L104

**WhisperLiveKit** — два независимых WS-протокола на одном сервере:

- Нативный `WS /asr`: конфиг в query (`language`, `mode=full|diff`, `target_language`, `context`),
  первым сообщением сервер шлёт `{"type":"config", ...}`, клиент шлёт **бинарные** кадры,
  пустой бинарный кадр (`b""`) = конец аудио, сервер отвечает `{"type":"ready_to_stop"}` и
  закрывает 1000; при переполнении сессий (по умолчанию 64) апгрейд отклоняется HTTP 403
  *до* создания WebSocket.
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/basic_server.py#L96-L231
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/docs/API.md#L375-L430
- Результат — **снимок состояния без поля `type`**: `{status, lines:[{speaker,text,start,end,
  translation?,detected_language?}], buffer_transcription, buffer_diarization, buffer_translation,
  remaining_time_*}`. `buffer_transcription` — «хвост», который ещё может измениться.
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/timed_objects.py#L207-L232
- Deepgram-совместимый `WS /v1/listen` — отдельный адаптер поверх того же движка.
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/basic_server.py#L224-L235

**speech-swift** (Swift/Hummingbird) — свой сервер на Swift:

- Маршруты `/health`, `/v1/models`, `/v1/realtime/models`, `/v1/audio/transcriptions`,
  `/v1/audio/speech`, плюс устаревшие `/transcribe` (с `sample_rate`, лимит 50 МиБ) и `/speak`.
  https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L119-L150
- WS-апгрейд только на `/v1/realtime`, `maxFrameSize: 1 << 24`, `autoPing: .disabled`.
  Обоснование в комментарии: «Hummingbird's autoPing watchdog expects a payload-matching
  control-frame pong, which `URLSessionWebSocketTask` doesn't reliably surface while a receive
  is pending behind synchronous work» — поэтому сервер сам шлёт `realtime.keepalive` каждые 15 с.
  https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L378-L388
  https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L1572-L1573
- `session.created` отправляется сразу после `accept()`, до любой конфигурации; входящие
  сообщения ограничены 50 МиБ, принимаются **только** текстовые кадры (`guard case .text`).
  https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L968-L1000
- Сервер умеет отдавать `input_audio_buffer.speech_started` / `speech_stopped`, `cleared`,
  `committed`, `conversation.item.input_audio_transcription.completed` — и, как и speaches,
  **не** отдаёт `transcription.delta`.

### 5. Deepgram `/v1/listen`: захваченный wire-протокол

Единственный в наборе *захват живой трассы* (MITM-прокси `dgproxy`, 2026-08-25, шесть сессий,
`model=nova-2-phonecall`). Ниже — только то, что помечено в источнике как ✅ Captured:

- Эндпоинт `wss://api.deepgram.com/v1/listen?<query>`, авторизация `Authorization: Token <API_KEY>`.
- Аудио — **бинарные кадры без заголовков**; наблюдались размеры 2080, 960, 800, 480 байт
  (≈60–260 мс при `mulaw`/8000). «Deepgram tolerates arbitrary chunking».
- Серверные сообщения — JSON-текст с дискриминатором `type`; захвачены `Results` и `Metadata`.
- `Results`: `{type, channel_index, duration, start, is_final, speech_final, from_finalize,
  channel:{alternatives:[{transcript, confidence, words:[{word,start,end,confidence,punctuated_word}]}]},
  metadata:{request_id, model_info, model_uuid}}`.
- Промежуточные `Results` приходят ~1 раз в секунду, **включая пустые во время тишины**;
  финальный может отличаться от последнего промежуточного (в захвате — сильно отличался).
- Пустой финал (`transcript:""`, `is_final:true`, `speech_final:true`) — легальный.
- `Metadata` — терминальная сводка (`duration`, `models[]`, `model_info{}`), `sha256:"incomplete"`,
  `transaction_key:"deprecated"`; при отсутствии аудио `models`/`model_info` отсутствуют.
- Таймаут неактивности ~12 с: сервер шлёт финальный `Metadata` и закрывает
  `1011 "Deepgram did not receive audio data or a text message within the timeout window"`.
- Управляющие сообщения клиента (документированы, в захвате не встречались): `{"type":"CloseStream"}`,
  `{"type":"KeepAlive"}`, `{"type":"Finalize"}` (последний даёт `from_finalize:true`).
- `UtteranceEnd {channel, last_word_end}` и `SpeechStarted {channel, timestamp}` — отдельные
  события, включаются `vad_events=true` / `utterance_end_ms`.
  https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/testing-proxy-dg/DEEPGRAM_PROTOCOL.md#L1-L279

Вторая независимая реализация того же протокола (Go/Fiber) подтверждает формы: структуры
`dgResults`, `dgChannel`, `dgAlternative`, `dgWord`, `dgMetadata` и обработку
`KeepAlive`/`Finalize`/`CloseStream`.
https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/internal/handlers/deepgram.go#L64-L140

Третья реализация (WhisperLiveKit) добавляет параметры `interim_results`, `vad_events`,
`endpointing` (число мс | `true` | `false`), `utterance_end_ms` (1000–5000, требует
`interim_results=true`), `encoding=linear16`, `sample_rate` (только 16000), `channels` (только 1);
`endpointing`/`vad_events` требуют включённого VAD и иначе отвергаются на входе.
https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/deepgram_compat.py#L78-L170

Разбор управляющего цикла (это лучший образец в наборе):

- 30 с без аудио и без `KeepAlive` → `Error` + close 1011 «stream timeout».
- Пустой бинарный кадр — **ошибка**, а не конец потока: «Empty binary frames are not an
  end-of-stream signal. Send a CloseStream control message» → close 4400.
- `CloseStream` → дослать остаток, отправить `Metadata`, close 1000.
- `Finalize` не поддержан и явно отвергается (4400) с объяснением, что flush терминальный.
- Неизвестные управляющие сообщения — игнорируются (логируются), не рвут соединение.
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/deepgram_compat.py#L1020-L1136

### 6. AssemblyAI streaming v3

Официальный гайд миграции v2 → v3 фиксирует **ровно тот тип изменений, от которого контракт
обязан защищаться**:

- Эндпоинт сменился целиком: `wss://api.assemblyai.com/v2/realtime/ws` →
  `wss://streaming.assemblyai.com/v3/ws` (другой хост **и** другой путь).
- Имена сообщений: `SessionBegins` → `Begin`, `PartialTranscript`/`FinalTranscript` → `Turn`,
  плюс новое `Termination`; поля: `message_type` → `type`, `session_id` → `id`, `text` → `transcript`.
- `Turn` несёт полный `transcript` хода и флаг `end_of_turn`; при `end_of_turn=false` это
  промежуточный текст (в примере перерисовывается через `\r`), при `true` — финальный.
  Добавлены `turn_is_formatted`, `turn_order`, `end_of_turn_confidence`, `expires_at`.
- Обработка ошибок переехала из in-band сообщений в события WebSocket.
- Клиент обязан явно завершать сессию сообщением `{"type":"Terminate"}`: биллинг идёт по времени
  жизни соединения, а не по длине аудио; незавершённые сессии закрываются через 3 часа и
  биллятся полностью.
  https://www.assemblyai.com/docs/streaming/guides/v2_to_v3_migration_js.md

Вывод для нас: **путь и хост — часть контракта**, а «переименовали поле» = мажорная версия.
Одновременно видно, что `end_of_turn` — это не `speech_final`: endpointing и «ход закончился»
семантически разные вещи, и AssemblyAI их слил в один флаг, что беднее, чем у Deepgram.

### 7. Wyoming

Протокол: `{type, data?, data_length?, payload_length?}\n` + опциональные `data_length` байт
дополнительного JSON + `payload_length` байт бинарного payload. Транспорт — TCP, UDS (`unix://`)
или stdio (`stdio://`), выбор по URI в клиенте.
https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/README.md#L1-L30
https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/wyoming/client.py#L60-L84

- Аудио: `audio-start {rate, width, channels, timestamp?}` / `audio-chunk {...}` + бинарный PCM
  в payload / `audio-stop {timestamp?}`.
- Возможности: `describe` → `info` с `asr.models[].{name, languages[], attribution{name,url},
  installed, description?, version?}` и флагами `supports_transcript_streaming`,
  `requires_external_vad`, `prefers_auto_gain_enabled`, `prefers_noise_reduction_enabled`.
- Стриминг: `transcript-start` → `transcript-chunk {text}` → `transcript` (обязателен для обратной
  совместимости) → `transcript-stop`. Ключевая фраза: «`text` — transcript so far, including text
  that may still be revised… **Each chunk replaces the previous one instead of being appended to it**».
- Ошибка: `{"type":"error","data":{"text","code"?}}` — единственный машинный код, без
  статусов и без категорий.
- Безопасность: «Wyoming has no authentication or encryption, **by design**».
  https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/README.md#L1-L200
  https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/wyoming/error.py#L1-L40

Ценность: Wyoming — лучшее доказательство, что «замена, а не накопление» является нормой для
частичных гипотез, и что `describe/info` как отдельный запрос возможностей работает годами.
Ограничение: нет аутентификации, нет версий протокола, `type`-строка без пространства имён.

### 8. gRPC bidirectional streaming

- Google Cloud Speech v2: `StreamingRecognize` — «Performs bidirectional streaming speech
  recognition: receive results while sending audio. **This method is only available via the gRPC
  API (not REST)**». Первое сообщение несёт `recognizer` + `streaming_config`, дальше — только
  `audio` (`bytes`, **максимум 15 KB на сообщение**).
  https://cloud.google.com/speech-to-text/v2/docs/reference/rpc/google.cloud.speech.v2
- `StreamingRecognitionResult`: `{alternatives[], is_final, stability (0.0–1.0, только для
  промежуточных), result_end_offset (Duration от начала аудио), channel_tag, language_code}`.
  «If there is no recognizable audio then no messages are streamed back» — то есть пустых
  промежуточных результатов нет.
- `StreamingRecognitionFeatures`: `interim_results`, `enable_voice_activity_events`,
  `voice_activity_timeout` (сервер сам закроет поток после тишины).
  https://cloud.google.com/speech-to-text/v2/docs/reference/rpc/google.cloud.speech.v2

Клиенты:

- **Rust/tonic 0.14.6**: `Endpoint::connect_with_connector` (кастомный коннектор, в т.ч. UDS) и
  `Server::serve_with_incoming` (сервер на произвольном `AsyncRead+AsyncWrite`, в т.ч. UDS).
  https://docs.rs/tonic/latest/tonic/transport/struct.Endpoint.html
  https://docs.rs/tonic/latest/tonic/transport/struct.Server.html
- **Swift/grpc-swift 2.4.4** (тег `2.4.4`, коммит от 2026-10-12): транспорт живёт в отдельном
  пакете `grpc-swift-nio-transport`; продукты `GRPCNIOTransportHTTP2`,
  `GRPCNIOTransportHTTP2Posix`, `GRPCNIOTransportHTTP2TransportServices`. UDS поддержан именно
  в **Posix**-варианте: `SocketAddress.unixDomainSocket(path:)` и
  `NIOClientBootstrap.connect(unixDomainSocketPath:)`.
  https://github.com/grpc/grpc-swift-nio-transport/blob/9765a256477383b9d441f20952118b75104304e8/Package.swift#L20-L32
  https://github.com/grpc/grpc-swift-nio-transport/blob/9765a256477383b9d441f20952118b75104304e8/Sources/GRPCNIOTransportCore/Client/Resolver/SocketAddress.swift#L120-L150
  https://github.com/grpc/grpc-swift-nio-transport/blob/9765a256477383b9d441f20952118b75104304e8/Sources/GRPCNIOTransportHTTP2Posix/NIOClientBootstrap+SocketAddress.swift#L25-L35

Ограничение, которое надо назвать прямо: gRPC-Web не поддерживает bidi, значит браузерный клиент
через gRPC невозможен; отладка `curl`-ом невозможна; любое изменение схемы требует перегенерации.

### 9. HTTP поверх Unix domain socket

- Docker: демон слушает `unix:///var/run/docker.sock` по умолчанию, доступ «requiring either
  `root` permission, or `docker` group membership»; сокет задаётся `-H unix://path` и группой
  `--group` (default `docker`); в документации есть прямое предупреждение, что смена привязки
  на TCP/группу создаёт риски повышения привилегий.
  https://docs.docker.com/reference/cli/dockerd/#daemon-socket-option
- Wyoming официально поддерживает `unix://` как транспорт.
  https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/wyoming/client.py#L60-L84
- Ограничение длины пути: `sun_path[104]` в macOS SDK (`/Library/Developer/CommandLineTools/
  SDKs/MacOSX.sdk/usr/include/sys/un.h:79`), то есть ≤103 символа + NUL. `~/Library/Application
  Support/Speech Runtime/...` легко приближается к лимиту — путь сокета нужно держать коротким.
- Идентичность пира: `getpeereid(3)` возвращает euid/egid пира для `SOCK_STREAM` и, по ману,
  «This mechanism is reliable; there is no way for either side to influence the credentials
  returned to its peer». На уровне сокет-опций — `LOCAL_PEERCRED` и `struct xucred` (`unix(4)`).
  Проверено локально: `man 3 getpeereid`, `man 4 unix` (macOS 27.0).
- Для Swift адресация UDS из `URLSession` **не документирована**: в TN3151 Apple рекомендует
  `URLSession` для HTTP и `Network.framework` для WebSocket, а для «HTTP, которого нет в
  URLSession» советует стороннюю библиотеку (упомянут `AsyncHTTPClient`).
  https://developer.apple.com/documentation/technotes/tn3151-choosing-the-right-networking-api
  При этом `NWEndpoint.unix(path:)` существует с macOS 10.14
  (https://developer.apple.com/documentation/network/nwendpoint/unix(path:)), а `NWProtocolWebSocket`
  — с macOS 10.15 (https://developer.apple.com/documentation/network/nwprotocolwebsocket), то есть
  WS поверх UDS технически собираем, но это ручной HTTP/WS-стек.
- `URLSessionConfiguration.usesClassicLoadingMode` действительно существует (macOS 15.4+,
  iOS 18.4+), и новый стек на Network.framework «supports … unix domain socket», но публичного
  способа задать UDS-URL в `URLSession` в документации нет.
  https://developer.apple.com/documentation/foundation/urlsessionconfiguration/usesclassicloadingmode
  https://forums.swift.org/t/pitch-new-http-loader-for-urlsession-on-darwin/77440

### 10. XPC

- XPC-соединение к Mach-сервису создаётся `xpc_connection_create_mach_service(name, queue, flags)`;
  сервис должен быть объявлен в launchd-плисте ключом `MachServices`, после чего демон
  «check in»-ится через `xpc_connection_create_mach_service(3)` или `bootstrap_check_in(3)`.
  Проверено локально: `man 3 xpc_connection_create_mach_service`, `man 5 launchd.plist`
  (секция `MachServices`, строки 529–566).
- XPC есть только на Apple-платформах; в Swift на Linux его нет `[INFERENCE: следует из
  отсутствия XPC в swift-corelibs]`.
- XPC-сообщения — не поток байт, а сериализованные XPC-объекты (словари/массивы/данные/ fd),
  поэтому «потоковое аудио» на нём означает либо message-per-chunk, либо передачу файлового
  дескриптора/`IOSurface` и отдельный протокол поверх.
- Практический вывод: XPC годится как *активатор и менеджер жизненного цикла* (launchd
  поднимает демон по требованию, `MachServices` даёт on-demand запуск), но не как основной
  прикладной протокол для кросс-языковых клиентов.

### 11. Бинарные vs JSON/base64 фреймы

| Протокол | Аудио на проводе | Накладные расходы |
|---|---|---|
| [OI] Realtime | JSON-текст, `audio` = base64 | +33 % байт, base64-кодек на кадр, лимит 15 MiB/событие |
| Deepgram | бинарный кадр, сырой PCM | нет (захвачено: 480–2080 байт/кадр) |
| WhisperLiveKit `/asr` | бинарный кадр | нет |
| AssemblyAI v3 | бинарный кадр | нет |
| Wyoming | JSON-заголовок + бинарный payload | ~40–120 байт заголовка на чанк |
| Google gRPC | `bytes` в protobuf | ~5 байт тега/длины, лимит 15 KB/сообщение |

Вывод: base64 — это свойство *чужого* протокола, а не разумный выбор. Нативный протокол обязан
быть бинарным; base64 появляется только в [OI]-адаптере.

### 12. Версионирование протокола

- [OI]: мажор в пути (`/v1/...`) на всё; для realtime исторически использовался заголовок
  `[OI]-Beta: realtime=v1` (в актуальной спецификации заголовка уже нет — версия выражена путём).
  `[INFERENCE]` по заголовку: он упоминается в клиентских SDK и гайдах, но в `openapi.yaml`
  `master` его нет.
- AssemblyAI: мажор меняет **хост и путь**, плюс переименовывает поля (см. §6).
- SpeechRail: версия контракта — `7.1.0` в документе, но **путь остаётся `/v1/realtime`**;
  явно сказано: «服务不提供旧事件翻译、旧字段或 profile alias、双协议分支或 `/v2` 兼容层»
  (нет перевода старых событий, старых полей, алиасов, ветвлений по протоколу и слоя `/v2`),
  а неизвестные поля отвергаются (fail-closed).
  https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/realtime-openai.md#L1-L20
- Wyoming: версии протокола нет вообще; расширение только аддитивное, «Servers are expected to
  drop unrecognized events».
- gRPC: версия = имя пакета (`speech.v1`) + `proto` эволюция по правилам protobuf.

### 13. Формат ошибок

- RFC 9457 (обsolete RFC 7807): медиатип `application/problem+json`; члены `type` (URI-ссылка,
  по умолчанию `about:blank`), `status`, `title`, `detail`, `instance`; расширения разрешены;
  «Consumers MUST use the `type` URI … as the problem type's primary identifier»; при
  несовпадении типа значения член «MUST be ignored». Пример с массивом `errors[{detail, pointer}]`
  для валидации.
  https://www.rfc-editor.org/rfc/rfc9457
- Реальные локальные API этот формат **не используют**: speaches отдаёт FastAPI-овский
  `{"detail": "..."}` и `HTTPException(415|400|500)`, [OI] — `{"error": {...}}`,
  WhisperLiveKit — `{"type":"error","error":"..."}` / `{"type":"Error","description":...}`,
  Wyoming — `{"type":"error","data":{"text","code"}}`. Единого стандарта в этой нише нет,
  поэтому его надо ввести самим.

### 14. Аутентификация локальных клиентов

- speaches: опциональный единый `api_key`; HTTP — `HTTPBearer(auto_error=False)` → 403 +
  `WWW-Authenticate: Bearer`; WS — ручная проверка **до** `accept()`, токен принимается ещё и
  из query (`?api_key=`).
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/dependencies.py#L59-L81
  https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/realtime/utils.py#L53-L75
- WhisperLiveKit: `--api-token`/`WLK_API_TOKEN`; WS закрывается `4401` до `accept()` при
  неверном токене.
  https://github.com/QuentinFuxa/WhisperLiveKit/blob/eb92c115ee7abc1e161b0b2159da31785b0f5566/whisperlivekit/basic_server.py#L100-L106
- gotranscribesrv: JWT (`gofiber/contrib/jwt`, `golang-jwt/jwt/v5`) — то есть в локальном
  сервере уже появляется полноценная токен-модель.
  https://github.com/sagostin/gotranscribesrv/blob/a5f4289078553ba66f83510c62146043bed91a7a/go.mod#L8-L20
- speech-swift `AudioServer` аутентификации не имеет (`[INFERENCE]`: в конструкторе и маршрутах
  нет ни токена, ни проверки заголовков).
  https://github.com/soniqo/speech-swift/blob/cbad3be29480825707890490f446b19d7288be2d/Sources/AudioServer/AudioServer.swift#L44-L80
- Wyoming: аутентификации нет **по дизайну** (см. §7).

### 15. Резидентность и переиспользование весов

Эталон в наборе — `SelfDisposingModel` в speaches: `ref_count` + `threading.RLock` +
`threading.Timer(ttl)`. `__enter__` грузит модель при первом обращении и инкрементит счётчик;
`__exit__` декрементит и при `ref_count <= 0` планирует выгрузку через `ttl` (или сразу при
`ttl == 0`, или никогда при `ttl < 0`); `unload()` отказывается выгружать при `ref_count > 0`
(`ValueError`), затем `gc.collect()`. `BaseModelManager` держит `OrderedDict` и один `Lock`.
https://github.com/speaches-ai/speaches/blob/993994f7984bf3fe9655b267448328cf66fccb42/src/speaches/executors/shared/base_model_manager.py#L37-L152

Это ровно та семантика, которую требует задача «переиспользование загруженных весов между
клиентами + минимальные накладные расходы в простое», и она наблюдаема снаружи через API
(`/api/ps`, 409 при выгрузке занятой модели). Подробный разбор реестра моделей — в
`research/wave1/G-model-management.md`; здесь важна только API-проекция.

### 16. Частичные гипотезы: что вообще выразимо

| Источник | Примитив | Ревизии | Стабильный префикс |
|---|---|---|---|
| [OI] realtime | `delta` (append-only) | нет | подразумевается |
| [OI] REST SSE | `delta` (append-only) | нет | подразумевается |
| Deepgram | полный текст сегмента, `is_final`/`speech_final` | нет | нет |
| AssemblyAI v3 | полный текст хода, `end_of_turn` | нет | нет |
| Wyoming | `transcript-chunk` = «so far», заменяет | нет | нет |
| Google gRPC | полный результат + `stability` | нет | частично (числом) |
| WhisperLiveKit | снимок `lines` + `buffer_transcription` | нет | нет (неявно) |
| SpeechRail | `hypothesis` с `revision` + `stable_prefix_codepoints` | да | да, явно |

Только SpeechRail делает то, что нужно: помечает ревизию и **отдельно** — длину доказуемо
стабильного префикса, а append-only дельту отдаёт лишь тогда, когда стабильность доказана:
«只有可证明的稳定前缀才能映射到官方 append-only delta；无法证明稳定时只发 hypothesis，最终统一发 completed».
https://github.com/hrygo/SpeechRail/blob/cdccc74084bc94f8c996b5b71a386455640ad1cb/contracts/realtime-openai.md#L290-L300

---

## Спецификация API v1 (черновик)

### 0. Три независимые оси

```
        ┌── транспорт ──────┐   ┌── фрейминг ────────┐   ┌── семантика ───────────┐
        │ unix:/…/speechd.sock│  │ HTTP/1.1 (REST)    │   │ native (speech.*)      │
        │ tcp:127.0.0.1:port │   │ WebSocket          │   │ [OI] realtime (compat) │
        │ fd:// (launchd)    │   │ gRPC / HTTP2       │   │ Deepgram /v1/listen    │
        └────────────────────┘   │ SSE                │   │ Wyoming (адаптер)      │
                                 └────────────────────┘   └────────────────────────┘
```

Правило: **семантика не зависит от транспорта, фрейминг не зависит от семантики.**
Один внутренний автомат сессии (admit audio → utterance → partial/final → terminal),
N адаптеров. Это то, чего не сделал ни один из изученных проектов (у WhisperLiveKit три
независимых обработчика, у speaches два, у SpeechRail один, но без совместимых слоёв).

### 1. Идентификаторы, версии, носители

- Базовая версия API: **`/v1`** в пути. Ломающее изменение = `/v2`, без исключений и без
  «тихих» алиасов.
- Версия протокола потоковой сессии: **`speech.v1`**, передаётся как WebSocket-сабпротокол
  (`Sec-WebSocket-Protocol: speech.v1`) и дублируется полем `protocol_version` в `session.created`.
  Несовместимая версия → отказ на апгрейде (HTTP 400 + problem+json), а не «соединение есть,
  но ничего не работает».
- Идентификаторы — префикс + 128 бит случайности (base32, lower-case), непрозрачны для клиента,
  никогда не переиспользуются:

| Префикс | Смысл | Время жизни |
|---|---|---|
| `sess_` | сессия (WS-соединение) | соединение |
| `utt_` | высказывание (utterance) | до терминального события |
| `req_` | HTTP/gRPC-запрос | запрос |
| `op_` | длительная операция (pull/install/load) | до завершения + TTL |
| `evt_` | событие сервера (для корреляции) | соединение |
| `cmt_` | commit (клиентский, опциональный) | до `input.committed` |

- Аудио-время на проводе — **в сэмплах**, а не в секундах с плавающей точкой:
  `audio_span: {start, end}` — полуоткрытый интервал `[start, end)` в сэмплах **заявленной
  клиентом частоты**. Это устраняет накопление ошибки и позволяет сверять «сколько байт я
  отправил» с «сколько сервер принял» (приём из SpeechRail).

### 2. Транспорт и адреса

| Приоритет | Адрес | Кому |
|---|---|---|
| основной | `tcp://127.0.0.1:<port>` | Swift, браузер, любой клиент «из коробки» |
| первый класс | `unix://<короткий путь>/speechd.sock` | Rust, CLI, демон↔демон, sandboxed-приложения |
| опция | `fd://<n>` (launchd `Sockets`) | автозапуск демона по требованию |
| второй | gRPC по любому из адресов | типизированные клиенты, мультиплексирование |

Требования:

- Слушать **только** `127.0.0.1` (и `::1`), никогда `0.0.0.0`. `Host`-заголовок проверяется
  на соответствие `127.0.0.1:<port>` / `localhost:<port>` (защита от DNS-rebinding).
- Порт по умолчанию фиксирован и публикуется в файле `runtime.json` (см. §3), потому что
  «порт занят» — обычное дело; клиент читает файл, а не угадывает.
- Путь UDS — короткий: `~/.speechd/run/s.sock` или `$TMPDIR/sd-<uid>.sock`; лимит macOS —
  103 символа (`sun_path[104]`).
- `Content-Type`/`Accept` для нативных REST-ответов — `application/json`;
  ошибки — `application/problem+json`.

### 3. Аутентификация

Два режима, оба обязательны к реализации, выбор — по адресу:

**(a) TCP loopback — bearer-токен.**

- При первом старте демон создаёт `token` (32 байта, base64url) в
  `~/Library/Application Support/SpeechRuntime/` с правами `0600` (каталог `0700`).
- Клиент передаёт `Authorization: Bearer <token>` на каждый HTTP-запрос и в заголовке
  WS-апгрейда. Токен **не** принимается в query-параметре: он попадает в логи и в историю
  браузера (в speaches и WhisperLiveKit это разрешено — считаю это ошибкой, которую не копируем).
- Дополнительно: `Origin` не из списка локальных схем → 403; отсутствие `Origin` (не-браузер)
  разрешено.
- Ротация: `POST /v1/admin/token/rotate` (только с текущим токеном) — старый живёт `grace_ms`.

**(b) UDS — права файла + идентичность пира.**

- Каталог `0700`, сокет `0600` (или `0660` + общая группа, если нужен доступ нескольким
  пользователям — по образцу Docker `--group`).
- Сервер обязан вызвать `getpeereid()`/`LOCAL_PEERCRED` и записать `peer_uid`/`peer_gid`
  в метаданные сессии; при настроенном `allowed_uids` — отвергнуть чужих с `403 forbidden`.
- Токен на UDS не требуется (файловая система уже является границей), но если передан —
  проверяется так же.

**Sandbox-замечание:** App-Store-sandboxed приложение не имеет доступа к произвольным путям,
поэтому сокет и токен следует класть в **app group container**
(`~/Library/Group Containers/<group>/`) либо выдавать temporary exception. `[INFERENCE]`:
конкретный набор entitlement'ов зависит от подписи и требует проверки на реальном sandbox-билде.

### 4. Ошибки

Единый машинный код (`code`) и единая таблица. HTTP-представление — RFC 9457; WS —
событие `error`; gRPC — `status.details` с тем же `code`.

**HTTP:**

```http
HTTP/1.1 404 Not Found
Content-Type: application/problem+json

{
  "type": "https://speechd.local/problems/model_not_found",
  "title": "Model not found",
  "status": 404,
  "detail": "Model 'whisper-large-v3-turbo:q4' is not installed. Installed: whisper-large-v3-turbo:q8, parakeet-tdt-0.6b:v3",
  "instance": "/v1/transcribe",
  "code": "model_not_found",
  "retryable": false,
  "param": "model"
}
```

`type` = `https://speechd.local/problems/<code>` — так `type` и `code` не могут разойтись,
и при этом соблюдается требование RFC 9457 использовать `type` как первичный идентификатор.

**WebSocket:**

```json
{"type":"error","seq":31,"event_id":"evt_9f","request_id":"cmt_12",
 "error":{"type":"invalid_request_error","code":"invalid_state",
          "message":"session.configure must be sent before the first audio frame",
          "retryable":false,"param":"model"}}
```

**Коды (закрытый список v1):**

| `code` | HTTP | retryable | Когда |
|---|---|---|---|
| `invalid_request` | 400 | нет | не разобрался JSON/форма/параметр |
| `unsupported_parameter` | 400 | нет | поле неизвестно в этой версии (fail-closed) |
| `invalid_state` | 409 | нет | операция недопустима сейчас (configure после аудио, update во время речи) |
| `unauthorized` | 401 | нет | нет/неверный токен |
| `forbidden` | 403 | нет | чужой uid, запрещённый Origin, лицензия не принята |
| `model_not_found` | 404 | нет | модели нет в каталоге |
| `model_not_installed` | 409 | нет | модель есть в каталоге, артефактов нет |
| `model_busy` | 409 | да | модель занята несовместимой операцией |
| `unsupported_model_capability` | 400 | нет | например, partials для модели без стриминга |
| `unsupported_audio_format` | 415 | нет | кодек/частота/каналы не поддержаны |
| `audio_too_large` | 413 | нет | тело/буфер больше лимита |
| `audio_overflow` | 429 | да | превышен `max_inflight_audio_ms` |
| `queue_full` | 429 | да | нет свободного слота (с `Retry-After`) |
| `capacity_exceeded` | 429 | да | превышен лимит сессий клиента/сервера |
| `cancelled` | — | — | отменено клиентом (ответ не отправляется: соединение уже закрыто) |
| `timeout` | 504 | да | не уложились в дедлайн |
| `backend_error` | 500 | да | движок упал |
| `backend_unavailable` | 503 | да | движок не готов/выгружен |
| `backpressure_final_lost` | — | — | только WS-close 1011 |
| `internal_error` | 500 | да | баг |

**Коды закрытия WebSocket** (RFC 6455 §7.4.2: 1000–2999 — протокол, 3000–3999 — библиотеки,
4000–4999 — приватное использование; проверено по тексту RFC):

| Код | Причина |
|---|---|
| 1000 | нормальное закрытие после `session.close` |
| 1001 | сервер уходит / `idle_timeout` |
| 1008 | нарушение политики (обычно auth) |
| 1009 | кадр/сообщение слишком велико |
| 1011 | внутренняя ошибка, включая `backpressure_final_lost` |
| 1013 | перегрузка, попробуйте позже |
| 4400 | некорректный запрос (закрытый набор `code` в reason) |
| 4401 | не аутентифицирован |
| 4408 | истёк дедлайн операции |
| 4429 | превышен лимит сессий |

### 5. Капабилити

`GET /v1/capabilities` — статическая (кешируемая) карта возможностей сборки:

```json
{
  "protocol_versions": ["speech.v1"],
  "api_version": "v1",
  "runtime": {"version": "0.1.0", "platform": "darwin-arm64", "pid": 4711, "uptime_s": 913},
  "transports": [
    {"scheme": "tcp", "address": "127.0.0.1:47821", "auth": ["bearer"]},
    {"scheme": "unix", "address": "/Users/me/.speechd/run/s.sock", "auth": ["peercred"]}
  ],
  "operations": ["transcribe", "stream", "detectSpeech", "diarize", "speakerEmbedding",
                 "listModels", "loadModel", "unloadModel", "installModel", "removeModel"],
  "compat": {"openai_audio": "v1", "openai_realtime": "transcription-v1",
             "deepgram_listen": "not_implemented"},
  "engines": [
    {"id": "mlx", "ready": true, "devices": ["ane", "gpu"]},
    {"id": "coreml", "ready": true},
    {"id": "onnx", "ready": false, "reason": "runtime not installed"}
  ],
  "limits": {
    "max_sessions_total": 32,
    "max_sessions_per_client": 8,
    "max_audio_body_bytes": 536870912,
    "max_inflight_audio_ms": 20000,
    "max_utterance_ms": 30000,
    "max_event_queue": 512,
    "idle_timeout_ms": 60000
  }
}
```

Правило: **всё, что клиент может нарушить, объявлено здесь и повторено в `session.created`.**
Ни один лимит не должен быть «магическим числом» в коде клиента.

### 6. Модели: метаданные

`GET /v1/models` возвращает [OI]-совместимый конверт (обязательные `id`, `object`, `created`,
`owned_by`) и **вложенные расширения** (не плоско — чтобы не засорять чужое пространство имён,
как это сделал speaches полями `language`/`task`):

```json
{
  "object": "list",
  "data": [
    {
      "id": "whisper-large-v3-turbo:q4",
      "object": "model",
      "created": 1761264000,
      "owned_by": "openai",
      "tasks": ["transcribe", "translate"],
      "languages": ["ru", "en", "de"],
      "digest": "sha256:5f1c…",
      "revision": "996f9c1b3f0a4d2e8b7c6a5f4e3d2c1b0a9f8e7d",
      "state": "installed",
      "variants": [
        {"engine": "mlx",    "precision": "q4", "size_bytes": 811597824, "state": "installed"},
        {"engine": "coreml", "precision": "fp16", "size_bytes": 1610612736, "state": "not_installed"}
      ],
      "capabilities": {
        "streaming": {"partials": true, "append_only_partials": false,
                      "stable_prefix": true, "min_chunk_ms": 20},
        "timestamps": ["word", "segment"],
        "diarization": false,
        "translation": true,
        "language_detection": true,
        "prompt": true,
        "keywords": false,
        "max_audio_ms": 1800000
      },
      "audio": {"sample_rates": [16000], "channels": [1],
                "containers": ["wav", "flac", "mp3", "ogg", "webm", "m4a"],
                "raw_pcm": true},
      "loaded": {"refs": 2, "since": "2026-10-25T09:12:03Z", "last_used": "2026-10-25T09:14:51Z",
                 "idle_unload_in_ms": 284000},
      "license": {"spdx": "MIT", "requires_acceptance": false}
    }
  ]
}
```

`GET /v1/models/{id}` — тот же объект. `?task=transcribe&state=loaded&installed=true` — фильтры.
Ключевое: **`capabilities.streaming.partials` и `append_only_partials` — разные вещи.**
Модель может отдавать частичные результаты, но не иметь права на append-only дельту.

### 7. Модели: жизненный цикл

`loadModel` / `unloadModel` (резидентность):

```http
POST /v1/models/whisper-large-v3-turbo:q4/load
Content-Type: application/json
{"engine": "mlx", "wait": true, "ttl_ms": 300000}

200 OK
{"model": "whisper-large-v3-turbo:q4", "state": "loaded",
 "already_loaded": false, "load_ms": 1180, "refs": 1}
```

- **Идемпотентность важнее «правильного» 409.** speaches отдаёт 409 на повторную загрузку —
  это заставляет каждого клиента писать спец-обработку. Возвращаем `200` + `already_loaded: true`.
- `wait=false` → `202` + `Location: /v1/operations/op_…`.
- `DELETE /v1/models/{id}/load?drain=true&timeout_ms=30000`:
  `200` (выгружено) | `409 model_busy` (есть `refs > 0` и `drain=false`) | `504` (не дождались).
  В теле всегда `refs`.

`installModel` / `removeModel` / pull — через ресурс операций:

```http
POST /v1/models/pull
{"name": "whisper-large-v3-turbo", "tag": "q4", "engine": "mlx",
 "revision": null, "accept_license": true}

202 Accepted
Location: /v1/operations/op_7d2c
{"operation_id": "op_7d2c", "state": "queued", "model": "whisper-large-v3-turbo:q4"}
```

```http
GET /v1/operations/op_7d2c
{"operation_id":"op_7d2c","kind":"pull","state":"running",
 "model":"whisper-large-v3-turbo:q4",
 "progress":{"phase":"download","bytes_done":412516352,"bytes_total":811597824,
             "files_done":3,"files_total":7},
 "started_at":"2026-10-25T09:20:01Z","error":null}
```

```http
GET /v1/operations/op_7d2c/events        # text/event-stream
data: {"operation_id":"op_7d2c","state":"running","progress":{"bytes_done":436207616,...}}
data: {"operation_id":"op_7d2c","state":"succeeded","model":"whisper-large-v3-turbo:q4"}
```

```http
DELETE /v1/models/whisper-large-v3-turbo:q4?engine=coreml
204 No Content
```
`409 model_busy`, если модель загружена и `?force=false`.

Правила, которые надо зафиксировать в контракте (они уже выведены в G-отчёте, здесь — только
проекция в API):

1. `pull` не грузит модель в память; `load` не качает артефакты.
2. `state` ∈ `not_installed | installing | installed | loading | loaded | error`.
3. Никакая операция не выбирает модель «по смыслу» — только явный `id`.
4. Версия модели — часть идентификатора (`name:tag`), а `digest`/`revision` — отдельные поля;
   `pull` без `tag` использует `latest` **тега**, а не «лучшую» модель.

### 8. `transcribe` (batch)

`POST /v1/transcribe` — нативный batch. Принимает либо `multipart/form-data` (файл + поля),
либо `application/json` с `audio` как data URL / base64 (для мелких файлов и для gRPC-паритета),
либо `audio/pcm` + `X-Audio-Rate` (сырой PCM).

```http
POST /v1/transcribe
Authorization: Bearer …
Content-Type: multipart/form-data; boundary=…

model=whisper-large-v3-turbo:q4
language=ru
prompt=kubectl, Istio, PostgreSQL
response_format=verbose_json
timestamp_granularities[]=word
timestamp_granularities[]=segment
temperature=0
[file]
```

```json
{
  "text": "Перезапусти под в namespace ingress, потом проверь kubectl get pods.",
  "language": "ru",
  "duration_ms": 4320,
  "segments": [
    {"id": 0, "start_ms": 0, "end_ms": 2210,
     "text": " Перезапусти под в namespace ingress,",
     "avg_logprob": -0.28, "no_speech_prob": 0.0098}
  ],
  "words": [
    {"word": "Перезапусти", "start_ms": 120, "end_ms": 610, "probability": 0.94}
  ],
  "model": "whisper-large-v3-turbo:q4",
  "engine": {"id": "mlx", "precision": "q4"},
  "usage": {"type": "duration", "seconds": 4.32},
  "timing": {"queue_ms": 3, "inference_ms": 890, "total_ms": 912}
}
```

Форматы (`response_format`):

| Значение | Тело | Примечание |
|---|---|---|
| `json` (default) | `{text, model, usage?}` | |
| `text` | `text/plain` | без JSON-обёртки |
| `verbose_json` | как выше | `language`, `duration`, `segments`, `words` |
| `srt` / `vtt` | `text/plain` | таймкоды из `segments` |
| `diarized_json` | `{task, duration, text, segments[{start,end,text,speaker}], usage}` | `speaker` — строка `S1…Sn` |

Правила:

- `timestamp_granularities[]` без `verbose_json` — предупреждение в `Warning`-заголовке,
  а не тихий игнор (speaches логирует warning, но клиент его не видит).
- `duration_ms` — целые миллисекунды (не float секунды, как в [OI]): детерминированная
  сериализация важнее совместимости на нативном пути.
- Отмена — разрыв соединения; сервер обязан остановить инференс и вернуть `cancelled` в лог,
  а не досчитывать в никуда.
- `usage` — обязательный блок (для наблюдаемости и для совместимости с [OI]-полем `usage`).

### 9. `stream` (WS): рукопожатие

```
GET /v1/stream HTTP/1.1
Host: 127.0.0.1:47821
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: …
Sec-WebSocket-Version: 13
Sec-WebSocket-Protocol: speech.v1
Authorization: Bearer <token>
```

Ответ: `101` + `Sec-WebSocket-Protocol: speech.v1`.

Отказы **до** апгрейда (важно: их видно как HTTP-статус, их можно отладить curl-ом):

| Ситуация | Ответ |
|---|---|
| нет/неверный токен | `401` + problem+json `unauthorized` |
| неизвестный `Sec-WebSocket-Protocol` | `400` + `unsupported_parameter` (`param: protocol`) |
| нет свободных сессий | `429` + `capacity_exceeded` + `Retry-After` |
| сервер в дренаже | `503` + `backend_unavailable` |

Это лучше, чем `403` без тела у WhisperLiveKit и `close(1008)` до `accept()` у speaches
(в ASGI это превращается в отказ рукопожатия, но структурированного тела с `code` клиент
не получает): причина видна до того, как клиент потерял контекст.

Сразу после `101` сервер шлёт `session.created`:

```json
{"type":"session.created","seq":0,"event_id":"evt_1",
 "session_id":"sess_7Kq2m9Vx",
 "protocol_version":"speech.v1",
 "created_at":"2026-10-25T09:31:02.114Z",
 "limits":{"max_audio_frame_bytes":65536,"max_inflight_audio_ms":20000,
           "max_utterance_ms":30000,"max_event_queue":512,
           "idle_timeout_ms":60000,"heartbeat_ms":15000},
 "config":null,
 "capabilities":{"partials":true,"stable_prefix":true,"vad":"server","diarization":false}}
```

`config: null` — намеренно: до `session.configure` сессия не сконфигурирована, и первый
бинарный кадр получит `invalid_state`. Это и есть техническая реализация требования
«runtime не выбирает модель сам».

### 10. `stream`: конфигурация сессии

Первый клиентский кадр — `session.configure`. Он же используется для смены конфигурации
на квиесцентной точке.

```json
{"type":"session.configure","event_id":"evt_c1",
 "model":"whisper-large-v3-turbo:q4",
 "task":"transcribe",
 "language":"ru",
 "prompt":"kubectl, Istio, PostgreSQL, CI/CD",
 "keywords":["kubectl","Istio"],
 "audio":{"format":"pcm16","rate":16000,"channels":1},
 "vad":{"mode":"server","threshold":0.5,"min_speech_ms":250,
        "min_silence_ms":500,"prefix_padding_ms":300,"max_utterance_ms":30000},
 "partials":{"enabled":true,"interval_ms":300,"stable_prefix":true},
 "timestamps":{"words":false,"segments":true},
 "diarization":{"enabled":false},
 "translate_to":null}
```

```json
{"type":"session.updated","seq":1,"event_id":"evt_2","session_id":"sess_7Kq2m9Vx",
 "model_revision":"996f9c1b3f0a4d2e8b7c6a5f4e3d2c1b0a9f8e7d",
 "effective":{
   "model":"whisper-large-v3-turbo:q4","engine":"mlx","precision":"q4",
   "task":"transcribe","language":"ru",
   "audio":{"format":"pcm16","rate":16000,"channels":1,"engine_rate":16000},
   "vad":{"mode":"server","threshold":0.5,"min_speech_ms":250,
          "min_silence_ms":500,"prefix_padding_ms":300,"max_utterance_ms":30000},
   "partials":{"enabled":true,"interval_ms":300,"stable_prefix":true},
   "timestamps":{"words":false,"segments":true},
   "diarization":{"enabled":false,"backend":null}}}
```

Правила конфигурации:

1. `model` обязателен. Нет `model` → `invalid_request` (`param: "model"`).
2. Всё, что сервер не может выполнить, — **явный отказ**, не тихий игнор и не «подставим
   ближайшее»: неизвестная модель → `model_not_found`; модель без стриминга →
   `unsupported_model_capability`; `rate` не из `audio.sample_rates` модели →
   `unsupported_audio_format`. Это прямое следствие случая `gpt-realtime-whisper`
   (VAD обязана быть выключена) и правила SpeechRail «fail closed, без алиасов».
3. `session.updated` возвращает **полный эффективный конфиг**, а не эхо запроса: клиент
   не должен догадываться, какой `engine` реально выбран.
4. Смена конфигурации посреди высказывания → `invalid_state`. Исключение: `partials.enabled`
   и `vad.threshold` можно менять всегда (они не влияют на уже принятое аудио).
5. `vad.mode`:
   - `"server"` — сервер режет высказывания сам (`min_silence_ms`, `max_utterance_ms`);
   - `"manual"` — режет только `input.commit`; VAD-события не отправляются.
   Третьего режима нет: `"off"` не нужен, потому что `manual` уже означает «не режь сам».

### 11. `stream`: аудио-кадры

- Клиент → сервер: **бинарные кадры WebSocket** с сырым `pcm16le`, mono, в заявленной `rate`.
  Рекомендуемый размер — 20–100 мс (при 16 кГц это 640–3200 байт); лимит `max_audio_frame_bytes`
  = 65536 (2 с при 16 кГц) — нарушение даёт `1009`.
- Кадр обязан быть непустым и иметь чётное число байт; иначе `invalid_request`
  (пустой кадр — **не** признак конца потока: это ошибка, как в Deepgram-адаптере WLK).
- Сервер ведёт один счётчик принятых сэмплов на сессию (`accepted_samples`); он монотонен,
  не сбрасывается на `clear` и является единственным источником истины для `audio_span`.
- Ресемплинг в частоту движка выполняет сервер, сохраняя фазу между `commit` (иначе на каждом
  стыке появляется щелчок — это отмечено в SpeechRail как обязательное требование).
- Никакого base64, никакого JSON на кадр. Контроль — только текстовыми кадрами.

### 12. `stream`: события

Все серверные события — JSON-текст с обязательными `type`, `seq` (монотонно +1 на соединение),
`event_id`. `seq` нужен для отладки и для обнаружения потери **финалов** (см. §13).

**Жизненный цикл:**

```json
{"type":"session.created", ...}
{"type":"session.updated", ...}
{"type":"session.closed","seq":57,"reason":"client_close",
 "stats":{"accepted_samples":812800,"audio_ms":50800,"utterances":3,
          "backend_ms":2410,"queue_wait_ms":37}}
```

**Вход:**

```json
{"type":"input.committed","seq":12,"commit_id":"cmt_4","utterance_id":"utt_2",
 "accepted_samples":48000}
{"type":"input.cleared","seq":13,"discarded_samples":16000}
```

**Высказывание и VAD:**

```json
{"type":"utterance.started","seq":14,"utterance_id":"utt_3","audio_offset":812800}
{"type":"speech.started","seq":15,"utterance_id":"utt_3","audio_offset":813120,"probability":0.87}
{"type":"speech.stopped","seq":19,"utterance_id":"utt_3","audio_offset":845440,"probability":0.11}
```

`speech.*` — акустическое наблюдение; `utterance.started` — решение о начале единицы работы.
Их нельзя сливать: Deepgram разделяет `SpeechStarted`/`UtteranceEnd` и `speech_final`,
и это правильно.

**Частичные и финальные гипотезы:**

```json
{"type":"transcript.partial","seq":16,"utterance_id":"utt_3","revision":0,
 "text":"перезапусти под","stable_prefix_chars":15,
 "audio_span":{"start":813120,"end":829440}}
```

```json
{"type":"transcript.partial","seq":17,"utterance_id":"utt_3","revision":1,
 "text":"перезапусти под в namespace","stable_prefix_chars":15,
 "audio_span":{"start":813120,"end":842240}}
```

```json
{"type":"transcript.final","seq":20,"utterance_id":"utt_3","revision":2,
 "text":"Перезапусти под в namespace ingress.",
 "audio_span":{"start":813120,"end":845440},
 "reason":"endpoint",
 "language":"ru",
 "segments":[{"start_ms":0,"end_ms":1010,"text":"Перезапусти под в namespace ingress."}],
 "commit_id":null}
```

Семантика, которую обязан соблюдать клиент:

1. `text` **заменяет** предыдущий текст того же `utterance_id` (не дополняет).
2. `revision` строго возрастает; событие с `revision` ≤ уже применённой **отбрасывается**.
3. `stable_prefix_chars` — длина префикса `text` в **Unicode code points**, который сервер
   гарантирует не менять в последующих ревизиях этого высказывания. `null` = гарантий нет.
   Это ровно то поле, которого не хватает всем остальным протоколам, и ровно то, что делает
   возможным корректный маппинг в append-only `delta` для [OI]-слоя.
4. `transcript.final` — единственное терминальное текстовое событие высказывания;
   ровно одно на `utterance_id`.
5. `reason` ∈ `endpoint | commit | max_duration | cancelled | error | closed`.

**Диаризация (opt-in):**

```json
{"type":"speaker.segment","seq":21,"utterance_id":"utt_3","speaker":"S1",
 "audio_span":{"start":813120,"end":845440},"transcript_revision":2,
 "confidence":0.93}
```
Может прийти после `transcript.final`; привязка — по `utterance_id` + `transcript_revision`.
Метки анонимны и живут только в сессии (`S1…S4`), никаких voiceprint-идентификаторов.

**Служебные:**

```json
{"type":"heartbeat","seq":30,"t":"2026-10-25T09:31:17Z"}
{"type":"warning","seq":31,"code":"diarization_unavailable",
 "message":"diarization requested but backend not ready; continuing without speaker labels"}
```

`warning` — для деградаций, которые не отменяют результат. Это важное отличие от
SpeechRail-подхода «либо отдельный `*.failed`, либо ничего»: клиенту нужен способ узнать,
что часть запрошенного не выполнена, не теряя основной результат.

### 13. `stream`: отмена, закрытие, backpressure, idle

**Отмена.**

```json
→ {"type":"utterance.cancel","event_id":"evt_x","utterance_id":"utt_3"}
← {"type":"transcript.final","seq":22,"utterance_id":"utt_3","revision":3,
   "text":"перезапусти под в","audio_span":{"start":813120,"end":829440},
   "reason":"cancelled"}
```

- Отмена **приоритетнее** приёма аудио: она не встаёт в очередь за уже отправленными кадрами.
- После отмены для этого `utterance_id` разрешено ровно одно терминальное событие и больше
  ничего.
- Если отменяемое высказывание уже получило финал — `409 invalid_state` (клиент опоздал,
  и это лучше, чем тихо «отменить» уже показанный текст).
- `session.close` с `flush: true` — дослать остаток и завершить все открытые высказывания
  финалами; `flush: false` — оборвать.

**Закрытие.**

```json
→ {"type":"session.close","event_id":"evt_y","flush":true}
← {"type":"session.closed","seq":57,"reason":"client_close","stats":{...}}
← WS close 1000
```

Причины `reason`: `client_close | idle_timeout | server_shutdown | error | capacity`.
Сервер всегда шлёт `session.closed` **до** close-кадра (иначе клиент не отличит «сервер
упал» от «сервер попросил уйти»).

**Backpressure.** Три независимых правила:

1. *Клиент → сервер (аудио).* Сервер не буферизует больше `max_inflight_audio_ms`.
   При превышении — перестаёт читать из сокета (естественный TCP-backpressure), а если клиент
   продолжает давить дольше `overflow_grace_ms` (2000 мс) — `error{code:"audio_overflow"}`
   и close `1009`. Молчаливое накопление в память запрещено: это главный способ уронить
   локальный демон одним клиентом.
2. *Сервер → клиент (события).* Частичные гипотезы **можно** выбрасывать/схлопывать: они
   заменяемы по определению, а `revision` делает потерю наблюдаемо безвредной. Финалы
   выбрасывать нельзя; если финал не удалось доставить за `final_delivery_timeout_ms` (5000 мс),
   сервер закрывает соединение `1011` с `backpressure_final_lost` и пишет это в `stats`.
3. *Мультиплексирование.* В v1 — одна сессия на соединение; несколько сессий = несколько
   соединений (дёшево на loopback/UDS). Мультиплексирование доступно только через gRPC-проекцию,
   где его даёт HTTP/2.

**Idle.** Таймер простоя считается по **данным** (бинарные кадры и текстовые команды),
но не по WS ping/pong — иначе прокси-«пустышки» будут держать сессии вечно (WhisperLiveKit
делает ровно так: «ping/pong frames do not reset this timer»). Сервер шлёт `heartbeat`
каждые `heartbeat_ms` (15000), чтобы клиент обнаружил мёртвый сервер без ожидания TCP-таймаута.
Это не косметика: speech-swift был вынужден отключить `autoPing` и завести
`realtime.keepalive` именно потому, что контрольные кадры ненадёжно доходят до
`URLSessionWebSocketTask`.

### 14. `detectSpeech`

`POST /v1/detect-speech`

```json
{
  "model": "silero-vad-v5",
  "audio": {"source": "file"},
  "threshold": 0.5,
  "neg_threshold": null,
  "min_speech_ms": 250,
  "min_silence_ms": 500,
  "speech_pad_ms": 0,
  "max_speech_ms": 30000
}
```

```json
{
  "duration_ms": 12840,
  "segments": [
    {"start_ms": 320, "end_ms": 4210},
    {"start_ms": 4980, "end_ms": 12760}
  ],
  "model": "silero-vad-v5",
  "timing": {"inference_ms": 41}
}
```

Замечание: у speaches этот эндпоинт называется `/v1/audio/speech/timestamps` — это его
собственное изобретение, не [OI], поэтому совместимость с ним не обязательна. Нативно —
`/v1/detect-speech` (глагол в пути, как `detectSpeech` в задаче).

### 15. `diarize`

`POST /v1/diarize`

```json
{"model": "sortformer-v2:fp16", "audio": {"source": "file"},
 "max_speakers": 4, "min_segment_ms": 250, "return_embeddings": false}
```

```json
{
  "duration_ms": 12840,
  "speakers": [{"label": "S1", "speech_ms": 6100}, {"label": "S2", "speech_ms": 4200}],
  "segments": [
    {"start_ms": 320, "end_ms": 4210, "speaker": "S1", "confidence": 0.94},
    {"start_ms": 4980, "end_ms": 12760, "speaker": "S2", "confidence": 0.88}
  ],
  "model": "sortformer-v2:fp16",
  "timing": {"inference_ms": 1180}
}
```

правила: метки анонимны и локальны для ответа; `confidence` обязателен (иначе клиент не может
отличить уверенную атрибуцию от «угадали»); при превышении `max_speakers` — не создавать новую
метку, а оставлять сегмент без `speaker` (так же, как SpeechRail описывает diarization-бюджет:
превышающие и неуверенные голоса остаются нераспознанными, вместо того чтобы переиспользовать
или ротировать уже выданные метки), и отразить это в `speakers[].overflow_segments`.

### 16. `speakerEmbedding`

`POST /v1/speaker-embeddings`

```json
{"model": "wespeaker-resnet34:fp16", "audio": {"source": "file"}, "normalize": true}
```

```json
{
  "object": "list",
  "data": [{"object": "embedding", "index": 0, "embedding": [0.0131, -0.0842, 0.0559]}],
  "model": "wespeaker-resnet34:fp16",
  "dimensions": 256,
  "usage": {"type": "duration", "seconds": 3.21}
}
```

Форма повторяет [OI]-embeddings (и speaches), потому что это единственная знакомая клиентам
схема. **Идентичность спикера — ответственность клиента**: runtime не хранит voiceprint-базу
и не выдаёт стабильных идентификаторов между запросами.

### 17. `health`

```http
GET /v1/health
200 OK
{"status":"ok","version":"0.1.0","uptime_s":913,"models_loaded":1,"sessions_active":2}
```

Требования: не трогать модели, не сканировать диск, отвечать за единицы миллисекунд
(это же health-проба супервизора). Детали — только по явному запросу:
`GET /v1/health?detail=true` (добавляет `engines[]`, `memory`, `disk_free_bytes`).

### 18. [OI]-совместимый слой: таблица соответствия

Монтируется на **свои** пути, чтобы не путаться с нативными:

| [OI] | Наш обработчик | Что теряется / чем платим |
|---|---|---|
| `POST /v1/audio/transcriptions` | `transcribe` | base64 не нужен (multipart); `duration` в float-секундах |
| `POST /v1/audio/translations` | `transcribe` + `task=translate` | только если модель умеет |
| `POST /v1/audio/speech` | TTS (вне этой задачи) | — |
| `GET /v1/models` | `listModels` | отдаём надмножество; лишние поля [OI]-клиент игнорирует |
| `WS /v1/realtime?intent=transcription` | `stream` + переименование | base64-кадры, 24 кГц, отсутствие ревизий |
| `POST /v1/realtime/transcription_sessions` | локальный эфемерный токен | смысла в эфемерности на loopback мало, но клиенты этого ждут |

**Маппинг событий (native → [OI]):**

| native | [OI] |
|---|---|
| `session.created` | `session.created` |
| `session.updated` | `session.updated` |
| `utterance.started` | `input_audio_buffer.committed`? **Нет** — не эмитим |
| `speech.started` | `input_audio_buffer.speech_started` |
| `speech.stopped` | `input_audio_buffer.speech_stopped` |
| `transcript.partial` | `conversation.item.input_audio_transcription.delta` — **только если** `stable_prefix_chars` не `null` и префикс монотонно растёт |
| `transcript.final` | `conversation.item.input_audio_transcription.completed` |
| `error` | `error` |
| `input.committed` | — (у [OI] нет такого события) |

Три жёстких правила совместимости:

1. **Не выдумывать дельты.** Если модель не даёт доказуемо стабильного префикса — [OI]-клиент
   получает только `.completed`. Это законно (дельты в [OI] — «returned when the text value is
   updated», их отсутствие не нарушает контракт) и честно.
2. **Не выдумывать `speech_started`.** Если `vad.mode = manual`, [OI]-клиент не получит
   VAD-событий; врать про акустику нельзя.
3. **Ошибки на [OI]-путях — в конверте [OI]**, `{"error":{"message","type","code","param"}}`,
   а не problem+json. Problem+json остаётся на нативных путях. Один и тот же `code` внутри.

Что **нельзя** сделать через [OI]-слой и что должно быть нативным:

- `commit` с подтверждением «сколько сэмплов принято» (у [OI] `commit` не возвращает
  `accepted_samples`; speaches эмитит `committed`, но без водяного знака);
- смена модели в середине сессии;
- выбор `engine`/`precision`;
- явный `load`/`unload` с `refs`;
- `detectSpeech`/`diarize`/`speakerEmbedding` как отдельные операции (у [OI] их нет);
- ревизии частичных гипотез и `stable_prefix_chars`;
- отмена отдельного высказывания (у [OI] отмена = только `response.cancel` для ответов модели).

### 19. gRPC-проекция

Один сервис, один пакет, те же `code` в `details`:

```proto
syntax = "proto3";
package speech.v1;

service SpeechRuntime {
  rpc GetCapabilities (CapabilitiesRequest) returns (Capabilities);
  rpc ListModels     (ListModelsRequest)    returns (ListModelsResponse);
  rpc LoadModel      (LoadModelRequest)     returns (LoadModelResponse);
  rpc UnloadModel    (UnloadModelRequest)   returns (UnloadModelResponse);
  rpc PullModel      (PullModelRequest)     returns (stream PullProgress);

  rpc Transcribe      (TranscribeRequest)      returns (TranscribeResponse);
  rpc TranscribeStream(stream StreamRequest)   returns (stream StreamEvent);
  rpc DetectSpeech    (DetectSpeechRequest)    returns (DetectSpeechResponse);
  rpc Diarize         (DiarizeRequest)         returns (DiarizeResponse);
  rpc SpeakerEmbedding(SpeakerEmbeddingRequest) returns (SpeakerEmbeddingResponse);
}

message StreamRequest {
  oneof payload {
    SessionConfig config = 1;   // первым сообщением, как в Google STT
    AudioChunk    audio  = 2;   // 20–100 мс PCM16LE
    Control       control = 3;  // commit / clear / cancel / close / heartbeat
  }
}

message AudioChunk { bytes pcm = 1; uint64 first_sample = 2; }  // first_sample — для проверки порядка

message StreamEvent {
  uint64 seq = 1;
  string event_id = 2;
  oneof event {
    SessionCreated  created  = 10;
    SessionUpdated  updated  = 11;
    InputCommitted  committed = 12;
    SpeechStarted   speech_started = 13;
    SpeechStopped   speech_stopped = 14;
    TranscriptPartial partial = 15;
    TranscriptFinal   final   = 16;
    SpeakerSegment  speaker  = 17;
    Warning         warning  = 18;
    ErrorEvent      error    = 19;
    SessionClosed   closed   = 20;
    Heartbeat       heartbeat = 21;
  }
}

message TranscriptPartial {
  string utterance_id = 1;
  uint32 revision = 2;
  string text = 3;
  optional uint32 stable_prefix_chars = 4;
  AudioSpan audio_span = 5;
}
```

Замечания по проекции:

- gRPC даёт то, чего нет у WS: мультиплексирование сессий в одном HTTP/2-соединении,
  flow control в обе стороны, дедлайны на уровне вызова, типизированные ошибки.
- Цена: кодогенерация в сборке, невозможность `curl`, отсутствие браузерного клиента,
  необходимость держать `.proto` и JSON-схему в синхроне (единственный способ — генерировать
  JSON-схему из `.proto` или наоборот, а не писать обе руками).
- `first_sample` в `AudioChunk` — дешёвая проверка порядка/потерь, которой нет в WS-версии
  (там порядок гарантирован TCP, и счётчик ведёт только сервер).

### 20. Сводная таблица операций и транспортов

| Операция | HTTP | WS `speech.v1` | gRPC | [OI] слой |
|---|---|---|---|---|
| `transcribe` | `POST /v1/transcribe` | — | `Transcribe` | `POST /v1/audio/transcriptions` |
| `stream` | — | `GET /v1/stream` | `TranscribeStream` | `WS /v1/realtime?intent=transcription` |
| `detectSpeech` | `POST /v1/detect-speech` | событие `speech.*` | `DetectSpeech` | — |
| `diarize` | `POST /v1/diarize` | событие `speaker.segment` | `Diarize` | `diarized_json` (только batch) |
| `speakerEmbedding` | `POST /v1/speaker-embeddings` | — | `SpeakerEmbedding` | — |
| `listModels` | `GET /v1/models` | — | `ListModels` | `GET /v1/models` |
| `loadModel` | `POST /v1/models/{id}/load` | — | `LoadModel` | — |
| `unloadModel` | `DELETE /v1/models/{id}/load` | — | `UnloadModel` | — |
| `install`/`pull` | `POST /v1/models/pull` + `GET /v1/operations/{id}` | — | `PullModel` | — |
| `remove` | `DELETE /v1/models/{id}` | — | — | — |
| `health` | `GET /v1/health` | `heartbeat` | — | — |
| `capabilities` | `GET /v1/capabilities` | `session.created.capabilities` | `GetCapabilities` | — |

---

## Ограничения и риски

1. **`stable_prefix_chars` — обещание, а не измерение.** Если движок не может доказать
   стабильность префикса, единственный честный ответ — `null`. Риск: соблазн «приблизить»
   стабильность эвристикой и получить мигающий текст в UI. Требование: значение выставляет
   только адаптер движка, и только при наличии доказательства (общий префикс двух последних
   декодирований + отсутствие правок в этой зоне в пределах окна). `[INFERENCE]`: конкретный
   критерий зависит от движка и должен быть измерен (см. следующий раздел).

2. **Одна сессия на соединение — это осознанное упрощение.** Оно стоит файловых дескрипторов
   и памяти на сессию. При 32 одновременных сессиях это нормально, при 300 — нет. Если
   появится сценарий «сотни коротких стримов», придётся вводить мультиплексирование в сам
   протокол (и это будет `speech.v2`, а не аддитивное расширение, потому что меняется
   адресация кадров).

3. **base64 в [OI]-слое — постоянный налог.** Для 24 кГц mono PCM16 это ~48 КБ/с → ~64 КБ/с
   base64 и лишний цикл кодирования на каждый кадр в клиенте. На накладные расходы в простое
   не влияет, на CPU при стриминге — влияет. Уменьшается только размером кадров, не отменяется.

4. **WS не даёт flow control.** Все правила backpressure — договорные. Клиент, который их
   игнорирует, будет отключён, а не «замедлен». Это может выглядеть как «сервер рвёт
   соединение без причины», поэтому `error` с `audio_overflow` и `stats` в `session.closed`
   обязательны.

5. **XPC и Wyoming закрепляют платформу/отсутствие безопасности.** XPC — только Apple и только
   через launchd; Wyoming — без аутентификации по дизайну, поэтому его можно поднимать только
   на loopback/UDS и только как явно включённый адаптер.

6. **gRPC-проекция удваивает поверхность контракта.** Два описания (proto + JSON) неизбежно
   расходятся, если не генерировать одно из другого. Риск поддержки выше, чем выгода, пока
   нет ни одного клиента, которому нужен именно gRPC.

7. **`response_format`-совместимость хрупка.** `verbose_json` в [OI] отдаёт секунды float,
   `diarized_json` — строковые метки спикеров; наш нативный формат — миллисекунды и `S1…Sn`.
   Конвертация туда-обратно в адаптере должна быть покрыта тестами на побайтовое совпадение
   эталонных примеров из спецификации.

8. **Токен в файле — не граница безопасности между пользователями одного Mac.** Любой процесс
   с тем же uid прочитает файл. Для мультипользовательской машины настоящая граница — UDS с
   `0660` + группа + `getpeereid()`. Это надо писать в документации, а не подразумевать.

9. **`pull` требует сети и лицензионного согласия.** Контракт обязан иметь `accept_license`
   и `403 license_not_accepted`, иначе установка «просто работает» и юридический риск
   переезжает в runtime.

---

## Выводы для Runtime

### Что взять

| Источник | Что берём | Почему |
|---|---|---|
| SpeechRail | fail-closed на неизвестных полях; `revision` + `stable_prefix_codepoints`; `audio_span` в сэмплах; `accepted_samples` в commit-квитанции; отмена приоритетнее приёма; одна терминальная точка на utterance | единственный контракт в наборе, который решает проблему честных частичных гипотез и «сколько аудио вы реально приняли» |
| Deepgram (захват) | бинарные кадры; `is_final`/`speech_final` как разные флаги; `Metadata` как терминальная сводка; `CloseStream` как явный конец; пустой бинарный кадр = ошибка | проверено на живом трафике, включая таймауты и форму сводки |
| WhisperLiveKit | управляющий цикл: idle-таймаут по данным, а не по ping/pong; неизвестные control-сообщения игнорируются; `Finalize` явно отвергается с объяснением | лучший образец обработки управляющих сообщений |
| [OI] | имена и дефолты `turn_detection`; `usage`-блок; `transcript.text.*` для SSE; `diarized_json` для совместимости | бесплатная совместимость там, где она не стоит ничего |
| Google STT | `stability` как идея «насколько результат устойчив»; конфиг первым сообщением в стриме; «нет аудио — нет событий» | подтверждает, что пустые промежуточные результаты не нужны |
| Wyoming | `describe/info` как отдельный запрос возможностей; «chunk заменяет предыдущий» | доказательство, что replace-семантика — норма |
| speaches | `SelfDisposingModel` (refcount + TTL) как модель резидентности; `/api/ps` как наблюдаемость | работает и наблюдаемо снаружи |
| speech-swift | app-level `heartbeat` вместо WS ping; отказ от `autoPing` из-за `URLSessionWebSocketTask` | конкретный Swift-грабли, проверенные на живом коде |

### Что не брать

- **Конфиг в query-параметрах** (WhisperLiveKit, AssemblyAI): не проходит схему, попадает
  в логи, не даёт атомарного обновления. Только `session.configure` сообщением.
- **Токен в query** (speaches, Deepgram-браузерный вариант): утечка в логи/историю.
- **409 на повторную загрузку модели** (speaches): ломает идемпотентность.
- **`[OI]-Beta`-стиль версионирования заголовком**: путь проще и виднее в логах.
- **Эхо запроса вместо эффективного конфига**: клиент должен видеть, что реально включилось.
- **Пустые промежуточные результаты во время тишины** (Deepgram): трафик и мигание UI
  без информации.
- **Снимок всего состояния на каждое обновление** (WhisperLiveKit): O(размер сессии) на тик,
  и клиент обязан делать diff сам.
- **Единый `api_key` без учёта uid** как единственная защита: на UDS есть `getpeereid()`.

### Рекомендация транспортов

**Основной: HTTP/1.1 + WebSocket по `tcp://127.0.0.1:<port>` с bearer-токеном.**

Почему основной именно он:
- Swift получает всё бесплатно: `URLSession` для REST, `URLSessionWebSocketTask` для стрима,
  `Codable` для схемы, ноль сторонних зависимостей. Для Apple-платформенного продукта это
  решающий аргумент — иначе каждая сторонняя программа обязана тащить свой HTTP-стек.
- Браузер (для отладки, для web-UI) тоже работает без адаптеров.
- `curl` работает — а значит, любой баг воспроизводим одной командой.
- Порт публикуется в `runtime.json`, поэтому «какой порт» — не проблема клиента.

**Первый класс (тот же протокол, другой адрес): `unix://` UDS.**

- Для Rust: `hyper`/`axum` + `hyper-util` с `UnixStream`, или `tokio` + `Tower` — стандартно.
- Для CLI/скриптов: `curl --unix-socket`.
- Даёт kernel-enforced идентичность (`getpeereid`) и файловые права вместо токена.
- Для Swift — только если клиент использует `NWConnection(.unix)` + собственный HTTP/1.1
  (реализуется один раз в официальном клиенте и дальше не виден приложениям).
- Ограничение: путь ≤103 символа (macOS `sun_path[104]`).

**Дополнительный: gRPC bidi (по любому из адресов).**

- Для Rust: `tonic` 0.14 — `connect_with_connector` для UDS, `serve_with_incoming` для сервера.
- Для Swift: `grpc-swift` 2.4.4 + `GRPCNIOTransportHTTP2Posix` (UDS поддержан в Posix-варианте,
  `SocketAddress.unixDomainSocket(path:)`).
- Когда оправдан: нужен типизированный контракт с кодогенерацией; нужно мультиплексировать
  много сессий в одном соединении; нужны дедлайны и flow control «из коробки».
- Когда не оправдан: единственный клиент — Swift-приложение; тогда это лишний build-step
  и потеря `curl`-отладки.

**Не рекомендуется как основной: XPC.**

- Только Apple, только launchd, только Swift/ObjC. Swift on Linux его не имеет.
- Полезен ровно для одного: **on-demand запуск демона** через `MachServices` + проверка
  «кто меня разбудил» через `xpc_connection_get_pid/euid`. Это можно сделать отдельным
  маленьким XPC-«активатором», не трогая прикладной протокол.
- `[INFERENCE]`: XPC-активатор + прикладной HTTP/WS — рабочая комбинация, но требует
  отдельного прототипа, потому что XPC-сервис и сетевой сервер живут в разных жизненных циклах.

**Не рекомендуется как основной: Wyoming.**

- Нет аутентификации по дизайну, нет версий, `type` без пространства имён.
- Ценность — только интероп с Home Assistant. Реализуется как адаптер поверх того же
  внутреннего автомата сессии (audio-start/chunk/stop + transcript-chunk/transcript),
  с принудительным loopback/UDS.

### Как интегрировать

1. **Внутренний автомат сессии — единственный источник истины.** Один actor/структура:
   `SessionState { config, accepted_samples, utterances, model_ref }`. Все адаптеры
   (WS-native, WS-[OI], HTTP-batch, SSE, gRPC) вызывают его методы и подписываются на его
   события. Совместимость = маппинг событий, а не второй автомат.
2. **Модель удерживается refcount-ом на время сессии**, не на время запроса: `load` на
   `session.configure`, `unload` при `session.closed`. Иначе «переиспользование весов между
   клиентами» не работает для стримов.
3. **Аудио нормализуется на границе** (16 кГц mono для движка) с сохранением фазы ресемплера
   между `commit`; наружу отдаются только сэмплы в частоте клиента.
4. **Все лимиты — в `capabilities`/`session.created`**, ни одного магического числа в клиентах.
5. **Один словарь кодов ошибок** — общий для HTTP/WS/gRPC, генерируется из одного файла.
6. **Совместимые слои включаются флагом и не влияют на нативный путь**:
   `compat.openai_audio`, `compat.openai_realtime`, `compat.deepgram_listen` (по умолчанию
   выключен — это отдельный объём работы и отдельная поверхность атаки).

---

## Неизвестные / что измерить

1. **Стоимость простоя.** Не подтверждено ни одним источником: сколько RSS/CPU занимает
   демон с 0 загруженных моделей, с 0 сессий, и сколько — с одной открытой, но молчащей
   WS-сессией. Нужно измерить: `ps`/`footprint` при трёх состояниях, за 10 минут, на Apple
   Silicon. Это ключевая метрика задачи («минимальные накладные расходы в простое»).
2. **Задержка «первый байт аудио → первый partial»** для каждой модели-кандидата, отдельно
   от времени загрузки модели. Ни один источник в наборе не публикует эту цифру в сравнимом
   виде; `timing`-блок в наших ответах существует именно чтобы её собирать.
3. **Реальная достижимость `stable_prefix_chars`** для Whisper-подобных (attention encoder-decoder)
   и для streaming-transducer/CTC движков. `[INFERENCE]`: у transducer/CTC префикс стабилен
   по построению, у Whisper — нет (передекодирование окна меняет хвост и иногда середину).
   Нужен эксперимент: доля высказываний, где префикс длиной ≥ N code points не менялся
   до финала.
4. **Работает ли `NWProtocolWebSocket` поверх `NWEndpoint.unix`** на macOS 27. Не проверено;
   если да — UDS для Swift становится дешёвым. Если нет — только ручной HTTP/1.1.
5. **Может ли `URLSession` адресовать unix-сокет** через новый загрузчик (`usesClassicLoadingMode=false`,
   macOS 15.4+) хоть каким-то документированным способом. Документации нет; нужен
   экспериментальный билд.
6. **Поведение `URLSessionWebSocketTask.maximumMessageSize`** по умолчанию и цена его
   увеличения: документация говорит только «receive calls will fail once the task reaches
   this limit», значение по умолчанию не указано. Нужно измерить на кадре 64 КБ / 1 МБ.
7. **XPC-активатор**: реально ли launchd поднимает демон по первому `xpc_connection_create_mach_service`
   без задержки, заметной для аудио-старта, и как это соотносится с «первым кадром через 30 мс».
8. **Sandbox**: какой минимальный набор entitlement'ов нужен sandboxed-приложению, чтобы
   подключиться к app-group-контейнерному сокету. Требует подписанного билда.
9. **Порог «audio_overflow»**. Значение `max_inflight_audio_ms = 20000` — гипотеза, а не
   измерение. Нужно понять, при каком объёме необработанного аудио p99-задержка финального
   результата становится неприемлемой для конкретных моделей.
10. **Совместимость [OI]-слоя с реальными клиентами**: проверено только по спецификации
    `openai-openapi`; ни один реальный клиент (`openai-python`, Open WebUI, Whisper-клиенты)
    не запускался. Нужен прогон хотя бы трёх клиентов против нашего слоя.
11. **Deepgram-слой**: захват трафика есть только для `nova-2-phonecall` + `mulaw`/8000.
    `linear16`, `diarize`, `multichannel`, `Finalize`, `vad_events` — не подтверждены
    (в источнике помечены ⚠️). Если делать Deepgram-совместимость, нужен свой захват.
12. **`sherpa-onnx` как движок**: в этой задаче не проверялось, есть ли у него собственный
    серверный протокол, который стоит поддерживать как ещё один адаптер (см. A1-отчёт).

---

## Источники

**Спецификации и RFC**

- RFC 9457 «Problem Details for HTTP APIs» — https://www.rfc-editor.org/rfc/rfc9457
- RFC 6455 «The WebSocket Protocol», §7.4.2 (диапазоны кодов закрытия) —
  https://www.rfc-editor.org/rfc/rfc6455#section-7.4.2
- [OI] OpenAPI (`openai/openai-openapi`, `master`, 2026-10-25): `/audio/transcriptions`
  (L787–L872), `CreateTranscriptionRequest` (L49181–L49344), `AudioResponseFormat` (L41107–L41123),
  `CreateTranscriptionResponseDiarizedJson` (L49342–L49430), `CreateTranscriptionResponseVerboseJson`
  (L49485–L49545), `VadConfig` (L79683–L79712), SSE-пример (L1044–L1120),
  `TranscriptTextDeltaEvent`/`Done`/`Segment` (L78071–L78230), `/realtime/transcription_sessions`
  (L21406–L21495), `RealtimeTranscriptionSessionCreateRequest` (L69063–L69160),
  `RealtimeTranscriptionSessionCreateRequestGA` (L69163–L69222), `input_audio_buffer.append`
  (L60281–L60320), `…input_audio_transcription.delta` (L61300–L61336), `RealtimeTurnDetection`
  (L70347–L70455) — https://github.com/openai/openai-openapi/blob/master/openapi.yaml
- Google Cloud Speech-to-Text v2, `google.cloud.speech.v2` (StreamingRecognize,
  StreamingRecognitionResult, StreamingRecognitionFeatures) —
  https://cloud.google.com/speech-to-text/v2/docs/reference/rpc/google.cloud.speech.v2
- AssemblyAI, «Migrating from Streaming v2 to Streaming v3» —
  https://www.assemblyai.com/docs/streaming/guides/v2_to_v3_migration_js.md
- Wyoming protocol, README (формат кадра, события, `describe`/`info`, безопасность) —
  https://github.com/rhasspy/wyoming/blob/08a0c258df83fea6e1643bcfe67e86243f01cf31/README.md
- Apple TN3151 «Choosing the right networking API» —
  https://developer.apple.com/documentation/technotes/tn3151-choosing-the-right-networking-api
- Apple `NWEndpoint.unix(path:)` — https://developer.apple.com/documentation/network/nwendpoint/unix(path:)
- Apple `NWProtocolWebSocket` — https://developer.apple.com/documentation/network/nwprotocolwebsocket
- Apple `URLSessionConfiguration.usesClassicLoadingMode` —
  https://developer.apple.com/documentation/foundation/urlsessionconfiguration/usesclassicloadingmode
- Apple `URLSessionWebSocketTask.maximumMessageSize` —
  https://developer.apple.com/documentation/foundation/urlsessionwebsockettask/maximummessagesize
- Swift Forums, «[Pitch] New HTTP loader for URLSession on Darwin» (статус: Pitch, 2025-01-24) —
  https://forums.swift.org/t/pitch-new-http-loader-for-urlsession-on-darwin/77440
- tonic 0.14.6: `Endpoint` (connect_with_connector), `Server` (serve_with_incoming) —
  https://docs.rs/tonic/latest/tonic/transport/struct.Endpoint.html ,
  https://docs.rs/tonic/latest/tonic/transport/struct.Server.html
- Docker `dockerd`, «Daemon socket option» — https://docs.docker.com/reference/cli/dockerd/#daemon-socket-option
- Локальные man-страницы (macOS 27.0): `man 3 getpeereid`, `man 4 unix`, `man 5 launchd.plist`
  (`MachServices`), `man 3 xpc_connection_create_mach_service`
- macOS SDK: `sys/un.h:79` — `char sun_path[104]`

**Код (permalink'и по SHA из таблицы выше)**

- speaches: `src/speaches/routers/stt.py#L118-L173`, `src/speaches/text_utils.py#L25-L26`,
  `src/speaches/executors/whisper.py#L187-L226`,
  `src/speaches/executors/shared/base_model_manager.py#L37-L152`,
  `src/speaches/routers/realtime_ws.py#L56-L139`,
  `src/speaches/realtime/input_audio_buffer_event_router.py#L33,L107-L145`,
  `src/speaches/realtime/input_audio_buffer.py#L115-L172`,
  `src/speaches/routers/vad.py#L24-L100`, `src/speaches/routers/speech_embedding.py#L27-L52`,
  `src/speaches/routers/misc.py#L27-L104`, `src/speaches/dependencies.py#L59-L123`,
  `src/speaches/main.py#L158-L186`, `src/speaches/realtime/utils.py#L53-L75`,
  `src/speaches/realtime/session.py#L9`, `src/speaches/types/realtime.py#L250-L410`
- WhisperLiveKit: `whisperlivekit/basic_server.py#L96-L260`,
  `whisperlivekit/deepgram_compat.py#L60-L170,L425-L600,L1020-L1136`,
  `whisperlivekit/timed_objects.py#L138-L232`, `docs/API.md#L375-L430`
- SpeechRail: `contracts/realtime-openai.md` (весь файл, ключевое — §1–§9),
  `contracts/realtime-field-matrix.json`, `contracts/openapi.yaml#L197-L262`
- gotranscribesrv: `testing-proxy-dg/DEEPGRAM_PROTOCOL.md#L1-L279`,
  `internal/handlers/deepgram.go#L64-L140`, `go.mod`
- speech-swift: `Sources/AudioServer/AudioServer.swift#L44-L80,L119-L150,L369-L395,L920-L1000,L1572-L1573`
- grpc-swift-nio-transport: `Package.swift#L20-L32`,
  `Sources/GRPCNIOTransportCore/Client/Resolver/SocketAddress.swift#L120-L150`,
  `Sources/GRPCNIOTransportHTTP2Posix/NIOClientBootstrap+SocketAddress.swift#L25-L35`

**Смежные отчёты волны 1**

- `research/wave1/G-model-management.md` — реестр моделей, CAS, pull/GC, резидентность.
- `research/wave1/C-speechrail.md` — архитектура SpeechRail, процессная модель, контракт v7.1.0.
- `research/wave1/A2-whisperlivekit-speaches.md` — реализация [OI]-совместимости в Python-серверах.
- `research/wave1/D2-streaming-asr.md` — потоковые движки и их пригодность к частичным результатам.
