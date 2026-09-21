# 0021 — Durable workspace reindex через document jobs

## Status

Accepted

## Context

После изменения index contract активные revisions помечаются `requires_reindex`,
но исходные объекты уже находятся в MinIO. Перестроение derived indexes должно
переживать рестарт worker, не требовать повторной загрузки и не снимать отметку до
подтверждённой записи в новый versioned namespace.

## Decision

Maintenance operation атомарно создаёт по одной `REINDEX_DOCUMENT` job для каждой
активной revision с `requires_reindex`, предварительно в той же транзакции сверяя
workspace с текущим application contract и помечая несовместимые revisions.
Существующий leased worker читает metadata
из PostgreSQL и original по сохранённому `object_uri`, индексирует его в namespace
текущего workspace contract и одной fenced-транзакцией завершает job, обновляет
version metadata revision и снимает `requires_reindex`.

## Consequences

Reindex использует существующие lease, retry и restart-recovery механизмы. Ошибка
оставляет revision помеченной и допускает безопасный повтор maintenance operation
после terminal failure. Старые derived namespaces остаются изолированными и могут
быть очищены отдельной эксплуатационной процедурой.

## Alternatives considered

- Одна workspace job: потребовала бы отдельной модели прогресса и повторной
  реализации leasing для множества документов.
- Повторный upload: нарушает требование использовать сохранённые originals и
  создаёт лишние immutable revisions.
