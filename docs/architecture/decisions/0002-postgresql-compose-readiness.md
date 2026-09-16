# 0002 — PostgreSQL readiness в Docker Compose

## Status

Accepted

## Context

Demo должен запускать `rag-api` только после готовности PostgreSQL, сохранять
данные при пересоздании контейнера и различать работоспособность процесса и
доступность обязательной зависимости. Создание application schema и управление
её версиями относятся к отдельному этапу миграций.

## Decision

PostgreSQL и `rag-api` работают в одной internal Compose-сети, данные PostgreSQL
хранятся в named volume, а `rag-api` зависит от успешного PostgreSQL healthcheck.
`GET /health/ready` выполняет короткий `SELECT 1` через отдельное соединение и
возвращает HTTP 503 при недоступности PostgreSQL; `GET /health/live` остаётся
чисто процессной проверкой без внешних вызовов.

## Consequences

Compose не запускает API до готовности БД, readiness отражает фактическую
доступность PostgreSQL, а пересоздание контейнера не удаляет данные. Каждый
readiness-запрос создаёт соединение; это приемлемо для Demo, но позднее может
быть заменено проверкой через общий connection pool. Таблицы приложения в этом
решении не создаются.

## Alternatives considered

- Считать успешный старт API достаточной readiness-проверкой: сбой PostgreSQL
  оставался бы незаметен для оркестратора.
- Проверять только TCP-порт PostgreSQL: открытый порт не подтверждает успешную
  аутентификацию и выполнение SQL.
- Создать application tables при старте контейнера: это смешало бы bootstrap
  инфраструктуры с versioned migrations следующего этапа.
