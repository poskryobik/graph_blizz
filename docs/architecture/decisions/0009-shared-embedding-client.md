# 0009 — Единый клиент embeddings

## Status

Accepted

## Context

`rag-api` и будущий worker должны получать embeddings из одного внешнего
OpenAI-compatible сервиса. Загрузка модели внутри процессов приложения создаст
разные runtime-пути, расход GPU-памяти и несовместимые правила обработки ошибок.

## Decision

Оба процесса используют один асинхронный adapter `EmbeddingClient`, настроенный
через `EmbeddingSettings`. Adapter вызывает `<base_url>/embeddings`, восстанавливает
порядок batch по индексам ответа и проверяет размерность каждого вектора.

## Consequences

Модель загружается только отдельным model-serving сервисом. Ошибки транспорта,
HTTP и протокола имеют стабильные application-типы; несовместимый ответ
отклоняется до передачи в graph/vector pipeline. Runtime зависит от HTTP-клиента,
а доступность embedding endpoint остаётся внешним эксплуатационным требованием.

## Alternatives considered

- Загружать модель в каждом процессе: отклонено из-за дублирования GPU-ресурсов
  и различий между API и worker.
- Использовать отдельные клиенты в API и worker: отклонено из-за расхождения
  validation и error mapping.
