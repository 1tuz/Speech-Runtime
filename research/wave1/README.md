# Волна 1 — сбор фактов

18 параллельных агентов, направления A–K. Каждый отчёт: итог, факты с permalink'ами, ограничения, выводы для runtime, что осталось измерить, источники.

## Готовые проекты

| Отчёт | Тема | Главное |
|---|---|---|
| [A1-sherpa-onnx.md](A1-sherpa-onnx.md) | sherpa-onnx как сервер и как ONNX-движок | 19 offline- и 6 online-семейств моделей, батч `DecodeStreams`, hotwords только у трансдьюсеров; **CoreML EP выключен в macOS-сборках**; сервер — одна модель на процесс |
| [A2-whisperlivekit-speaches.md](A2-whisperlivekit-speaches.md) | WhisperLiveKit и speaches | WLK: алгоритмы стриминга (LocalAgreement, SimulStreaming) и протокол; speaches: `refcount` + TTL жизненного цикла, проект заморожен с 2026-04 |
| [A3-gotranscribesrv-whisperkit.md](A3-gotranscribesrv-whisperkit.md) | gotranscribesrv и WhisperKit | gotranscribesrv — GPL-3.0 биллинговый шлюз без русского языка; WhisperKit: HTTP-сервер собирается только с `BUILD_ALL=1`, `ModelManager` — образец каталога |
| [A4-discovery.md](A4-discovery.md) | Поиск других проектов (22 шт.) | audio.cpp ближе всех по набору функций; LocalAI — эталон watchdog и gRPC-контракта бэкендов; Wyoming — единственный настоящий протокол |

## Аудиты кандидатов

| Отчёт | Тема | Главное |
|---|---|---|
| [B1-speech-swift-server.md](B1-speech-swift-server.md) | Серверная часть speech-swift | 9 HTTP + 1 WS маршрут; один `InferenceGate` на процесс (1 инференс), `ModelCache` без выгрузки, нет auth/CORS/Origin, ASR не потоковый |
| [B2-speech-swift-engines.md](B2-speech-swift-engines.md) | Библиотечная часть speech-swift | 61 модуль, 14 протоколов `AudioCommon`, матрица «модель × операция × движок × доступно через сервер», оценка подключения WhisperKit и FluidAudio |
| [C-speechrail.md](C-speechrail.md) | SpeechRail | Python-супервизор + MLX-воркеры по framed IPC; лучший манифест артефактов; batch XOR streaming, выбора модели нет |

## Модели и компоненты

| Отчёт | Тема | Главное |
|---|---|---|
| [D1-russian-asr.md](D1-russian-asr.md) | Русские ASR-модели | WER по наборам, пунктуация и регистр, латиница; **T-one не может выдать латиницу**; конверсии в ONNX/CoreML/MLX |
| [D2-streaming-asr.md](D2-streaming-asr.md) | Потоковые модели | Задержка и языки запечены в артефакт модели; «Parakeet Ultra» — пост-тренировка, не модель NVIDIA |
| [D3-apple-native.md](D3-apple-native.md) | Системные API Apple | В `SpeechTranscriber` русского **нет**, в `DictationTranscriber` есть; работает из CLI без entitlements; измеренный лаг 0.44 с median |
| [E1-vad-turn.md](E1-vad-turn.md) | VAD и конец реплики | Silero v6 по умолчанию, FireRedVAD вторым, Smart Turn v3.2 для EOT; паттерн «веса общие, состояние на сессию» |
| [E2-diarization-embeddings.md](E2-diarization-embeddings.md) | Диаризация и эмбеддинги | Nemotron-3-Diarization, Sortformer, pyannote, WeSpeaker/CAM++; дизайн реестра спикеров и граница с клиентом |

## Архитектура и API

| Отчёт | Тема | Главное |
|---|---|---|
| [F-swift-vs-rust.md](F-swift-vs-rust.md) | Swift vs Rust vs гибрид | Доступ к CoreML/MLX из Rust, потокобезопасность MLX и CoreML, замеры IPC и памяти процессов |
| [G-model-management.md](G-model-management.md) | Управление моделями | Ollama: CAS + manifest list по движкам, `refCount` + `keep_alive`; LM Studio TTL; дизайн реестра и раскладки на диске |
| [H-api-contract.md](H-api-contract.md) | Контракт API v1 | Три независимые оси (транспорт, фрейминг, семантика), partial как «замена с ревизией», ошибки RFC 9457, [OI]-совместимость |

## Язык, сообщество, право

| Отчёт | Тема | Главное |
|---|---|---|
| [I-ru-it-adaptation.md](I-ru-it-adaptation.md) | Адаптация к русскому IT-языку | Hotwords только у трансдьюсеров, CTC-WS и GPU-PB, prompt-биасинг, дообучение; ловушка charwise-словаря GigaAM |
| [J-community.md](J-community.md) | Опыт сообщества | Краши CoreML при двух моделях, `fatalError` в MLX, время компиляции ANE, память, провалы локальных серверов |
| [K-licenses-maintenance.md](K-licenses-maintenance.md) | Лицензии и активность | Sortformer v1 и DiariZen — non-commercial, TEN VAD запрещает «конкурирующее» развёртывание, gated-модели нельзя зеркалировать |
