# 0008 — Neo4j connectivity boundary

## Status

Accepted

## Context

Demo-контур использует Neo4j как graph storage. Инфраструктура должна проверять
реальное Bolt-соединение и сохранять графовые данные при пересоздании контейнера,
но Neo4j не должен становиться публичным application API.

## Decision

Neo4j запускается как зафиксированный Compose-сервис с healthcheck, internal
network и named volume. Внутренний connectivity adapter владеет официальным
Neo4j driver, проверяет соединение через `verify_connectivity()` и отображает
ошибки driver в стабильную application-level ошибку.

## Consequences

- Готовность сервиса и сохранность данных проверяются на реальном Neo4j.
- Владение driver и его закрытие имеют одну явную границу.
- Neo4j доступен приложению только внутри Compose-сети; новый FastAPI endpoint не
  появляется.
- Для локальной диагностики требуется выполнить команду внутри Compose-сети.

## Alternatives considered

- Публичный FastAPI endpoint: не выбран, потому что graph storage является
  внутренней инфраструктурой, а не пользовательским API.
- Проверка только открытого TCP-порта: не выбрана, потому что она не подтверждает
  готовность Neo4j принимать аутентифицированные Cypher-запросы.
