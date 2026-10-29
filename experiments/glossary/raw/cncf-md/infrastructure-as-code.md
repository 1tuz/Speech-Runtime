# Инфраструктура как код (IaC)

> For AI agents: the complete glossary index is at [llms.txt](https://glossary.cncf.io/ru/llms.txt).
> Markdown versions of every glossary page are available by appending `index.md` to the page URL.

Инфраструктура как код (Infrastructure as Code) — подход, при котором описание инфраструктуры хранится в виде одного или нескольких файлов. 
Этот подход пришел на смену традиционной модели, в которой инфраструктура как услуга (Infrastructure as a Service) предоставляется вручную, 
обычно с помощью shell-скриптов или других инструментов конфигурирования.

## Какую проблему решает

Подход к разработке нативных облачных приложений требует, чтобы инфраструктура была одноразовой и воспроизводимой. 
Также она должна [масштабироваться](https://glossary.cncf.io/scalability/) по запросу автоматизированным и воспроизводимым образом, желательно без вмешательства человека. 
Ручное управление инфраструктурой не удовлетворяет требованиям к скорости и масштабированию, которые предъявляются к [нативным облачным приложениям](https://glossary.cncf.io/ru/cloud-native-apps/). 
Изменения инфраструктуры, сделанные вручную, не обладают воспроизводимостью, кроме того, они плохо масштабируются и приводят к ошибкам в конфигурации.

## Как именно решает проблему

Представляя ресурсы дата-центра (серверы, балансировщики нагрузки, подсети и т. п.) в виде кода, 
инфраструктурные команды получают единый источник истины для всех конфигураций и возможность 
управлять центром обработки данных с помощью пайплайнов [CI](https://glossary.cncf.io/ru/continuous-integration/)/[CD](https://glossary.cncf.io/ru/continuous-delivery/), 
используя при этом контроль версий и стратегии развертывания.

---

- Canonical URL: https://glossary.cncf.io/ru/infrastructure-as-code/
- Status: Completed
- Последнее изменение October 9, 2024: `` [ru] Localize `Infrastructure-as-code` (#3227) `` ([a7624f5](https://github.com/cncf/glossary/commit/a7624f52747d8febedc994c6817301d6898bac41))
- Source: https://github.com/cncf/glossary/blob/main/content/ru/infrastructure-as-code.md
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
