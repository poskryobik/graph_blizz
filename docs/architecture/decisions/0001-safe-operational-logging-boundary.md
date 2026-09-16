# 0001 — Allow-list boundary для operational logging

## Status

Accepted

## Context

Рекурсивное удаление значений по именам ключей и регулярным выражениям не может
надёжно распознать все aliases, способы сериализации и произвольный document
content. Одновременно такие эвристики дают false positive для корректных полей
вроде `response_time_ms`, `content_type` и длинных числовых метрик.

## Decision

JSON formatter сериализует только фиксированные operational fields,
типизированные `OperationalEvent` и явно разрешённые extras с проверкой типа.
Произвольные message, mapping и exception text не сериализуются.

## Consequences

Logging boundary не зависит от полноты deny-list и сохраняет известные метрики.
Новые события и extras требуют явного добавления в схему; произвольный
диагностический текст через application logger недоступен.

## Alternatives considered

- Расширять deny-list aliases и регулярные выражения: остаются неизвестные пути
  обхода и false positive.
- Рекурсивно очищать все mapping: ключи и неоднозначные строковые значения сами
  остаются каналом утечки.
