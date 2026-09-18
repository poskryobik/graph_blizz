# 0015 — Durable indexing jobs в PostgreSQL

## Status

Accepted

## Context

Асинхронному indexing нужен восстанавливаемый после рестарта workflow, связанный с
конкретной неизменяемой ревизией документа. Одновременно две mutation-операции над
одним logical document не должны выполняться или ожидать выполнения.

## Decision

Хранить jobs в application schema PostgreSQL с проверяемым lifecycle, попытками,
lease/heartbeat и allow-listed кодом ошибки без произвольного текста. Traceback,
сообщения исключений и credentials в job не сохраняются. Частичный уникальный индекс запрещает
более одной mutation job в активном статусе для одного `document_id`.

## Consequences

Состояние очереди переживает рестарт, а будущий worker может атомарно claim-ить строки
без изменения схемы. Согласованность переходов остаётся обязанностью repository/worker,
но допустимые значения и ключевые инварианты дополнительно защищены constraints БД.

## Alternatives considered

- In-memory очередь: теряет задания при рестарте процесса.
- Внешний broker: добавляет инфраструктуру, не нужную для MVP с PostgreSQL queue.
