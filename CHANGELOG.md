# Changelog

## [Unreleased]

### Fixed

- Запуск unit-тестов описан через воспроизводимое `uv`-окружение без зависимости от глобального `pytest`.
- Operational logging переведён на строгую allow-list схему: произвольные payload и сообщения больше не могут вывести секреты, а разрешённые метрики не редактируются по совпадению имени.

### Added

- Добавлен FastAPI application factory и независимый от внешних сервисов `GET /health/live`.
- Добавлен Compose-bootstrap `rag-api` и PostgreSQL с persistent volume, internal network и фактической проверкой БД через `GET /health/ready`.
- Добавлены отдельные unit, integration и e2e runner'ы и общий verification pipeline.
- Добавлены Ruff lint/format check и gradual mypy в общий verification pipeline.
- Добавлена типизированная конфигурация Demo/production для хранилищ, LightRAG, model endpoints и logging с безопасной проверкой production-секретов.
- Добавлено структурированное operational JSON-логирование с task-local context, измерением операций и строгой allow-list границей для полей и событий.
