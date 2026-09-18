# 0012 — Реестр LightRAG runtime по авторизованному workspace

## Status

Accepted

## Context

LightRAG runtime владеет соединениями с несколькими хранилищами и должен жить
дольше одного запроса. При этом физический namespace обязан определяться
серверным `storage_key`, а конкурентные запросы к одному workspace не должны
создавать несколько runtime. Завершение процесса должно освобождать все уже
созданные ресурсы и не допускать новые runtime во время остановки.

## Decision

Application process владеет одним `LightRAGRuntimeRegistry`. Его публичный метод
получения runtime принимает только `AuthorizedWorkspaceContext`, лениво создаёт
ровно один runtime на `workspace_id` с namespace из `storage_key` и сериализует
создание с закрытием registry. После начала `close()` новые вызовы `get()`
отклоняются; `close()` идемпотентно завершает все созданные runtime.

## Consequences

Клиент не может передать физический namespace через registry API, workspace
изолированы, а lifecycle ресурсов становится явным. Инициализация runtime в
одном registry сериализована, что упрощает определённость гонки `get()`/`close()`
и согласуется с process-global storage environment LightRAG 1.5.7, но не даёт
параллельно инициализировать разные workspace в одном процессе.

## Alternatives considered

- Принимать `storage_key` в `get()`: отклонено, поскольку это переносит security
  boundary физического namespace на вызывающий код.
- Хранить общие initialization tasks вне lifecycle lock: отклонено из-за более
  сложной семантики отмены и гонки с `close()` без практической параллельности,
  так как factory уже сериализует process-global storage environment.
