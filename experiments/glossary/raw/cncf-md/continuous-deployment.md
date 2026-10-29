# Непрерывное развертывание (CD)

> For AI agents: the complete glossary index is at [llms.txt](https://glossary.cncf.io/ru/llms.txt).
> Markdown versions of every glossary page are available by appending `index.md` to the page URL.

Непрерывное развертывание (Continuous Deployment, CD) развивает идеи [непрерывной доставки](https://glossary.cncf.io/ru/continuous-delivery/), 
позволяя выкладывать готовое программное обеспечение непосредственно в production.
Непрерывное развертывание (CD) идет рука об руку с [непрерывной интеграцией](https://glossary.cncf.io/ru/continuous-integration/) (CI), поэтому обычно их объединяют в единый процесс CI/CD.
CI помогает убедиться, что изменения, внесенные в код приложения, работают как и было задумано, 
а CD автоматически развертывает приложение в целевые окружения (от тестовых до production).

## Какую проблему решает

Выпуск новых версий программного обеспечения может быть трудоемким процессом и сопровождаться ошибками.
Поэтому многие организации стараются уменьшить количество релизов, чтобы избежать инцидентов в production 
и сократить время, в течение которого инженеры должны оставаться на связи (в т. ч. в нерабочие часы).
Традиционные модели развертывания программного обеспечения приводят к тому, что организации попадают в порочный круг, 
в котором процесс выпуска программного обеспечения не отвечает потребностям организации 
ни в контексте стабильности, ни в контексте скорости реализации новых функций.

## Как именно решает проблему

Автоматизируя релизный цикл и заставляя организации чаще развертывать ПО в production, 
CD делает для команд эксплуатации то же самое, что CI для команд разработки.
То есть автоматизирует этапы развертывания ПО в production, сокращая вероятность ошибок и негативных последствий и снижая общий риск.
Кроме того, организации привыкают к частым изменениям в production и лучше к ним адаптируются, что повышает стабильность.

## Связанные термины

* [Непрерывная интеграция](https://glossary.cncf.io/ru/continuous-integration/)
* [Непрерывная доставка](https://glossary.cncf.io/ru/continuous-delivery/)

---

- Canonical URL: https://glossary.cncf.io/ru/continuous-deployment/
- Status: Completed
- Последнее изменение December 26, 2023: ` Review initial Russian content ` ([8c3a264](https://github.com/cncf/glossary/commit/8c3a26457ac876b9c9ce688c68a7682a5ad7db01))
- Source: https://github.com/cncf/glossary/blob/main/content/ru/continuous-deployment.md
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
