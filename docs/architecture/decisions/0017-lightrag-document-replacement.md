# 0017 — Замена индексированного документа через lifecycle LightRAG

## Status

Accepted

## Context

Изменённый source должен стать новой immutable revision, но прежняя revision
обязана оставаться активной до полной замены индекса. Прямые записи application
в Qdrant и Neo4j нарушили бы storage lifecycle LightRAG и могли бы оставить
частично обновлённый граф после сбоя или потери worker lease.

## Decision

API атомарно создаёт revision и durable job и переводит document в `UPDATING`.
Worker под document advisory lock удаляет старую версию публичной операцией
`LightRAG.adelete_by_doc_id`, индексирует точную новую revision через
`LightRAG.ainsert`, затем одной fenced PostgreSQL-операцией активирует revision,
возвращает document в `READY` и завершает job.

## Consequences

Старый source остаётся канонически активным до успеха, а writes в vector/graph
storages принадлежат LightRAG. Между успешным удалением и повторной вставкой
внешний индекс может временно не содержать документ; durable retry и document
lock обеспечивают повтор операции, а PostgreSQL projection не переключается
частично.

## Alternatives considered

- Прямо обновлять Qdrant и Neo4j из application: нарушает границу LightRAG и
  дублирует его cleanup semantics.
- Сразу переключать `active_revision`: выдаёт новую revision до успешной
  индексации и не позволяет атомарно подтвердить replacement.
