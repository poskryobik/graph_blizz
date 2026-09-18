# 0013 — Demo query service с workspace-scoped sources

## Status

Accepted

## Context

Demo API должен выполнять Graph RAG query без users, memberships и JWT,
но не может принимать от клиента физический namespace. Ответу
нужны минимальные публичные sources, хотя точная трассировка
использованных chunks отложена до отдельного этапа MVP.

## Decision

Независимый от HTTP `QueryService` принимает только
`AuthorizedWorkspaceContext`, получает runtime через
`LightRAGRuntimeRegistry` и выполняет LightRAG query. Минимальный
Demo-список sources строится из документов со статусом `READY`,
выбранных repository строго по `workspace_id` авторизованного
контекста.

## Consequences

Транспорт не знает physical namespace, а query path можно переиспользовать
при будущей замене security adapter. Sources не могут сослаться на
неготовый или чужой workspace-документ. На Demo-этапе список означает
набор доступных проиндексированных источников, а не точные citations
конкретного ответа.

## Alternatives considered

- Передавать namespace из HTTP payload: отклонено из-за риска cross-workspace
  доступа.
- Парсить citations из текста LLM: отклонено как ненадёжный контракт;
  точная source traceability будет добавлена отдельно.
