# R1 — русские ASR-корпуса с референсами: что лицензионно пригодно для итерации 2

Дата среза: **2026-10-28**. Ссылки проверялись HTTP GET в этот день; недоступное помечено явно. Лицензии цитируются дословно; «не указана» — это не вывод о правовом статусе, а факт отсутствия поля.

Дополняет [`wave2/V8-eval-plan.md`](../wave2/V8-eval-plan.md) §2.1. Уточнения к V8: точные часы Common Voice ru v27.0; дословный текст лицензии Golos (это **не** NC, а собственная BY-SA-подобная с ShareAlike и запретом сублицензирования); отсутствие публичных датасетов GigaAM; `taiga_speech*` — не доклады, а TTS-озвучка.

## Таблица

| Набор | Лицензия (дословно) | Референсы | Объём | Домен / IT | Доступ | Speaker-disjoint | Блокеры |
|---|---|---|---|---|---|---|---|
| **Common Voice ru, v27.0** (`common-voice/cv-dataset`) | «CC0 license» (блог Mozilla Foundation о релизе 18) | `validated`: «two or more validations where `up_votes` > `down_votes`» | 293.15 ч / **256.8 ч валидированных**, 203 534 клипа, 7 093 766 588 байт | чтение общих предложений; IT не целевая | **только Mozilla Data Collective** с 10.2025 | да (`client_id`) | домен `datacollective.mozillafoundation.org` из этой сети **не открылся (timeout)**; лицензия заявлена в блоге, не в карточке HF |
| **FLEURS ru_ru** (`google/fleurs`, `70bb2e84`) | `cc-by-4.0` | предложения FLoRes, начитанные носителями; «Speakers of the train sets are different than speakers from the dev/test sets» | 3 693 записи (2 562 / 356 / 775), 2.64 ГБ parquet, ≈12 ч | википедийная лексика; IT нет | HF datasets-server / parquet | да (по сплитам) | мало; нет IT-терминов |
| **Golos** (`SberDevices/golos`) | «Публичная лицензия с указанием авторства и с сохранением условий»; PDF: «**This license is not a Creative Commons license**», переработка CC BY-SA 4.0; §2(a)(1) «non-sublicensable»; §3(b)(1) требует ShareAlike для Adapted Material; NC-условия нет | «manually annotated on the crowd-sourcing platform» | 1 227.4 ч train + 12.6 ч test; 20.5 ГБ opus, ≈144 ГБ wav | crowd-начитка и farfield-команды; IT нет | прямые ссылки `sc.link/*`; HF-репозиторий пуст | **не заявлен** (`speaker_id` в README нет) | ShareAlike, без сублицензирования; правовой статус — к юристу |
| **OpenSTT** (`snakers4/open_stt`) | «CC-BY-NC and commercial usage available after agreement» | вручную только val: 7.7 + 4.9 + 4.5 ч; остальное align/subtitles/ASR | 20 108 ч, 2 369 ГБ wav | radio, youtube, книги, телефония | торрент, Azure, Zenodo | **нет** («Speaker labels coming soon») | NC: нельзя ни в продукт, ни в публичные фикстуры |
| **Russian LibriSpeech** (OpenSLR SLR96) | «The dataset is Public Domain in the USA» | текст LibriVox + выравнивание | ≈98 ч, 9.1 ГБ | аудиокниги; IT нет | прямая ссылка OpenSLR | по чтецу/книге, поле не опубликовано | домен далёк от IT; PD только «in the USA» |
| **SOVA RuDevices** (`sovaai/sova-dataset`) | «Creative Commons BY 4.0» | «manual annotating», 98–99 % | 101 ч, 10.42 ГБ | речь «на колонку»; IT нет | Yandex.Disk по ссылке | не заявлен | нет IT |
| **`bond005/podlodka_speech`** (`4a916148`) | **не указана**: поля `license` в карточке нет | происхождение не описано | 107 сегментов (67/20/20), 188 МБ | **русский IT-подкаст** | HF parquet | по `episode` | прав на распространение нет; 107 сегментов мало для теста |
| **GigaAM «датасеты»** | — | — | — | — | **не опубликованы** (у `ai-sage` один датасет — английский `TimeGround-1M`) | — | тренировочные данные (700k ч pretrain / 4k ч ASR) закрыты |
| `bond005/taiga_speech_v2` | не указана | — | 12 314 записей, 2.59 ГБ | **не IT**: новости, озвученные TTS-голосом | HF | по `voice` | синтетика; название вводит в заблуждение |
| `MaratDV/russian-call-center-speech-ru` | `other` / `commercial-use`; «Redistribution … is not permitted» | **транскриптов не заявлено** | 832 ч, 9 215 файлов; IT/Software 86.18 ч | колл-центры | HF | нет | без референсов WER не считается |
| `SpeechDataAI/russian-conversational-speech` | `other` (`speechdata-commercial`) | «time-aligned human-verified transcripts» | 1 000 ч, 100 дикторов | диалоги, не IT | по заявке, **$85/ч** | да | платно; IT-лексика не заявлена |

## Как легально получить русскую IT-речь

1. **Готового CC-корпуса русской IT-речи нет.** Проверены HF (podlodka, habr, russian podcast, russian asr/speech, sova, dusha), OpenSLR, каталог SOVA, Mozilla/Golos/OpenSTT. IT-домен есть только у `bond005/podlodka_speech` (без лицензии) и в коммерческом колл-центр-корпусе (без транскриптов).
2. **YouTube — не источник прав по умолчанию.** Справка YouTube: «The standard YouTube license is the default setting for all YouTube uploads», «YouTube can't grant you rights to use someone else's content». CC BY — только при явном выборе автором. Текст `youtube.com/t/terms` из этой сети не открылся, поэтому утверждений о запрете скачивания здесь нет.
3. **TED/TEDx — CC BY-NC-ND 4.0**, причём «no derivative works … you cannot edit, remix, create, modify or alter the form», и правки транскриптов отдельно запрещены. Для ASR-оценки это ограничение читается буквально.
4. **Викимедиа-записи — рабочий CC-канал, но не IT.** Пример: запись русской вики-конференции на Wikimedia Commons, `CC BY-SA 4.0` (ShareAlike).
5. **Собственная запись** — единственный путь с полностью известными правами: согласие дикторов, отсутствие третьих прав, письменная фиксация условий. Совпадает с V8 §2.2 (R1–R4, 5–7 ч test).
6. **Договорные пути:** письменное разрешение Podlodka (они продают записи), коммерческая лицензия OpenSTT, покупка `SpeechDataAI` ($85/ч, human-verified, но домен не IT).

## Выводы

1. Лицензионно чистых и одновременно человечески проверенных наборов три: **Common Voice ru** (CC0, 256.8 ч валидированных), **RuLS** (PD in the USA, 98 ч), **FLEURS ru** (CC BY 4.0, ≈12 ч). Golos добавляет 1 227 ч, но под собственной BY-SA-подобной лицензией без сублицензирования.
2. **Ни один публичный набор не покрывает IT-лексику.** Единственный IT-датасет — 107 сегментов Podlodka без лицензии: годится как dev-калибровка только после письменного разрешения.
3. OpenSTT (20 108 ч) выпадает из продуктового контура: NC и нет speaker labels.
4. Блокер Common Voice организационный, не лицензионный: CC0, но выдача только через Mozilla Data Collective, недоступный из этой сети (timeout). Фактическую выдачу архива ru проверить не удалось.
5. «Датасеты SberDevices/GigaAM» как артефакт не существуют; `taiga_speech_v2` — TTS-озвучка новостей, не доклады.

## Рекомендация: минимально достаточный путь к итерации 2

1. **Якоря для сверки с публичными числами (без IT):** FLEURS ru test 775 записей — прямое сопоставление с таблицами GigaAM/Parakeet/Qwen3; RuLS 98 ч — объёмный PD-набор; Common Voice ru — как только решится доступ через MDC.
2. **IT-контур:** 4–6 ч собственной записи по регистрам V8 §2.2 с ручной транскрипцией двумя аннотаторами; 107 сегментов Podlodka — отдельный dev-набор **после** письменного разрешения.
3. **Спикеры:** CV — по `client_id`; FLEURS — официальные сплиты; RuLS — по чтецу/книге (размечать самим); Podlodka — по `episode`. Golos в тест не брать без подтверждённого speaker-disjoint.
4. **Не использовать** в тест-наборе: OpenSTT (NC), `taiga_speech*`, `MaratDV` (нет референсов), YouTube-видео без явной CC-метки.

## Проверено / не проверено

Проверено HTTP GET: FLEURS (`cardData.license = cc-by-4.0`, `sha 70bb2e84`), CV ru v27.0 (`totalHrs 293.15`, `validHrs 256.8`, `clips 203534`, 7 093 766 588 байт), отсутствие `license` у `bond005/podlodka_speech` (`sha 4a916148`), текст лицензии Golos (PDF `license/en_us.pdf`, 157 632 байта). Не проверено: фактическая выдача Common Voice через MDC (timeout), доступность `sc.link/*`, состав перезаливов Golos, качество референсов FLEURS против аудио.
