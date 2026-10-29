# Волна 3 — артефакты и корпус

Две задачи после пилота: под какой лицензией и в каком формате существуют кандидаты, и откуда взять русскую речь с проверенными референсами. Пилот на 12 фрагментах в Git не входит: аудио и гипотезы лежат в `experiments/corpus/local/` (gitignored).

| Отчёт | Тема | Главное |
|---|---|---|
| [R1-ru-corpora.md](R1-ru-corpora.md) | Русские корпуса с референсами | CC0/CC BY/PD есть только у общих доменов (Common Voice ru 256.8 ч валидированных, FLEURS ru ≈12 ч, RuLS 98 ч). Русской IT-речи с лицензией нет: единственный IT-набор — 107 сегментов Podlodka без лицензии. Golos — собственная BY-SA-подобная лицензия без сублицензирования; OpenSTT — NC и без speaker labels |
| [R2-asr-artifacts.md](R2-asr-artifacts.md) | Артефакты ASR-кандидатов | Nemotron 3.5 multilingual: OpenMDW-1.1, `ru-RU` = `prompt_id 11`, CoreML `multilingual/2240ms` ≈ 610 МБ; `latin/`-шип кириллицу отбрасывает. GigaAM v3 и GigaAM Multilingual — MIT. Parakeet v3/Ultra/Redux — CC-BY-4.0, ru есть у всех. Qwen3-ASR — Apache-2.0, CoreML-порт существует (в волне 2 его не было). Voxtral Mini/Small 2507 русского не поддерживает |
| [R3-extra-corpora.md](R3-extra-corpora.md) | Дополнительные корпуса: FLEURS, CS-YODAS, Podlodka, ToneWebinars | FLEURS `ru_ru` test = **775** примеров (2.50 ч, 344 диктора), CC-BY-4.0, скачаны только TSV. CS-YODAS `rus` = 1081 запись, **CC-BY-NC-4.0** (некоммерческий контур), транскрипты — автоматические ASR-черновики без ручной выверки, целевых реплик ≈ 1 ч. `bond005/podlodka_speech` без лицензии, test дублирует validation. ToneWebinars — Apache-2.0 поверх MIT-исходника с whisper-текстом |

## Что подтвердилось прогонами (2026-10-28)

Прогоны на Veche-bench и FLEURS изменили три утверждения выше:

- **GigaAM Multilingual работает и даёт лучший CER** (1.74 % на Veche test против 1.98 % у GigaAM v3), но CTC-голова пишет латиницу кириллицей: `latin_form_accuracy` = 0 %.
- **Parakeet Ultra на русском лучше v3 по WER** (5.79–5.89 % против 6.18–6.20 %), но разница меньше, чем эффект языковой подсказки: `--language ru` обнуляет латиницу у обеих моделей.
- **Qwen3-ASR-0.6B на русском вдвое хуже остальных** (12.27 % WER), причём 8-bit и fp16 совпадают — дело не в квантовании. Опубликованные английские метрики к русскому не переносятся.

## Что изменилось после волны 2

- **Уточнено:** «CoreML нет у Qwen3-ASR» — порт `FluidInference/qwen3-asr-0.6b-coreml` существует; «FluidAudio Nemotron только английский» — есть multilingual-конверсия с `prompt_id` для `ru-RU`.
- **Добавлено:** GigaAM Multilingual опубликован (MIT, `ru,en,kk,ky,uz`), FLEURS ru 4.4/3.0 по карточке; Parakeet Ultra (FLEURS ru 5.21 против 5.51 у v3) и Redux (7.91, только Photon).
- **Проверено прогоном:** Nemotron multilingual на CoreML действительно даёт русский текст на 12 фрагментах, но латиницу IT-терминов транслитерирует непоследовательно (`Прометеос`, `кубернеет`, `апписервер`, `ДКос`). Наблюдение на 12 фрагментах, не метрика.
