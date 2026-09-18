# 0011 — LightRAG Core как граница storage и model runtime

## Status

Accepted

## Context

Demo indexing и query должны использовать LightRAG Core, но хранение
данных уже разделено между PostgreSQL, Qdrant и Neo4j. Embeddings и
generation также уже имеют общие application-адаптеры к внешним
OpenAI-compatible endpoint. Runtime не должен загружать модели
локально или давать клиенту контроль над physical namespace.

## Decision

Фабрика создаёт pinned LightRAG Core с `PGKVStorage`,
`PGDocStatusStorage`, `QdrantVectorDBStorage` и `Neo4JStorage`. Она
адаптирует существующие `EmbeddingClient` и `LLMClient` к callback-контракту
LightRAG и передаёт server-generated workspace namespace явным параметром.
Создание workspace-scoped registry остаётся отдельным этапом. Временная
установка native storage environment сериализуется process-wide lock, который
одинаково действует между async tasks, event loops и threads.
`POSTGRES_WORKSPACE` при этом намеренно остаётся пустым: pinned LightRAG 1.5.7
держит один process-wide PostgreSQL pool, поэтому namespace задаётся только
на уровне каждого storage из явного `LightRAG(workspace=...)`.

## Consequences

Semantic graph/vector writes проходят через поддерживаемый lifecycle
LightRAG, а model serving остаётся вне процесса application. LightRAG storage
получают свои native environment settings из валидированной application
конфигурации на время инициализации. Фабрика владеет созданными
model-клиентами с момента успешного создания каждого ресурса и закрывает
частично созданный набор при ошибке следующего конструктора или storage init.
Process-wide сериализация ограничивает только создание runtime; это принятое
ограничение LightRAG 1.5.7, читающего настройки из `os.environ`. Исходные
значения environment всегда восстанавливаются перед освобождением lock.
Одновременно живые runtime могут разделять PostgreSQL pool, но сохраняют разные
workspace-фильтры; refcount `ClientManager` позволяет закрыть один runtime без
нарушения работы остальных. При ошибке или отмене инициализации фабрика с
ограниченным временем очищает также storages, успевшие инициализироваться до
того, как LightRAG сменил общий статус на `INITIALIZED`, и затем сохраняет
исходный exception, включая `CancelledError`.

## Alternatives considered

- Вызывать Qdrant, Neo4j и PostgreSQL напрямую из application-сервисов:
  отклонено, так как это обходит LightRAG lifecycle.
- Использовать встроенные model helpers LightRAG: отклонено, чтобы не
  дублировать общие application-клиенты и их error mapping.
- Инициализировать несколько runtime параллельно через process-global
  `os.environ`: отклонено из-за смешивания credentials и workspace между ними.
- Сразу реализовать runtime registry: отклонено как scope следующего
  этапа.
