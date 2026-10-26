# Офлайн-скоринг ru-IT (экспериментальный стенд)

Реализация шагов 0–1 из [`research/wave2/V8-eval-plan.md`](../../research/wave2/V8-eval-plan.md) §10:
замороженный нормализатор `norm-v1` (§2.5), словарь терминов (§2.3) и единый скорер
WER/CER + термины (§3.1–§3.2) с валидатором набора (§2.2–§2.6).

**Статус: стенд и метрики экспериментальные.** Это не бенчмарк моделей: здесь нет ни одного
измерения ASR. Числа, которые печатает CLI на `fixtures/smoke`, — синтетические пары
«референс → вручную внесённый дефект»; они проверяют арифметику скорера и ничего не говорят
о качестве распознавания.

Только стандартная библиотека Python 3. Новых зависимостей нет.

## Что внутри

| Файл | Назначение |
|---|---|
| `norm.py` | `norm-v1`: lowercasing, пунктуация, `ё → е`, числа в слова, `CI/CD` ↔ `cicd`, филлеры (§2.5) |
| `align.py` | Levenshtein по токенам с операциями S/I/D и картой «референс → гипотеза» |
| `wer.py` | corpus-level WER/CER + разбивка S/I/D (§3.1) |
| `terms.py` | `exact_latin` / `translit` / `corrupt` / `miss`, recall, Latin-form accuracy, precision, F1, case accuracy (§3.2) |
| `stats.py` | парный bootstrap разницы WER и биномиальный ДИ (§2.6) |
| `dataset.py` | валидатор набора: манифест, метаданные, утечка дикторов/встреч, покрытие |
| `cli.py` | `validate` / `score` / `compare` |
| `terms.json` | кандидат-словарь: 366 терминов, 6 классов (§2.3) |
| `fixtures/smoke/` | синтетический набор для self-check, аудио отсутствует намеренно |
| `tests/test_scorer.py` | 87 self-check на правила V8 и крайние случаи |

## Запуск

Из корня репозитория:

```bash
# 1. Self-checks стенда (без моделей и без аудио)
python3 -m unittest discover -s experiments/asr_eval/tests -p 'test_*.py'

# 2. Валидация набора: структура, утечка, покрытие, пороги V8
python3 experiments/asr_eval/cli.py validate \
  --dataset experiments/asr_eval/fixtures/smoke \
  --terms experiments/asr_eval/terms.json

# 3. Скор по TSV внешнего раннера: <id>\t<текст>
python3 experiments/asr_eval/cli.py score \
  --dataset experiments/asr_eval/fixtures/smoke \
  --hypotheses experiments/asr_eval/fixtures/smoke/hyp-runner-a.tsv \
  --terms experiments/asr_eval/terms.json --json /tmp/score-a.json

# 4. Скор по JSON-отчёту asr-bench (ровно одна система в results[])
python3 experiments/asr_eval/cli.py score --report /path/to/report.json \
  --terms experiments/asr_eval/terms.json --json /tmp/score.json

# 5. Парный bootstrap разницы WER двух систем
python3 experiments/asr_eval/cli.py compare \
  --dataset experiments/asr_eval/fixtures/smoke \
  --a experiments/asr_eval/fixtures/smoke/hyp-runner-a.tsv \
  --b experiments/asr_eval/fixtures/smoke/hyp-runner-b.tsv --replications 1000 --seed 0
```

Коды возврата: `0` — успех; `1` — набор невалиден (`validate`); `2` — ошибка входных данных
(несовпадение id, дубликаты, невалидный манифест, смешение систем в отчёте), без traceback.

Разделение «раннер движка / скорер» — из §7.1: движки пишут TSV `<id>\t<гипотеза>` или
JSON-отчёт, все числа считает один и тот же скорер. `score` пишет в JSON `norm_version` и
`vocabulary{path,version,sha256}` — без них числа между прогонами несравнимы.

## Решения `norm-v1`

Полный список — в docstring `norm.py`; ключевое:

- Числа → слова детерминированно, без `num2words` (LGPL-зависимость не добавлена).
  `1.29` и «один точка двадцать девять» нормализуются одинаково.
- Версии и CIDR (`1.29.0`, `10.0.0.0/8`) остаются цифрами: это термины из §2.3 класс 5.
- Цифры внутри буквенно-цифровых токенов (`k8s`, `S3`, `p50`) не трогаются.
- Склейка только по явным правилам (`ci` + `cd` → `cicd`), не по всему латинскому прогону;
  поэтому `kubectl CI/CD Helm` → `kubectl cicd helm`, а `postgre sql` ≠ `postgresql`.
- Порядковые суффиксы (`500-е`) отделяются как токен: `пятьсот` + `е`. Порядковые числительные
  не реализованы — осознанное ограничение версии.

## Известные ограничения

1. **Термины ищутся по формам словаря в референсе.** Ручная разметка `terms[]` с позициями
   (§2.5) не является подтверждённым эталоном: метрики терминов считаются по совпадению форм
   `accept`/`accept_translit` в референсе, а не по валидированной аннотации. Пока аннотация не
   выверена человеком, term recall/precision — предварительные.
2. **`corrupt` — эвристика.** Порог edit distance масштабируется длиной формы
   (`min(2, max(1, len/3))`), сравнение идёт только с формами текущего термина и только для
   латинских токенов. Это сужает ложные срабатывания, но не является определением из V8.
3. **Term precision** считает «терминоподобным» точное вхождение формы `accept`/`accept_translit`; искажения в
   precision не входят, потому что отнести их к термину можно только по референсу.
4. **Пороги покрытия в `dataset.py` — предлагаемые** (§2.2–§2.6), не измеренные. Валидатор
   проверяет механику (утечка, счётчики, доли), а не достаточность набора.
5. **Словарь `terms.json` — кандидат** (366 записей, 6 классов). Формы `accept`/`accept_translit`
   нужно вычитать вручную: от них напрямую зависят term-метрики. `wordform`/`command`/`number`
   не имеют `accept_translit` по построению.
6. **Пунктуация (§3.3), стриминговые латентности (§3.4), диаризация (§3.5), VAD (§3.6)** не
   реализованы — это следующие шаги, не «недоделка» текущего.
7. **Синтетические фикстуры не являются бенчмарком.** Совпадение WER на них ничего не
   подтверждает про модели.

## Данные

`experiments/corpus/` — публичный sample русской IT-речи (5 сегментов, 174.29 с), полученный
для dev-калибровки. Лицензия неизвестна, транскрипты `unverified`, ASR по ним не запускался.
Подробности и ограничения — в [`experiments/corpus/README.md`](../corpus/README.md).
Использовать этот sample как эталон качества нельзя.
