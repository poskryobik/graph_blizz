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
Host-конфигурация по умолчанию использует локальную Ollama с моделью
`qwen3:1.7b` и endpoint `http://localhost:11434/v1` без обязательного API key.
Для `rag-api` Compose использует `host.docker.internal:11434`; Ollama должна
слушать адрес, доступный контейнеру, а не только loopback host.

## Consequences

Одна конфигурация endpoint/model обслуживает оба сценария без локальной загрузки
модели. Ошибки timeout, транспорта, HTTP и несовместимого протокола имеют
стабильные application-типы. Поддержка намеренно ограничена текстовым assistant
content; streaming, tools и multimodal responses потребуют отдельного решения.
Привязка Compose к host model server требует явно разрешить Ollama принимать
соединения с Docker bridge; endpoint, модель, timeout и необязательный API key
остаются переопределяемыми для других OpenAI-compatible провайдеров.

## Alternatives considered

- Использовать отдельные клиенты extraction и generation: отклонено из-за
  расхождения HTTP-контрактов и обработки ошибок.
- Загружать модель в `rag-api` или worker: отклонено из-за дублирования ресурсов
  и привязки application runtime к конкретному model stack.
- Подключить provider-specific SDK: отклонено, поскольку Demo требует общий
  OpenAI-compatible HTTP-контракт без зависимости от конкретного поставщика.
