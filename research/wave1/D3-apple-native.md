# D3. Системные речевые API Apple как адаптер Runtime (SpeechAnalyzer / SpeechTranscriber / DictationTranscriber / SpeechDetector / SFSpeechRecognizer)

Дата исследования: 2026‑10‑25.
Все локальные измерения выполнены на этой машине: Apple Silicon, **macOS 27.0 (build 26A428)**, **Xcode 27.1 (27A9275)**, **macOS SDK 27.0**. Зонды — отдельные Mach‑O бинарники на Swift в `/tmp/srr/probe/` (в проекте ничего не создавалось). Источник истины про API — `.swiftinterface` из SDK 27.0 и runtime‑значения; README/блогам не доверял.

---

## Итог

1. **Для русского языка новый движок Apple недоступен.** `SpeechTranscriber.supportedLocales` на macOS 27.0 — **45 локалей, `ru_RU` отсутствует**; `SpeechTranscriber.supportedLocale(equivalentTo: ru-RU)` → `nil`, `AssetInventory.status` → `unsupported`. Русский есть только у `DictationTranscriber` (54 локали, `ru_RU` и `uk_UA` присутствуют) — это **старая модель уровня on‑device SFSpeechRecognizer**, а не новый «Apple Intelligence»‑ASR.
2. **Русский ассет уже предустановлен в системе**: `DictationTranscriber.installedLocales` = `["en_US", "ru_RU"]`; распознавание работает без скачивания и без включения Siri/диктовки в настройках (это прямо обещано в WWDC25‑277).
3. **SpeechAnalyzer не требует app bundle, Info.plist, entitlements или TCC** для файлового/буферного входа. Проверено: голый CLI‑бинарник (`Bundle.main.bundleIdentifier == nil`), лежащий в `/private/tmp`, успешно транскрибирует файл; в логах `tccd` за время прогона — ноль обращений. То же работает из job'а, поднятого через `launchctl bootstrap gui/$UID`, и из ad-hoc‑подписанного **sandboxed** `.app`.
4. **SFSpeechRecognizer (legacy) для демона — блокер**: требуется `kTCCServiceSpeechRecognition` + `NSSpeechRecognitionUsageDescription`. Без ключа в Info.plist процесс падает (SIGABRT, exit 134) с системным сообщением TCC; с ключом, но без выданного разрешения, запрос **молча висит** (не вернул ни результата, ни ошибки за 60 с). Никакого prompt из фонового процесса без UI не будет.
5. **Модели реально шарятся между клиентами**: 6 одновременных процессов и 16 одновременных анализаторов в одном процессе на одной локали — все успешно, суммарное время не деградирует. RSS процесса остаётся **18–22 MB** даже на 66 с аудио и при 16 параллельных анализаторах — веса живут вне адресного пространства клиента (WWDC25 прямо говорит: «operates outside of your application's memory space»).
6. **Ассеты — системный общий кэш, не файлы приложения.** `ru_RU` предустановлен; скачанный в тесте `de_DE` для `SpeechTranscriber` — это cryptex‑DMG `UC_SPEECH_ASR_TRANSCRIPTION_DE…dmg`, **≈339 MB**, версия ассета `3100.50006.81732`. Скачивание 339 MB заняло **23.8 с**. Управлять версиями/удалением нельзя — только `reserve/release` и `status`; лимит **5** зарезервированных локалей на приложение (6‑й `reserve` → `nil`).
7. **Задержки (измерено, 16 kHz mono Int16, чанки 100 мс в реальном темпе, `DictationTranscriber` ru + volatileResults):** лаг волатильных результатов **median 0.44 с, p95 0.73 с, min 0.20 с**; финализация — через ≈0.5–0.8 с после конца речи. Батч: **RTF 0.019** (66.57 с аудио за 1.25 с), «холодный» первый результат ≈0.6–1.2 с (загрузка/маппинг модели).
8. **Таймстемпы — пословные**: атрибут `audioTimeRange` (`CMTimeRange`) на каждом run `AttributedString`; опционально `transcriptionConfidence`; `SpeechTranscriber` умеет ещё и `alternatives`. Таймкоды точны до сэмпла (WWDC).
9. **Чего в Speech.framework нет вообще**: диаризации, speaker‑эмбеддингов, трансляции, выбора модели по имени/пути, управления версиями моделей. `SpeechDetector` (VAD) — только внутренний гейтинг: при `reportResults: true` на 15.35 с аудио с 5 с тишины пришло **0 результатов**; анализатор **только** с `SpeechDetector` (без транскрайбера) — `analyzeSequence` **зависает** (не завершился за 15 с).
10. **Модель жизненного цикла жёсткая**: анализатор одноразовый (повторный `analyzeSequence` после `finalizeAndFinish` вернул `nil`, результатов нет), а переиспользование объекта‑модуля в новом `SpeechAnalyzer` **роняет процесс** (exit 133/SIGTRAP). Правильный паттерн демона: **новый модуль + новый анализатор на сессию**, веса держатся `ModelRetention.processLifetime`, цена тёплой сессии — 0.11 с на 9.8 с аудио.

---

## Факты

### A. Поверхность API и версии ОС

1. `SpeechAnalyzer` — `final public actor`, доступен с macOS 26.0 / iOS 26.0.
   `final public actor SpeechAnalyzer : Swift::Sendable` — SDK 27.0: `.../MacOSX27.0.sdk/System/Library/Frameworks/Speech.framework/Modules/Speech.swiftmodule/arm64e-apple-macos.swiftinterface:228`.
   Документация: <https://developer.apple.com/documentation/speech/speechanalyzer>.

2. В API ровно три модуля: `SpeechTranscriber`, `DictationTranscriber`, `SpeechDetector` (+ протоколы `SpeechModule`, `LocaleDependentSpeechModule`). Никаких «engine id», «model path», «diarization», «embedding» в разделе Modules нет: <https://developer.apple.com/documentation/speech>.

3. **Разделение по версиям ОС** (важно для адаптера):
   - macOS 26+: `SpeechAnalyzer`, `SpeechTranscriber`, `DictationTranscriber`, `SpeechDetector`, `AssetInventory`, `AnalysisContext`, `SpeechAnalyzer.Options.ModelRetention`.
   - **Только macOS 27+**: `SpeechAnalyzer.Options.ignoresResourceLimits` (`Speech.swiftinterface:456`), `AssetInputSequenceProvider` (`:672`), `AnalyzerInputConverter` (`:518`), `CaptureInputSequenceProvider` (`:719`), `SFSpeechError.Code.cannotConfigureAudioSystem` (`:707`).
   - Официальные «June 2026» изменения (то есть macOS 27 / iOS 27): <https://developer.apple.com/documentation/updates/speech>.

4. Один анализатор — одна входная последовательность: «The analyzer can only analyze one input sequence at a time» — <https://developer.apple.com/documentation/speech/speechanalyzer>.

5. Ограничение одновременных сессий есть и оно «консервативное»; превышение даёт `SFSpeechError.Code.insufficientResources`; обход — `Options(ignoresResourceLimits: true)` (macOS 27+), при этом «the system allows an unlimited number of analyzers… eventually exceed the system's actual capacity» — <https://developer.apple.com/documentation/speech/speechanalyzer>. Коды ошибок доступны как статические свойства: `noModel`, `assetLocaleNotAllocated`, `tooManyAssetLocalesAllocated`, `incompatibleAudioFormats`, `moduleOutputFailed`, `cannotAllocateUnsupportedLocale`, `insufficientResources`, `unexpectedAudioFormat`, `audioDisordered` — `Speech.swiftinterface:683–709`.

### B. Языки и русский (главный вывод)

6. Замер `SpeechTranscriber.supportedLocales` (macOS 27.0, 26A428) — **45 локалей, `ru_RU` нет**:
   `bn_IN, de_AT, de_CH, de_DE, en_AU, en_CA, en_GB, en_IE, en_IN, en_NZ, en_SG, en_US, en_ZA, es_CL, es_ES, es_MX, es_US, fr_BE, fr_CA, fr_CH, fr_FR, gu_IN, hi_IN, it_CH, it_IT, ja_JP, kn_IN, ko_KR, ks_IN, mai_IN, ml_IN, mr_IN, mul_IN, ne_IN, or_IN, pa_IN, pt_BR, pt_PT, ta_IN, te_IN, ur_IN, yue_CN, zh_CN, zh_HK, zh_TW`.
   `SpeechTranscriber.supportedLocale(equivalentTo: Locale("ru-RU"))` → `nil`; `SpeechTranscriber.isAvailable` → `true` (железо подходит, языка нет).

7. `DictationTranscriber.supportedLocales` — **54 локали, включая `ru_RU` и `uk_UA`** (полный список в `/tmp/srr/probe/probe.swift`, вывод: `ar_SA, ca_ES, cs_CZ, da_DK, de_AT, de_CH, de_DE, el_GR, en_*, es_*, fi_FI, fr_*, he_IL, hi_IN, hr_HR, hu_HU, id_ID, it_CH, it_IT, ja_JP, ko_KR, ms_MY, nb_NO, nl_BE, nl_NL, pl_PL, pt_BR, pt_PT, ro_RO, ru_RU, sk_SK, sv_SE, th_TH, tr_TR, uk_UA, vi_VN, yue_CN, zh_CN, zh_HK, zh_TW`).
   `DictationTranscriber.installedLocales` на этой машине: `["en_US", "ru_RU"]` — русский ассет уже стоит в системе.

8. `AssetInventory.status(forModules:)` для `ru_RU`: `SpeechTranscriber` → **`unsupported`**, `DictationTranscriber` → **`supported`** (и распознавание идёт без загрузки — см. п. 20).

9. Официальное обоснование разницы: `DictationTranscriber` «uses the same speech-to-text machine learning models as system dictation features do, or as `SFSpeechRecognizer` does when configured for on-device operation» и «does not support languages or locales that `SFSpeechRecognizer` only supports via network access» — <https://developer.apple.com/documentation/speech/dictationtranscriber>. Иными словами, для `ru_RU` это модель эпохи iOS 10‑ASR, а не новый SpeechTranscriber‑движок.
   WWDC25/277 добавляет, что пользователю **не нужно** включать Siri или клавиатурную диктовку в настройках для конкретного языка: <https://developer.apple.com/videos/play/wwdc2025/277/> («you will NOT need to tell your users to go into Settings and turn on Siri or keyboard dictation for any particular language»).

10. `SFSpeechRecognizer` (legacy) на этой машине: `supportedLocales()` — 62 локали, `ru-RU` присутствует; `SFSpeechRecognizer(locale: ru-RU).supportsOnDeviceRecognition == true`; `SFSpeechRecognizer.authorizationStatus() == 0` (notDetermined).

### C. Ассеты и «общий системный кэш моделей»

11. Модели — системные ассеты, скачиваемые с серверов Apple и **разделяемые между приложениями**: «Once you download, install, or use an asset, the system retains and updates it automatically, and shares it with other apps… The system may unsubscribe your app from assets that haven't been used in a while» — <https://developer.apple.com/documentation/speech/assetinventory>.
    Там же: «The system makes a certain number of locale-specific asset reservations available to your app to limit storage space and network usage».

12. Лимит резерваций замерен: `AssetInventory.maximumReservedLocales == 5`. Пятый `reserve(locale:)` вернул `true`, шестой (`pt_BR`) и седьмой (`nl_NL`) — `nil` (не бросили исключение, а вернули nil через `try?`). `release(reservedLocale:)` возвращает `true` и уменьшает счётчик.
    В SDK: `Speech.swiftinterface:33–43` (`maximumReservedLocales`, `reservedLocales`, `reserve`, `release`).

13. Резервации **не переживают перезапуск процесса у бинарника без bundle id**: первый прогон зарезервировал `de_DE`, следующий процесс увидел `reservedLocales == []`. У приложения с CFBundleIdentifier они, по документации, принадлежат приложению (`[INFERENCE]`: у беспакетного CLI «приложение» не идентифицируется, поэтому состояние не сохраняется).

14. Статусы: `unsupported / downloading / supported / installed` (Comparable) — `Speech.swiftinterface:44–53`, <https://developer.apple.com/documentation/speech/assetinventory/status>. На практике: `de_DE/ja_JP/zh_CN` для `SpeechTranscriber` → `supported` (можно скачать), после `downloadAndInstall()` → `installed`.

15. Реальный размер и вид ассета (`de_DE`, SpeechTranscriber): `/System/Library/AssetsV2/com_apple_MobileAsset_UAF_Speech_AutomaticSpeechRecognition/purpose_auto/f653e47b….asset` — **339 MB**, внутри `AssetData/Restore/UC_SPEECH_ASR_TRANSCRIPTION_DE_GENERIC_H17S_Cryptex.dmg` + `SecureAssetData/*.dmg`, `Info.plist` c `AssetSpecifier = com.apple.speech.asr.transcription.de`, `AssetVersion = 3100.50006.81732.13.303315,0`, `__ContainsCryptexContents = true`, `__RequiresPersonalization = true`. То есть это **cryptex, монтируемый системой**, а не файлы, с которыми работает приложение.

16. Скачивание `de_DE` из беспакетного CLI (без entitlements, без bundle id): `AssetInventory.assetInstallationRequest(supporting:)` → non‑nil, `downloadAndInstall()` → **23.8 s**, после чего `status == installed`. Никаких TCC/entitlement‑запросов не потребовалось. Прогресс доступен через `AssetInstallationRequest.progress` (`totalUnitCount` в моём прогоне был 0 — прогресс на момент вызова ещё не посчитан).
    Документация про консолидацию запросов: <https://developer.apple.com/documentation/speech/assetinstallationrequest>.

17. **Ловушка интеграции**: если ассет локали не установлен, `availableCompatibleAudioFormats` **пустой**, `SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith:)` → `nil`, а `analyzeSequence(from:)` падает с **`SFSpeechErrorDomain Code=3 "Audio format is not supported"`** (вводящее в заблуждение сообщение). Замерено на `fr_FR` и `es_ES` (оба `status == supported`, `formats.count == 0`). Автоматической докачки при анализе не происходит.
    Отдельно наблюдался один случай, когда прогон с неустановленным ассетом `DictationTranscriber(de_DE)` **завис на >300 с**, при этом каталог ассетов вырос на ~340 MB — то есть система в этот момент тянула ассет. Воспроизвести повторно не удалось (после появления файла ответ — мгновенная ошибка code 3). Считаю это недетерминированным поведением при параллельной загрузке.

### D. Форматы аудио

18. Все модули принимают **только 8 kHz или 16 kHz, 1 канал, Int16**:
    `SpeechTranscriber.availableCompatibleAudioFormats` → `[1 ch, 16000 Hz, Int16; 1 ch, 8000 Hz, Int16]`; у `DictationTranscriber` — то же; `bestAvailableAudioFormat` → `1 ch, 16000 Hz, Int16`. У `SpeechDetector` — `0 ch, 0 Hz` (формат определяет транскрайбер).
    Следствие для Runtime: обязательный ресемплинг из 44.1/48 kHz Float32 в 16 kHz Int16 на входе адаптера.

19. `analyzeSequence(from: AVAudioFile)` сам конвертирует: файл 44.1 kHz Float32 успешно распознан (текст совпал с 16 kHz прогоном). Для потоковой подачи буферов конвертация на стороне клиента (в macOS 27 есть `AnalyzerInputConverter`, в 26 — только `AVAudioConverter`).

### E. Потоковость, задержки, точность (измерено)

20. **Реальное время** (`DictationTranscriber` ru_RU, `reportingOptions: [.volatileResults]`, подача чанками 100 мс; файл 15.35 с):
    - 40 волатильных результатов + 1 финальный;
    - лаг «конец распознанного аудио → момент выдачи результата»: **mean 0.54 с, median 0.44 с, p95 0.73 с, min 0.20 с, max 3.93 с** (max — это финальный результат);
    - финальный результат пришёл через 0.5–0.8 с после конца речи.
    Метод: `/tmp/srr/probe/stream.swift` (фидер с `Task.sleep(100ms)`, лог `lag = wallclock − CMTime.end`).

21. **Батч**: 66.57 с русского аудио (`preset: .longDictation`) — **1.25 с**, RTF **0.019**; 10.69 с — 0.60–0.86 с, RTF 0.056–0.088 (первый прогон в процессе дороже). Новый `SpeechTranscriber` (de_DE, 9.81 с) — RTF **0.019–0.020**, пять последовательных тёплых сессий по 0.11 с на 9.81 с аудио (RTF 0.011).
    Первый результат в «холодном» процессе: 0.18–0.20 с (`SpeechTranscriber` de) и 0.59–1.17 с (`DictationTranscriber` ru) — включает подгрузку модели.

22. **Память**: `maximum resident set size` процесса: 20 316 160 B (**19.4 MB**) для 66.57 с аудио; 23 674 880 B (**22.6 MB**) для 16 одновременных анализаторов. Во время 11‑секундного стриминга RSS плавал в диапазоне 17.6–18.0 MB. Ни одного вспомогательного процесса с моделью в `ps` не появилось. Согласуется с WWDC25: модель «retained in system storage… does not increase the run‑time memory size. It operates outside of your application's memory space» — <https://developer.apple.com/videos/play/wwdc2025/277/>.

23. **Параллелизм**: 16 одновременных `SpeechAnalyzer` в одном процессе (`DictationTranscriber`, ru) — все 16 вернули полный текст, без `insufficientResources`. 6 одновременных **процессов** по 10.69 с — все ≈0.93 с (RTF 0.086), суммарно 1.5 с стенового времени. Три локали одновременно в одном процессе (`ru_RU` dictation + `de_DE` speech + `fr_FR` speech) — 0.65 с суммарно.
    Это прямо подтверждает тезис «several transcriber instances can share the same backing engine instances and models» — <https://developer.apple.com/documentation/speech/speechtranscriber>.

24. **Таймстемпы**: атрибут `audioTimeRange` (CMTimeRange) на run'ах `AttributedString`; гранулярность — слово/токен. Пример (`SpeechTranscriber`, de_DE): `'Guten' [0.240..0.540]`, `' Tag,' [0.540..1.080]`, `' wir' [1.080..1.320]`…; у `DictationTranscriber` ru финальный результат на 144 символа дал 24 run'а с диапазонами. Есть `Result.range`, `isFinal`, `resultsFinalizationTime`, `alternatives`, атрибуты `audioTimeRange`/`transcriptionConfidence` — <https://developer.apple.com/documentation/speech/speechtranscriber/result>, `Speech.swiftinterface:136–140, 393–397`.

### F. Кастомная лексика / IT‑термины

25. `AnalysisContext.contextualStrings: [ContextualStringsTag: [String]]` — слова/фразы, сгруппированные по тегу (`AnalysisContext.ContextualStringsTag.general` есть из коробки), задаётся на анализатор через `setContext(_:)` (в macOS 27 `context` — get‑only, `Speech.swiftinterface:250`; сеттер — `setContext`): <https://developer.apple.com/documentation/speech/analysiscontext>.
    Замер (ru, один и тот же файл, список `["kubectl","PostgreSQL","CI/CD","деплоймент","поды","кластер"]`):
    - без контекста: `… деплоить сервис для Uber кластер проверить через QB и обновить дипло Mint post ГРЭС SQL Pay Plan SCD …`
    - с контекстом: `… дипло сервис для Uber class проверить поды через QB и обновить деплоймент post ГРЭС SQL Pay Plan SCD …`
    То есть контекст **принимается и меняет выдачу** (`поды`, `деплоймент` появились), но `kubectl` так и остался `QB`. Один прогон — не статистика; см. «Что измерить».

26. **Кастомная языковая модель** доступна **только у `DictationTranscriber`**: `DictationTranscriber.ContentHint.customizedLanguage(modelConfiguration:)` + `SFSpeechLanguageModel.Configuration(languageModel:)` (`Speech.swiftinterface:95`, заголовок `SFSpeechLanguageModel.h`, macOS 14+). У `SpeechTranscriber` инициализатора с `contentHints` нет вообще (только `transcriptionOptions/reportingOptions/attributeOptions`).
    Замерено: `SFCustomLanguageModelData(...).export(to:)` и `SFSpeechLanguageModel.prepareCustomLanguageModel(for:configuration:)` **работают из беспакетного CLI без TCC** (экспорт 999 B → скомпилированная модель **13 337 244 B**). Эффект от подключения `customizedLanguage` в моём тесте на качестве не проявился (выдача совпала с baseline). Пометка: API компиляции — ОК, влияние — не подтверждено.
    Документация: <https://developer.apple.com/documentation/speech/dictationtranscriber> (раздел «Improve accuracy»).

27. Прочие «подсказки» `DictationTranscriber`: `ContentHint.shortForm`, `.farField`, `.atypicalSpeech` (`Speech.swiftinterface:91–101`); пресеты и их содержимое (замерено):
    - `phrase` = {shortForm}, без опций;
    - `shortDictation`/`longDictation` = {punctuation};
    - `progressiveShortDictation` = {punctuation} + reporting {frequentFinalization, volatileResults};
    - `progressiveLongDictation` = {punctuation} + {volatileResults};
    - `timeIndexedLongDictation` = {punctuation} + attrs {audioTimeRange}.
    У `SpeechTranscriber`: `transcription` (пусто), `transcriptionWithAlternatives` (+alternatives), `timeIndexedTranscriptionWithAlternatives` (+audioTimeRange), `progressiveTranscription` (+fastResults, volatileResults), `timeIndexedProgressiveTranscription`.
    Важно: `.punctuation` в конфигурации **не дал** знаков препинания на русском (`Коллеги мы выкатили новую версию сервиса в прод проверьте пожалуйста…`), тогда как на немецком (`SpeechTranscriber`) пунктуация и заглавные есть. [INFERENCE] пунктуация зависит от локали/модели, а не только от опции.

### G. VAD (SpeechDetector)

28. `SpeechDetector` — отдельный модуль с `SensitivityLevel {low, medium, high}`, обязателен вместе с транскрайбером: «This module only functions in conjunction with a SpeechTranscriber or DictationTranscriber module» — <https://developer.apple.com/documentation/speech/speechdetector>.
29. `SpeechDetector.Result` — «currently only support error handling from the VAD model» (там же). Замерено: 15.35 с аудио с 2 с тишины в начале и 3 с в конце, `reportResults: true` → **0 результатов** за всю сессию. Текстовый результат при этом полный.
30. Анализатор **только** с `SpeechDetector` — `analyzeSequence(from:)` **не завершился за 15 с** (ни ошибки, ни возврата). Не вешать вызовы синхронно: нужен таймаут на стороне Runtime.
31. VAD влияет на качество: с `SpeechDetector(medium)` на том же файле `метрики` → `метрики`, а `Grafana` → `Graf` (без VAD — `графе`); т.е. гейтинг меняет выдачу. Предупреждение Apple: включение VAD всегда — это tradeoff точности против энергии (там же).

### H. TCC / entitlements / демон

32. **Файловый/буферный путь SpeechAnalyzer — без TCC.** Прогон `./transcribe ru16k.wav` (бинарник без Info.plist, `Bundle.main.bundleIdentifier == nil`) + проверка `log show --start … --predicate 'process == "tccd" OR process == "authd"'` → пусто; процесс распознал текст. Микрофон при этом не запрашивался.
33. **Микрофон — отдельный TCC.** `CaptureInputSequenceProvider.providerWithSession(from:…)` создаётся без разрешения (`AVCaptureDevice.authorizationStatus(for: .audio) == 0`), но `analyzerInputs` в headless‑CLI **не выдал ни одного буфера за 4 с**, статус остался `notDetermined` — без UI prompt'а и подписанного бандла захват не стартует.
34. **SFSpeechRecognizer жёстко требует TCC.** `SFSpeechRecognizer.requestAuthorization` из CLI без `NSSpeechRecognitionUsageDescription`: **exit 134 (SIGABRT)**, в системном логе — `Refusing authorization request for service kTCCServiceSpeechRecognition … without NSSpeechRecognitionUsageDescription key` и `This app has crashed because it attempted to access privacy-sensitive data without a usage description. The app's Info.plist must contain an NSSpeechRecognitionUsageDescription key` (заголовок `SFSpeechRecognizer.h` это и декларирует: «If this key is not present, your app will crash when you call this method»).
35. С валидным ключом, но без гранта: `SFSpeechURLRecognitionRequest` с `requiresOnDeviceRecognition = true` для `ru-RU` **не вернул ни результата, ни ошибки** за 60 с (и за 25 с во втором прогоне) при `authorizationStatus == 0`. Для демона без UI это означает «молчаливый висяк».
36. **Запуск из launchd**: LaunchAgent‑plist (`ProgramArguments` → мой бинарник, `ProcessType: Background`), поднятый `launchctl bootstrap gui/$UID`, отработал полностью (лог в `StandardOutPath`): 41 результат, финальный текст, RTF 0.153. `launchctl bootstrap user/$UID` даёт `Bootstrap failed: 5: Input/output error` — обычное поведение для agent'ов, требующих сессии.
37. **App Sandbox совместим**: ad-hoc‑подписанный `.app` с `com.apple.security.app-sandbox` (+ `network.client`) успешно транскрибировал файл из своего бандла (RTF 0.060), контейнер `~/Library/Containers/org.test.srrsandbox` создан. Побочно подтверждён и sandbox: обращение к `/tmp/...wav` вне контейнера дало `com.apple.coreaudio.avfaudio Code=-54`.
38. Модели в системе не привязаны к пользовательскому кэшу: `~/Library/Caches/com.apple.speech.localspeechrecognition` пуст (0 B), `~/Library/Group Containers/group.com.apple.CoreSpeech` пуст, ассеты лежат в `/System/Library/AssetsV2/...` (root:wheel, читаются пользователем).

### I. Жизненный цикл (важно для демона)

39. Анализатор **одноразовый**: после `finalizeAndFinish(through:)` повторный `analyzeSequence(from:)` на том же объекте вернул `nil`, поток результатов уже завершён. Документация: «the analyzer won't accept different input sequences or modules… Module result streams terminate» — <https://developer.apple.com/documentation/speech/speechanalyzer>.
40. **Переиспользование модуля между анализаторами роняет процесс**: `SpeechAnalyzer(modules: [de])` вторым анализатором с тем же объектом `SpeechTranscriber` → **exit 133 (SIGTRAP)**.
41. Правильный паттерн (проверено): новый `SpeechTranscriber` + новый `SpeechAnalyzer` на каждую сессию, `Options(priority:modelRetention: .processLifetime)`. Пять сессий подряд: 0.212 s, затем 0.118 / 0.110 / 0.111 / 0.115 s на 9.81 с аудио — модель остаётся тёплой.
42. `ModelRetention`: `whileInUse` (освобождать при деаллокации анализатора), `lingering` (держать «некоторое время» для переиспользования другим анализатором), `processLifetime` (до выхода процесса) — <https://developer.apple.com/documentation/speech/speechanalyzer/options/modelretention-swift.enum>. Принудительное освобождение: `SpeechModels.endRetention()` — <https://developer.apple.com/documentation/speech/speechmodels>.
    Замер: разницы в латентности между тремя режимами на этой машине не видно (0.48–0.61 с на 10.69 с аудио во всех трёх, RSS 18 MB стабильно) — потому что веса и так вне процесса.
43. `prepareToAnalyze(in:)` (preheat) существует и в 26, и в 27 (`Speech.swiftinterface:230–231`); в тесте «холодный» старт с ним дал первый результат за 0.39 с против 0.30 с без него — на одном прогоне разницы не подтвердил.

### J. Качество на русском (одна проба, синтетика)

44. TTS‑пример (`say -v Milena`, 10.7 с): «Привет. Нужно задеплоить сервис в Kubernetes кластер, проверить поды через kubectl и обновить деплоймент PostgreSQL. Пайплайн CI/CD упал на этапе сборки.»
    Лучший полученный текст (`DictationTranscriber`, `preset: .longDictation` + `contextualStrings`):
    `Привет нужно за дипло сервис для Uber class проверить поды через QB и обновить деплоймент post ГРЭС SQL Pay Plan SCD упал на этапе сборки`.
    Ошибки: `Kubernetes → Uber class`, `kubectl → QB`, `PostgreSQL → post ГРЭС SQL`, `CI/CD → Pay Plan SCD`.
45. Второй пример (15.35 с, бытовая лексика + Grafana/Jira): распознан почти дословно, ошибки — `Grafana → графе/Graf`, `Jira → жира`, `алерты → алерта`, пунктуации нет.
    **Важно:** это синтетическая речь TTS, не естественная. Оценка на реальной русской речи с IT‑терминами не проводилась (см. «Что измерить»).

---

## Ограничения и риски

1. **Русский = старая модель.** `SpeechTranscriber` (новый движок, лучше на дальней речи и длинных записях) для `ru_RU` недоступен; качество и терминология на уровне on‑device SFSpeechRecognizer. Прогноз «когда появится ru» Apple не публикует.
2. **Качество на IT‑терминах низкое** (п. 44): `kubectl`, `Kubernetes`, `PostgreSQL`, `CI/CD` распознаются неверно даже с `contextualStrings`. Для Runtime это означает необходимость пост‑коррекции/словаря на уровне приложения, а не движка.
3. **SFSpeechRecognizer непригоден для headless‑демона**: TCC + обязательный `NSSpeechRecognitionUsageDescription`; без гранта запросы висят без ошибки. Плюс он делит модель с `DictationTranscriber`, то есть не даёт ничего сверх, кроме сети.
4. **Микрофон в демоне**: `CaptureInputSequenceProvider` требует TCC на микрофон и UI для первого гранта. Для серверного демона единственный чистый путь — клиент присылает уже захваченный PCM по IPC.
5. **Лимит 5 локалей** на приложение и неявное «отписывание» от неиспользуемых ассетов (документация) → Runtime не может держать «все языки установленными» через Apple‑адаптер; нужен LRU по резервациям + честная ошибка клиенту.
6. **Нет управления версиями/удалением моделей**: версии задаёт Apple (`3100.50006.81732`), файлы — cryptex DMG в `/System/Library/AssetsV2`. Runtime не может ни пинить версию, ни откатить, ни гарантировать воспроизводимость результата между обновлениями ОС/ассетов. Это фундаментальное расхождение с «каталогом моделей» из целевой архитектуры.
7. **Ввод только 8/16 kHz Int16 mono** — обязательный ресемплинг; апсемплинг 44.1→16 kHz теряет качество, а нативный формат Apple другой не поддерживает.
8. **Ошибки неинформативны**: отсутствие ассета маскируется под «Audio format is not supported» (Code=3), отсутствие VAD‑транскрайбера — под вечное зависание, отсутствие TCC — под вечное молчание. Адаптеру нужны собственные предпроверки (`AssetInventory.status == .installed`) и таймауты.
9. **Одноразовость анализатора + краш при переиспользовании модуля**: любая попытка «пула анализаторов» или «пула модулей» приведёт к утечке ресурсов или SIGTRAP. Пул допустим только на уровне «библиотека + процесс».
10. **Платформенная привязка**: API только Apple Silicon/новые ОС (macOS 26+); `ignoresResourceLimits` и `AnalyzerInputConverter` — macOS 27+. Архитектура Runtime не должна закладываться на эти API как на единственные.
11. **Нет диаризации/эмбеддингов** — эти возможности Runtime обязаны закрываться другими адаптерами (FluidAudio/WhisperKit/sherpa-onnx), Apple‑адаптер их не даст принципиально.
12. **Не проверено на реальном system‑домене** (`LaunchDaemon`, root, без логина пользователя): нет прав для sudo, SSH‑ключ не принят. Все положительные результаты получены в контексте залогиненной сессии (CLI, LaunchAgent в `gui/$UID`, sandboxed app). Для system‑daemon есть дополнительный риск: ассеты и `AssetInventory` привязаны к пользовательской сессии/приложению, и поведение под root не подтверждено.

---

## Выводы для Runtime

**Брать как адаптер — да, но с чёткой ролью: «системный ASR для русского и европейских языков, без установки моделей, с разделением весов между клиентами».**

Что взять:

1. **Роль адаптера `apple.speech`**: операции `asr.batch` и `asr.stream` для локалей из `DictationTranscriber.supportedLocales` (включая `ru_RU`, `uk_UA`) и из `SpeechTranscriber.supportedLocales` (45 локалей, без ru). `vad` — только как внутренний флаг гейтинга, не как отдельная операция с таймстемпами. `diarization`/`embeddings` — **не поддерживается** этим адаптером (возвращать `unsupported`).
2. **Две «модели» в каталоге Runtime**: `apple.dictation.<locale>` (старый движок, ru включён) и `apple.speech.<locale>` (новый движок, ru нет). Выбор модели — всегда явный со стороны клиента (как и требует архитектура). При запросе `apple.speech.ru` — детерминированная ошибка `unsupported_language` с подсказкой `available: apple.dictation.ru`.
3. **Модели не качать своим менеджером.** `AssetInventory` — единственный путь: `status` → при `supported` вызвать `assetInstallationRequest(supporting:)` + `downloadAndInstall()` (прогресс через `progress`), при `installed` — работать сразу. Всё остальное (версии, размер, удаление) — вне зоны Runtime; в манифесте модели показывать «managed by OS, version unknown/unpinned».
4. **Разделение весов получается бесплатно**: один процесс‑демон держит `ModelRetention.processLifetime` и создаёт **новый модуль + новый анализатор на каждую сессию** (п. 41). Тёплая сессия — ~0.11 с накладных на 10 с аудио. Пул анализаторов не делать (п. 39–40).
5. **Параллелизм**: до 16 одновременных анализаторов в одном процессе проверено; для нескольких локалей сразу — тоже. Это дешевле, чем отдельный процесс на клиента. Лимит системы консервативный: ловить `insufficientResources` и, на macOS 27+, опционально `ignoresResourceLimits` — но только с собственным семафором (Runtime сам ограничивает N).
6. **Никакого IPC‑микрофона от Apple**: демон принимает PCM от клиента (у клиента есть UI и TCC), либо отдельный подписанный helper с микрофонным TCC. `CaptureInputSequenceProvider` в демоне не использовать.
7. **Не использовать SFSpeechRecognizer вообще** — те же модели, что `DictationTranscriber`, но плюс TCC, Info.plist и «тихие» зависания.
8. **Стриминг делать на volatile‑результатах**: `reportingOptions: [.volatileResults]` (или пресет `progressiveLongDictation`) + отправка промежуточного текста с флагом `is_final=false`; финал — по `isFinal == true`. Таймстемпы — из `audioTimeRange` (пословные), включать `attributeOptions: [.audioTimeRange]` только когда клиент их просит (лишний объём AttributedString).
9. **Лексикон**: на входе API Runtime принимать `contextual_strings` (→ `AnalysisContext.contextualStrings[.general]`) и `custom_vocabulary` (→ `SFCustomLanguageModelData` + `SFSpeechLanguageModel` + `ContentHint.customizedLanguage`, компилируется без TCC, кэшировать файл по хешу словаря). Работает только для `DictationTranscriber`; для `SpeechTranscriber` — молча игнорировать с предупреждением в ответе.
10. **Контракт ошибок адаптера** — маппинг: `AssetInventory.status == .unsupported` → `unsupported_language`; `formats.isEmpty` → `model_not_installed` (не «bad format»!); `SFSpeechErrorDomain Code=3` при установленном `status` — считать `internal_model_error`; `insufficientResources` → `busy`; собственный таймаут на `analyzeSequence` (зависания реальны, п. 17, 30).
11. **Формат на входе**: жёстко 16 kHz mono Int16 → ресемплер в адаптере (единый для всех движков, чтобы не дублировать).
12. **Диагностика при старте**: отдавать в `/health` `SpeechTranscriber.isAvailable`, списки `supportedLocales`/`installedLocales` обоих транскрайберов, `maximumReservedLocales` и текущие `reservedLocales` — это дешёвые и полностью детерминированные вызовы.

Что не брать:

- Не строить на Apple‑адаптере «каталог моделей» с версиями/откатом — это невозможно (п. 6 ограничений).
- Не полагаться на `SpeechDetector` как на VAD‑сервис с сегментами.
- Не пытаться выносить Apple‑движок в отдельный XPC ради «общих весов» — веса и так общие, а анализатор одноразовый.
- Не использовать `AssetInputSequenceProvider`/`AnalyzerInputConverter` как обязательные: они только в macOS 27+, а база (26) должна работать.

---

## Неизвестные / что измерить

1. **Появится ли `ru_RU` в `SpeechTranscriber`** в следующих ОС. Проверять тривиально: `SpeechTranscriber.supportedLocale(equivalentTo: Locale("ru-RU"))` (у меня — `nil` на 27.0/26A428). Нужен периодический чек в CI на новых бетах.
2. **Реальное качество `DictationTranscriber` на естественной русской речи с IT‑терминами** (WER на 30–50 фразах реальных записей, а не TTS). Мой замер — один синтетический пример; цифру WER не привожу сознательно.
3. **Эффект `contextualStrings` и `customizedLanguage` статистически** (A/B на 50+ фразах, 3 размера словаря). В моём тесте custom LM не изменил выдачу; возможно, нужен иной формат весов `SFCustomLanguageModelData` или модель применяется только к части декодера.
4. **Поведение под `LaunchDaemon` (system domain, root, без логина)**: не проверено (нет sudo). Нужно: запустить бинарник от root, проверить `AssetInventory.status` для `ru_RU` и успешность `analyzeSequence`. Ожидание `[INFERENCE]`: ассеты в `/System/Library/AssetsV2` читаемы, но `AssetInventory`/TCC‑подсистема могут требовать пользовательскую сессию.
5. **Точный лимит одновременных анализаторов** (`insufficientResources`) на разных SoC и при разных локалях. У меня 16 прошли без ошибки — реальный предел выше; нужен перебор до отказа на M‑серии разных классов.
6. **Кто именно исполняет модель** (в процессе, в ANE‑драйвере, в скрытом XPC): видимого процесса нет, RSS 19–22 MB. Для планирования CPU/ANE‑бюджета демона нужно `powermetrics`/`Instruments`‑профилирование (требует root).
7. **Поведение при параллельной загрузке ассета и анализе** (наблюдал один 300‑секундный висяк) — воспроизвести контролируемо и понять, есть ли гарантированный таймаут.
8. **Сохранение резерваций для подписанного приложения** с CFBundleIdentifier между перезапусками и после переустановки (у беспакетного CLI не сохраняются).
9. **Требуется ли `com.apple.security.network.client` под sandbox для `downloadAndInstall()`** — не проверял (в sandbox‑тесте сеть не использовалась).
10. **Влияние `ignoresResourceLimits` (macOS 27+)** на деградацию латентности при 20+ одновременных сессиях.
11. **Стабильность пунктуации по локалям**: `.punctuation` не сработал на русском и сработал на немецком — проверить на en/de/es/fr и решить, обещать ли клиенту пунктуацию вообще.
12. **iOS/iPadOS‑поведение** (фоновые приложения, приостановка) — вне задачи, но для клиентов Runtime на iOS релевантно.

---

## Источники

Документация Apple (читалось напрямую с developer.apple.com, markdown‑вариант):

- SpeechAnalyzer — <https://developer.apple.com/documentation/speech/speechanalyzer>
- SpeechTranscriber — <https://developer.apple.com/documentation/speech/speechtranscriber>
- SpeechTranscriber.Result — <https://developer.apple.com/documentation/speech/speechtranscriber/result>
- SpeechTranscriber.isAvailable — <https://developer.apple.com/documentation/speech/speechtranscriber/isavailable>
- DictationTranscriber — <https://developer.apple.com/documentation/speech/dictationtranscriber>
- SpeechDetector — <https://developer.apple.com/documentation/speech/speechdetector>
- AnalysisContext — <https://developer.apple.com/documentation/speech/analysiscontext>
- AssetInventory — <https://developer.apple.com/documentation/speech/assetinventory>
- AssetInventory.Status — <https://developer.apple.com/documentation/speech/assetinventory/status>
- AssetInstallationRequest — <https://developer.apple.com/documentation/speech/assetinstallationrequest>
- SpeechModels — <https://developer.apple.com/documentation/speech/speechmodels>
- SpeechAnalyzer.Options.ModelRetention — <https://developer.apple.com/documentation/speech/speechanalyzer/options/modelretention-swift.enum>
- Speech framework (индекс) — <https://developer.apple.com/documentation/speech>
- Speech updates (June 2026) — <https://developer.apple.com/documentation/updates/speech>
- Sample: Bringing advanced speech-to-text capabilities to your app — <https://developer.apple.com/documentation/speech/bringing-advanced-speech-to-text-capabilities-to-your-app>

WWDC:

- WWDC25 session 277, «Bring advanced speech-to-text to your app with SpeechAnalyzer» (полный транскрипт) — <https://developer.apple.com/videos/play/wwdc2025/277/>

Локальные SDK‑заголовки и интерфейсы (macOS SDK 27.0, Xcode 27.1):

- `…/MacOSX27.0.sdk/System/Library/Frameworks/Speech.framework/Modules/Speech.swiftmodule/arm64e-apple-macos.swiftinterface` (742 строки; ключевые: `:33–53` AssetInventory, `:228–262` SpeechAnalyzer, `:263` endRetention, `:453–477` Options/ModelRetention, `:514–524` AnalyzerInputConverter, `:668–680` AssetInputSequenceProvider, `:683–709` SFSpeechError.Code, `:719` CaptureInputSequenceProvider)
- `…/Speech.framework/Headers/SFSpeechRecognizer.h` (авторизация, `NSSpeechRecognitionUsageDescription`, `supportsOnDeviceRecognition`)
- `…/Speech.framework/Headers/SFSpeechLanguageModel.h` (macOS 14+; `prepareCustomLanguageModel(for:configuration:)`)
- `…/Speech.framework/Headers/SFErrors.h` (SFSpeechErrorDomain)
- `/System/Library/AssetsV2/com_apple_MobileAsset_UAF_Speech_AutomaticSpeechRecognition/purpose_auto/*/Info.plist` (AssetSpecifier, версия ассета, `__ContainsCryptexContents`)

Локальные измерения (воспроизводимые зонды; все — в `/tmp/srr/probe/`, вне проекта):

- `probe.swift` — списки локалей, `isAvailable`, `maximumReservedLocales`, `reservedLocales`
- `transcribe.swift` / `cold.swift` / `stream.swift` / `reuse3.swift` / `multi.swift` / `limits.swift` — RTF, латентность, RSS, параллелизм, переиспользование
- `assets.swift` / `fmt.swift` / `fmt2.swift` / `missing.swift` — статусы, резервации, скачивание, поведение без ассета
- `sfsr.swift` / `sfsr2.swift` / `auth.swift` — SFSpeechRecognizer и TCC
- `mic.swift` / `mic2.swift` — `CaptureInputSequenceProvider` без разрешений
- `vadd2.swift` / `detonly.swift` — `SpeechDetector`
- `vocab.swift` / `lm.swift` — `contextualStrings` и кастомная LM
- `SB.app` — sandboxed‑проверка; `daemon.plist` — LaunchAgent‑проверка через `launchctl bootstrap gui/$UID`

Прочее:

- Локальный поиск по клонам (`/tmp/srr/repos`) показал, что ни один из них (speech-swift, SpeechRail, FluidAudio, WhisperKit, sherpa-onnx, speaches) не оборачивает Apple `SpeechAnalyzer`: в `FluidAudio/.../SlidingWindowAsrManager.swift` есть только комментарий «Similar to Apple's SpeechAnalyzer» — готового референсного адаптера в этих репозиториях нет.
- Веб‑поиск на момент исследования был недоступен (все провайдеры вернули 403/429/бот‑челлендж), поэтому community‑отчёты и сторонние бенчмарки в отчёт не включены; все цифры — либо документация Apple, либо мои измерения на этой машине.
