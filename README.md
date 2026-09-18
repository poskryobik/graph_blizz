# Graph Blizz

Минимальный backend Graph RAG приложения на FastAPI.

## Установка и запуск

```bash
uv sync --dev
uv run uvicorn backend.app:app --reload
```

Для запуска текущего Demo-контура с PostgreSQL, MinIO, Qdrant и Neo4j:

```bash
./scripts/init.sh
```

Скрипт проверяет Docker/Compose, при отсутствии создаёт `.env` из
`.env.example`, запускает сервисы, применяет Alembic migrations, создаёт bucket
MinIO, проверяет API и выполняет пробные запросы к настроенным embedding и LLM
endpoint. Только после успешных проверок он сообщает о готовности Demo. Его можно
безопасно запускать повторно: данные в named volumes не удаляются.

По умолчанию embedding endpoint указывает на opt-in GPU-сервис. Перед
инициализацией запустите его командой
`docker compose --profile gpu up -d vllm-embeddings` либо задайте доступный из
`rag-api` OpenAI-compatible endpoint через
`GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL` (и при необходимости передайте
`GRAPH_BLIZZ_EMBEDDING__API_KEY`). Аналогично, локальная Ollama должна быть
доступна контейнеру по `GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL`. Если
любой model endpoint недоступен или возвращает несовместимый ответ, `init.sh`
завершится с ошибкой и не объявит Demo готовым.

После запуска полный сценарий без `Authorization` header можно повторить
следующими командами:

```bash
workspace_id="$(curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"name":"Demo workspace","slug":"demo-workspace"}' \
  http://localhost:8000/workspaces | uv run python -c \
  'import json,sys; print(json.load(sys.stdin)["id"])')"

printf 'The Aurora Finch observatory is located on Cedar Island.\n' > /tmp/aurora-finch.txt
curl --fail --silent --show-error \
  -F 'file=@/tmp/aurora-finch.txt;type=text/plain' \
  "http://localhost:8000/v1/workspaces/${workspace_id}/documents"

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"query":"Where is the Aurora Finch observatory located?"}' \
  "http://localhost:8000/v1/workspaces/${workspace_id}/query"
```

Demo работает строго в owner-only режиме: все запросы выполняются от одного
настроенного bootstrap owner без входа в систему. Это не multi-user security:
нет изоляции пользователей, memberships, RBAC или ACL, поэтому режим нельзя
использовать как production-аутентификацию.

PostgreSQL и Neo4j остаются доступны только сервисам в изолированной
internal-сети.
Web-интерфейсы доступны с host по следующим адресам:

- API: `http://localhost:8000`, Swagger UI: `http://localhost:8000/docs`, ReDoc: `http://localhost:8000/redoc`;
- MinIO S3 API: `http://localhost:9000`;
- MinIO Console: `http://localhost:9001`.
- Qdrant Web UI и REST API: `http://localhost:6333/dashboard` и `http://localhost:6333`.

Host-порты можно переопределить через `GRAPH_BLIZZ_API_PORT`,
`GRAPH_BLIZZ_MINIO_API_PORT`, `GRAPH_BLIZZ_MINIO_CONSOLE_PORT` и
`GRAPH_BLIZZ_QDRANT_PORT` соответственно.
Например, `GRAPH_BLIZZ_API_PORT=8080 docker compose up -d --build --wait`
опубликует Swagger UI по адресу `http://localhost:8080/docs`. Данные сохраняются
в named volumes `postgres_data`, `minio_data`, `qdrant_data` и `neo4j_data`. Compose использует только
локальные demo-секреты по умолчанию; их
можно заменить через `GRAPH_BLIZZ_POSTGRES__PASSWORD`,
`GRAPH_BLIZZ_MINIO__ACCESS_KEY`, `GRAPH_BLIZZ_MINIO__SECRET_KEY` и
`GRAPH_BLIZZ_NEO4J__PASSWORD` в локальном
`.env`. Остановка без `--volumes` сохраняет данные, а
`docker compose down --volumes` удаляет их.

### GPU embeddings через vLLM

GPU-сервис не входит в обычный `docker compose up`: он запускается только через
profile `gpu` и предоставляет OpenAI-compatible endpoint
`POST http://localhost:8001/v1/embeddings`:

```bash
docker compose --profile gpu up -d vllm-embeddings
curl http://localhost:8001/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"ai-sage/Giga-Embeddings-instruct-480M-0826","input":["Граф знаний"]}'
```

Нужны NVIDIA GPU, рабочий NVIDIA Container Toolkit и доступ к Hugging Face при
первой загрузке. Модель строго зафиксирована как
`ai-sage/Giga-Embeddings-instruct-480M-0826`, а загруженные файлы сохраняются в
named volume `huggingface_cache`. Host-порт задаётся через
`GRAPH_BLIZZ_EMBEDDING_PORT` (по умолчанию `8001`), а host endpoint для тестов и
клиентов — через `GRAPH_BLIZZ_EMBEDDING_HOST_BASE_URL`. Внутри Compose
`rag-api` использует `http://vllm-embeddings:8000/v1`; его можно переопределить
только отдельной переменной `GRAPH_BLIZZ_COMPOSE_EMBEDDING_BASE_URL`.
Для локального запуска приложения вне Compose передайте этот host endpoint в
`GRAPH_BLIZZ_EMBEDDING__BASE_URL`.

Реальная GPU-проверка отделена от обычного CI и запускается явно после готовности
сервиса:

```bash
GRAPH_BLIZZ_RUN_GPU_TESTS=1 uv run --frozen pytest -q \
  -m gpu tests/integration/test_vllm_embeddings.py
```

Обычные unit-тесты проверяют Compose и OpenAI API contract через локальный stub,
а `verify-integration.sh` исключает marker `gpu`.

Приложение обращается к endpoint только через общий async
`backend.embeddings.EmbeddingClient`; клиент поддерживает single и batch вызовы,
проверяет `GRAPH_BLIZZ_EMBEDDING__DIMENSION` и не загружает embedding-модель в
процесс `rag-api` или worker.

LightRAG Core зафиксирован на версии `1.5.7`. Production factory
`backend.rag.create_lightrag_runtime` подключает его internal KV/status storage к
PostgreSQL, vector storage к Qdrant, graph storage к Neo4j, а model callbacks —
к общим `EmbeddingClient` и `LLMClient`. Для создания runtime обязательны
server-generated namespace из ASCII-букв, цифр и `_`, а также точная
`GRAPH_BLIZZ_EMBEDDING__DIMENSION`; для стандартной Giga Embeddings модели Compose
явно задаёт `1024`. Локальные runtime-файлы размещаются под
`GRAPH_BLIZZ_LIGHTRAG__WORKING_DIR/<namespace>`. Runtime необходимо закрывать
через `await runtime.close()` или `async with`; workspace registry создаётся
отдельным следующим этапом. Фабрика владеет обоими model-клиентами с момента
их создания и закрывает уже созданные ресурсы при любой последующей ошибке.
Поскольку LightRAG 1.5.7 читает native storage settings из process environment,
инициализация runtime сериализована в пределах процесса, включая разные event
loops и threads; после инициализации исходное environment восстанавливается.
PostgreSQL pool остаётся общим для процесса, но workspace хранится на каждом
LightRAG storage, поэтому одновременно живые runtime изолированы, а закрытие
одного не закрывает pool до освобождения последней ссылки. Отмена создания
runtime пробрасывается вызывающему коду после ограниченной по времени очистки
частично инициализированных storages и model-клиентов.

Extraction и generation используют один async
`backend.llm.LLMClient` и одну модель через OpenAI-compatible endpoint
`POST <base_url>/chat/completions`. Endpoint, модель, API key и timeout задаются
через `GRAPH_BLIZZ_EXTERNAL_LLM__BASE_URL`,
`GRAPH_BLIZZ_EXTERNAL_LLM__MODEL`, `GRAPH_BLIZZ_EXTERNAL_LLM__API_KEY` и
`GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS`. Клиент поддерживает текстовые
OpenAI-compatible messages, возвращает content первого assistant choice и не
загружает модель в процесс приложения.

По умолчанию при запуске приложения на host используется локальная Ollama с
моделью `qwen3:1.7b` по адресу `http://localhost:11434/v1`; API key не нужен.
В `rag-api` Compose передаёт endpoint
`http://host.docker.internal:11434/v1`. На Linux alias добавляется только сервису
`rag-api`. Ollama при таком запуске должна слушать адрес, доступный контейнеру,
а не только loopback host. Compose endpoint можно изменить через
`GRAPH_BLIZZ_COMPOSE_EXTERNAL_LLM_BASE_URL`; модель и timeout — через обычные
`GRAPH_BLIZZ_EXTERNAL_LLM__MODEL` и
`GRAPH_BLIZZ_EXTERNAL_LLM__TIMEOUT_SECONDS`. Для другого OpenAI-compatible
провайдера также можно задать `GRAPH_BLIZZ_EXTERNAL_LLM__API_KEY`.

MinIO доступен приложению по `http://minio:9000`. Runtime adapter
`backend.storage.ObjectStore` выполняет S3-совместимые `put/get/delete`; bucket
`GRAPH_BLIZZ_MINIO__BUCKET` должен быть создан при bootstrap окружения.
Original document source сохраняется до запуска indexing по Demo-ключу
`workspace/{workspace_id}/document/{document_id}/source`; его S3 URI и SHA-256
фиксируются в metadata документа, а ошибка последующего indexing source не удаляет.

Qdrant доступен приложению по `http://qdrant:6333`. Внутренний adapter
`backend.storage.QdrantConnectivity` проверяет соединение и явно закрывает
официальный client; отдельный публичный application endpoint для Qdrant не
предоставляется.

Neo4j доступен приложению по `bolt://neo4j:7687`. Внутренний adapter
`backend.storage.Neo4jConnectivity` проверяет Bolt-соединение и явно закрывает
официальный driver. Сервис не публикует host-порты и отдельный публичный
application endpoint для Neo4j не предоставляется.

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
./scripts/verify-demo.sh
./scripts/verify.sh
```

`lint.sh` запускает Ruff lint и format check, а `typecheck.sh` — mypy в gradual
режиме. Общий pipeline выполняет обе проверки перед тестами. Каждый test runner
выбирает только свой каталог и marker. При падении любой проверки её runner и
общий pipeline завершаются с ненулевым кодом.
`verify-demo.sh` последовательно запускает unit и integration regression suites,
а затем обязательный полный Demo Graph RAG happy path; он также останавливается
на первой ошибке и выводит название текущего этапа.

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

## Document API

В режиме `demo_owner` UTF-8 документы поддерживаемых Demo-форматов (`text/plain`
и `text/markdown`, включая зарегистрированные текстовые расширения) загружаются
multipart-запросом `POST /v1/workspaces/{workspace_id}/documents` в поле `file`.
Размер source ограничен 10 MiB. Запрос синхронно сохраняет original source и
выполняет inline indexing, поэтому успешный ответ `201` уже содержит статус
`READY` и может выполняться долго. Ошибка indexing возвращает `502`, а сохранённая
metadata переходит в `FAILED`; временная ошибка object storage возвращает `503`.
Каждый новый source сохраняется под неизменяемым ключом с номером ревизии;
логический `document_id` остаётся стабильным, а Demo API читает активную ревизию.

Список и отдельная metadata читаются через
`GET /v1/workspaces/{workspace_id}/documents` и
`GET /v1/workspaces/{workspace_id}/documents/{document_id}`. Все операции проходят
через `AuthorizedWorkspaceContext`, работают без JWT в `demo_owner` и возвращают
`404` для отсутствующего workspace или документа в выбранном workspace. Клиент не
может задавать или читать `storage_key`, `source_key`, object URI и content hash.

## Query API

В `demo_owner` режиме Graph RAG запрос выполняется без `Authorization` header:

```bash
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{"query":"Что сказано в загруженных документах?"}' \
  http://localhost:8000/v1/workspaces/12345678-1234-5678-1234-567812345678/query
```

Ответ содержит `answer`, UUID полей `workspace_id` и `request_id`, а также
минимальные `document_id`/`filename` для проиндексированных (`READY`)
документов этого workspace. Поля physical namespace не принимаются: runtime
выбирается сервером только после Demo-owner авторизации. Ошибка runtime
возвращает безопасный `502` без внутренних storage-идентификаторов.

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
- [ADR 0006: базовая граница парсеров для Demo](docs/architecture/decisions/0006-demo-parser-boundary.md)
- [ADR 0007: Qdrant connectivity boundary](docs/architecture/decisions/0007-qdrant-connectivity-boundary.md)
- [ADR 0008: Neo4j connectivity boundary](docs/architecture/decisions/0008-neo4j-connectivity-boundary.md)
- [ADR 0009: единый клиент embeddings](docs/architecture/decisions/0009-shared-embedding-client.md)
- [ADR 0010: единый OpenAI-compatible LLM-клиент](docs/architecture/decisions/0010-shared-openai-compatible-llm-client.md)
- [ADR 0011: LightRAG Core как граница storage и model runtime](docs/architecture/decisions/0011-lightrag-storage-runtime-wiring.md)
- [ADR 0012: реестр LightRAG runtime по авторизованному workspace](docs/architecture/decisions/0012-workspace-runtime-registry.md)
- [ADR 0013: Demo query service с workspace-scoped sources](docs/architecture/decisions/0013-demo-query-service.md)
- [ADR 0014: неизменяемые ревизии документов](docs/architecture/decisions/0014-immutable-document-revisions.md)
