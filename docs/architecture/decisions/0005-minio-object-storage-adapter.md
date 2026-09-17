# 0005 — MinIO через S3-совместимый ObjectStorage adapter

## Status

Accepted

## Context

Demo-контур должен сохранять исходные объекты вне PostgreSQL, переживать
пересоздание контейнера MinIO и не связывать прикладной код с деталями SDK.
Существующая конфигурация уже описывает S3-совместимые endpoint, region, bucket,
статические credentials и path-style addressing.

## Decision

MinIO запускается в Docker Compose с named volume и healthcheck. Синхронный
`ObjectStore` использует S3 API через `boto3`, предоставляет только операции
`put/get/delete` и преобразует ошибки SDK в стабильные ошибки storage boundary.
Создание bucket остаётся задачей bootstrap/deployment, а не побочным эффектом
конструктора или каждой операции адаптера.

## Consequences

Прикладной код не зависит от MinIO-specific API, а тот же адаптер можно направить
на другое S3-совместимое хранилище. Вызовы блокирующие и рассчитаны на текущий
Demo; при использовании из async request path их потребуется выполнять вне event
loop. Перед первой записью bucket должен быть создан инфраструктурой.

## Alternatives considered

- Использовать MinIO-specific Python SDK: он проще для одного провайдера, но не
  использует уже заданную S3/path-style конфигурацию и сильнее связывает boundary
  с MinIO.
- Автоматически создавать bucket в конструкторе: это добавило бы сетевой побочный
  эффект и потребовало бы административных прав для обычных runtime credentials.
