# Глоссарий терминов cloud native / Kubernetes (русские определения)

Реестр русскоязычных определений терминов, собранных из двух официальных источников.
Назначение — канонические русские формулировки для нормализации ASR-гипотез и для сверки
словаря `experiments/asr_eval/terms.json`.

- **Дата извлечения: 2026-10-28** (все URL ниже запрошены в этот день)
- Реестр: `registry.json` (JSON, кодировка UTF-8)
- Сырые данные, которые были фактически разобраны: `raw/`

## Источники

| id | Источник | Индексная страница | Ревизия (на 2026-10-28) | Лицензия |
|---|---|---|---|---|
| `cncf-glossary-ru` | Cloud Native Glossary (CNCF), русская локализация | https://glossary.cncf.io/ru/ | репозиторий `cncf/glossary` @ `c6493fa4824c440cca36a970f9ca68eeabc544bb` (2026-10-19); ETag индексной страницы `8a4102eb21388a06663cc3b94c2b3787-ssl` | **CC BY 4.0** (документация) |
| `kubernetes-website-ru-glossary` | Kubernetes Documentation, глоссарий (RU) | https://kubernetes.io/ru/docs/reference/glossary/ | репозиторий `kubernetes/website` @ `73d2cca5ed07cb180b68a794a4f4a77faf907a67` (2026-10-28); ETag индексной страницы `bc71310063668def74913f40270f5664-ssl-df` | **CC BY 4.0** |

Английские версии тех же страниц (https://glossary.cncf.io/ и https://kubernetes.io/docs/reference/glossary/)
использовались только для получения английского канонического названия термина; определения в реестр
взяты исключительно из русских страниц.

## Лицензии — проверено по файлам, а не по памяти

### `cncf/glossary`

- `LICENSE` в корне репозитория — **Apache License 2.0** (`https://raw.githubusercontent.com/cncf/glossary/main/LICENSE`,
  11 357 байт; GitHub API: `spdx_id = Apache-2.0`). Это лицензия **кода**.
- Документация — **CC BY 4.0**. Доказательства:
  - `README.md`, раздел `## License` (строка 41): «All code contributions are under the Apache 2.0 license.
    Documentation is distributed under CC BY 4.0.» — https://github.com/cncf/glossary/blob/main/README.md#license
  - каждая страница глоссария в футере содержит `License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)`
    (видно в `raw/cncf-md/*.md`, блок после `---`);
  - футер русской индексной страницы: «Документация распространяется на условиях лицензии CC BY 4.0»
    (`raw/cncf-glossary-ru-index.html`, также `Documentation Distributed under CC BY 4.0`).
- Русский перевод — часть того же репозитория (`content/ru/...`), то есть наследует ту же лицензию CC BY 4.0.
  Отдельной лицензии на перевод не заявлено.

### `kubernetes/website`

- `LICENSE` в корне — полный текст **Creative Commons Attribution 4.0 International**
  (`https://raw.githubusercontent.com/kubernetes/website/main/LICENSE`, 18 657 байт; GitHub API: `spdx_id = CC-BY-4.0`).
  Это лицензия **документации**, а не кода.
- Футер русской страницы: «Документация распространяется под лицензией CC BY 4.0» со ссылкой на
  `https://git.k8s.io/website/LICENSE` (`raw/kubernetes-glossary-ru-index.html`).
- Русский перевод лежит в том же репозитории (`content/ru/docs/reference/glossary/*.md`) и наследует CC BY 4.0.
- Копии обоих файлов лицензий сохранены: `raw/licenses/LICENSE-cncf-glossary.txt`,
  `raw/licenses/LICENSE-kubernetes-website.txt`.

В каждой записи реестра поля `license` и `license_evidence_url` заполнены; `license_evidence_url`
указывает на документ, где лицензия заявлена явно.

## Состав реестра

| Показатель | Значение |
|---|---|
| Всего записей | **77** |
| Из них из CNCF Glossary (RU) | 44 |
| Из них из Kubernetes Glossary (RU) | 33 |
| Объединено как дубликаты (один термин в двух источниках) | 4 (`Cluster`, `Containers`, `Nodes`, `Container Runtime Interface`) |
| Уже есть в `experiments/asr_eval/terms.json` | 5 |
| Новых (отсутствуют в `terms.json`) | 72 |
| Записей с непустым `variants` | 2 |

### Структура записи

```json
{
  "canonical_term": "Kubernetes",
  "ru_title": "Kubernetes",
  "ru_definition": "Kubernetes (часто сокращается до K8s) — это система ...",
  "source_url": "https://glossary.cncf.io/ru/kubernetes/",
  "source_revision": "content/ru/kubernetes.md @ cncf/glossary commit 8c3a26457ac876b9c9ce688c68a7682a5ad7db01 (2023-12-26); retrieved 2026-10-28",
  "license": "CC BY 4.0",
  "license_evidence_url": "https://github.com/cncf/glossary/blob/main/README.md#license",
  "variants": [],
  "notes": "",
  "source_id": "cncf-glossary-ru"
}
```

Корневой объект: `version`, `retrieval_date`, `description`, `sources` (по одному элементу на источник
с лицензией и её доказательством), `counts`, `terms_json_comparison`, `entries`.

Обязательные поля в каждой записи: `canonical_term`, `ru_definition`, `source_url`, `license`,
`license_evidence_url`; плюс `source_revision`, `variants`, `notes`, `ru_title`, `source_id`.

### Как получены определения

- Определения взяты дословно из русского текста источника. Удалена только Markdown-разметка:
  ссылки `[текст](url)` → `текст`, выделение `**`/`*`/`_`, код-тики, HTML-комментарии.
- В исходниках Kubernetes глоссария дополнительно раскрыты shortcode-вставки
  `{{< glossary_tooltip text="контейнеров" term_id="container" >}}` → `контейнеров`
  (берётся атрибут `text`), прочие shortcode-вставки удалены. Символ `<!--more-->` отброшен,
  поэтому `ru_definition` содержит краткое и полное описание подряд (как на странице после «[+]»).
- Формулировки не переписывались, термины не переводились заново, опечатки источника сохранены
  (например, «преназначен» в индексной странице CNCF).

### `variants` — только буквально из источника

Поле `variants` заполнено **только** там, где источник содержит явное поле `aka:` во front matter
(и только если значение не совпадает с `canonical_term`):

| Термин | `variants` | Источник значения |
|---|---|---|
| `API server` | `["kube-apiserver"]` | `aka:` в `content/ru/docs/reference/glossary/kube-apiserver.md` |
| `Cloud Provider` | `["Поставщик облачных услуг (Cloud Service Provider)"]` | `aka:` в `content/ru/docs/reference/glossary/cloud-provider.md` |

У CNCF-глоссария поля `aka/aliases` во front matter нет — там `variants` пуст.
Транслитерации, произношения и типичные ASR-ошибки **не добавлялись** нигде: таких данных в источниках нет.

## Что исключено

1. **Непереведённые термины Kubernetes.** На русской индексной странице 160 терминов, но только у 37
   есть русский текст (`content/ru/docs/reference/glossary/*.md`); у остальных показывается английское
   определение. В реестр попали только эти 37 (из них 4 слились с CNCF-записями → 33 записи).
2. **Служебные страницы CNCF-глоссария**: `contribute`, `contributor-ladder`, `style-guide`
   (не термины), а также `index.md` / `llms.txt` (навигация).
3. **Английские определения и английские заголовки** как источники `ru_definition` — не использовались.
4. **Прочие локали** CNCF-глоссария (bn, de, es, fr, hi, it, ja, ko, pt-br, tr, ur, vi, zh-cn, zh-tw).
5. **Синонимы/транслитерации/варианты произношения** — не выдумывались (см. раздел `variants`).
6. **`experiments/asr_eval/terms.json` не изменялся**; сверка выполнена только на чтение.

## Сверка с `experiments/asr_eval/terms.json`

Сравнение по нормализованной форме `canonical_term` (нижний регистр, только `a-z0-9а-я`) против
`id` + `accept` + `accept_translit` из `terms.json` (версия `terms-v1`, 366 терминов).

Уже присутствуют в `terms.json` (5):

| `canonical_term` | id в `terms.json` |
|---|---|
| Kubernetes | `kubernetes` |
| containerd | `containerd` |
| CRI-O | `crio` |
| Docker | `docker` |
| etcd | `etcd` |

Новых — 72; полный список в `registry.json` → `terms_json_comparison.new_terms`.
Среди них `Pod`, `Node`, `Service`, `Namespace`, `Secret`, `Controller`, `Control Plane`, `Cluster`,
`Container`, `CRI`, `Ingress`, `DevOps`, `Observability` и др. — в `terms.json` этих id нет.

## Воспроизведение

Все команды выполнялись из корня репозитория; `rtk` — префикс-обёртка, `$PY` — интерпретатор
`experiments/.venv/bin/python` (Python 3.12).

### 1. Индексные страницы и лицензии

```bash
curl -sS --compressed -o raw/cncf-glossary-ru-index.html https://glossary.cncf.io/ru/
curl -sS --compressed -o raw/cncf-glossary-en-index.html https://glossary.cncf.io/
curl -sS --compressed -o raw/kubernetes-glossary-ru-index.html https://kubernetes.io/ru/docs/reference/glossary/
curl -sS --compressed -o raw/kubernetes-glossary-en-index.html https://kubernetes.io/docs/reference/glossary/
curl -sS -o raw/cncf-glossary-ru-llms.txt https://glossary.cncf.io/ru/llms.txt
curl -sS -o raw/licenses/LICENSE-cncf-glossary.txt https://raw.githubusercontent.com/cncf/glossary/main/LICENSE
curl -sS -o raw/licenses/LICENSE-kubernetes-website.txt https://raw.githubusercontent.com/kubernetes/website/main/LICENSE
curl -sS https://api.github.com/repos/cncf/glossary/license
curl -sS https://api.github.com/repos/kubernetes/website/license
```

### 2. Статьи CNCF-глоссария (русские, Markdown-версия страницы)

```bash
curl -sS --compressed -o raw/cncf-md/kubernetes.md https://glossary.cncf.io/ru/kubernetes/index.md
# шаблон для всех 44 терминов:
# https://glossary.cncf.io/ru/<slug>/index.md
```

Список slug'ов берётся из `raw/cncf-glossary-ru-llms.txt` (44 термина + 3 служебные страницы).
Английские заголовки — из того же пути без `/ru`:

```bash
curl -sS --compressed https://glossary.cncf.io/kubernetes/index.md
```

### 3. Исходники Kubernetes-глоссария (по закреплённому коммиту)

```bash
SHA=73d2cca5ed07cb180b68a794a4f4a77faf907a67
curl -sS "https://api.github.com/repos/kubernetes/website/contents/content/ru/docs/reference/glossary?ref=$SHA"
curl -sS -o raw/k8s-md/pod.md "https://raw.githubusercontent.com/kubernetes/website/$SHA/content/ru/docs/reference/glossary/pod.md"
curl -sS -o raw/k8s-en-md/pod.md "https://raw.githubusercontent.com/kubernetes/website/$SHA/content/en/docs/reference/glossary/pod.md"
# шаблон для всех 37 файлов каталога content/ru/docs/reference/glossary (кроме index.md)
```

### 4. Проверка реестра

```bash
$PY - <<'PY'
import json
d = json.load(open('experiments/glossary/registry.json'))
req = ('canonical_term', 'ru_definition', 'source_url', 'license', 'license_evidence_url')
assert len(d['entries']) >= 25
assert all(all(e.get(k) for k in req) for e in d['entries'])
assert all(e['license'] == 'CC BY 4.0' for e in d['entries'])
print(len(d['entries']), d['counts'], d['terms_json_comparison']['already_present_count'],
      d['terms_json_comparison']['new_count'])
PY
```

Фактический вывод на 2026-10-28: `77 {'total': 77, 'cncf_glossary_ru': 44, 'kubernetes_glossary_ru': 37, 'merged_duplicates': 4} 5 72`.

Дополнительно проверено: все 44 значения `source_url` с доменом `glossary.cncf.io` вернули HTTP 200;
все 33 якоря `#term-…` присутствуют в сохранённой индексной странице
`raw/kubernetes-glossary-ru-index.html`.

## Состав `raw/`

| Путь | Что это | Размер |
|---|---|---|
| `cncf-glossary-ru-index.html` | индексная страница CNCF-глоссария (RU) | 43.7K |
| `cncf-glossary-en-index.html` | индексная страница CNCF-глоссария (EN) | 46.8K |
| `cncf-glossary-ru-llms.txt` | машинный индекс CNCF (RU), список терминов | 19.0K |
| `kubernetes-glossary-ru-index.html` | глоссарий Kubernetes (RU), все 160 терминов на одной странице | 764K |
| `kubernetes-glossary-en-index.html` | глоссарий Kubernetes (EN) | 693K |
| `cncf-md/*.md` | 44 русские статьи CNCF-глоссария (Markdown-версия страниц) | 248K |
| `k8s-md/*.md` | 37 исходных файлов `content/ru/docs/reference/glossary` | 148K |
| `k8s-en-md/*.md` | 36 английских файлов тех же терминов (для `canonical_term`); `container-runtime-interface.md` в EN-каталоге этого коммита нет | 144K |
| `licenses/LICENSE-cncf-glossary.txt` | Apache-2.0 (репозиторий `cncf/glossary`) | 11.1K |
| `licenses/LICENSE-kubernetes-website.txt` | CC BY 4.0 (репозиторий `kubernetes/website`) | 18.2K |

Каждый файл < 2 МБ. Сохранены только те страницы, которые реально разбирались при построении реестра.
Сжатые транспортные ответы уже распакованы `curl --compressed` (заголовок `content-encoding: gzip` в ответе).

## Ограничения

- Ревизия Kubernetes-глоссария закреплена коммитом `73d2cca5…` (HEAD `main` на 2026-10-28 10:07 UTC);
  английский файл `container-runtime-interface.md` в этом коммите в `content/en/...` отсутствует
  (термин слит с `cri.md`), поэтому английское название для этой записи взято из индексной страницы EN-глоссария.
- Поле `source_revision` для CNCF-записей — коммит и дата последнего изменения русского файла
  (`Последнее изменение …` в футере Markdown-страницы), а не дата сборки сайта.
- Определения Kubernetes-глоссария содержат краткое и полное описание, склеенные в один текст
  (на сайте они разделены кнопкой «[+]»).

## Приложение: полный список терминов (77)

### CNCF Glossary (RU), 44

| `canonical_term` | `ru_title` |
|---|---|
| Abstraction | Абстракция |
| Agile Software Development | Гибкий подход к разработке программного обеспечения (Agile) |
| API Gateway | Шлюз API |
| Application Programming Interface (API) | Программный интерфейс приложения (API) |
| Autoscaling | Автомасштабирование |
| Bare Metal Machine | Bare metal-компьютер |
| Blue Green Deployment | Сине-зеленое развертывание |
| Canary Deployment | Канареечное развертывание |
| Chaos Engineering | Хаос-инженерия |
| Client-Server Architecture | Архитектура клиент-сервер |
| Cloud Computing | Облачные вычисления |
| Cloud Native Apps | Нативные облачные приложения |
| Cloud Native Security | Нативная облачная безопасность |
| Cloud Native Technology | Нативная облачная технология |
| Cluster | Кластер |
| Containerization | Контейнеризация |
| Container Orchestration | Оркестрация контейнеров |
| Containers | Контейнер |
| Continuous Delivery (CD) | Непрерывная доставка (CD) |
| Continuous Deployment (CD) | Непрерывное развертывание (CD) |
| Continuous integration (CI) | Непрерывная интеграция (CI) |
| Datacenter | Дата-центр |
| DevOps | DevOps |
| DevSecOps | DevSecOps |
| Digital Certificate | Цифровой сертификат |
| Distributed Apps | Распределенные приложения |
| Distributed System | Распределенная система |
| eBPF | eBPF |
| Edge Computing | Граничные (edge) вычисления |
| Event Streaming | Потоковая передача событий |
| Function as a Service (FaaS) | Функция как сервис (FaaS) |
| Horizontal Scaling | Горизонтальное масштабирование |
| Idempotence | Идемпотентность |
| Immutable Infrastructure | Неизменяемая инфраструктура |
| Infrastructure as a Service (IaaS) | Инфраструктура как услуга (IaaS) |
| Infrastructure as Code (IaC) | Инфраструктура как код (IaC) |
| Ingress | Ingress |
| Kubernetes | Kubernetes |
| Microservices Architecture | Микросервисная архитектура |
| Monolithic Apps | Монолитные приложения |
| Multitenancy | Мультитенантность |
| Mutual Transport Layer Security (mTLS) | Протокол взаимной защиты транспортного уровня (mTLS) |
| Nodes | Узлы |
| Observability | Наблюдаемость |

### Kubernetes Glossary (RU), 33

| `canonical_term` | `ru_title` |
|---|---|
| API-initiated eviction | Вытеснение, инициированное через API |
| API server | API-сервер |
| Cloud Controller Manager | Cloud Controller Manager |
| Cloud Provider | Облачный провайдер |
| containerd | containerd |
| Container Runtime | Container Runtime |
| Container Runtime Interface | Container Runtime Interface |
| Controller | Контроллер (Controller) |
| Control Plane | Управляющий слой (Control Plane) |
| CRI-O | CRI-O |
| DaemonSet | DaemonSet |
| Deployment | Деплоймент (Deployment) |
| Docker | Docker |
| etcd | etcd |
| Finalizer | Финализатор (Finalizer) |
| Garbage Collection | Сбор мусора |
| Job | Job |
| kube-controller-manager | kube-controller-manager |
| Kubelet | Kubelet |
| kube-proxy | kube-proxy |
| kube-scheduler | kube-scheduler |
| Label | Лейбл |
| Name | Имя |
| Namespace | Пространство имен |
| Object | Объект |
| Pod | Pod |
| Pod Disruption | Нарушение работы подов |
| Secret | Секрет (Secret) |
| Selector | Селектор |
| Service | Сервис (Service) |
| ServiceAccount | ServiceAccount |
| UID | UID |
| Workload | Рабочая нагрузка |

## Дополнение к словарю скорера

`terms-supplement.json` — 72 термина из этих же глоссариев, которых нет в
`experiments/asr_eval/terms.json` (`terms-v1`, 366 терминов). Формат совпадает,
файл читается тем же `terms.Vocabulary.from_path`, но **не слит** с основным
словарём: иначе изменились бы метрики уже сделанных прогонов.

| | |
|---|---|
| Всего | 72 (62 `tools`, 10 `abbrev`) |
| Уже были в `terms-v1` | 5 (`kubernetes`, `containerd`, `crio`, `docker`, `etcd`) — исключены |
| Произношения и ошибки ASR | не заполнялись: в глоссариях их нет |
| Источник каждого | `source_url` + `source_revision` + `license` в записи |

Покрытие на Veche-bench (`data_simple`, 1288 реплик): дополнение находит 8
вхождений в 8 сегментах (`application-programming-interface-api`,
`continuous-integration-ci`, `devops`) против 83 вхождений `terms-v1` в 80
сегментах. То есть глоссарий расширяет словарь, но не увеличивает объём
терминных измерений на этом корпусе: встречи говорят «API», «CI», «DevOps», а не
«Abstraction» или «Multitenancy».

```bash
python3 -c "import sys;sys.path.insert(0,'experiments/asr_eval');import terms;\
v=terms.Vocabulary.from_path('experiments/glossary/terms-supplement.json');print(len(v.terms))"
```
