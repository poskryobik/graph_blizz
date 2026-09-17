# 0010 — Единый OpenAI-compatible LLM-клиент

## Status

Accepted

## Context

Extraction и generation должны обращаться к одной настраиваемой внешней модели.
Отдельные клиенты для этих сценариев создадут разные HTTP-контракты и правила
обработки ошибок, а загрузка модели в процессах приложения нарушит границу
внешнего model-serving сервиса.

## Decision

Extraction и generation используют один асинхронный `LLMClient`, настроенный
через `ExternalLLMSettings`. Клиент вызывает `<base_url>/chat/completions`,
передаёт OpenAI-compatible messages и возвращает текст первого assistant choice.

## Consequences

Одна конфигурация endpoint/model обслуживает оба сценария без локальной загрузки
модели. Ошибки timeout, транспорта, HTTP и несовместимого протокола имеют
стабильные application-типы. Поддержка намеренно ограничена текстовым assistant
content; streaming, tools и multimodal responses потребуют отдельного решения.

## Alternatives considered

- Использовать отдельные клиенты extraction и generation: отклонено из-за
  расхождения HTTP-контрактов и обработки ошибок.
- Загружать модель в `rag-api` или worker: отклонено из-за дублирования ресурсов
  и привязки application runtime к конкретному model stack.
- Подключить provider-specific SDK: отклонено, поскольку Demo требует общий
  OpenAI-compatible HTTP-контракт без зависимости от конкретного поставщика.
