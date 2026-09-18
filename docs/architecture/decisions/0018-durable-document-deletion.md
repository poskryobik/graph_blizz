# 0018 — Durable document deletion

## Status

Accepted

## Context

Удаление документа затрагивает PostgreSQL и несколько хранилищ LightRAG, поэтому
его нельзя надёжно завершить в HTTP-транзакции. При сбое документ не должен
выглядеть удалённым до подтверждения LightRAG, а исходные immutable revisions
нужны для аудита и безопасного повторного выполнения.

## Decision

`DELETE` под документным lock атомарно переводит `READY` в `DELETING` и создаёт
job типа `DELETE_DOCUMENT` для текущей active revision. Worker использует общий
leased lifecycle, document lock и поддерживаемый `LightRAG.adelete_by_doc_id`,
после чего одной fenced-транзакцией переводит документ в `DELETED`, а job — в
`SUCCEEDED`.

Source revision rows и объекты сохраняются бессрочно в рамках простой retention
policy MVP. Их очистка не входит в delete workflow.

## Consequences

- Сбой или retry не публикует преждевременный `DELETED`.
- Повторный DELETE в `DELETING` возвращает тот же активный job, а в `DELETED` —
  устойчивый terminal результат.
- Повтор worker после частичного сбоя может снова вызвать идемпотентное удаление
  LightRAG.
- Source storage продолжает занимать место до появления отдельной retention job.

## Alternatives considered

- Синхронное удаление из HTTP: не обеспечивает атомарность между PostgreSQL и
  LightRAG и плохо восстанавливается после рестарта.
- Прямые записи в vector/graph storage: зависят от внутренних деталей LightRAG и
  могут оставить его хранилища несогласованными.
- Немедленная очистка revisions и objects: лишает retry точной source identity и
  усложняет восстановление.
