# Changelog

## [Unreleased]

### Fixed

- Запуск unit-тестов описан через воспроизводимое `uv`-окружение без зависимости от глобального `pytest`.
- Operational logging переведён на строгую allow-list схему: произвольные payload и сообщения больше не могут вывести секреты, а разрешённые метрики не редактируются по совпадению имени.

### Added

- Добавлен единый async OpenAI-compatible LLM-клиент для extraction и generation с общей моделью, timeout и стабильными transport/HTTP/protocol ошибками.
- Добавлен единый async-клиент OpenAI-compatible embeddings для API и будущего worker с timeout, стабильными ошибками и проверкой batch-индексов и размерности векторов.
- Добавлен opt-in GPU Compose profile с vLLM endpoint `/v1/embeddings`, точной Giga Embeddings model, постоянным Hugging Face cache и раздельными host/container URL; обычный CI проверяет контракт без GPU.
- Добавлен internal-only Neo4j с persistent volume, реальным healthcheck и внутренним connectivity adapter без нового публичного application API.
- Добавлен Qdrant с persistent volume, web-доступом и внутренним connectivity adapter без нового публичного application API.
- Добавлены типизированные plain text и Markdown parsers с immutable результатами и registry для MIME types и расширений Demo.
- Добавлен доступ с host к Swagger UI, ReDoc, MinIO S3 API и web Console через настраиваемые Compose-порты; PostgreSQL остаётся internal-only.
- Добавлено сохранение original document source в MinIO до indexing с SHA-256 и S3 URI в metadata; source остаётся доступен после ошибки indexing.
- Добавлен постоянный реестр документов с workspace-local `source_key` и Demo-статусами жизненного цикла.
- Добавлены MinIO с persistent volume и S3-совместимый adapter `put/get/delete` для хранения объектов.
- Добавлены owner-only `POST /workspaces` и `GET /workspaces/{workspace_id}` через DemoOwner security boundary без клиентского доступа к `storage_key`.
- Добавлена заменяемая security boundary и `demo_owner` режим без `Authorization`, IdP и RBAC/ACL tables.
- Добавлено постоянное хранение workspace с серверным неизменяемым `storage_key` и операциями create/read/rename.
- Добавлены Alembic-миграции для отдельной PostgreSQL-схемы `graph_blizz` с идемпотентным обновлением до актуальной версии.
- Добавлен FastAPI application factory и независимый от внешних сервисов `GET /health/live`.
- Добавлен Compose-bootstrap `rag-api` и PostgreSQL с persistent volume, internal network и фактической проверкой БД через `GET /health/ready`.
- Добавлены отдельные unit, integration и e2e runner'ы и общий verification pipeline.
- Добавлены Ruff lint/format check и gradual mypy в общий verification pipeline.
- Добавлена типизированная конфигурация Demo/production для хранилищ, LightRAG, model endpoints и logging с безопасной проверкой production-секретов.
- Добавлено структурированное operational JSON-логирование с task-local context, измерением операций и строгой allow-list границей для полей и событий.
