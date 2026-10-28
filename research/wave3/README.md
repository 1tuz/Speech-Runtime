# Волна 3 — артефакты и корпус

Две задачи после пилота: под какой лицензией и в каком формате существуют кандидаты, и откуда взять русскую речь с проверенными референсами. Пилот на 12 фрагментах в Git не входит: аудио и гипотезы лежат в `experiments/corpus/local/` (gitignored).

| Отчёт | Тема | Главное |
|---|---|---|
| [R1-ru-corpora.md](R1-ru-corpora.md) | Русские корпуса с референсами | CC0/CC BY/PD есть только у общих доменов (Common Voice ru 256.8 ч валидированных, FLEURS ru ≈12 ч, RuLS 98 ч). Русской IT-речи с лицензией нет: единственный IT-набор — 107 сегментов Podlodka без лицензии. Golos — собственная BY-SA-подобная лицензия без сублицензирования; OpenSTT — NC и без speaker labels |
| [R2-asr-artifacts.md](R2-asr-artifacts.md) | Артефакты ASR-кандидатов | Nemotron 3.5 multilingual: OpenMDW-1.1, `ru-RU` = `prompt_id 11`, CoreML `multilingual/2240ms` ≈ 610 МБ; `latin/`-шип кириллицу отбрасывает. GigaAM v3 и GigaAM Multilingual — MIT. Parakeet v3/Ultra/Redux — CC-BY-4.0, ru есть у всех. Qwen3-ASR — Apache-2.0, CoreML-порт существует (в волне 2 его не было). Voxtral Mini/Small 2507 русского не поддерживает |

## Что изменилось после волны 2

- **Уточнено:** «CoreML нет у Qwen3-ASR» — порт `FluidInference/qwen3-asr-0.6b-coreml` существует; «FluidAudio Nemotron только английский» — есть multilingual-конверсия с `prompt_id` для `ru-RU`.
- **Добавлено:** GigaAM Multilingual опубликован (MIT, `ru,en,kk,ky,uz`), FLEURS ru 4.4/3.0 по карточке; Parakeet Ultra (FLEURS ru 5.21 против 5.51 у v3) и Redux (7.91, только Photon).
- **Проверено прогоном:** Nemotron multilingual на CoreML действительно даёт русский текст на 12 фрагментах, но латиницу IT-терминов транслитерирует непоследовательно (`Прометеос`, `кубернеет`, `апписервер`, `ДКос`). Наблюдение на 12 фрагментах, не метрика.
