# Протокол взаимной защиты транспортного уровня (mTLS)

> For AI agents: the complete glossary index is at [llms.txt](https://glossary.cncf.io/ru/llms.txt).
> Markdown versions of every glossary page are available by appending `index.md` to the page URL.

Протокол взаимной защиты транспортного уровня — это метод, используемый для аутентификации и шифрования сообщений, передаваемых между двумя [сервисами](https://glossary.cncf.io/service/). 
Протокол взаимной защиты транспортного уровня — это расширение стандартного [Протокола защиты транспортного уровня](https://glossary.cncf.io/transport-layer-security/) (TLS), 
но вместо проверки подлинности только одной стороны осуществляется валидация обеих.

## Какую проблему решает

[Микросервисы](https://glossary.cncf.io/ru/microservices-architecture/) взаимодействуют по сети, но этот канал коммуникации может быть взломан аналогично тому, как это происходит с Wi-Fi.
mTLS гарантирует, что неавторизованная сторона не сможет прослушивать канал коммуникации и выполнять несанкционированные действия. 

## Как именно решает проблему

mTLS гарантирует безопасность и аутентичность для трафика в обоих направлениях между клиентом и сервером, 
обеспечивая дополнительный уровень безопасности для пользователей, осуществляющих вход в сеть или приложение.
Он также проверяет соединения с клиентскими устройствами, которые не требуют входа в систему, например, относящимся к категории Интернета вещей (IoT).
mTLS может предотвратить атаки посредника (on-path), спуфинг-атаки, атаки с подстановкой учетных данных, атаки путем полного перебора (brute force) и другие.

---

- Canonical URL: https://glossary.cncf.io/ru/mutual-transport-layer-security/
- Status: Completed
- Последнее изменение August 2, 2024: ` [ru] Translate mutual-transport-layer-security.md (#3255) ` ([e99e08b](https://github.com/cncf/glossary/commit/e99e08bdfa47d696761f5cc999f20f0c7de02b70))
- Source: https://github.com/cncf/glossary/blob/main/content/ru/mutual-transport-layer-security.md
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
