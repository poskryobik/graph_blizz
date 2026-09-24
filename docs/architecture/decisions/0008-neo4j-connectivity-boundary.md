# 0008 — Neo4j connectivity boundary

## Status

Accepted

## Context

Demo-контур использует Neo4j как graph storage. Инфраструктура должна проверять
реальное Bolt-соединение и сохранять графовые данные при пересоздании контейнера,
но Neo4j не должен становиться публичным application API. При этом для
диагностики нужны стандартные интерфейсы Neo4j Browser/HTTP и Bolt.

## Decision

Neo4j запускается как зафиксированный Compose-сервис с healthcheck, named volume
и публикацией диагностических host-портов Browser/HTTP и Bolt. Application
connectivity остаётся внутренней: `rag-api` и `rag-worker` используют
`bolt://neo4j:7687`. Внутренний connectivity adapter владеет официальным
Neo4j driver, проверяет соединение через `verify_connectivity()` и отображает
ошибки driver в стабильную application-level ошибку.

## Consequences

- Готовность сервиса и сохранность данных проверяются на реальном Neo4j.
- Владение driver и его закрытие имеют одну явную границу.
- Neo4j доступен приложению только внутри Compose-сети; host-порты нужны для
  Neo4j Browser, ручных Cypher-запросов и диагностики в доверенной среде.
- Опубликованные порты не являются пользовательской аутентификацией: пароль
  Neo4j обязателен, а exposure ограничивается network/firewall вне Graph Blizz.
- Новый FastAPI endpoint для Neo4j не появляется.

## Alternatives considered

- Публичный FastAPI endpoint: не выбран, потому что graph storage является
  внутренней инфраструктурой, а не пользовательским API.
- Проверка только открытого TCP-порта: не выбрана, потому что она не подтверждает
  готовность Neo4j принимать аутентифицированные Cypher-запросы.
