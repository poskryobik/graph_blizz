# Graph Blizz

Минимальный backend Graph RAG приложения на FastAPI.

## Установка и запуск

```bash
uv sync --dev
uv run uvicorn backend.app:app --reload
```

Для запуска текущего Demo-контура с PostgreSQL и MinIO:

```bash
docker compose up -d --build --wait
```

Все сервисы находятся в изолированной internal-сети, PostgreSQL и MinIO не
публикуют порты на host, а `rag-api` доступен на порту `GRAPH_BLIZZ_API_PORT`
(по умолчанию `8000`). Данные сохраняются в named volumes `postgres_data` и
`minio_data`. Compose использует только локальные demo-секреты по умолчанию; их
можно заменить через `GRAPH_BLIZZ_POSTGRES__PASSWORD`,
`GRAPH_BLIZZ_MINIO__ACCESS_KEY` и `GRAPH_BLIZZ_MINIO__SECRET_KEY` в локальном
`.env`. Остановка без `--volumes` сохраняет данные, а
`docker compose down --volumes` удаляет их.

MinIO доступен приложению по `http://minio:9000`. Runtime adapter
`backend.storage.ObjectStore` выполняет S3-совместимые `put/get/delete`; bucket
`GRAPH_BLIZZ_MINIO__BUCKET` должен быть создан при bootstrap окружения.
Original document source сохраняется до запуска indexing по Demo-ключу
`workspace/{workspace_id}/document/{document_id}/source`; его S3 URI и SHA-256
фиксируются в metadata документа, а ошибка последующего indexing source не удаляет.

## Проверки

Ruff, mypy и pytest устанавливаются в проектное окружение командой `uv sync --dev`.
Проверки разделены pytest-маркерами `unit`, `integration` и `e2e`. Запустить
отдельную группу или весь pipeline можно из любого рабочего каталога:

```bash
./scripts/lint.sh
./scripts/typecheck.sh
./scripts/verify-unit.sh
./scripts/verify-integration.sh
./scripts/verify-e2e.sh
./scripts/verify.sh
```

`lint.sh` запускает Ruff lint и format check, а `typecheck.sh` — mypy в gradual
режиме. Общий pipeline выполняет обе проверки перед тестами. Каждый test runner
выбирает только свой каталог и marker. При падении любой проверки её runner и
общий pipeline завершаются с ненулевым кодом.

## Health API

`GET /health/live` возвращает `{"status":"healthy"}` без обращения к внешним
сервисам. `GET /health/ready` выполняет `SELECT 1` в PostgreSQL и возвращает
`{"status":"ready"}` с HTTP 200 либо `{"status":"unready"}` с HTTP 503.

## Конфигурация

Настройки загружаются из переменных с префиксом `GRAPH_BLIZZ_`; вложенные поля
разделяются двойным подчёркиванием. Например,
`GRAPH_BLIZZ_POSTGRES__HOST=postgres`. Полный локальный шаблон находится в
`.env.example`; его можно скопировать в `.env`, которую приложение загружает при
старте.

По умолчанию приложение запускается в `development` с явным режимом
`GRAPH_BLIZZ_AUTH__MODE=demo_owner` и не подключается к внешним сервисам при
создании FastAPI application. Для `production` режим `demo_owner`, отсутствующие,
короткие и placeholder-секреты отклоняются при загрузке. Секреты следует
генерировать или передавать через secret manager, не добавляя их в репозиторий.

В `demo_owner` режиме `Authorization` header и внешний IdP не нужны. Каждый
endpoint может получить настроенного bootstrap owner через application
`IdentityResolver`, а `DemoOwnerAccessPolicy` выдаёт ему полный набор Demo/MVP
permissions и связывает операции с server-resolved workspace namespace.
Идентификатор owner задаётся через `GRAPH_BLIZZ_AUTH__DEMO_OWNER_ID` (по
умолчанию `demo-owner`). Режим не создаёт и не читает таблицы principals,
memberships, RBAC или ACL. Другие auth modes не имеют неявного fallback в
`demo_owner`.

## Workspace API

В режиме `demo_owner` workspace создаётся запросом `POST /workspaces` с JSON-полями
`name` и `slug`, а читается по UUID через `GET /workspaces/{workspace_id}`. Оба
endpoint работают без `Authorization` header и перед доступом получают
workspace-контекст через `DemoOwnerAccessPolicy`. Поле `storage_key` создаётся
сервером, не принимается в запросе и не возвращается публичным API.

## Миграции PostgreSQL

Application metadata хранится в отдельной схеме `graph_blizz`, которой управляет
линейная история Alembic. После настройки `GRAPH_BLIZZ_POSTGRES__*` примените все
миграции перед запуском новой версии приложения:

```bash
uv run alembic upgrade head
```

Команда безопасна для повторного запуска на уже актуальной базе. Новая миграция
создаётся командой `uv run alembic revision -m "описание"`; application objects
должны явно использовать схему `graph_blizz`. Решение зафиксировано в
[ADR 0003](docs/architecture/decisions/0003-versioned-postgresql-migrations.md).

## Operational logging

Приложение настраивает logger `graph_blizz` при создании FastAPI application.
Формат по умолчанию — JSON; уровень, формат и имя сервиса задаются через
`GRAPH_BLIZZ_LOGGING__LEVEL`, `GRAPH_BLIZZ_LOGGING__FORMAT` и
`GRAPH_BLIZZ_LOGGING__SERVICE_NAME`. Контекст операции может содержать
`request_id`, `workspace_id`, `document_id`, `operation`, `duration_ms` и
`result`. JSON formatter пропускает только эти поля, типизированные имена событий
`OperationalEvent` и явно разрешённые operational extras. Произвольные message,
mapping, exception text и неизвестные extra-поля не пересекают logging boundary;
credentials, Authorization, document content, embeddings, prompts и полные model
responses никогда не логируются. Решение и его ограничения зафиксированы в
[ADR 0001](docs/architecture/decisions/0001-safe-operational-logging-boundary.md).

## Архитектурные решения

- [ADR 0001: allow-list boundary для operational logging](docs/architecture/decisions/0001-safe-operational-logging-boundary.md)
- [ADR 0002: PostgreSQL readiness в Docker Compose](docs/architecture/decisions/0002-postgresql-compose-readiness.md)
- [ADR 0003: версионируемые миграции PostgreSQL через Alembic](docs/architecture/decisions/0003-versioned-postgresql-migrations.md)
- [ADR 0004: security boundary и DemoOwner adapter](docs/architecture/decisions/0004-demo-owner-security-boundary.md)
- [ADR 0005: MinIO через S3-совместимый ObjectStorage adapter](docs/architecture/decisions/0005-minio-object-storage-adapter.md)
